"""E2 API: NSE index registry, index data refresh, Index Analysis, Index Explorer chart, RRG."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query, Request
from sqlalchemy import select

from .. import database as db
from ..analytics.sr import compute_zones, nearest, zone_record
from ..config import INDEX_PARAM_META, config_hash, describe_config
from ..indices.data import BASIS, IndexDataError, PROVIDER
from ..indices.registry import CATEGORY_LABELS
from ..patterns.base import SeriesContext
from ..services import Services
from ..timeutil import asof_cutoff, epoch_s, iso, now_ist

router = APIRouter(prefix="/api")

CATEGORIES = Literal["broad", "sector", "thematic", "strategy", "fixed_income"]


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _public(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in r.items() if k not in ("data_verified", "intraday_detail")}
    out["category_label"] = CATEGORY_LABELS.get(r["category"], r["category"])
    out["data_verified"] = db.loads(r.get("data_verified"))
    out["intraday_detail"] = db.loads(r.get("intraday_detail"))
    out["active"] = bool(r["active"])
    out["analysis_enabled"] = bool(r["analysis_enabled"])
    return out


def _item(s: Services, code: str) -> dict[str, Any]:
    it = s.indices.registry.get(code) or s.indices.registry.find(code)
    if it is None:
        raise HTTPException(status_code=404, detail=f"unknown index {code!r} (not in the NSE index registry)")
    return it


# ------------------------------------------------------------------ registry
@router.get("/indices")
def list_indices(
    request: Request,
    category: CATEGORIES | None = None,
    subcategory: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=60),
    status: Literal["ok", "pending", "unavailable", "error"] | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    s = svc(request)
    rows = s.indices.registry.all(active_only=not include_inactive)
    if category:
        rows = [r for r in rows if r["category"] == category]
    if subcategory:
        rows = [r for r in rows if r["subcategory"] == subcategory]
    if q:
        u = q.strip().upper()
        rows = [r for r in rows if u in r["code"].upper() or u in r["name"].upper()]
    if status:
        rows = [r for r in rows if r["data_status"] == status]
    counts: dict[str, dict[str, int]] = {}
    for r in s.indices.registry.all(active_only=True):
        c = counts.setdefault(r["category"], {"total": 0, "ok": 0, "intraday": 0})
        c["total"] += 1
        c["ok"] += int(r["data_status"] == "ok")
        c["intraday"] += int(r.get("intraday_status") == "verified")
    return {
        "count": len(rows),
        "items": [_public(r) for r in rows],
        "counts": counts,
        "source": s.settings.index_catalogue_url,
        "data_provider": f"{PROVIDER} ({s.settings.index_history_url})",
        "job": s.index_job,
    }


@router.get("/indices/status")
def indices_status(request: Request) -> dict[str, Any]:
    s = svc(request)
    rows = s.indices.registry.all(active_only=True)
    return {
        "job": s.index_job,
        "registry": len(rows),
        "equity": sum(1 for r in rows if r["analysis_enabled"]),
        "data_ok": sum(1 for r in rows if r["data_status"] == "ok"),
        "data_error": sum(1 for r in rows if r["data_status"] == "error"),
        "pending": sum(1 for r in rows if r["data_status"] == "pending"),
        "intraday_verified": sum(1 for r in rows if r.get("intraday_status") == "verified"),
        "config": {"version": s.index_cfg.version, "config_hash": config_hash(s.index_cfg), "parameters": [p for p in describe_config(s.index_cfg, INDEX_PARAM_META) if p["name"] != "version"]},
    }


@router.post("/indices/refresh")
def indices_refresh(request: Request, force: bool = Body(False, embed=True), intraday: bool = Body(True, embed=True)) -> dict[str, Any]:
    s = svc(request)
    if not s.settings.network_enabled:
        raise HTTPException(status_code=409, detail="network disabled (PAS_NETWORK=0)")
    return s.refresh_indices(background=True, force=force, intraday=intraday)


# ------------------------------------------------------------------ analysis
@router.get("/indices/overview")
def indices_overview(
    request: Request,
    timeframe: Literal["1d", "1w"] = "1d",
    benchmark: str | None = Query(None, max_length=60),
    asof: date | None = None,
    codes: str | None = Query(None, max_length=4000, description="comma-separated official index symbols (default: all)"),
) -> dict[str, Any]:
    _check_asof(asof)
    s = svc(request)
    bench = benchmark or s.index_cfg.rrg_default_benchmark
    if bench not in s.index_cfg.rrg_benchmarks:
        raise HTTPException(status_code=422, detail=f"benchmark must be one of {list(s.index_cfg.rrg_benchmarks)}")
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    out = s.index_analysis.overview(asof_cutoff(asof), timeframe, bench, code_list)
    out["asof"] = asof.isoformat() if asof else None
    return out


@router.get("/rrg")
def rrg(
    request: Request,
    benchmark: str | None = Query(None, max_length=60),
    asof: date | None = None,
    trail: int | None = Query(None, ge=2, le=52),
    codes: str | None = Query(None, max_length=4000, description="comma-separated official index symbols"),
    category: str | None = Query(None, max_length=200, description="comma-separated categories"),
) -> dict[str, Any]:
    """RRG-style rotation of indices vs a benchmark on completed weekly candles."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.index_cfg
    bench = benchmark or cfg.rrg_default_benchmark
    if bench not in cfg.rrg_benchmarks:
        raise HTTPException(status_code=422, detail=f"benchmark must be one of {list(cfg.rrg_benchmarks)}")
    tr = trail or cfg.rrg_trail_weeks
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    try:
        out = s.index_analysis.rrg(bench, asof_cutoff(asof), tr, code_list)
    except IndexDataError as exc:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": str(exc)})
    if category:
        cats = {c.strip() for c in category.split(",")}
        out["items"] = [e for e in out["items"] if e.get("category") in cats]
    out["asof"] = asof.isoformat() if asof else None
    out["trail_options"] = list(cfg.rrg_trail_options)
    out["benchmarks"] = list(cfg.rrg_benchmarks)
    _persist_rrg(s, out, bench, asof)
    return out


def _persist_rrg(s: Services, out: dict[str, Any], bench: str, asof: date | None) -> None:
    """Keep the calculation metadata and result (one row per benchmark / as-of / last week / parameters)."""
    key = f"{bench}|{asof}|{out.get('benchmark_last_week')}|{out['config_hash']}|{out['params']}|{len(out['items'])}"
    run_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    t = db.rrg_runs
    with s.engine.begin() as conn:
        if conn.execute(select(t.c.run_id).where(t.c.run_id == run_id)).first():
            out["run_id"] = run_id
            return
        conn.execute(
            t.insert().values(
                run_id=run_id,
                created_at=db.now_ist_iso(),
                benchmark=bench,
                asof=asof.isoformat() if asof else None,
                last_week=out.get("benchmark_last_week"),
                params_json=db.dumps(out["params"]),
                config_version=out["config_version"],
                config_hash=out["config_hash"],
                data_basis=out["data_basis"],
                result_json=db.dumps(out["items"]),
            )
        )
    out["run_id"] = run_id


# ------------------------------------------------------------------ explorer chart
@router.get("/index/chart")
def index_chart(
    request: Request,
    code: str = Query(..., min_length=2, max_length=80),
    timeframe: Literal["1h", "4h", "1d", "1w", "1mo", "3mo", "6mo", "12mo"] = "1d",
    asof: date | None = None,
) -> dict[str, Any]:
    """Index Explorer payload: candles of the official index data, S/R zones, data source."""
    _check_asof(asof)
    s = svc(request)
    it = _item(s, code)
    cutoff = asof_cutoff(asof)
    if not it["analysis_enabled"]:
        raise HTTPException(status_code=422, detail={"status": "not_applicable", "message": f"{it['name']} is a {it['index_type']} index; price analysis is limited to equity price indices"})
    try:
        frame = s.indices.frame(it["code"], timeframe, cutoff)
    except IndexDataError as exc:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": str(exc)})
    if frame.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": "no completed candles at the cutoff"})
    n = len(frame)
    first = max(0, n - s.settings.chart_max_candles)
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    candles = [{"t": epoch_s(frame.index[i]), "i": i, "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": None if not np.isfinite(v[i]) else v[i]} for i in range(first, n)]
    from ..config import MULTI_MONTH_TIMEFRAMES

    multi = timeframe in MULTI_MONTH_TIMEFRAMES
    if multi:
        zones = []  # S/R zones are not computed on multi-month candles (not validated)
    else:
        ctx = SeriesContext(it["code"], timeframe, frame, s.cfg)
        zones = [zone_record(z, ctx) for z in nearest(compute_zones(ctx), s.cfg.sr_display_per_side)]
    intraday = timeframe in ("1h", "4h")
    provider = f"yahoo ({it['intraday_ticker']}, verified against official closes)" if intraday else f"{PROVIDER} (official NSE index history)"
    notes = []
    if timeframe == "1w":
        notes.append("Weekly candles are aggregated from completed official daily candles (Monday-labelled NSE weeks); the current week appears once it has closed.")
    partial = None
    if multi:
        from ..data.periods import split_complete

        notes.append("Multi-month candles are aggregated from completed official daily candles; S/R zones are not computed on them. The running period is shown separately as incomplete.")
        _, part = split_complete(s.indices.frame(it["code"], "1d", cutoff), timeframe, cutoff, s.calendar)
        if len(part):
            r = part.iloc[-1]
            partial = {"t": epoch_s(part.index[-1]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]), "v": float(r["volume"]) if np.isfinite(r["volume"]) else None, "sessions": int(r["source_bars"]), "last_session": r["last_session"].isoformat(), "period_end": None}
    if it["data_status"] != "ok":
        notes.append(f"Data status {it['data_status']}: {it.get('data_status_reason') or ''}")
    return {
        "symbol": it["code"],
        "code": it["code"],
        "name": it["name"],
        "category": it["category"],
        "category_label": CATEGORY_LABELS.get(it["category"], it["category"]),
        "subcategory": it["subcategory"],
        "timeframe": timeframe,
        "asof": asof.isoformat() if asof else None,
        "data_cutoff": iso(cutoff),
        "candles": candles,
        "future_bars": [],
        "sr_zones": zones,
        "sr_zones_note": "Nearest support/resistance zones from k=3 swing clusters over the last 250 completed bars (same rules as stocks).",
        "intraday_available": it.get("intraday_status") == "verified",
        "data_source": {
            "provider": provider,
            "price_basis": BASIS,
            "price_basis_label": "Official price-return index level (PR); no corporate-action adjustment applies",
            "scan_price_basis": None,
            "adjusted_close_available": False,
            "note": "Index levels from the official NSE index history; volume is the traded quantity of the constituents where NSE publishes it.",
        },
        "registry": _public(it),
        "notes": notes,
        "partial_candle": partial,
    }


@router.get("/indices/{code}")
def index_detail(request: Request, code: str) -> dict[str, Any]:
    s = svc(request)
    return _public(_item(s, code))
