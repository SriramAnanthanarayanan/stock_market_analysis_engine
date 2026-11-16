"""Darvas box API (SignalConfig 1.4.0, docs/darvas-box.md): the box state of every stock in
the latest daily scan (stored evidence) and one stock's boxes for the chart. Screening
aid, not trade recommendations."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_

from .. import database as db
from ..data.ohlcv import prepare_series
from ..models import Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..signals.darvas import STATE_LABELS, STATE_RANK, detect_darvas
from ..timeutil import asof_cutoff, now_ist
from .signals import NO_SCAN, NO_SIGNALS, _empty, _row, _version, evidence_rows, has_signals, scan_summary

router = APIRouter(prefix="/api")
DARVAS_SINCE = (1, 4, 0)
DEFAULT_STATES = ("breakout", "near_top", "in_box", "forming_bottom")
OLD_SCAN = "The latest daily scan was run before the Darvas box scanner existed (signal config {v}). A new 1D scan computes it (after an upgrade the scheduler starts one automatically); refresh this page when it has finished."


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


@router.get("/scan/darvas")
def scan_darvas(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    state: str | None = Query(None, max_length=120, description="comma-separated: breakout, near_top, in_box, forming_bottom, forming_top, breakdown"),
    min_tests: int = Query(0, ge=0, le=50),
    volume_confirmed: bool = Query(False, description="breakouts only: volume >= darvas_breakout_volume_ratio x the reference"),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Darvas box state of the latest completed daily candle (one row per stock)."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    if _version(scan.get("signal_config_version")) < DARVAS_SINCE:
        return _empty(scan, OLD_SCAN.format(v=scan.get("signal_config_version")))
    wanted = set(state.split(",")) if state else set(DEFAULT_STATES)
    unknown = wanted - set(STATE_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown Darvas state {sorted(unknown)}; allowed {sorted(STATE_LABELS)}")
    t = db.signal_evidence
    rows = evidence_rows(s, scan["scan_id"], universe, ("darvas",), and_(t.c.signal_type == "darvas_box", t.c.status.in_(sorted(wanted))))
    items = []
    for e in rows:
        if fno and e["fno_status"] != fno:
            continue
        r = _row(e)
        if (r.get("top_tests") or 0) < min_tests:
            continue
        if volume_confirmed and not ((r.get("event") or {}).get("volume_ok")):
            continue
        r.pop("geometry", None)  # chart payload: /api/darvas/symbol
        items.append(r)
    items.sort(key=lambda r: (STATE_RANK.get(r.get("state"), 9), (r.get("event") or {}).get("sessions_ago") or 0, r.get("distance_to_top_pct") if r.get("distance_to_top_pct") is not None else 99))
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "state_labels": STATE_LABELS,
        "params": {k: getattr(cfg, k) for k in ("darvas_confirm_sessions", "darvas_new_high_lookback", "darvas_max_depth_pct", "darvas_near_pct", "darvas_track_sessions", "darvas_breakout_volume_ratio", "level_tests_score")},
        "note": (
            f"Darvas box on completed daily candles: the top is a new {cfg.darvas_new_high_lookback}-session high that the next {cfg.darvas_confirm_sessions} sessions do not exceed; "
            f"the bottom is the lowest low after it that the next {cfg.darvas_confirm_sessions} sessions do not undercut. Breakout = close above the top of a complete box "
            f"(volume-confirmed at {cfg.darvas_breakout_volume_ratio}x), breakdown = close below the bottom. Boxes deeper than {cfg.darvas_max_depth_pct:g} % are ignored (ASSUMPTION). "
            "Not trade recommendations."
        ),
    }


@router.get("/darvas/symbol")
def darvas_symbol(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    asof: date | None = None,
) -> dict[str, Any]:
    """One stock's Darvas boxes (current advance) for the chart: the stored row of the latest
    daily scan, else computed on demand from cached candles (not stored)."""
    _check_asof(asof)
    s = svc(request)
    sym = symbol.strip().upper()
    if s.universe.stock(sym) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {sym!r}")
    scan = find_latest_scan(s.engine, "all", "1d", asof)
    if scan is not None and has_signals(scan) and _version(scan.get("signal_config_version")) >= DARVAS_SINCE:
        t = db.signal_evidence
        rows = evidence_rows(s, scan["scan_id"], "all", ("darvas",), t.c.signal_type == "darvas_box", symbol=sym)
        return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "darvas": _row(rows[0]) if rows else None}
    raw = s.store.load_current(sym, "1d")
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no cached daily data for {sym}"})
    frame = prepare_series(raw, sym, "1d", asof_cutoff(asof), s.calendar).frame
    return {"symbol": sym, "source": "adhoc", "scan": None, "darvas": detect_darvas(frame, s.signal_cfg, symbol=sym), "note": "No daily scan with the Darvas scanner covers this date; computed on demand and not stored."}
