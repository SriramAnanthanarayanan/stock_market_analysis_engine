"""NSE universe: NIFTY 500 constituents and F&O membership snapshots.

Rules:

* A refresh validates the downloaded file before replacing anything. On any
  failure the previous successful data is kept and the error is recorded in
  ``universe_refresh_log``. An empty universe is never returned because a live
  download failed.
* F&O membership comes from NSE's market-lot file (``fo_mktlots.csv``). A stock
  is F&O ``YES`` in a snapshot when it has a lot size for the nearest listed
  expiry month. Each successful refresh stores a dated snapshot.
* Historical membership for an as-of date uses the latest snapshot on or before
  that date. Before the first snapshot it is ``UNKNOWN`` - it is never inferred
  from today's list, volume, liquidity or market capitalisation.
"""

from __future__ import annotations

import csv
import io
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import requests
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import database as db
from ..config import Settings
from ..timeutil import now_ist
from .calendar import merge_holiday_file, parse_nse_holiday_payload

log = logging.getLogger(__name__)

FNO_YES, FNO_NO, FNO_UNKNOWN = "YES", "NO", "UNKNOWN"
NIFTY500_COLUMNS = ["Company Name", "Industry", "Symbol", "Series", "ISIN Code"]

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class UniverseError(RuntimeError):
    pass


def nse_get(url: str, timeout: float = 30.0, attempts: int = 3) -> requests.Response:
    """GET an NSE resource with browser-like headers and bounded retries."""
    last: Exception | None = None
    with requests.Session() as session:
        session.headers.update(BROWSER_HEADERS)
        for attempt in range(attempts):
            try:
                resp = session.get(url, timeout=timeout)
                if resp.status_code == 200 and resp.content:
                    return resp
                last = UniverseError(f"HTTP {resp.status_code} ({len(resp.content)} bytes) from {url}")
            except requests.RequestException as exc:
                last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (2**attempt) + random.uniform(0, 0.5))
    raise UniverseError(str(last))


# ------------------------------------------------------------------ parsing


def parse_nifty500_csv(text: str, min_rows: int = 400, max_rows: int = 600) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in NIFTY500_COLUMNS if c not in df.columns]
    if missing:
        raise UniverseError(f"NIFTY 500 file missing columns {missing}; got {list(df.columns)}")
    df = df[NIFTY500_COLUMNS].apply(lambda s: s.str.strip())
    df = df[df["Symbol"].notna() & (df["Symbol"] != "")]
    if not min_rows <= len(df) <= max_rows:
        raise UniverseError(f"NIFTY 500 file has {len(df)} rows; expected {min_rows}-{max_rows}")
    if df["Symbol"].duplicated().any():
        raise UniverseError("NIFTY 500 file has duplicate symbols")
    return df.reset_index(drop=True)


@dataclass
class FnoRow:
    symbol: str
    underlying: str
    near_month: str
    near_month_lot: str
    months_with_lots: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.near_month_lot)


def parse_fno_mktlots(text: str, min_active: int = 100, max_active: int = 400) -> list[FnoRow]:
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise UniverseError("F&O file is empty")
    header = rows[0]
    if len(header) < 3 or header[0].upper() != "UNDERLYING" or header[1].upper() != "SYMBOL":
        raise UniverseError(f"unexpected F&O header {header[:4]}")
    months = header[2:]
    sep = next((i for i, r in enumerate(rows) if r[0].lower().startswith("derivatives on individual securities")), None)
    if sep is None:
        raise UniverseError("F&O file has no 'Derivatives on Individual Securities' section")
    out: list[FnoRow] = []
    for r in rows[sep + 1 :]:
        if len(r) < 3 or not r[1]:
            continue
        lots = (r[2:] + [""] * len(months))[: len(months)]
        out.append(
            FnoRow(
                symbol=r[1],
                underlying=r[0],
                near_month=months[0],
                near_month_lot=lots[0],
                months_with_lots=[m for m, lot in zip(months, lots) if lot],
            )
        )
    symbols = [r.symbol for r in out]
    if len(set(symbols)) != len(symbols):
        raise UniverseError("F&O file has duplicate stock symbols")
    active = sum(1 for r in out if r.active)
    if not min_active <= active <= max_active:
        raise UniverseError(f"F&O file lists {active} active stock underlyings; expected {min_active}-{max_active}")
    return out


# ------------------------------------------------------------------ service


@dataclass
class StockInfo:
    symbol: str
    company: str | None
    industry: str | None
    in_nifty500: bool
    fno_status: str


@dataclass
class UniverseMembers:
    universe: str
    asof: date | None
    stocks: list[StockInfo]
    warnings: list[str]
    nifty500_snapshot: str | None
    fno_snapshot: str | None


class UniverseService:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.nifty500_csv = settings.data_dir / "nifty500.csv"
        self.fno_csv = settings.data_dir / "nse_fno.csv"
        self.holidays_json = settings.data_dir / "nse_holidays.json"

    # ------------------------------------------------------------ logging
    def _log_start(self, list_name: str, source: str) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(
                db.universe_refresh_log.insert().values(
                    list_name=list_name, started_at=db.now_ist_iso(), status="running", source=source
                )
            )
            return int(res.inserted_primary_key[0])

    def _log_end(self, log_id: int, status: str, rows: int | None = None, error: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(db.universe_refresh_log)
                .where(db.universe_refresh_log.c.id == log_id)
                .values(finished_at=db.now_ist_iso(), status=status, rows=rows, error=error)
            )

    # ------------------------------------------------------------ refresh
    def refresh_nifty500(self, text: str | None = None, source: str | None = None) -> dict[str, Any]:
        source = source or self.settings.nifty500_url
        log_id = self._log_start("nifty500", source)
        try:
            if text is None:
                if not self.settings.network_enabled:
                    raise UniverseError("network disabled (PAS_NETWORK=0)")
                text = nse_get(self.settings.nifty500_url).text
            df = parse_nifty500_csv(text, self.settings.nifty500_min_rows, self.settings.nifty500_max_rows)
            self._store_nifty500(df)
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.nifty500_csv.with_suffix(".csv.tmp")
            df.to_csv(tmp, index=False)
            tmp.replace(self.nifty500_csv)
            self._log_end(log_id, "ok", rows=len(df))
            return {"status": "ok", "rows": len(df)}
        except Exception as exc:
            log.error("NIFTY 500 refresh failed: %s", exc)
            self._log_end(log_id, "error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    def _store_nifty500(self, df: pd.DataFrame) -> None:
        today = now_ist().date().isoformat()
        stamp = db.now_ist_iso()
        symbols = df["Symbol"].tolist()
        with self.engine.begin() as conn:
            conn.execute(update(db.stocks).where(db.stocks.c.symbol.not_in(symbols)).values(in_nifty500=0))
            for rec in df.to_dict("records"):
                stmt = sqlite_insert(db.stocks).values(
                    symbol=rec["Symbol"],
                    company=rec["Company Name"],
                    industry=rec["Industry"],
                    isin=rec["ISIN Code"],
                    series=rec["Series"],
                    in_nifty500=1,
                    first_seen=today,
                    last_seen=today,
                    updated_at=stamp,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["symbol"],
                    set_={
                        "company": stmt.excluded.company,
                        "industry": stmt.excluded.industry,
                        "isin": stmt.excluded.isin,
                        "series": stmt.excluded.series,
                        "in_nifty500": 1,
                        "last_seen": today,
                        "updated_at": stamp,
                    },
                )
                conn.execute(stmt)
            self._write_snapshot(conn, "nifty500", today, [(s, None) for s in symbols])

    def refresh_fno(self, text: str | None = None, source: str | None = None, snapshot_date: date | None = None) -> dict[str, Any]:
        source = source or self.settings.fno_url
        log_id = self._log_start("fno", source)
        try:
            if text is None:
                if not self.settings.network_enabled:
                    raise UniverseError("network disabled (PAS_NETWORK=0)")
                text = nse_get(self.settings.fno_url).text
            rows = parse_fno_mktlots(text, self.settings.fno_min_active, self.settings.fno_max_active)
            snap = (snapshot_date or now_ist().date()).isoformat()
            self._store_fno(rows, snap)
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            out = pd.DataFrame(
                [
                    {
                        "symbol": r.symbol,
                        "underlying": r.underlying,
                        "near_month": r.near_month,
                        "near_month_lot": r.near_month_lot,
                        "months_with_lots": " ".join(r.months_with_lots),
                        "fno_active": "YES" if r.active else "NO",
                        "snapshot_date": snap,
                    }
                    for r in rows
                ]
            )
            tmp = self.fno_csv.with_suffix(".csv.tmp")
            out.to_csv(tmp, index=False)
            tmp.replace(self.fno_csv)
            active = sum(1 for r in rows if r.active)
            self._log_end(log_id, "ok", rows=active)
            return {"status": "ok", "rows": active, "snapshot_date": snap}
        except Exception as exc:
            log.error("F&O refresh failed: %s", exc)
            self._log_end(log_id, "error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    def _store_fno(self, rows: list[FnoRow], snap: str) -> None:
        stamp = db.now_ist_iso()
        with self.engine.begin() as conn:
            for r in rows:
                if not r.active:
                    continue
                stmt = sqlite_insert(db.stocks).values(
                    symbol=r.symbol, company=r.underlying.title(), in_nifty500=0, first_seen=snap, last_seen=snap, updated_at=stamp
                )
                # Never overwrite NIFTY 500 metadata with the abbreviated F&O name.
                stmt = stmt.on_conflict_do_nothing(index_elements=["symbol"])
                conn.execute(stmt)
            self._write_snapshot(
                conn,
                "fno",
                snap,
                [
                    (r.symbol, {"lot": r.near_month_lot, "near_month": r.near_month, "months": r.months_with_lots})
                    for r in rows
                    if r.active
                ],
            )

    @staticmethod
    def _write_snapshot(conn: Any, list_name: str, snap: str, items: list[tuple[str, dict | None]]) -> None:
        conn.execute(
            delete(db.universe_snapshots).where(
                (db.universe_snapshots.c.list_name == list_name) & (db.universe_snapshots.c.snapshot_date == snap)
            )
        )
        if items:
            conn.execute(
                db.universe_snapshots.insert(),
                [
                    {"list_name": list_name, "snapshot_date": snap, "symbol": s, "info_json": db.dumps(info) if info else None}
                    for s, info in items
                ],
            )

    def refresh_holidays(self, payload: dict | None = None) -> dict[str, Any]:
        log_id = self._log_start("holidays", self.settings.holidays_url)
        try:
            if payload is None:
                if not self.settings.network_enabled:
                    raise UniverseError("network disabled (PAS_NETWORK=0)")
                payload = nse_get(self.settings.holidays_url).json()
            rows = parse_nse_holiday_payload(payload)
            merge_holiday_file(self.holidays_json, rows, "NSE holiday-master API (CM segment)", db.now_ist_iso())
            self._log_end(log_id, "ok", rows=len(rows))
            return {"status": "ok", "rows": len(rows)}
        except Exception as exc:
            log.error("Holiday refresh failed: %s", exc)
            self._log_end(log_id, "error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    def ensure_loaded(self) -> dict[str, Any]:
        """Startup bootstrap: refresh if possible, else fall back to local CSVs."""
        result: dict[str, Any] = {}
        if self.has_snapshot("nifty500"):
            result["nifty500"] = "present"
        else:
            res = self.refresh_nifty500()
            if res["status"] != "ok" and self.nifty500_csv.exists():
                res = self.refresh_nifty500(text=self.nifty500_csv.read_text(encoding="utf-8"), source=f"local:{self.nifty500_csv}")
            result["nifty500"] = res
        if self.has_snapshot("fno"):
            result["fno"] = "present"
        else:
            result["fno"] = self.refresh_fno()
        return result

    # ------------------------------------------------------------ queries
    def has_snapshot(self, list_name: str) -> bool:
        with self.engine.connect() as conn:
            n = conn.execute(
                select(func.count()).select_from(db.universe_snapshots).where(db.universe_snapshots.c.list_name == list_name)
            ).scalar()
        return bool(n)

    def snapshot_dates(self, list_name: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(db.universe_snapshots.c.snapshot_date)
                .where(db.universe_snapshots.c.list_name == list_name)
                .distinct()
                .order_by(db.universe_snapshots.c.snapshot_date)
            ).all()
        return [r[0] for r in rows]

    def applicable_snapshot(self, list_name: str, asof: date | None) -> str | None:
        dates = self.snapshot_dates(list_name)
        if not dates:
            return None
        if asof is None:
            return dates[-1]
        eligible = [d for d in dates if d <= asof.isoformat()]
        return eligible[-1] if eligible else None

    def snapshot_symbols(self, list_name: str, snap: str) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(db.universe_snapshots.c.symbol).where(
                    (db.universe_snapshots.c.list_name == list_name) & (db.universe_snapshots.c.snapshot_date == snap)
                )
            ).all()
        return {r[0] for r in rows}

    def fno_status_map(self, asof: date | None) -> tuple[dict[str, str] | None, str | None]:
        """Returns ({symbol: YES} for members, snapshot) or (None, None) when UNKNOWN."""
        snap = self.applicable_snapshot("fno", asof)
        if snap is None:
            return None, None
        return {s: FNO_YES for s in self.snapshot_symbols("fno", snap)}, snap

    def fno_status(self, symbol: str, asof: date | None) -> str:
        members, _ = self.fno_status_map(asof)
        if members is None:
            return FNO_UNKNOWN
        return members.get(symbol, FNO_NO)

    def members(self, universe: str, asof: date | None) -> UniverseMembers:
        warnings: list[str] = []
        n500_snap = self.applicable_snapshot("nifty500", asof)
        if n500_snap is None:
            dates = self.snapshot_dates("nifty500")
            if dates:
                n500_snap = dates[0]
                warnings.append(
                    f"No NIFTY 500 snapshot on or before {asof}; using constituents from {n500_snap} "
                    "(survivorship bias: historical membership is not reconstructed)."
                )
        n500 = self.snapshot_symbols("nifty500", n500_snap) if n500_snap else set()
        if not n500:
            warnings.append("NIFTY 500 constituents unavailable; refresh the universe.")
        fno_map, fno_snap = self.fno_status_map(asof)
        if fno_map is None:
            warnings.append(
                "F&O membership is UNKNOWN for this date (no snapshot on or before it); "
                "F&O and Non-F&O segments are empty rather than inferred."
            )

        def fno_of(sym: str) -> str:
            if fno_map is None:
                return FNO_UNKNOWN
            return fno_map.get(sym, FNO_NO)

        if universe == "nifty500":
            chosen = set(n500)
        elif universe == "fno":
            chosen = set(fno_map or {})
        elif universe == "non_fno":
            chosen = {s for s in n500 if fno_of(s) == FNO_NO}
        elif universe == "all":
            chosen = set(n500) | set(fno_map or {})
        else:
            raise ValueError(f"unknown universe {universe!r}")

        meta = self._stock_meta(chosen)
        stocks = [
            StockInfo(
                symbol=s,
                company=meta.get(s, {}).get("company"),
                industry=meta.get(s, {}).get("industry"),
                in_nifty500=s in n500,
                fno_status=fno_of(s),
            )
            for s in sorted(chosen)
        ]
        return UniverseMembers(universe, asof, stocks, warnings, n500_snap, fno_snap)

    def _stock_meta(self, symbols: set[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.stocks)).mappings().all()
        return {r["symbol"]: dict(r) for r in rows if r["symbol"] in symbols}

    def stock(self, symbol: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.stocks).where(db.stocks.c.symbol == symbol)).mappings().first()
        return dict(row) if row else None

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        with self.engine.connect() as conn:
            for name in ("nifty500", "fno", "holidays"):
                last_ok = conn.execute(
                    select(db.universe_refresh_log)
                    .where((db.universe_refresh_log.c.list_name == name) & (db.universe_refresh_log.c.status == "ok"))
                    .order_by(db.universe_refresh_log.c.id.desc())
                    .limit(1)
                ).mappings().first()
                last_any = conn.execute(
                    select(db.universe_refresh_log)
                    .where(db.universe_refresh_log.c.list_name == name)
                    .order_by(db.universe_refresh_log.c.id.desc())
                    .limit(1)
                ).mappings().first()
                out[name] = {
                    "last_success": last_ok["finished_at"] if last_ok else None,
                    "last_success_rows": last_ok["rows"] if last_ok else None,
                    "last_attempt": last_any["started_at"] if last_any else None,
                    "last_attempt_status": last_any["status"] if last_any else None,
                    "last_error": last_any["error"] if last_any and last_any["status"] == "error" else None,
                }
        out["nifty500"]["snapshots"] = len(self.snapshot_dates("nifty500"))
        out["fno"]["snapshots"] = len(self.snapshot_dates("fno"))
        out["fno"]["first_snapshot"] = (self.snapshot_dates("fno") or [None])[0]
        return out
