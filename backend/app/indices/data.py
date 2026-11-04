"""Index market data (E2).

* Daily (and weekly, resampled) candles for every equity index in the registry
  come from the **official NSE index history** (``/api/historicalOR/indicesHistory``,
  ``csv=true`` returns up to one year per request). One provider for all indices
  keeps the RRG and Index Analysis on a single, consistent price-return basis.
* Each index's cached closes are verified against the official reference closes
  published with the catalogue (previous day, 1 week, 30 days, 1 year ago).
* Intraday (1H / 4H) index candles come from Yahoo **only** for tickers listed in
  ``data/index_intraday_candidates.json`` whose daily closes match the official
  reference closes. Unverified or missing mappings are reported as unavailable;
  nothing is substituted (no ETF or proxy).
* Weekly candles: Monday-labelled NSE weeks (09:15 IST bar start) aggregated
  from completed daily candles; a week is used only once its last trading day
  has closed (same completion rule as stock weekly bars).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests

from .. import database as db
from ..config import Settings
from ..data.calendar import NSECalendar
from ..data.ohlcv import clean_ohlcv, prepare_series
from ..data.store import OhlcvStore, merge_incremental
from ..providers.base import ADJ_CLOSE, OHLCV_COLUMNS
from ..timeutil import IST, now_ist, parse_iso
from .registry import IndexRegistry

log = logging.getLogger(__name__)

PROVIDER = "nse_indices"
BASIS = "index_price_return"  # official price-return index levels (no dividend adjustment exists for PR indices)
KEY_PREFIX = "IDX:"
HISTORY_MAX = "max"  # market_data.history_period of an index cache backfilled to the start of NSE history
_SESSION = pd.Timedelta(hours=9, minutes=15)
REF_TOLERANCE = 0.0005  # 0.05 % (official values are rounded to 2 decimals)
INTRADAY_TOLERANCE = 0.003  # Yahoo vs official close (Yahoo may round / revise)


def index_key(code: str) -> str:
    return f"{KEY_PREFIX}{code}"


class IndexDataError(RuntimeError):
    pass


# ------------------------------------------------------------------ NSE history
def parse_history(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """NSE index history records -> canonical daily frame (09:15 IST bar starts)."""
    recs = []
    for r in rows:
        try:
            d = datetime.strptime(str(r["EOD_TIMESTAMP"]).title(), "%d-%b-%Y").date()
            c = float(r["EOD_CLOSE_INDEX_VAL"])
            # Older years publish only the close (open/high/low empty or 0): those
            # bars become flat close-only candles below, never dropped.
            o, h, l = (float(r[k]) if r.get(k) not in (None, "", "-") else 0.0 for k in ("EOD_OPEN_INDEX_VAL", "EOD_HIGH_INDEX_VAL", "EOD_LOW_INDEX_VAL"))
        except (KeyError, TypeError, ValueError):
            continue
        vol = r.get("HIT_TRADED_QTY")
        try:
            v = float(vol) if vol not in (None, "", "-") else np.nan
        except (TypeError, ValueError):
            v = np.nan
        recs.append((pd.Timestamp(d).tz_localize(IST) + _SESSION, o, h, l, c, v))
    if not recs:
        idx = pd.DatetimeIndex([], tz=IST, name="ts")
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS + [ADJ_CLOSE]}, index=idx)
    df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Some indices publish open/high/low as 0 on days without a real intraday range: use the close.
    for k in ("open", "high", "low"):
        bad = ~(df[k] > 0)
        df.loc[bad, k] = df.loc[bad, "close"]
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    df[ADJ_CLOSE] = df["close"]  # price-return index: no corporate-action adjustment exists
    return df.astype("float64")


class NseIndexHistory:
    """Official index history. ``fetch`` splits ranges into <= 1-year requests."""

    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._session: requests.Session | None = None
        self._lock = threading.Lock()

    def _warm(self) -> requests.Session:
        from ..data.universe import BROWSER_HEADERS

        if self._session is None:
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            s.get("https://www.nseindia.com/", timeout=30)
            s.get(self.settings.index_history_page, timeout=30)  # sets the cookies the API expects
            self._session = s
        return self._session

    def _get(self, name: str, start: date, end: date) -> list[dict[str, Any]]:
        params = {"indexType": name, "from": start.strftime("%d-%m-%Y"), "to": end.strftime("%d-%m-%Y"), "csv": "true"}
        last: Exception | None = None
        for attempt in range(self.settings.download_max_attempts):
            try:
                with self._lock:
                    s = self._warm()
                    r = s.get(self.settings.index_history_url, params=params, timeout=60)
                    self._sleep(self.settings.index_request_delay_s)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    data = r.json().get("data")
                    return data if isinstance(data, list) else []
                last = IndexDataError(f"HTTP {r.status_code} for {name} {params['from']}..{params['to']}")
                self._session = None  # cookies may have expired
            except (requests.RequestException, ValueError) as exc:
                last = exc
                self._session = None
            self._sleep(self.settings.download_backoff_base_s * (2**attempt) + random.uniform(0, 0.5))
        raise IndexDataError(str(last))

    def fetch(self, name: str, start: date, end: date) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        cur = start
        while cur <= end:
            stop = min(end, cur + timedelta(days=364))
            rows += self._get(name, cur, stop)
            cur = stop + timedelta(days=1)
        return parse_history(rows)

    def fetch_back(self, name: str, before: date, floor: date, empty_stop: int) -> pd.DataFrame:
        """History strictly before ``before``, one year per request going back,
        until ``empty_stop`` consecutive years are empty (the index did not exist
        yet; one empty answer can be a transient NSE glitch) or ``floor``."""
        frames: list[pd.DataFrame] = []
        stop = before - timedelta(days=1)
        empties = 0
        while stop >= floor and empties < max(1, empty_stop):
            start = max(floor, stop - timedelta(days=364))
            got = self.fetch(name, start, stop)  # one request (<= 1 year)
            if got.empty:
                empties += 1
            else:
                empties = 0
                frames.append(got)
            stop = start - timedelta(days=1)
        if not frames:
            return parse_history([])
        out = pd.concat(frames).sort_index()
        return out[~out.index.duplicated(keep="last")]


# ------------------------------------------------------------------ helpers
def verify_against_reference(frame: pd.DataFrame, reference: dict[str, Any], tolerance: float = REF_TOLERANCE) -> dict[str, Any]:
    """Compare cached closes with the official reference closes of the catalogue."""
    checks = []
    if frame is None or frame.empty:
        return {"status": "no_data", "checks": []}
    by_date = {ts.date(): float(c) for ts, c in frame["close"].items()}
    for key, ref in (reference or {}).items():
        if not ref:
            continue
        try:
            d = datetime.strptime(ref["date"].title(), "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        got = by_date.get(d)
        if got is None:
            checks.append({"ref": key, "date": d.isoformat(), "official": ref["close"], "cached": None, "ok": None})
            continue
        ok = abs(got - ref["close"]) <= tolerance * max(abs(ref["close"]), 1e-9)
        checks.append({"ref": key, "date": d.isoformat(), "official": ref["close"], "cached": round(got, 4), "ok": bool(ok)})
    decided = [c for c in checks if c["ok"] is not None]
    if not decided:
        return {"status": "unverified", "checks": checks}
    return {"status": "verified" if all(c["ok"] for c in decided) else "mismatch", "checks": checks, "matched": sum(1 for c in decided if c["ok"]), "compared": len(decided)}


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Completed-or-not weekly bars from daily candles (Monday 09:15 IST labels)."""
    if daily.empty:
        return daily.copy()
    idx = daily.index.tz_convert(IST)
    monday = (idx.normalize() - pd.to_timedelta(idx.day_of_week, unit="D")) + _SESSION
    g = daily.groupby(monday, sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(min_count=1),
        }
    )
    if ADJ_CLOSE in daily.columns:
        out[ADJ_CLOSE] = g[ADJ_CLOSE].last()
    out.index = pd.DatetimeIndex(out.index).tz_convert(IST)
    out.index.name = "ts"
    return out


def weekly_from_daily(daily_completed: pd.DataFrame, cutoff: datetime, calendar: NSECalendar) -> pd.DataFrame:
    """Weekly bars whose week has fully closed by ``cutoff``."""
    w = resample_weekly(daily_completed)
    if w.empty:
        return w
    ends = calendar.bar_ends(w.index, "1w")
    return w[np.asarray(ends <= pd.Timestamp(cutoff).tz_convert(IST))]


@dataclass
class RefreshState:
    state: str = "idle"  # idle | running | done | error
    started_at: str | None = None
    finished_at: str | None = None
    total: int = 0
    done: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class IndexDataService:
    def __init__(self, engine, settings: Settings, calendar: Callable[[], NSECalendar], yahoo_market=None, history: NseIndexHistory | None = None):
        self.engine = engine
        self.settings = settings
        self._calendar = calendar
        self.registry = IndexRegistry(engine)
        self.store = OhlcvStore(settings.cache_dir, engine, PROVIDER, BASIS)
        self.history = history or NseIndexHistory(settings)
        self.yahoo = yahoo_market  # MarketDataService for verified intraday tickers
        self.state = RefreshState()
        self._lock = threading.Lock()
        # Cleaned daily + weekly frames per index, keyed by the cache file identity
        # (last fetch time, rows): a refresh invalidates them automatically.
        self._frames: dict[str, tuple[tuple, pd.DataFrame, pd.DatetimeIndex, pd.DataFrame, pd.DatetimeIndex]] = {}
        self._frames_lock = threading.Lock()

    @property
    def calendar(self) -> NSECalendar:
        return self._calendar()

    # ---------------------------------------------------------------- catalogue
    def refresh_catalogue(self, payload: dict | None = None, min_rows: int = 50) -> dict[str, Any]:
        from ..data.universe import nse_get
        from .registry import parse_catalogue

        if payload is None:
            if not self.settings.network_enabled:
                raise IndexDataError("network disabled (PAS_NETWORK=0)")
            payload = nse_get(self.settings.index_catalogue_url).json()
        return self.registry.upsert(parse_catalogue(payload, min_rows), self.settings.index_catalogue_url)

    # ---------------------------------------------------------------- daily data
    def _fresh(self, code: str, now: datetime) -> bool:
        meta = self.store.meta(index_key(code), "1d")
        if not meta or meta.get("status") != "ok" or not self.store.path(index_key(code), "1d").exists():
            return False
        last_fetch = parse_iso(meta.get("last_fetch_at"))
        latest_end = self.calendar.latest_completed_bar_end("1d", now) + timedelta(minutes=self.settings.daily_settle_minutes)
        return last_fetch is not None and last_fetch >= pd.Timestamp(min(latest_end, now))

    def refresh_one(self, item: dict[str, Any], now: datetime | None = None, force: bool = False) -> str:
        now = now or now_ist()
        code, name = item["code"], item["name"]
        key = index_key(code)
        meta = self.store.meta(key, "1d")
        shallow = bool(meta and meta.get("status") == "ok" and meta.get("history_period") != HISTORY_MAX)
        if not force and not shallow and self._fresh(code, now):
            return "fresh"
        cached = None if force else self.store.load_current(key, "1d")
        end = now.date()
        if cached is None or len(cached) < 10:
            start = end - timedelta(days=365 * self.settings.index_history_years)
            mode = "full"
        else:
            start = cached.index[-6].date()
            mode = "incremental"
        new = self.history.fetch(name, start, end)
        if new.empty and cached is None:
            raise IndexDataError(f"official index history returned no data for {name!r}")
        merged = new
        depth = HISTORY_MAX
        if cached is not None:
            merged, mismatch = merge_incremental(cached, new, self.settings.adjustment_mismatch_tolerance)
            if mismatch:  # provider revised history: re-download completely
                start = end - timedelta(days=365 * self.settings.index_history_years)
                merged = self.history.fetch(name, start, end)
                mode = "full (revised history)"
        if mode.startswith("full") or shallow:
            # Older history beyond the first window (full download) or beyond a
            # cache made before full-history backfill (shallow). A failure keeps
            # what is cached and retries on the next refresh.
            try:
                older = self.history.fetch_back(name, merged.index[0].date(), date(self.settings.index_history_floor_year, 1, 1), self.settings.index_backfill_empty_stop)
            except IndexDataError as exc:
                log.warning("index %s: older history not available now (%s); retried next refresh", code, exc)
                older, depth = None, (meta or {}).get("history_period") or f"{self.settings.index_history_years}y"
            if older is not None and not older.empty:
                merged = pd.concat([older[older.index < merged.index[0]], merged]).sort_index()
                mode = mode if mode.startswith("full") else f"{mode} + backfill"
        fetched = db.now_ist_iso()
        self.store.save(key, "1d", merged)
        self.store.upsert_meta(key, "1d", file_path=str(self.store.path(key, "1d")), first_ts=merged.index[0].isoformat(), last_ts=merged.index[-1].isoformat(), rows=len(merged), last_fetch_at=fetched, last_full_fetch_at=fetched if mode.startswith("full") else None, status="ok", error=None, history_period=depth)
        ver = verify_against_reference(merged, item.get("reference") or {})
        status = "ok" if ver["status"] in ("verified", "unverified") else "error"
        reason = None if status == "ok" else f"cached closes differ from the official reference closes: {ver['checks']}"
        self.registry.set_status(
            code,
            data_provider=PROVIDER,
            data_status=status,
            data_status_reason=reason,
            data_verified=db.dumps(ver),
            last_refresh_at=fetched,
            first_date=merged.index[0].date().isoformat(),
            last_date=merged.index[-1].date().isoformat(),
            rows=len(merged),
        )
        return mode

    def refresh_all(self, force: bool = False, progress: Callable[[str], None] | None = None) -> RefreshState:
        with self._lock:
            items = [r for r in self.registry.all(active_only=True) if r["analysis_enabled"]]
            st = RefreshState(state="running", started_at=db.now_ist_iso(), total=len(items))
            self.state = st
            for it in items:
                try:
                    self.refresh_one(it, force=force)
                except Exception as exc:  # recorded per index, never fatal
                    st.failed[it["code"]] = str(exc)[:300]
                    self.registry.set_status(it["code"], data_status="error", data_status_reason=f"refresh failed: {str(exc)[:300]}")
                    log.warning("index %s refresh failed: %s", it["code"], exc)
                st.done += 1
                if progress:
                    progress(f"{st.done}/{st.total}")
            st.state = "done"
            st.finished_at = db.now_ist_iso()
            st.message = f"{st.total - len(st.failed)} indices refreshed, {len(st.failed)} failed"
            return st

    # ---------------------------------------------------------------- intraday mapping
    def candidates(self) -> dict[str, list[str]]:
        p = self.settings.data_dir / "index_intraday_candidates.json"
        if not p.exists():
            p = Path(__file__).resolve().parent.parent.parent / "data" / "index_intraday_candidates.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.get("candidates", {}).items() if isinstance(v, list)}
        except (OSError, ValueError):
            return {}

    def verify_intraday(self, provider) -> dict[str, Any]:
        """Check each configured Yahoo candidate against the official reference closes."""
        out: dict[str, Any] = {}
        reg = {r["code"]: r for r in self.registry.all()}
        for code, tickers in self.candidates().items():
            item = reg.get(code)
            if item is None:
                continue
            result = {"status": "failed", "ticker": None, "tried": []}
            for tk in tickers:
                res = provider.fetch([tk], "1d", period="2y")
                df = res.data.get(tk)
                ver = verify_against_reference(df, item.get("reference") or {}, INTRADAY_TOLERANCE) if df is not None else {"status": "no_data", "checks": []}
                result["tried"].append({"ticker": tk, **ver})
                if ver["status"] == "verified" and ver.get("compared", 0) >= 3:
                    result.update(status="verified", ticker=tk)
                    break
            self.registry.set_status(
                code,
                intraday_provider="yahoo" if result["status"] == "verified" else None,
                intraday_ticker=result["ticker"],
                intraday_status=result["status"],
                intraday_detail=db.dumps(result["tried"]),
                intraday_checked_at=db.now_ist_iso(),
            )
            out[code] = result
        for code, item in reg.items():
            if code not in out and not item.get("intraday_status"):
                self.registry.set_status(code, intraday_status="unavailable", intraday_detail=db.dumps("no verified intraday provider mapping configured"))
        return out

    # ---------------------------------------------------------------- frames
    def _cleaned(self, code: str, item: dict[str, Any]):
        key = index_key(code)
        meta = self.store.meta(key, "1d") or {}
        ident = (meta.get("last_fetch_at"), meta.get("rows"), meta.get("last_ts"))
        with self._frames_lock:
            hit = self._frames.get(code)
            if hit and hit[0] == ident:
                return hit[1:]
        raw = self.store.load_current(key, "1d")
        if raw is None or raw.empty:
            raise IndexDataError(f"no daily data cached for {code} ({item.get('data_status')}: {item.get('data_status_reason') or 'not downloaded yet'})")
        daily, _ = clean_ohlcv(raw, "1d", self.calendar)
        d_ends = self.calendar.bar_ends(daily.index, "1d")
        weekly = resample_weekly(daily)
        w_ends = self.calendar.bar_ends(weekly.index, "1w")
        with self._frames_lock:
            self._frames[code] = (ident, daily, d_ends, weekly, w_ends)
        return daily, d_ends, weekly, w_ends

    def data_version(self) -> str:
        """Changes whenever any index was refreshed (cache key for derived results)."""
        rows = self.registry.all()
        return max((r.get("last_refresh_at") or "" for r in rows), default="") + f"|{len(rows)}"

    def frame(self, code: str, timeframe: str, cutoff: datetime) -> pd.DataFrame:
        """Completed candles of an index at the cutoff (raises IndexDataError when unavailable)."""
        item = self.registry.get(code)
        if item is None:
            raise IndexDataError(f"unknown index {code!r}")
        if timeframe in ("1d", "1w"):
            daily, d_ends, weekly, w_ends = self._cleaned(code, item)
            cut = pd.Timestamp(cutoff).tz_convert(IST)
            if timeframe == "1d":
                return daily[np.asarray(d_ends <= cut)]
            # A weekly bar is complete only when its last trading day closed by the
            # cutoff, so every included week contains only candles <= cutoff.
            return weekly[np.asarray(w_ends <= cut)]
        if item.get("intraday_status") != "verified" or not item.get("intraday_ticker") or self.yahoo is None:
            raise IndexDataError(f"reliable intraday data is not available for {code}: no verified provider mapping (daily and weekly only)")
        tk = item["intraday_ticker"]
        raw = self.yahoo.store.load_current(tk, "1h")
        if raw is None:
            self.yahoo.ensure([tk], "1h", cutoff=cutoff)
            raw = self.yahoo.store.load_current(tk, "1h")
        if raw is None or raw.empty:
            raise IndexDataError(f"intraday download failed for {code} ({tk})")
        return prepare_series(raw, code, timeframe, cutoff, self.calendar).frame
