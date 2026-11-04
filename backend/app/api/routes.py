"""HTTP API. NSE India is the only market, so there is no market parameter."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, text

from .. import __version__
from .. import database as db
from ..config import TIMEFRAMES, describe_config
from ..models import ChartResponse, FnoStatus, PatternInfo, RefreshRequest, RefreshResponse, ScanResponse, ScanSummary, StatusSlug, Timeframe, Universe
from ..net import ssl_status
from ..patterns.base import CATALOGUE, CATEGORY_LABELS, SPEC_BY_SLUG, Status
from ..scanner import (
    UNIVERSE_SUPERSETS,
    ScanRequest,
    find_latest_scan,
    get_scan,
    history,
    recent_scans,
    scan_counts,
    scan_matches,
    scan_symbol_counts,
)
from ..services import Services
from ..timeutil import now_ist
from .chart import match_fields, build_chart

router = APIRouter(prefix="/api")

STATUS_SLUGS = {s.value for s in Status}


def svc(request: Request) -> Services:
    return request.app.state.services


def _parse_list(value: str | None, allowed: set[str], name: str) -> list[str] | None:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise HTTPException(status_code=422, detail=f"invalid {name} {bad}; allowed: {sorted(allowed)}")
    return items or None


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _summary(scan: dict[str, Any] | None) -> dict[str, Any] | None:
    if scan is None:
        return None
    keys = ScanSummary.model_fields.keys()
    return {k: scan.get(k) for k in keys}


# ------------------------------------------------------------------ health


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    s = svc(request)
    out: dict[str, Any] = {"time": now_ist().isoformat(timespec="seconds")}
    degraded: list[str] = []
    try:
        with s.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        out["database"] = {"status": "ok", "path": str(s.settings.db_path), "schema_version": db.current_schema_version(s.engine)}
    except Exception as exc:
        out["database"] = {"status": "error", "error": str(exc)}
        degraded.append("database")
    uni = s.universe.status()
    uni["bootstrap"] = s.bootstrap_state
    out["universe"] = uni
    if not uni["nifty500"]["snapshots"]:
        degraded.append("universe")
    md = s.market_data.status()
    md["network_enabled"] = s.settings.network_enabled
    out["market_data"] = md
    if md.get("last_error"):
        degraded.append("market_data")
    out["calendar"] = {"holiday_years": sorted(s.calendar.covered_years), "sources": s.calendar.sources}
    out["last_scan"] = {tf: _summary(find_latest_scan(s.engine, "all", tf, None)) for tf in TIMEFRAMES}
    sched: dict[str, Any] = {"enabled": s.settings.scheduler_enabled}
    if s.scheduler is not None:
        sched.update(s.scheduler.status())
    else:
        sched["running"] = False
    out["scheduler"] = sched
    out["active_scans"] = [_summary(x) for x in s.scans.active()]
    out["app"] = {
        "name": "Indian Price Action Screener",
        "version": __version__,
        "phase": "P1",
        "uptime_s": round(time.time() - s.started_at, 1),
        "config_version": s.cfg.version,
        "config_hash": s.config_hash,
    }
    out["ssl"] = ssl_status()
    out["status"] = "degraded" if degraded else "ok"
    out["degraded_components"] = degraded
    return out


# -------------------------------------------------------------------- scan


@router.get("/scan", response_model=ScanResponse)
def get_scan_results(
    request: Request,
    universe: Universe = "nifty500",
    timeframe: Timeframe = "1d",
    pattern: str | None = Query(None, description="pattern slug or comma-separated slugs"),
    category: str | None = Query(None, description="category slug or comma-separated slugs"),
    status: str | None = Query(None, description="status slug or comma-separated slugs"),
    min_quality: float = Query(0.0, ge=0, le=100),
    asof: date | None = None,
    include_developing: bool = False,
) -> dict[str, Any]:
    s = svc(request)
    _check_asof(asof)
    patterns = _parse_list(pattern, set(SPEC_BY_SLUG), "pattern")
    categories = _parse_list(category, set(CATEGORY_LABELS), "category")
    statuses = _parse_list(status, STATUS_SLUGS, "status")
    single = patterns[0] if patterns and len(patterns) == 1 else None
    found = find_latest_scan(s.engine, universe, timeframe, asof, single)
    active = None
    for u in UNIVERSE_SUPERSETS[universe]:
        active = active or s.scans.active_for(u, timeframe, asof)
    note = None
    source = found
    if found is None and active is not None:
        source = active
        note = "No completed scan yet; showing partial results of the scan in progress."
    elif found is None:
        note = "No scan for this universe/timeframe/date yet. Use POST /api/scan/refresh to start one."
    unimplemented = [p for p in (patterns or []) if not SPEC_BY_SLUG[p].implemented]
    if unimplemented:
        names = ", ".join(SPEC_BY_SLUG[p].name for p in unimplemented)
        note = ((note + " ") if note else "") + f"{names}: scheduled for P2, not detected yet."
    matches = (
        scan_matches(s.engine, source["scan_id"], universe, patterns, categories, statuses, min_quality, include_developing)
        if source
        else []
    )
    return {
        "requested": {
            "universe": universe,
            "timeframe": timeframe,
            "asof": asof.isoformat() if asof else None,
            "pattern": patterns,
            "category": categories,
            "status": statuses,
            "min_quality": min_quality,
            "include_developing": include_developing,
        },
        "scan": _summary(found),
        "active_scan": _summary(active),
        "detection_counts": scan_counts(s.engine, source["scan_id"], universe) if source else {},
        "symbol_counts": scan_symbol_counts(s.engine, source["scan_id"], universe) if source else {},
        "total_matches": len(matches),
        "matches": [match_fields(m) for m in matches],
        "note": note,
    }


@router.get("/scan/status/{scan_id}", response_model=ScanSummary)
def scan_status(request: Request, scan_id: str) -> dict[str, Any]:
    scan = get_scan(svc(request).engine, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id!r} not found")
    return _summary(scan)


@router.get("/scans", response_model=list[ScanSummary])
def scans(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    return [_summary(x) for x in recent_scans(svc(request).engine, limit)]


@router.post("/scan/refresh", response_model=RefreshResponse)
def refresh_scan(request: Request, body: RefreshRequest) -> dict[str, Any]:
    s = svc(request)
    _check_asof(body.asof)
    patterns = list(body.patterns or [])
    for p in patterns:
        if p not in SPEC_BY_SLUG:
            raise HTTPException(status_code=422, detail=f"unknown pattern {p!r}")
        if not SPEC_BY_SLUG[p].implemented:
            raise HTTPException(status_code=422, detail=f"{SPEC_BY_SLUG[p].name} is scheduled for P2 and cannot be scanned yet")
    if body.category:
        if body.category not in CATEGORY_LABELS:
            raise HTTPException(status_code=422, detail=f"unknown category {body.category!r}")
        in_cat = [p.slug for p in CATALOGUE if p.category == body.category and p.implemented]
        if not in_cat:
            raise HTTPException(status_code=422, detail=f"no implemented patterns in {body.category!r} yet (P2)")
        patterns = sorted(set(patterns) & set(in_cat)) if patterns else in_cat
    req = ScanRequest(
        universe=body.universe,
        timeframe=body.timeframe,
        asof=body.asof,
        patterns=tuple(sorted(patterns)) if patterns else None,
        full_refresh=body.full_refresh,
        trigger="manual",
    )
    scan_id, created = s.scans.submit(req)
    scan = get_scan(s.engine, scan_id)
    return {"scan_id": scan_id, "created": created, "state": scan["state"] if scan else "queued"}


# ------------------------------------------------------------------- chart


@router.get("/chart", response_model=ChartResponse)
def chart(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    timeframe: Timeframe = "1d",
    pattern_id: str | None = Query(None, min_length=8, max_length=64, pattern=r"^[0-9a-f]+$"),
    asof: date | None = None,
) -> dict[str, Any]:
    _check_asof(asof)
    return build_chart(svc(request), symbol, timeframe, pattern_id, asof)


# ----------------------------------------------------------------- history


@router.get("/history")
def history_endpoint(
    request: Request,
    symbol: str | None = Query(None, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    pattern: str | None = None,
    timeframe: Timeframe | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    fno_status: FnoStatus | None = None,
    status: StatusSlug | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    if pattern is not None and pattern not in SPEC_BY_SLUG:
        raise HTTPException(status_code=422, detail=f"unknown pattern {pattern!r}")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    rows = history(svc(request).engine, symbol.upper() if symbol else None, pattern, timeframe, date_from, date_to, fno_status, status, limit)
    return {
        "count": len(rows),
        "items": [match_fields(r) for r in rows],
        "note": "P1: stored detection records per scan. Cross-scan lifecycle tracking and outcome analytics arrive in P3.",
    }


# --------------------------------------------------------------- overview


@router.get("/overview")
def overview(request: Request, universe: Universe = "nifty500", asof: date | None = None) -> dict[str, Any]:
    s = svc(request)
    _check_asof(asof)
    per_tf: dict[str, Any] = {}
    for tf in TIMEFRAMES:
        scan = find_latest_scan(s.engine, universe, tf, asof)
        per_tf[tf] = {
            "scan": _summary(scan),
            "counts": scan_counts(s.engine, scan["scan_id"], universe) if scan else {},
        }
    return {"universe": universe, "asof": asof.isoformat() if asof else None, "timeframes": per_tf, "data": s.market_data.status()}


# ---------------------------------------------------------------- reference


@router.get("/patterns", response_model=list[PatternInfo])
def patterns() -> list[dict[str, Any]]:
    return [
        {
            "slug": p.slug,
            "name": p.name,
            "category": p.category,
            "category_label": CATEGORY_LABELS[p.category],
            "family": p.family,
            "implemented": p.implemented,
            "phase": p.phase,
        }
        for p in CATALOGUE
    ]


@router.get("/config")
def config(request: Request) -> dict[str, Any]:
    s = svc(request)
    with s.engine.connect() as conn:
        versions = conn.execute(select(db.config_versions.c.config_hash, db.config_versions.c.version, db.config_versions.c.created_at)).all()
    return {
        "version": s.cfg.version,
        "hash": s.config_hash,
        "parameters": describe_config(s.cfg),
        "registered_versions": [{"hash": h, "version": v, "created_at": c} for h, v, c in versions],
        "note": "Initial engineering defaults - not validated thresholds. Change only through a versioned, evaluated update (docs/validation.md).",
    }


@router.get("/universe")
def universe(request: Request, universe: Universe = "all", asof: date | None = None, include_members: bool = False) -> dict[str, Any]:
    s = svc(request)
    _check_asof(asof)
    m = s.universe.members(universe, asof)
    out: dict[str, Any] = {
        "universe": universe,
        "asof": asof.isoformat() if asof else None,
        "count": len(m.stocks),
        "fno_counts": {k: sum(1 for x in m.stocks if x.fno_status == k) for k in ("YES", "NO", "UNKNOWN")},
        "nifty500_snapshot": m.nifty500_snapshot,
        "fno_snapshot": m.fno_snapshot,
        "warnings": m.warnings,
        "status": s.universe.status(),
    }
    if include_members:
        out["members"] = [x.__dict__ for x in m.stocks]
    return out


@router.post("/universe/refresh")
def universe_refresh(request: Request) -> dict[str, Any]:
    s = svc(request)
    res = {"nifty500": s.universe.refresh_nifty500(), "fno": s.universe.refresh_fno(), "holidays": s.universe.refresh_holidays()}
    if res["holidays"].get("status") == "ok":
        s.reload_calendar()
    return res
