"""P3 API: pattern history (tracks), outcomes, labels, evaluation and exports."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select

from .. import database as db
from ..analytics.outcomes import aggregate
from ..config import TIMEFRAME_SOURCE_INTERVAL
from ..data.ohlcv import prepare_series
from ..evaluation.engine import ConfigOverrideError, EvalOptions, Evaluator, make_variant
from ..evaluation.labels import EXPORT_FIELDS, LabelStore, to_csv
from ..lifecycle.tracking import epoch_index
from ..models import CompareRequest, EvaluateRequest, LabelIn, LabelOut, SweepRequest, Timeframe, TrackOut, Universe
from ..patterns.base import SPEC_BY_SLUG, STATUS_LABELS, SeriesContext, Status
from ..scanner import find_latest_scan, get_scan, scan_matches
from ..services import Services
from ..timeutil import asof_cutoff, iso, now_ist

router = APIRouter(prefix="/api")

STATES = ("active", "frozen", "closed")


def svc(request: Request) -> Services:
    return request.app.state.services


def _labels(s: Services) -> LabelStore:
    return LabelStore(s.engine, s.settings.validation_modulus)


def _download(content: str, filename: str, media: str) -> Response:
    return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ tracks


def _track_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: r[k] for k in TrackOut.model_fields if k in r and k not in ("pattern_name", "status_label", "outcome")},
        "pattern_name": SPEC_BY_SLUG[r["pattern"]].name,
        "status_label": STATUS_LABELS[Status(r["status"])],
        "outcome": db.loads(r.get("outcome_json")),
    }


@router.get("/tracks", response_model=list[TrackOut])
def tracks(
    request: Request,
    symbol: str | None = Query(None, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    pattern: str | None = None,
    timeframe: Timeframe | None = None,
    state: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    fno_status: str | None = Query(None, pattern=r"^(YES|NO|UNKNOWN)$"),
    breakouts_only: bool = False,
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict[str, Any]]:
    s = svc(request)
    if pattern is not None and pattern not in SPEC_BY_SLUG:
        raise HTTPException(422, f"unknown pattern {pattern!r}")
    if state is not None and state not in STATES:
        raise HTTPException(422, f"state must be one of {STATES}")
    if status is not None and status not in {x.value for x in Status}:
        raise HTTPException(422, f"unknown status {status!r}")
    t = db.pattern_tracks
    q = select(t)
    for col, val in (("symbol", symbol.upper() if symbol else None), ("pattern", pattern), ("timeframe", timeframe), ("state", state), ("status", status)):
        if val:
            q = q.where(getattr(t.c, col) == val)
    if date_from:
        q = q.where(t.c.first_detected_at >= date_from.isoformat())
    if date_to:
        q = q.where(t.c.first_detected_at <= f"{date_to.isoformat()}T23:59:59+05:30")
    if breakouts_only:
        q = q.where(t.c.breakout_time.is_not(None))
    q = q.order_by(t.c.updated_at.desc()).limit(limit)
    with s.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(q).mappings()]
    if fno_status:
        rows = [r for r in rows if s.universe.fno_status(r["symbol"], None) == fno_status]
    return [_track_out(r) for r in rows]


@router.get("/tracks/{track_id}")
def track_detail(request: Request, track_id: str) -> dict[str, Any]:
    s = svc(request)
    with s.engine.connect() as conn:
        row = conn.execute(select(db.pattern_tracks).where(db.pattern_tracks.c.track_id == track_id)).mappings().first()
        if row is None:
            raise HTTPException(404, f"track {track_id!r} not found")
        evs = conn.execute(select(db.track_events).where(db.track_events.c.track_id == track_id).order_by(db.track_events.c.id)).mappings().all()
        snaps = conn.execute(
            select(db.detections.c.scan_id, db.detections.c.detection_id, db.detections.c.status, db.detections.c.quality, db.detections.c.created_at, db.detections.c.formation_start, db.detections.c.formation_end)
            .where(db.detections.c.track_id == track_id)
            .order_by(db.detections.c.created_at)
        ).mappings().all()
    return {
        "track": _track_out(dict(row)),
        "events": [{**dict(e), "detail": db.loads(e["detail_json"])} for e in evs],
        "snapshots": [dict(x) for x in snaps],
        "geometry": db.loads(row["geometry_json"]),
        "note": "Outcomes are measured on candles after the breakout and never influence detection.",
    }


@router.get("/outcomes/summary")
def outcomes_summary(request: Request, pattern: str | None = None, timeframe: Timeframe | None = None) -> dict[str, Any]:
    s = svc(request)
    t = db.pattern_tracks
    q = select(t.c.pattern, t.c.timeframe, t.c.breakout_direction, t.c.outcome_json).where(t.c.outcome_json.is_not(None))
    if pattern:
        q = q.where(t.c.pattern == pattern)
    if timeframe:
        q = q.where(t.c.timeframe == timeframe)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    with s.engine.connect() as conn:
        for p, tf, direction, oj in conn.execute(q):
            groups.setdefault((p, tf, direction or "?"), []).append(db.loads(oj))
    horizons = s.settings.outcome_horizons
    return {
        "horizons": list(horizons),
        "min_sample": s.settings.outcome_min_sample,
        "groups": [
            {"pattern": p, "pattern_name": SPEC_BY_SLUG[p].name, "timeframe": tf, "direction": d, "tracks": len(v), "horizons": aggregate(v, horizons, s.settings.outcome_min_sample)}
            for (p, tf, d), v in sorted(groups.items())
        ],
        "note": (
            "Descriptive post-breakout statistics from tracked live scans, measured from the close of the bar that confirmed the break. "
            "They are not success rates or predictions; groups below the minimum sample are flagged as insufficient."
        ),
    }


# ------------------------------------------------------------------ labels


def _label_values(s: Services, body: LabelIn) -> dict[str, Any]:
    if body.pattern not in SPEC_BY_SLUG:
        raise HTTPException(422, f"unknown pattern {body.pattern!r}")
    if body.asof > now_ist().date():
        raise HTTPException(422, "asof cannot be in the future")
    if body.formation_start_t >= body.formation_end_t:
        raise HTTPException(422, "formation start must be before formation end")
    symbol = body.symbol.upper()
    if s.universe.stock(symbol) is None:
        raise HTTPException(422, f"unknown symbol {symbol!r}")
    interval = TIMEFRAME_SOURCE_INTERVAL[body.timeframe]
    raw = s.store.load_current(symbol, interval)
    if raw is None:
        s.market_data.ensure([symbol], interval, cutoff=asof_cutoff(body.asof))
        raw = s.store.load_current(symbol, interval)
    if raw is None or raw.empty:
        raise HTTPException(422, f"no cached {interval} data for {symbol}; labels must reference real candles")
    frame = prepare_series(raw, symbol, body.timeframe, asof_cutoff(body.asof), s.calendar).frame
    ctx = SeriesContext(symbol, body.timeframe, frame, s.cfg)
    pos = epoch_index(ctx)
    for name, t in (("formation_start_t", body.formation_start_t), ("formation_end_t", body.formation_end_t)):
        if t not in pos:
            raise HTTPException(422, f"{name} {t} is not a completed {body.timeframe} candle of {symbol} as of {body.asof}")
    for name, line in (("upper_line", body.upper_line), ("lower_line", body.lower_line)):
        for p in line or []:
            if p.t not in pos:
                raise HTTPException(422, f"{name} point {p.t} is not a completed candle of the chart")
    for p in body.swing_points or []:
        if p.t not in pos:
            raise HTTPException(422, f"swing point {p.t} is not a completed candle of the chart")
    return {
        "symbol": symbol,
        "timeframe": body.timeframe,
        "pattern": body.pattern,
        "verdict": body.verdict,
        "asof": body.asof.isoformat(),
        "formation_start": iso(ctx.times[pos[body.formation_start_t]]),
        "formation_end": iso(ctx.times[pos[body.formation_end_t]]),
        "upper_line": [p.model_dump() for p in body.upper_line] if body.upper_line else None,
        "lower_line": [p.model_dump() for p in body.lower_line] if body.lower_line else None,
        "swing_points": [p.model_dump() for p in body.swing_points] if body.swing_points else None,
        "dataset": body.dataset,
        "detection_id": body.detection_id,
        "notes": body.notes,
    }


@router.get("/labels", response_model=list[LabelOut])
def list_labels(
    request: Request,
    symbol: str | None = Query(None, max_length=40),
    timeframe: Timeframe | None = None,
    pattern: str | None = None,
    dataset: str | None = Query(None, pattern=r"^(tuning|validation)$"),
    verdict: str | None = Query(None, pattern=r"^(valid|invalid|near_miss|false_detection)$"),
) -> list[dict[str, Any]]:
    return _labels(svc(request)).list(symbol.upper() if symbol else None, timeframe, pattern, dataset, verdict)


@router.get("/labels/export")
def export_labels(request: Request, format: str = Query("csv", pattern=r"^(csv|json)$")) -> Response:
    rows = _labels(svc(request)).list()
    stamp = now_ist().strftime("%Y%m%d-%H%M")
    if format == "json":
        return _download(json.dumps(rows, indent=2), f"labels-{stamp}.json", "application/json")
    return _download(to_csv(rows, EXPORT_FIELDS), f"labels-{stamp}.csv", "text/csv")


@router.post("/labels", response_model=LabelOut, status_code=201)
def create_label(request: Request, body: LabelIn) -> dict[str, Any]:
    s = svc(request)
    return _labels(s).create(_label_values(s, body))


@router.put("/labels/{label_id}", response_model=LabelOut)
def update_label(request: Request, label_id: str, body: LabelIn) -> dict[str, Any]:
    s = svc(request)
    out = _labels(s).update(label_id, _label_values(s, body))
    if out is None:
        raise HTTPException(404, f"label {label_id!r} not found")
    return out


@router.delete("/labels/{label_id}", status_code=204)
def delete_label(request: Request, label_id: str) -> Response:
    if not _labels(svc(request)).delete(label_id):
        raise HTTPException(404, f"label {label_id!r} not found")
    return Response(status_code=204)


# -------------------------------------------------------------- evaluation


def _selected_labels(s: Services, flt) -> list[dict[str, Any]]:
    labels = _labels(s).list(flt.symbol.upper() if flt.symbol else None, flt.timeframe, flt.pattern, flt.dataset)
    if not labels:
        raise HTTPException(422, "no labels match the selection; label charts in the Pattern Explorer first")
    return labels


def _variant(s: Services, overrides: dict[str, Any]):
    try:
        return make_variant(s.cfg, overrides)
    except ConfigOverrideError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/evaluation/run")
def evaluation_run(request: Request, body: EvaluateRequest) -> dict[str, Any]:
    s = svc(request)
    labels = _selected_labels(s, body.labels)
    cfg = _variant(s, body.overrides)
    res = Evaluator(s).evaluate(labels, cfg, EvalOptions(**body.options.model_dump()))
    res["run_id"] = Evaluator(s).save_run("evaluate", cfg, body.overrides, res["label_count"], res["metrics"], {"cases": res["cases"], "skipped": res["skipped"], "notes": res["notes"], "options": res["options"]})
    return res


@router.post("/evaluation/compare")
def evaluation_compare(request: Request, body: CompareRequest) -> dict[str, Any]:
    s = svc(request)
    labels = _selected_labels(s, body.labels)
    a, b = _variant(s, body.a_overrides), _variant(s, body.b_overrides)
    ev = Evaluator(s)
    res = ev.compare(labels, a, b, EvalOptions(**body.options.model_dump()))
    res["run_id"] = ev.save_run("compare", b, {"a": body.a_overrides, "b": body.b_overrides}, len(labels), res["diff"], res)
    return res


@router.post("/evaluation/sweep")
def evaluation_sweep(request: Request, body: SweepRequest) -> dict[str, Any]:
    s = svc(request)
    labels = _selected_labels(s, body.labels)
    for v in body.values:
        _variant(s, {body.param: v})  # validates name and type
    ev = Evaluator(s)
    res = ev.sweep(labels, s.cfg, body.param, list(body.values), EvalOptions(**body.options.model_dump()))
    res["run_id"] = ev.save_run("sweep", s.cfg, {body.param: body.values}, len(labels), {"rows": res["rows"]}, res)
    return res


@router.get("/evaluation/runs")
def evaluation_runs(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    t = db.evaluation_runs
    with svc(request).engine.connect() as conn:
        rows = conn.execute(select(t.c.run_id, t.c.created_at, t.c.kind, t.c.config_hash, t.c.config_version, t.c.overrides_json, t.c.label_count).order_by(t.c.created_at.desc()).limit(limit)).mappings().all()
    return [{**{k: v for k, v in r.items() if k != "overrides_json"}, "overrides": db.loads(r["overrides_json"])} for r in rows]


def _run(s: Services, run_id: str) -> dict[str, Any]:
    with s.engine.connect() as conn:
        row = conn.execute(select(db.evaluation_runs).where(db.evaluation_runs.c.run_id == run_id)).mappings().first()
    if row is None:
        raise HTTPException(404, f"evaluation run {run_id!r} not found")
    d = dict(row)
    d["summary"] = db.loads(d.pop("summary_json"))
    d["details"] = db.loads(d.pop("details_json"))
    d["overrides"] = db.loads(d.pop("overrides_json"))
    return d


@router.get("/evaluation/runs/{run_id}")
def evaluation_run_detail(request: Request, run_id: str) -> dict[str, Any]:
    return _run(svc(request), run_id)


CASE_FIELDS = ["type", "reason", "symbol", "timeframe", "asof", "fno_status", "pattern", "dataset", "label_id", "verdict", "label_window", "detection_id", "detection_window", "status", "quality", "iou"]


@router.get("/evaluation/runs/{run_id}/export")
def evaluation_export(request: Request, run_id: str, format: str = Query("csv", pattern=r"^(csv|json)$"), cases: str = Query("all", pattern=r"^(all|tp|fp|fn)$")) -> Response:
    run = _run(svc(request), run_id)
    rows = (run.get("details") or {}).get("cases") or []
    if cases != "all":
        rows = [c for c in rows if c["type"] == cases.upper()]
    rows = [{**c, "config_version": run["config_version"], "config_hash": run["config_hash"]} for c in rows]
    name = f"{run_id}-{cases}"
    if format == "json":
        return _download(json.dumps({"run": {k: run[k] for k in ("run_id", "kind", "created_at", "config_version", "config_hash", "overrides")}, "cases": rows}, indent=2), f"{name}.json", "application/json")
    return _download(to_csv(rows, CASE_FIELDS + ["config_version", "config_hash"]), f"{name}.csv", "text/csv")


# ------------------------------------------------------------------ export


DETECTION_FIELDS = [
    "scan_id", "pattern_id", "symbol", "company", "fno_status", "timeframe", "pattern", "category", "status", "status_reason",
    "quality", "quality_label", "formation_start", "formation_end", "formation_length", "upper_touches", "lower_touches",
    "width_pct", "move_pct", "volume_ratio", "last_close", "last_candle_time", "detected_at", "config_version", "config_hash",
    "track_id", "metrics",
]


@router.get("/export/detections")
def export_detections(
    request: Request,
    scan_id: str | None = Query(None, max_length=120),
    universe: Universe = "all",
    timeframe: Timeframe = "1d",
    asof: date | None = None,
    format: str = Query("csv", pattern=r"^(csv|json)$"),
) -> Response:
    s = svc(request)
    scan = get_scan(s.engine, scan_id) if scan_id else find_latest_scan(s.engine, universe, timeframe, asof)
    if scan is None:
        raise HTTPException(404, "no matching scan")
    rows = scan_matches(s.engine, scan["scan_id"], universe if not scan_id else "all", include_developing=True)
    out = [
        {**{k: r.get(k) for k in DETECTION_FIELDS}, "pattern_id": r["detection_id"], "detected_at": r["created_at"], "metrics": r["metrics"]}
        for r in rows
    ]
    name = f"detections-{scan['scan_id']}"
    if format == "json":
        return _download(json.dumps({"scan": {k: scan[k] for k in ("scan_id", "timeframe", "universe", "asof", "config_version", "config_hash", "finished_at")}, "detections": out}, indent=2), f"{name}.json", "application/json")
    return _download(to_csv(out, DETECTION_FIELDS), f"{name}.csv", "text/csv")
