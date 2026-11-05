"""Confluence Dashboard API: stocks supported by several independent kinds of
evidence, scored from persisted scanner evidence (docs/confluence-engine.md)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request

from ..confluence.engine import CATEGORY_LABELS
from ..confluence.service import SCREENS, screen_filter, snapshot, snapshot_rows
from ..models import Universe
from ..services import Services
from ..timeutil import now_ist
from .signals import scan_summary

router = APIRouter(prefix="/api")

NOTE = (
    "Confluence = 100 x weighted mean of five 0-1 categories (trend, setup, trigger, volume + candlestick, relative strength) over the "
    "categories with data, plus a same-session breakout + Marubozu + volume-shock bonus (capped at 100). Correlated evidence is capped, "
    "not added. Bands are presentation bands, not probabilities; weights are placeholders. Analytical filters, not trade recommendations."
)


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _in_universe(r: dict[str, Any], universe: str) -> bool:
    if universe == "nifty500":
        return r["in_nifty500"]
    if universe == "fno":
        return r["fno_status"] == "YES"
    if universe == "non_fno":
        return r["in_nifty500"] and r["fno_status"] == "NO"
    return True


def _run_info(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run.get(k) for k in ("snapshot_id", "created_at", "timeframe", "asof", "primary_scan_id", "context_scan_id", "config_version", "config_hash", "counts")}


@router.get("/confluence")
def confluence(
    request: Request,
    universe: Universe = "all",
    timeframe: Literal["1d", "1w"] = "1d",
    asof: date | None = None,
    direction: Literal["bullish", "bearish"] = "bullish",
    screen: str | None = Query(None, max_length=40),
    min_score: float = Query(0.0, ge=0, le=100),
    band: str | None = Query(None, max_length=40),
    include_unranked: bool = False,
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
    stage: str | None = Query(None, max_length=200, description="comma-separated stage keys"),
    vcp: str | None = Query(None, max_length=200, description="comma-separated VCP statuses"),
    fresh_breakout: bool = False,
    volume_shock: bool = False,
    q: str | None = Query(None, max_length=40),
    limit: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    _check_asof(asof)
    s = svc(request)
    try:
        sc_dir, sc_test = screen_filter(screen)
    except KeyError:
        raise HTTPException(status_code=422, detail=f"unknown screen {screen!r}; allowed {[x['key'] for x in SCREENS]}")
    run = snapshot(s, universe, timeframe, asof)
    if run is None:
        return {"snapshot": None, "count": 0, "items": [], "note": "No completed scan with the advanced scanners for this universe / timeframe / date. Run a 1D scan (and a 1W scan for Stage Analysis / RS context)."}
    want_dir = sc_dir or direction
    stages = set(stage.split(",")) if stage else None
    vcps = set(vcp.split(",")) if vcp else None
    needle = (q or "").strip().upper()
    items = []
    for r in snapshot_rows(s, run["snapshot_id"]):
        if r["direction"] != want_dir or not _in_universe(r, universe):
            continue
        if not include_unranked and not r["ranked"]:
            continue
        if r["score"] < min_score or (band and r["band"] != band) or (fno and r["fno_status"] != fno):
            continue
        sm = r["summary"]
        if stages is not None and sm.get("stage") not in stages:
            continue
        if vcps is not None and sm.get("vcp_status") not in vcps:
            continue
        if fresh_breakout and not (sm.get("fresh_breakout") or sm.get("week52")):
            continue
        if volume_shock and not sm.get("volume_shock"):
            continue
        if needle and needle not in r["symbol"] and needle not in (r.get("company") or "").upper():
            continue
        if sc_test is not None and not sc_test(r):
            continue
        items.append(r)
    items.sort(key=lambda r: (-r["score"], -r["active_signals"], r["symbol"]))
    return {
        "snapshot": _run_info(run),
        "scan": scan_summary(run["primary"]),
        "context_scan": scan_summary(run["context"]) if run.get("context") else None,
        "direction": want_dir,
        "screen": screen,
        "count": len(items),
        "items": items[:limit],
        "bands": s.confluence_cfg.bands,
        "weights": s.confluence_cfg.weights,
        "min_coverage": s.confluence_cfg.min_coverage,
        "category_labels": CATEGORY_LABELS,
        "note": NOTE + ("" if run.get("context") else " No weekly scan: Stage Analysis / RS / RRG context unavailable."),
    }


@router.get("/confluence/symbol")
def confluence_symbol(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    timeframe: Literal["1d", "1w"] = "1d",
    asof: date | None = None,
) -> dict[str, Any]:
    _check_asof(asof)
    s = svc(request)
    run = snapshot(s, "all", timeframe, asof)
    if run is None:
        raise HTTPException(status_code=404, detail="no confluence snapshot: run a 1D scan with the advanced scanners")
    sym = symbol.strip().upper()
    rows = [r for r in snapshot_rows(s, run["snapshot_id"]) if r["symbol"] == sym]
    if not rows:
        raise HTTPException(status_code=404, detail=f"{sym} is not in the latest scan")
    return {"symbol": sym, "snapshot": _run_info(run), "bullish": next((r for r in rows if r["direction"] == "bullish"), None), "bearish": next((r for r in rows if r["direction"] == "bearish"), None), "note": NOTE}


@router.post("/confluence/refresh")
def confluence_refresh(
    request: Request,
    universe: Universe = Body("all", embed=True),
    timeframe: Literal["1d", "1w"] = Body("1d", embed=True),
    asof: date | None = Body(None, embed=True),
) -> dict[str, Any]:
    """Recompute the snapshot from the stored evidence (no scanner runs)."""
    _check_asof(asof)
    s = svc(request)
    run = snapshot(s, universe, timeframe, asof, force=True)
    if run is None:
        raise HTTPException(status_code=404, detail="no completed scan with the advanced scanners")
    return {"snapshot": _run_info(run)}


@router.get("/confluence/screens")
def confluence_screens() -> dict[str, Any]:
    return {"items": [{k: v for k, v in sc.items() if k != "test"} for sc in SCREENS]}
