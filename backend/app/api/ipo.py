"""IPO Base Breakout API: scanner rows of the latest daily scan and the per-symbol
payload (listing, discovery period, base, pivot, breakout, geometry) for charts.
docs/ipo-base.md. Analytical screening, not trade recommendations."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_

from .. import database as db
from ..config import config_hash
from ..data.ohlcv import prepare_series
from ..ipo import STATUS_LABELS, detect_ipo_base
from ..ipo.detector import STATUS_RANK
from ..models import Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..timeutil import asof_cutoff, now_ist
from .signals import evidence_rows, scan_summary

router = APIRouter(prefix="/api")
DEFAULT_STATUSES = ("discovery_phase", "base_forming", "base_validated", "near_pivot", "breakout", "failed_breakout")


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _row(e: dict[str, Any], with_geometry: bool = False) -> dict[str, Any]:
    m = dict(e["metrics"])
    if not with_geometry:
        m.pop("geometry", None)
    return {**m, "symbol": e["symbol"], "company": e["company"], "fno_status": e["fno_status"], "in_nifty500": e["in_nifty500"], "evidence_id": e["evidence_id"], "explanation": e.get("explanation") or m.get("explanation")}


@router.get("/scan/ipo-base")
def scan_ipo_base(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    status: str | None = Query(None, max_length=200, description="comma-separated statuses (default: everything except too_young / invalidated)"),
    include_too_young: bool = False,
    include_invalidated: bool = False,
    min_quality: float = Query(0.0, ge=0, le=100),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Recent listings (<= ipo.max_age_bars sessions) with their post-listing base state."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.ipo_cfg
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return {"scan": None, "count": 0, "items": [], "note": "No completed daily scan yet. Run a 1D scan."}
    if not scan.get("ipo_config_hash"):
        return {"scan": scan_summary(scan), "count": 0, "items": [], "note": "This daily scan was run before the IPO base scanner existed. Run a new scan."}
    wanted = set(status.split(",")) if status else set(DEFAULT_STATUSES) | ({"too_young"} if include_too_young else set()) | ({"invalidated"} if include_invalidated else set())
    unknown = wanted - set(STATUS_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown IPO base status {sorted(unknown)}; allowed {sorted(STATUS_LABELS)}")
    t = db.signal_evidence
    rows = evidence_rows(s, scan["scan_id"], universe, ("ipo_base",), and_(t.c.status.in_(sorted(wanted)), (t.c.quality >= min_quality) | t.c.quality.is_(None)))
    items = [_row(e) for e in rows if not fno or e["fno_status"] == fno]
    items = [x for x in items if (x.get("quality") or 0) >= min_quality]
    items.sort(key=lambda r: (STATUS_RANK.get(r["status"], 9), -(r.get("quality") or 0)))
    with s.engine.connect() as conn:
        from sqlalchemy import func, select

        dated = conn.execute(select(func.count()).select_from(db.stocks).where(db.stocks.c.listing_date.is_not(None))).scalar()
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "listing_dates_loaded": int(dated or 0),
        "status_labels": STATUS_LABELS,
        "params": {k: getattr(cfg, k) for k in ("min_age_bars", "max_age_bars", "discovery_bars", "min_base_bars", "max_base_bars", "min_base_depth_pct", "max_base_depth_pct", "min_resistance_touches", "near_pivot_pct", "breakout_volume_ratio", "failed_breakout_bars")},
        "note": (
            f"Listings of the last {cfg.max_age_bars} sessions: price discovery (first {cfg.discovery_bars} sessions) -> settled volatility -> a post-listing base "
            f"({cfg.min_base_bars}-{cfg.max_base_bars} sessions, {cfg.min_base_depth_pct}-{cfg.max_base_depth_pct} % deep, >= {cfg.min_resistance_touches} touches of its high) -> "
            f"breakout = close above the base high (pivot) on >= {cfg.breakout_volume_ratio}x volume. Listing date: official NSE equity list" + ("" if not cfg.first_candle_fallback else ", else the first candle (flagged)") + ". "
            "Engineering defaults, not validated on labelled charts; quality is not a probability. An IPO base is not a VCP."
        ),
    }


@router.get("/ipo/symbol")
def ipo_symbol(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    asof: date | None = None,
) -> dict[str, Any]:
    """The IPO base of one stock: stored by the latest daily scan, else computed on demand (not stored)."""
    _check_asof(asof)
    s = svc(request)
    sym = symbol.strip().upper()
    if s.universe.stock(sym) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {sym!r}")
    listing, src = s.universe.listing_date(sym)
    scan = find_latest_scan(s.engine, "all", "1d", asof)
    if scan is not None and scan.get("ipo_config_hash"):
        rows = evidence_rows(s, scan["scan_id"], "all", ("ipo_base",), symbol=sym)
        if rows:
            return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "ipo": _row(rows[0], with_geometry=True), "listing_date": listing.isoformat() if listing else None, "listing_date_source": src}
        return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "ipo": None, "listing_date": listing.isoformat() if listing else None, "listing_date_source": src, "note": "Not a recent listing (or the post-listing history is not evaluable) in the latest daily scan."}
    raw = s.store.load_current(sym, "1d")
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no cached daily data for {sym}"})
    frame = prepare_series(raw, sym, "1d", asof_cutoff(asof), s.calendar).frame
    r = detect_ipo_base(frame, s.ipo_cfg, listing, symbol=sym, config_hash=config_hash(s.ipo_cfg), calendar=s.calendar)
    return {"symbol": sym, "source": "adhoc", "scan": None, "ipo": r, "listing_date": listing.isoformat() if listing else None, "listing_date_source": src, "note": "No daily scan with the IPO base scanner covers this date; computed on demand and not stored."}
