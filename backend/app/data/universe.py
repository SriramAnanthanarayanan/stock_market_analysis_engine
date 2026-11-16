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
* Recent IPOs (universe ``ipo``, part of ``all``) come from NSE's list of past
  public issues: mainboard IPOs, plus SME IPOs that have moved to the main board,
  listed within ``recent_ipo_years`` of the as-of date and trading on the main
  board now (NSE equity list, EQ / BE series). FPOs of old listings, rights
  entitlements, demerger / direct listings and SME stocks still on NSE Emerge are
  not IPOs of this universe. Membership follows the IPO listing date, so an as-of
  date sees the listings of the 3 years before it (IPOs delisted since are missing).
"""

from __future__ import annotations

import csv
import io
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
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
def nse_get_json(url: str, warmup_url: str, timeout: float = 30.0, attempts: int = 3) -> Any:
    """GET an NSE JSON API. NSE's APIs need the cookies of a page visit first."""
    last: Exception | None = None
    with requests.Session() as session:
        session.headers.update({**BROWSER_HEADERS, "Accept": "application/json,text/html,*/*"})
        for attempt in range(attempts):
            try:
                session.get(warmup_url, timeout=timeout)
                resp = session.get(url, timeout=timeout)
                if resp.status_code == 200 and resp.content:
                    return resp.json()
                last = UniverseError(f"HTTP {resp.status_code} ({len(resp.content)} bytes) from {url}")
            except (requests.RequestException, ValueError) as exc:
                last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (2**attempt) + random.uniform(0, 0.5))
    raise UniverseError(str(last))


@dataclass(frozen=True)
class EquityListRow:
    symbol: str
    company: str | None
    series: str | None
    listing_date: date


def parse_equity_list_rows(text: str, min_rows: int = 500) -> dict[str, EquityListRow]:
    """NSE equity list (EQUITY_L.csv): SYMBOL -> (company, SERIES, DATE OF LISTING dd-MMM-yyyy).
    Column names carry stray spaces in the official file; they are stripped."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise UniverseError("equity list: empty file")
    reader.fieldnames = [f.strip().upper() for f in reader.fieldnames]
    if "SYMBOL" not in reader.fieldnames or "DATE OF LISTING" not in reader.fieldnames:
        raise UniverseError(f"equity list: unexpected columns {reader.fieldnames}")
    out: dict[str, EquityListRow] = {}
    for row in reader:
        sym = (row.get("SYMBOL") or "").strip().upper()
        raw = (row.get("DATE OF LISTING") or "").strip()
        if not sym or not raw:
            continue
        try:
            d = pd.to_datetime(raw, format="%d-%b-%Y").date()
        except (ValueError, TypeError):
            continue
        out[sym] = EquityListRow(sym, (row.get("NAME OF COMPANY") or "").strip() or None, (row.get("SERIES") or "").strip().upper() or None, d)
    if len(out) < min_rows:
        raise UniverseError(f"equity list: only {len(out)} rows with a listing date (expected >= {min_rows})")
    return out


def parse_equity_list(text: str, min_rows: int = 500) -> dict[str, date]:
    """NSE equity list (EQUITY_L.csv): SYMBOL -> DATE OF LISTING."""
    return {k: v.listing_date for k, v in parse_equity_list_rows(text, min_rows).items()}


IPO_SECURITY_TYPES = ("Equity", "SME")
MAINBOARD_SERIES = ("EQ", "BE")
# An equity-list listing date more than this before the issue's listing date means
# the issue was an FPO / re-issue of an old listing, not an IPO.
IPO_LISTING_TOLERANCE_DAYS = 7


@dataclass(frozen=True)
class PastIssue:
    symbol: str
    company: str | None
    security_type: str
    listing_date: date
    issue_price: float | None
    price_range: str | None
    issue_start: str | None
    issue_end: str | None


def _nse_date(raw: Any) -> date | None:
    try:
        return pd.to_datetime(str(raw).strip(), format="%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def parse_past_issues(payload: Any, min_rows: int = 100) -> list[PastIssue]:
    """NSE past public issues (``/api/public-past-issues``): equity IPOs (mainboard
    ``Equity`` and ``SME``) with a listing date. Debt, InvIT and REIT issues and
    issues without a listing date (withdrawn / postponed / not listed yet) are skipped."""
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise UniverseError("past issues: unexpected payload")
    out: dict[tuple[str, date], PastIssue] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("securityType") or "").strip()
        sym = str(it.get("symbol") or "").strip().upper()
        ld = _nse_date(it.get("listingDate"))
        if typ not in IPO_SECURITY_TYPES or not sym or ld is None:
            continue
        try:
            price = float(str(it.get("issuePrice") or "").replace(",", "").strip())
        except ValueError:
            price = None
        company = str(it.get("companyName") or it.get("company") or "").strip() or None
        out[(sym, ld)] = PastIssue(sym, company, typ, ld, price, (str(it.get("priceRange") or "").strip() or None), (str(it.get("ipoStartDate") or "").strip() or None), (str(it.get("ipoEndDate") or "").strip() or None))
    if len(out) < min_rows:
        raise UniverseError(f"past issues: only {len(out)} equity IPOs with a listing date (expected >= {min_rows})")
    return sorted(out.values(), key=lambda p: (p.listing_date, p.symbol))


def adjusted_issue_price(issue: dict[str, Any] | None, discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    """Issue price of an ``ipo_listings`` row on the split-adjusted basis of the OHLC
    history: band-checked NSE issue price / split factor since the listing.
    ``discovery`` (low / high of the first sessions) guards against an unrecorded
    split: an adjusted issue price far outside the listing period's range is not shown."""
    out: dict[str, Any] = {"issue_price": None, "issue_price_raw": None, "split_factor": None, "issue_price_note": None, "ipo_security_type": None}
    if not issue:
        return out
    out["ipo_security_type"] = issue.get("security_type")
    price, note = checked_issue_price(issue.get("issue_price"), issue.get("price_range"))
    out["issue_price_raw"] = price
    if price is None:
        out["issue_price_note"] = note
        return out
    factor = issue.get("split_factor")
    if factor is None:
        out["issue_price_note"] = "split / bonus history since the IPO not checked yet: issue price not comparable with the chart"
        return out
    adj = price / factor if factor > 0 else None
    lo, hi = (discovery or {}).get("low"), (discovery or {}).get("high")
    if adj is not None and lo and hi and not (lo / 4.0 <= adj <= hi * 2.5):
        out["issue_price_note"] = f"issue price {price:g} (adjusted {adj:.2f}) does not fit the listing-period range {lo:g}-{hi:g}: an unrecorded split / bonus? Not shown"
        return out
    out["issue_price"] = round(adj, 2) if adj is not None else None
    out["split_factor"] = factor
    if factor != 1.0:
        out["issue_price_note"] = f"issue price {price:g} adjusted for splits / bonus since the IPO (/{factor:g})"
    return out


def checked_issue_price(price: float | None, price_range: str | None, tolerance: float = 0.05) -> tuple[float | None, str | None]:
    """The issue price when it is consistent with the issue's price band, else (None, why).
    NSE's ``issuePrice`` field is sometimes not the issue price (live check 2026-11-16:
    145 of 1,303 issues outside their own band, e.g. 783.05 for a Rs.37-39 issue)."""
    if price is None:
        return None, "no issue price in the NSE list"
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", (price_range or "").replace(",", ""))]
    nums = [x for x in nums if x > 0]
    if not nums:
        return price, None
    lo, hi = min(nums), max(nums)
    if lo * (1 - tolerance) <= price <= hi * (1 + tolerance):
        return price, None
    return None, f"NSE issue price {price:g} is outside the price band {price_range}: not shown"


def years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - years, day=28)


def recent_ipo_set(engine: Engine, asof: date | None = None, years: int = 3) -> set[str]:
    """Symbols of the recent-IPO universe: mainboard-traded IPOs listed in the
    ``years`` before ``asof`` (default: today), inclusive of ``asof``."""
    end = asof or now_ist().date()
    start = years_before(end, years)
    t = db.ipo_listings
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.c.symbol).where((t.c.mainboard == 1) & (t.c.listing_date > start.isoformat()) & (t.c.listing_date <= end.isoformat()))
        ).all()
    return {r[0] for r in rows}




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

    def refresh_listing_dates(self, text: str | None = None, source: str | None = None, min_rows: int = 500) -> dict[str, Any]:
        """Official first trading (listing) dates of the universe's stocks from NSE's
        equity list; used by the IPO base scanner. Failures keep the stored dates."""
        source = source or self.settings.equity_list_url
        log_id = self._log_start("listing_dates", source)
        try:
            if text is None:
                if not self.settings.network_enabled:
                    raise UniverseError("network disabled (PAS_NETWORK=0)")
                text = nse_get(self.settings.equity_list_url).text
            dates = parse_equity_list(text, min_rows)
            stamp = db.now_ist_iso()
            n = 0
            with self.engine.begin() as conn:
                for sym, src in conn.execute(select(db.stocks.c.symbol, db.stocks.c.listing_date_source)).all():
                    d = dates.get(sym)
                    if d is None or src == "nse_ipo":  # the IPO listing date (past issues) is kept
                        continue
                    conn.execute(update(db.stocks).where(db.stocks.c.symbol == sym).values(listing_date=d.isoformat(), listing_date_source="nse_equity_list", updated_at=stamp))
                    n += 1
            self._log_end(log_id, "ok", rows=n)
            return {"status": "ok", "rows": n, "listed": len(dates)}
        except Exception as exc:
            log.error("listing-date refresh failed: %s", exc)
            self._log_end(log_id, "error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    def refresh_recent_ipos(self, payload: Any = None, equity_text: str | None = None, source: str | None = None, min_rows: int | None = None, equity_min_rows: int = 500) -> dict[str, Any]:
        """NSE past public issues + the NSE equity list -> ``ipo_listings`` and stock
        rows for the recent-IPO universe. A failure keeps the stored list."""
        source = source or self.settings.ipo_issues_url
        log_id = self._log_start("recent_ipos", source)
        try:
            if payload is None or equity_text is None:
                if not self.settings.network_enabled:
                    raise UniverseError("network disabled (PAS_NETWORK=0)")
                if payload is None:
                    payload = nse_get_json(self.settings.ipo_issues_url, self.settings.ipo_issues_page)
                if equity_text is None:
                    equity_text = nse_get(self.settings.equity_list_url).text
            issues = parse_past_issues(payload, self.settings.ipo_issues_min_rows if min_rows is None else min_rows)
            equity = parse_equity_list_rows(equity_text, equity_min_rows)
            stamp = db.now_ist_iso()
            today = now_ist().date()
            window_start = years_before(today, self.settings.recent_ipo_years)
            t = db.ipo_listings
            members: list[PastIssue] = []
            with self.engine.begin() as conn:
                kept = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(select(t.c.symbol, t.c.listing_date, t.c.split_factor, t.c.splits_checked_at)).all()}
                conn.execute(delete(t))
                for p in issues:
                    eq = equity.get(p.symbol)
                    mainboard = bool(
                        eq is not None and eq.series in MAINBOARD_SERIES and eq.listing_date >= p.listing_date - timedelta(days=IPO_LISTING_TOLERANCE_DAYS)
                    )
                    conn.execute(
                        t.insert().values(
                            symbol=p.symbol, listing_date=p.listing_date.isoformat(), company=p.company, security_type=p.security_type, issue_price=p.issue_price,
                            price_range=p.price_range, issue_start=p.issue_start, issue_end=p.issue_end, equity_series=eq.series if eq else None,
                            equity_list_date=eq.listing_date.isoformat() if eq else None, mainboard=int(mainboard), source="nse_past_issues", fetched_at=stamp,
                            split_factor=kept.get((p.symbol, p.listing_date.isoformat()), (None, None))[0],
                            splits_checked_at=kept.get((p.symbol, p.listing_date.isoformat()), (None, None))[1],
                        )
                    )
                    if mainboard and p.listing_date > window_start - timedelta(days=366):
                        members.append(p)
                # stock rows (company, series, IPO listing date) for the universe members;
                # the window is checked again at query time, a year of slack keeps rows for older as-of dates
                latest: dict[str, PastIssue] = {}
                for p in members:
                    if p.symbol not in latest or p.listing_date > latest[p.symbol].listing_date:
                        latest[p.symbol] = p
                for sym, p in latest.items():
                    eq = equity[sym]
                    ins = sqlite_insert(db.stocks).values(
                        symbol=sym, company=eq.company or p.company, series=eq.series, in_nifty500=0, listing_date=p.listing_date.isoformat(),
                        listing_date_source="nse_ipo", first_seen=today.isoformat(), last_seen=today.isoformat(), updated_at=stamp,
                    )
                    conn.execute(
                        ins.on_conflict_do_update(
                            index_elements=["symbol"],
                            set_={"listing_date": p.listing_date.isoformat(), "listing_date_source": "nse_ipo", "last_seen": today.isoformat(), "updated_at": stamp},
                        )
                    )
            n_recent = len(recent_ipo_set(self.engine, today, self.settings.recent_ipo_years))
            self._log_end(log_id, "ok", rows=n_recent)
            return {"status": "ok", "issues": len(issues), "recent_ipos": n_recent, "window_start": window_start.isoformat()}
        except Exception as exc:
            log.error("recent-IPO refresh failed: %s", exc)
            self._log_end(log_id, "error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    def refresh_ipo_splits(self, provider: Any, symbols: set[str] | None = None, pause_s: float = 0.2, only_unchecked: bool = False) -> dict[str, Any]:
        """Split / bonus factor since the IPO listing for the recent IPOs (provider
        split events after the listing date, multiplied). The OHLC history is
        split-adjusted, the NSE issue price is not; issue / factor puts it on the
        chart's basis. A symbol the provider cannot answer keeps its stored factor."""
        log_id = self._log_start("ipo_splits", getattr(provider, "name", "provider"))
        t = db.ipo_listings
        wanted = symbols if symbols is not None else self.recent_ipo_symbols()
        with self.engine.connect() as conn:
            q = select(t.c.symbol, t.c.listing_date).where(t.c.symbol.in_(sorted(wanted)) & (t.c.mainboard == 1))
            if only_unchecked:
                q = q.where(t.c.splits_checked_at.is_(None))
            rows = conn.execute(q).all()
        checked = changed = unknown = 0
        for i, (sym, listed) in enumerate(rows):
            ev = provider.splits(sym)
            if ev is None:
                unknown += 1
                continue
            factor = 1.0
            for d, r in ev.items():
                if d > date.fromisoformat(listed):
                    factor *= r
            with self.engine.begin() as conn:
                conn.execute(update(t).where((t.c.symbol == sym) & (t.c.listing_date == listed)).values(split_factor=factor, splits_checked_at=db.now_ist_iso()))
            checked += 1
            changed += factor != 1.0
            if pause_s and i + 1 < len(rows):
                time.sleep(pause_s)
        self._log_end(log_id, "ok" if checked or not rows else "error", rows=checked, error=None if checked or not rows else "provider gave no split history")
        return {"status": "ok", "checked": checked, "with_split_or_bonus": changed, "unknown": unknown}

    def recent_ipo_symbols(self, asof: date | None = None) -> set[str]:
        return recent_ipo_set(self.engine, asof, self.settings.recent_ipo_years)

    def ipo_issue(self, symbol: str) -> dict[str, Any] | None:
        """The latest IPO of a symbol in the past-issues list (issue price, listing date)."""
        t = db.ipo_listings
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.symbol == symbol).order_by(t.c.listing_date.desc()).limit(1)).mappings().first()
        return dict(row) if row else None

    def ipo_issue_map(self) -> dict[str, dict[str, Any]]:
        t = db.ipo_listings
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).order_by(t.c.listing_date)).mappings().all()
        return {r["symbol"]: dict(r) for r in rows}

    def listing_date(self, symbol: str) -> tuple[date | None, str | None]:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.stocks.c.listing_date, db.stocks.c.listing_date_source).where(db.stocks.c.symbol == symbol)).first()
        if row is None or not row[0]:
            return None, None
        return date.fromisoformat(row[0]), row[1]

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
        with self.engine.connect() as conn:
            have_dates = conn.execute(select(func.count()).select_from(db.stocks).where(db.stocks.c.listing_date.is_not(None))).scalar()
        result["listing_dates"] = "present" if have_dates else self.refresh_listing_dates()
        with self.engine.connect() as conn:
            have_ipos = conn.execute(select(func.count()).select_from(db.ipo_listings)).scalar()
        result["recent_ipos"] = "present" if have_ipos else self.refresh_recent_ipos()
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

        ipos: set[str] = set()
        if universe in ("ipo", "all"):
            ipos = self.recent_ipo_symbols(asof)
            if not ipos and universe == "ipo":
                warnings.append("Recent-IPO list unavailable (NSE past issues not loaded); refresh the universe.")
            elif asof is not None and asof < now_ist().date():
                warnings.append(f"Recent IPOs: listings of the {self.settings.recent_ipo_years} years before the as-of date that still trade on the NSE main board (IPOs delisted since are missing).")
        if universe == "nifty500":
            chosen = set(n500)
        elif universe == "fno":
            chosen = set(fno_map or {})
        elif universe == "non_fno":
            chosen = {s for s in n500 if fno_of(s) == FNO_NO}
        elif universe == "ipo":
            chosen = set(ipos)
        elif universe == "all":
            chosen = set(n500) | set(fno_map or {}) | ipos
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
            for name in ("nifty500", "fno", "holidays", "recent_ipos"):
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
