"""F&O OI activity service: NSE bhavcopy ingest, backfill, daily selection runs.

Flow per NSE session D (docs/fno-activity.md):

1. ingest(D): F&O bhavcopy (UDiFF, else legacy format), full equity bhavcopy
   (spot, volume, delivery), index closes -> fno_futures / fno_underlying /
   fno_option_summary. Raw files come from the cache or nsearchives.
2. compute_run(D): the user's selection rule (fno/selection.py, unchanged)
   on D and the previous stored session, plus informational context
   (fno/enrich.py), notices and flags -> fno_runs / fno_signals.

The watchlist ("existing positions") is evaluated on request with the same
unchanged rule, so editing it never needs a rerun.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import zipfile
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from .. import database as db
from ..config import config_hash, describe_config
from ..timeutil import now_ist
from . import store
from .config import DEFAULT_FNO_CONFIG, FNO_PARAM_META, INDEX_CHART_CODES, FnoConfig
from .enrich import enrich
from .fetch import FetchError, NseArchiveFetcher, eq_url, fo_urls, index_url, raw_path
from .options import option_summaries
from .parse import ParseError, parse_equity, parse_fo, parse_index, zip_csv_text
from .selection import front_expiry, process_bhavcopy, process_watchlist

if TYPE_CHECKING:  # pragma: no cover
    from ..services import Services

log = logging.getLogger(__name__)

SOURCES = [
    {"name": "NSE F&O bhavcopy (UDiFF)", "url": "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip", "use": "futures close + OI for the selection rule; options OI / settlement for PCR, OI walls, ATM IV", "from": "July 2024"},
    {"name": "NSE F&O bhavcopy (legacy format)", "url": "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip", "use": "same, for sessions before the UDiFF format", "from": "before July 2024"},
    {"name": "NSE full equity bhavcopy", "url": "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv", "use": "spot close, high / low, traded quantity, delivery %", "from": "all sessions"},
    {"name": "NSE index closes", "url": "https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv", "use": "spot of index futures (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50)", "from": "all sessions"},
]

LONG = ("Strong Long Buildup", "Long Buildup", "Short Covering")
SHORT = ("Strong Short Buildup", "Short Buildup", "Long Unwinding")


def _fin(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class FnoService:
    def __init__(self, services: "Services", cfg: FnoConfig = DEFAULT_FNO_CONFIG, fetcher: NseArchiveFetcher | None = None):
        self.s = services
        self.engine = services.engine
        self.cfg = cfg
        self.fetcher = fetcher or NseArchiveFetcher()
        self.raw_root = services.settings.cache_dir / "bhavcopy"
        self.backfill_sessions = int(os.environ.get("PAS_FNO_BACKFILL_SESSIONS", "260"))
        self.raw_retention_days = int(os.environ.get("PAS_FNO_RAW_RETENTION_DAYS", "45"))
        self.recent_retry_days = 5  # missing files of the last N calendar days are retried (publication delay)
        self.job: dict[str, Any] = {"state": "idle"}
        self._lock = threading.Lock()

    @property
    def config_hash(self) -> str:
        return config_hash(self.cfg)

    def register(self) -> None:
        from ..scanner import register_config

        register_config(self.engine, self.cfg)

    # ------------------------------------------------------------ sessions
    def latest_session(self, now=None) -> date:
        return self.s.calendar.latest_completed_bar_end("1d", now or now_ist()).date()

    def target_sessions(self, n: int, end: date) -> list[date]:
        cal = self.s.calendar
        d = end if cal.is_trading_day(end) else cal.previous_trading_day(end)
        out: list[date] = []
        while len(out) < max(1, n):
            out.append(d)
            d = cal.previous_trading_day(d)
        return sorted(out)

    def _recent(self, d: date) -> bool:
        return (now_ist().date() - d).days <= self.recent_retry_days

    def _needs_ingest(self, d: date) -> bool:
        sess = store.get_session(self.engine, d)
        if sess is None or sess["fo_status"] == "error":
            return True
        if sess["fo_status"] == "missing":
            return self._recent(d)
        if (sess.get("eq_status") != "ok" or sess.get("idx_status") != "ok") and self._recent(d):
            return True
        if sess.get("summary_config_hash") != self.config_hash:
            return any(raw_path(self.raw_root, "fo", d, src).exists() for src, _ in fo_urls(d))
        return False

    # ------------------------------------------------------------ files
    def _load_fo(self, d: date, network: bool) -> tuple[str | None, str | None]:
        urls = fo_urls(d)
        if d < date(2024, 7, 1):
            urls = list(reversed(urls))
        for source, _ in urls:
            p = raw_path(self.raw_root, "fo", d, source)
            if p.exists():
                return zip_csv_text(p.read_bytes()), source
        if not network:
            return None, None
        for source, url in urls:
            body = self.fetcher.get(url, "zip")
            if body is not None:
                p = raw_path(self.raw_root, "fo", d, source)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(body)
                return zip_csv_text(body), source
        return None, None

    def _load_csv(self, kind: str, d: date, url: str, network: bool) -> str | None:
        p = raw_path(self.raw_root, kind, d)
        if p.exists():
            return p.read_bytes().decode("utf-8-sig", errors="replace")
        if not network:
            return None
        body = self.fetcher.get(url, "csv")
        if body is None:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return body.decode("utf-8-sig", errors="replace")

    def _cleanup_raw(self) -> int:
        if self.raw_retention_days <= 0 or not self.raw_root.exists():
            return 0
        cutoff = (now_ist().date() - timedelta(days=self.raw_retention_days)).strftime("%Y%m%d")
        removed = 0
        for p in self.raw_root.glob("*/*.csv*"):
            if p.name[:8].isdigit() and p.name[:8] < cutoff:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # ------------------------------------------------------------ ingest
    def ingest(self, d: date, network: bool | None = None) -> str:
        """Store one session. Returns ok / pending (not published yet) / missing."""
        network = self.s.settings.network_enabled if network is None else network
        recent = self._recent(d)
        fo_text, fo_source = self._load_fo(d, network)
        if fo_text is None:
            if not recent:
                store.upsert_session(self.engine, d, fo_status="missing", error="NSE has no F&O bhavcopy for this date (market holiday or not published)")
                return "missing"
            return "pending"
        fo = parse_fo(fo_text)
        notes: list[str] = []
        equity = index = None
        eq_text = self._load_csv("eq", d, eq_url(d), network)
        if eq_text is not None:
            try:
                equity = parse_equity(eq_text)
            except (ParseError, ValueError) as exc:
                notes.append(f"equity bhavcopy: {exc}")
        idx_text = self._load_csv("idx", d, index_url(d), network)
        if idx_text is not None:
            try:
                index = parse_index(idx_text)
            except (ParseError, ValueError) as exc:
                notes.append(f"index file: {exc}")
        stock_syms = set(fo.loc[fo["INSTRUMENT"].isin(["FUTSTK", "OPTSTK"]), "SYMBOL"].astype(str))
        spot: dict[str, float] = {}
        if equity is not None:
            spot.update({s: float(c) for s, c in zip(equity["symbol"], equity["close"]) if s in stock_syms and pd.notna(c)})
        if index is not None:
            spot.update({s: float(c) for s, c in zip(index["symbol"], index["close"]) if pd.notna(c)})
        summaries = option_summaries(fo, spot, d, self.cfg)
        with self.engine.begin() as conn:
            n_fut = store.write_futures(conn, d, fo)
            n_eq, n_idx = store.write_underlying(conn, d, equity, index, stock_syms)
            n_opt = store.write_option_summaries(conn, d, summaries, self.config_hash)
        missing_state = "pending" if recent else "missing"
        store.upsert_session(
            self.engine,
            d,
            fo_status="ok",
            fo_source=fo_source,
            fo_rows=int(len(fo)),
            futures_rows=n_fut,
            option_symbols=n_opt,
            eq_status="ok" if equity is not None else ("error" if eq_text is not None else missing_state),
            eq_rows=n_eq,
            idx_status="ok" if index is not None else ("error" if idx_text is not None else missing_state),
            idx_rows=n_idx,
            summary_config_hash=self.config_hash,
            error="; ".join(notes) or None,
        )
        return "ok"

    # ------------------------------------------------------------ selection runs
    def _rows(self, d: date, prev: date, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df is None or df.empty:
            return []
        records = df.to_dict("records")
        hist = store.history(self.engine, sorted({str(r["SYMBOL"]) for r in records}), d, self.cfg.rank_history_days + 10)
        lots_today, lots_prev = store.contract_lots(self.engine, d), store.contract_lots(self.engine, prev)
        out = []
        for rank, r in enumerate(records, 1):
            flags = []
            key = (str(r["SYMBOL"]), r["EXPIRY_DT"])
            a, b = lots_prev.get(key), lots_today.get(key)
            if a and b and a != b:
                flags.append({"code": "lot_size_changed", "text": f"Lot size {a:,.0f} on {prev} -> {b:,.0f} on {d}: probable corporate action (bonus / split). The price and OI changes compare different share bases; the row is kept as the original rule produced it."})
            if r["OPEN_INT_yesterday"] == 0:
                flags.append({"code": "prev_oi_zero", "text": "Previous-session OI was 0, so the OI change is infinite (original rule)."})
            out.append(
                {
                    "rank": rank,
                    "symbol": str(r["SYMBOL"]),
                    "instrument": str(r["INSTRUMENT"]),
                    "expiry": r["EXPIRY_DT"],
                    "close_today": _fin(r["CLOSE_today"]),
                    "oi_today": _fin(r["OPEN_INT_today"]),
                    "close_prev": _fin(r["CLOSE_yesterday"]),
                    "oi_prev": _fin(r["OPEN_INT_yesterday"]),
                    "price_change_pct": float(r["Price_Change_Pct"]) if pd.notna(r["Price_Change_Pct"]) else None,
                    "oi_change_pct": float(r["OI_Change_Pct"]) if pd.notna(r["OI_Change_Pct"]) else None,
                    "signal": r.get("Signal") if "Signal" in r else r.get("Status"),
                    "priority": int(r["Priority"]) if "Priority" in r and pd.notna(r["Priority"]) else None,
                    "enrichment": enrich(r, d, hist, self.cfg),
                    "flags": flags,
                }
            )
        return out

    def _notices(self, d: date, prev: date, fronts: tuple[str | None, str | None]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        expected = self.s.calendar.previous_trading_day(d)
        if prev != expected:
            out.append({"level": "warn", "code": "gap", "text": f"The previous stored F&O session is {prev}; {expected} is missing, so the changes span more than one session."})
        if fronts[0] and fronts[1] and fronts[0] != fronts[1]:
            out.append(
                {
                    "level": "warn",
                    "code": "rollover",
                    "text": f"Front-month expiry changed: {fronts[1]} on {prev} -> {fronts[0]} on {d}. The rule compares each day's own front month, so no contract matches and this session has no signals (original behaviour, unchanged).",
                }
            )
        sess = store.get_session(self.engine, d) or {}
        if sess.get("eq_status") != "ok":
            out.append({"level": "info", "code": "equity_missing", "text": f"Equity bhavcopy for {d} is {sess.get('eq_status') or 'missing'}: stock spot, basis, volume, delivery and stock ATM IV are empty."})
        if sess.get("idx_status") != "ok":
            out.append({"level": "info", "code": "index_missing", "text": f"Index closes for {d} are {sess.get('idx_status') or 'missing'}: index spot, basis and index ATM IV are empty."})
        n_prev = sum(1 for x in store.sessions(self.engine, "ok") if x["trade_date"] < d.isoformat())
        if n_prev < 20:
            out.append({"level": "info", "code": "short_history", "text": f"Only {n_prev} earlier sessions are stored; 10 / 20-session ranges, volume ratio, delivery surge and 20-day OI change need 10-20, ranks need 10."})
        return out

    def compute_run(self, d: date) -> dict[str, Any] | None:
        prev = store.previous_ok_session(self.engine, d)
        if prev is None:
            return None
        latest_df, prev_df = store.futures_frame(self.engine, d), store.futures_frame(self.engine, prev)
        sig = process_bhavcopy(latest_df, prev_df, self.cfg)
        fronts = (front_expiry(latest_df), front_expiry(prev_df))
        rows = self._rows(d, prev, sig)
        store.write_run(self.engine, d, self.config_hash, self.cfg.version, prev, fronts, self._notices(d, prev, fronts), rows)
        return {"trade_date": d.isoformat(), "prev_date": prev.isoformat(), "signals": len(rows)}

    # ------------------------------------------------------------ jobs
    def start_update(self, background: bool = True, sessions: int | None = None, recompute: bool = False, trigger: str = "manual") -> dict[str, Any]:
        with self._lock:
            if self.job.get("state") == "running":
                return dict(self.job)
            self.job = {"state": "running", "trigger": trigger, "phase": "download", "started_at": db.now_ist_iso(), "done": 0, "total": 0, "errors": []}

        def run() -> None:
            job = self.job
            try:
                job.update(self._update(sessions or self.backfill_sessions, recompute, job))
                job.update(state="done", phase="done", finished_at=db.now_ist_iso())
            except Exception as exc:
                log.exception("F&O bhavcopy update failed")
                job.update(state="error", error=f"{type(exc).__name__}: {exc}", finished_at=db.now_ist_iso())

        if background:
            threading.Thread(target=run, name="fno-update", daemon=True).start()
        else:
            run()
        return dict(self.job)

    def _update(self, n: int, recompute: bool, job: dict[str, Any]) -> dict[str, Any]:
        network = self.s.settings.network_enabled
        targets = self.target_sessions(n, self.latest_session())
        todo = [d for d in targets if self._needs_ingest(d)]
        job.update(total=len(todo), done=0)
        ingested: set[date] = set()
        fails = 0
        for i, d in enumerate(todo):
            job["progress"] = f"{d.isoformat()} ({i + 1}/{len(todo)})"
            try:
                if self.ingest(d, network) == "ok":
                    ingested.add(d)
                fails = 0
            except FetchError as exc:
                fails += 1
                job["errors"].append(f"{d}: {exc}")
                if fails >= 3:
                    raise FetchError(f"NSE archives unreachable ({fails} sessions in a row failed); last: {exc}") from exc
            except (ParseError, zipfile.BadZipFile, ValueError, KeyError) as exc:
                store.upsert_session(self.engine, d, fo_status="error", error=f"{type(exc).__name__}: {exc}")
                job["errors"].append(f"{d}: {type(exc).__name__}: {exc}")
            job["done"] = i + 1
        job["phase"] = "selection"
        ok = [date.fromisoformat(x["trade_date"]) for x in store.sessions(self.engine, "ok")]
        computed = 0
        prev: date | None = None
        for d in ok:
            run = store.get_run(self.engine, d, self.config_hash)
            stale = run is None or run.get("prev_date") != (prev.isoformat() if prev else None) or d in ingested or (prev in ingested if prev else False)
            if prev is not None and (recompute or stale):
                self.compute_run(d)
                computed += 1
            prev = d
        removed = self._cleanup_raw()
        return {"ingested": len(ingested), "runs_computed": computed, "raw_removed": removed}

    def scheduled_refresh(self) -> str:
        """Evening job: fetch the latest session's files once NSE publishes them."""
        if self.job.get("state") == "running":
            return "busy"
        d = self.latest_session()
        sess = store.get_session(self.engine, d)
        if sess and sess["fo_status"] == "ok" and sess.get("eq_status") == "ok" and sess.get("idx_status") == "ok" and store.get_run(self.engine, d, self.config_hash):
            return "up to date"
        self.start_update(background=False, sessions=10, trigger="scheduler")
        return str(self.job.get("state"))

    # ------------------------------------------------------------ read API
    def status(self) -> dict[str, Any]:
        rows = store.sessions(self.engine, status=None)
        ok = [r for r in rows if r["fo_status"] == "ok"]
        latest = self.latest_session()
        return {
            "job": dict(self.job),
            "sessions": {
                "stored": len(ok),
                "first": ok[0]["trade_date"] if ok else None,
                "last": ok[-1]["trade_date"] if ok else None,
                "missing": [r["trade_date"] for r in rows if r["fo_status"] == "missing"][-10:],
                "errors": [{"trade_date": r["trade_date"], "error": r["error"]} for r in rows if r["fo_status"] == "error"][-10:],
                "partial": [{"trade_date": r["trade_date"], "equity": r["eq_status"], "index": r["idx_status"]} for r in ok if r["eq_status"] != "ok" or r["idx_status"] != "ok"][-10:],
            },
            "latest_completed_session": latest.isoformat(),
            "backfill_sessions": self.backfill_sessions,
            "network": self.s.settings.network_enabled,
            "config": {"version": self.cfg.version, "hash": self.config_hash},
            "sources": SOURCES,
        }

    def config_payload(self) -> dict[str, Any]:
        return {"version": self.cfg.version, "hash": self.config_hash, "params": describe_config(self.cfg, FNO_PARAM_META)}

    def _api_row(self, x: dict[str, Any], d: date, companies: dict[str, str]) -> dict[str, Any]:
        sym, inst = x["symbol"], x["instrument"]
        if inst == "FUTIDX":
            code = INDEX_CHART_CODES.get(sym)
            chart = {"instrument": "index", "symbol": code} if code else None
        else:
            chart = {"instrument": "stock", "symbol": sym}
        sig = x["signal"]
        oi_chg = x["oi_change_pct"]
        return {
            "rank": x["rank"],
            "symbol": sym,
            "company": companies.get(sym),
            "instrument": inst,
            "expiry": x["expiry"],
            "signal": sig,
            "priority": x.get("priority"),
            "side": "long" if sig in LONG else "short" if sig in SHORT else "neutral",
            "close_today": x["close_today"],
            "oi_today": x["oi_today"],
            "close_prev": x["close_prev"],
            "oi_prev": x["oi_prev"],
            "price_change_pct": _fin(x["price_change_pct"]),
            "oi_change_pct": _fin(oi_chg),
            "oi_change_infinite": oi_chg is not None and math.isinf(oi_chg),
            "context": x["enrichment"],
            "flags": x["flags"],
            "chart": chart,
            "t": int(self.s.calendar.session_open(d).timestamp()),
        }

    def dates(self) -> list[dict[str, Any]]:
        return store.run_dates(self.engine, self.config_hash)

    def activity(self, d: date | None = None) -> dict[str, Any]:
        chash = self.config_hash
        runs = store.run_dates(self.engine, chash)
        watch = store.watchlist(self.engine)
        base = {"config": {"version": self.cfg.version, "hash": chash}, "rules": self.rules(), "watchlist_symbols": watch}
        if not runs:
            return {**base, "trade_date": None, "prev_date": None, "notices": [], "signals": [], "watchlist": [], "dates": []}
        d = d or date.fromisoformat(runs[0]["trade_date"])
        run = store.get_run(self.engine, d, chash)
        if run is None:
            raise KeyError(f"no F&O run for {d}")
        prev = date.fromisoformat(run["prev_date"]) if run["prev_date"] else None
        sig = [{**x, "expiry": x["expiry_label"], "enrichment": db.loads(x["enrichment_json"]) or {}, "flags": db.loads(x["flags_json"]) or []} for x in store.run_signals(self.engine, d, chash)]
        wl: list[dict[str, Any]] = []
        if watch and prev is not None:
            wdf = process_watchlist(store.futures_frame(self.engine, d), store.futures_frame(self.engine, prev), watch)
            if not wdf.empty:
                wdf = wdf.sort_values("SYMBOL")
            wl = self._rows(d, prev, wdf)
        companies = store.company_names(self.engine, sorted({x["symbol"] for x in sig + wl}))
        sess = store.get_session(self.engine, d) or {}
        return {
            **base,
            "trade_date": d.isoformat(),
            "prev_date": run["prev_date"],
            "front_expiry": {"latest": run["latest_front_expiry"], "prev": run["prev_front_expiry"]},
            "computed_at": run["created_at"],
            "data": {"fo_source": sess.get("fo_source"), "equity": sess.get("eq_status"), "index": sess.get("idx_status")},
            "notices": db.loads(run["notices_json"]) or [],
            "signals": [self._api_row(x, d, companies) for x in sig],
            "watchlist": [self._api_row(x, d, companies) for x in wl],
            "watchlist_missing": sorted(set(watch) - {x["symbol"] for x in wl}),
            "dates": runs[:400],
        }

    def rules(self) -> list[dict[str, Any]]:
        c = self.cfg
        return [
            {"signal": "Strong Long Buildup", "oi": f"> {c.oi_strong_pct:g}%", "stock": f"> +{c.price_strong_stock_pct:g}%", "index": f"> +{c.price_strong_index_pct:g}%"},
            {"signal": "Long Buildup", "oi": f"> {c.oi_normal_pct:g}%", "stock": f"> +{c.price_normal_stock_pct:g}%", "index": f"> +{c.price_normal_index_pct:g}%"},
            {"signal": "Strong Short Buildup", "oi": f"> {c.oi_strong_pct:g}%", "stock": f"< -{c.price_strong_stock_pct:g}%", "index": f"< -{c.price_strong_index_pct:g}%"},
            {"signal": "Short Buildup", "oi": f"> {c.oi_normal_pct:g}%", "stock": f"< -{c.price_normal_stock_pct:g}%", "index": f"< -{c.price_normal_index_pct:g}%"},
        ]

    # ------------------------------------------------------------ watchlist
    def set_watchlist(self, symbols: list[str]) -> list[str]:
        return store.set_watchlist(self.engine, symbols)

    def known_symbols(self) -> list[str]:
        sess = store.sessions(self.engine, "ok")
        return store.futures_symbols(self.engine, date.fromisoformat(sess[-1]["trade_date"])) if sess else []
