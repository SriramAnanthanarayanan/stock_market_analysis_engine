"""VCP API: the VCP scanner (stored daily-scan results + Stage Analysis context)
and the per-symbol VCP payload (contractions, pivot, geometry, explanation) for charts.

Stage Analysis is a separate trend-context filter (spec 6.8): the user's own
stage classification of the latest weekly scan is shown next to each VCP and can
filter it, but never changes the VCP's structural status or score.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from .. import database as db
from ..config import config_hash
from ..data.ohlcv import prepare_series
from ..models import Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..stage.analysis import STAGE_LABELS
from ..timeutil import asof_cutoff, now_ist
from ..vcp import STATUS_LABELS, detect_vcp
from .signals import scan_summary, universe_clause

router = APIRouter(prefix="/api")

DEFAULT_STATUSES = ("near_pivot", "final_contraction", "breakout", "forming", "failed_breakout")
STAGE_FILTERS = Literal["all", "stage2", "stage2_or_stage1", "exclude_other"]


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def vcp_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("metrics", "quality", "gates", "explanation", "geometry", "depths"):
        col = {"quality": "quality_json", "depths": "depths_json"}.get(k, f"{k}_json")
        d[k if k not in ("quality",) else "quality_components"] = db.loads(d.pop(col, None))
    d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
    d["in_nifty500"] = bool(d.get("in_nifty500"))
    d["qualified"] = bool(d.get("qualified"))
    d["depths_pct"] = d.pop("depths", None) or []
    m = d.get("metrics") or {}
    d["pivot_crossed_low_volume"] = bool(m.get("pivot_crossed_low_volume"))
    d["breakout_confirmation"] = m.get("breakout_confirmation")
    return d


def stage_context(s: Services, universe: str, asof: date | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    scan = find_latest_scan(s.engine, universe, "1w", asof)
    if scan is None or not scan.get("stage_config_hash"):
        return {}, None
    t = db.stage_results
    with s.engine.connect() as conn:
        rows = conn.execute(select(t.c.symbol, t.c.stage, t.c.stage_since, t.c.metrics_json).where(t.c.scan_id == scan["scan_id"])).all()
    out = {}
    for sym, stage, since, mj in rows:
        m = db.loads(mj) or {}
        out[sym] = {"stage": stage, "stage_label": STAGE_LABELS.get(stage, stage), "stage_since": since, "rs": m.get("rs"), "ema30_direction": m.get("ema30_direction"), "week": m.get("week")}
    return out, {"scan_id": scan["scan_id"], "finished_at": scan.get("finished_at"), "stage_config_version": scan.get("stage_config_version")}


def stage_ok(stage: str | None, flt: str, exclude: set[str]) -> bool | None:
    if stage is None:
        return None if flt != "all" or exclude else True
    if stage in exclude:
        return False
    if flt == "stage2":
        return stage in ("stage2_confirmed", "early_stage2")
    if flt == "stage2_or_stage1":
        return stage in ("stage2_confirmed", "early_stage2", "stage1")
    if flt == "exclude_other":
        return stage != "other"
    return True


@router.get("/scan/vcp")
def scan_vcp(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    status: str | None = Query(None, max_length=200, description="comma-separated statuses (default: everything except developing / invalidated)"),
    include_developing: bool = False,
    include_invalidated: bool = False,
    min_quality: float = Query(0.0, ge=0, le=100),
    stage: STAGE_FILTERS = "all",
    exclude_stages: str | None = Query(None, max_length=200, description="comma-separated stage keys to exclude"),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """VCP formations of the latest daily scan with Stage Analysis context (latest weekly scan)."""
    _check_asof(asof)
    s = svc(request)
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return {"scan": None, "count": 0, "items": [], "note": "No completed daily scan yet. Run a 1D scan."}
    if not scan.get("vcp_config_hash"):
        return {"scan": scan_summary(scan), "count": 0, "items": [], "note": "This daily scan was run before the VCP detector existed. Run a new scan."}
    wanted = set(status.split(",")) if status else set(DEFAULT_STATUSES) | ({"developing"} if include_developing else set()) | ({"invalidated"} if include_invalidated else set())
    unknown = wanted - set(STATUS_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown VCP status {sorted(unknown)}; allowed {sorted(STATUS_LABELS)}")
    excl = set(x for x in (exclude_stages or "").split(",") if x)
    t = db.vcp_formations
    q = select(t).where(t.c.scan_id == scan["scan_id"]).where(t.c.status.in_(sorted(wanted))).where(t.c.quality >= min_quality)
    uc = universe_clause(t, universe)
    if uc is not None:
        q = q.where(uc)
    with s.engine.connect() as conn:
        rows = [vcp_row(r) for r in conn.execute(q.order_by(t.c.quality.desc())).mappings()]
    ctx, wscan = stage_context(s, universe, asof)
    items = []
    for r in rows:
        if fno and r["fno_status"] != fno:
            continue
        st = ctx.get(r["symbol"])
        r["stage"] = st
        ok = stage_ok(st["stage"] if st else None, stage, excl)
        r["trend_context_ok"] = ok
        if (stage != "all" or excl) and ok is not True:
            continue
        r.pop("geometry", None)  # chart payload: /api/vcp/symbol
        items.append(r)
    rank = {k: i for i, k in enumerate(("breakout", "near_pivot", "final_contraction", "forming", "failed_breakout", "developing", "invalidated"))}
    items.sort(key=lambda r: (rank.get(r["status"], 99), -r["quality"]))
    return {
        "scan": scan_summary(scan),
        "stage_scan": wscan,
        "count": len(items),
        "items": items,
        "status_labels": STATUS_LABELS,
        "note": (
            "VCP on completed daily candles: successive contractions T1 > T2 > ... (each <= "
            f"{s.vcp_cfg.max_next_contraction_ratio} x the previous, >= {s.vcp_cfg.min_contraction_pct} %), volume drying up (final <= {s.vcp_cfg.final_volume_ratio} x the 50-day average), "
            f"a tight final contraction (<= {s.vcp_cfg.max_final_contraction_pct} %) and its pivot; breakout = close above the pivot on >= {s.vcp_cfg.breakout_volume_ratio} x volume. "
            "Quality is an explainable fit score, not a probability. Stage = your Stage Analysis of the latest weekly scan (context only). "
            "Synthetic tests only - real-chart accuracy is not validated."
        ),
    }


@router.get("/vcp/symbol")
def vcp_symbol(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    asof: date | None = None,
) -> dict[str, Any]:
    """The VCP of one stock: stored by the latest daily scan, else computed on demand (not stored)."""
    _check_asof(asof)
    s = svc(request)
    sym = symbol.strip().upper()
    if s.universe.stock(sym) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {sym!r}")
    scan = find_latest_scan(s.engine, "all", "1d", asof)
    t = db.vcp_formations
    if scan is not None and scan.get("vcp_config_hash"):
        with s.engine.connect() as conn:
            row = conn.execute(select(t).where((t.c.scan_id == scan["scan_id"]) & (t.c.symbol == sym))).mappings().first()
        ctx, _ = stage_context(s, "all", asof)
        if row is not None:
            v = vcp_row(row)
            v["contraction_list"] = _contractions(s, scan["scan_id"], v["vcp_id"])
            return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "vcp": v, "stage": ctx.get(sym)}
        return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "vcp": None, "stage": ctx.get(sym), "note": "No VCP candidate in the latest daily scan."}
    cutoff = asof_cutoff(asof)
    raw = s.store.load_current(sym, "1d")
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no cached daily data for {sym}"})
    frame = prepare_series(raw, sym, "1d", cutoff, s.calendar).frame
    v = detect_vcp(frame, s.vcp_cfg, symbol=sym, config_hash=config_hash(s.vcp_cfg))
    return {"symbol": sym, "source": "adhoc", "scan": None, "vcp": v, "stage": None, "note": "No daily scan with the VCP detector covers this date; computed on demand and not stored."}


def _contractions(s: Services, scan_id: str, vcp_id: str) -> list[dict[str, Any]]:
    t = db.vcp_contractions
    with s.engine.connect() as conn:
        rows = conn.execute(select(t).where((t.c.scan_id == scan_id) & (t.c.vcp_id == vcp_id)).order_by(t.c.seq)).mappings().all()
    last = max((r["seq"] for r in rows), default=0)
    return [{**dict(r), "provisional": bool(r["provisional"]), "final": bool(r["final"]) if r.get("final") is not None else r["seq"] == last, "label": f"T{r['seq']}"} for r in rows]
