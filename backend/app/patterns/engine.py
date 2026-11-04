"""Detection engine: run detectors, deduplicate, evaluate lifecycle, build geometry.

Output ``Detection`` objects carry everything the API, the database and the
chart need: status, quality breakdown, gates (explanation) and the versioned
geometry contract (``GEOMETRY_SCHEMA_VERSION``) in candle timestamps/prices.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import DetectionConfig, config_hash
from ..data.calendar import NSECalendar
from ..lifecycle import BAND, NECK_DOWN, NECK_UP, BoundaryModel, evaluate_lifecycle
from ..timeutil import epoch_s, iso
from .base import SPEC_BY_SLUG, STATUS_LABELS, Candidate, Line, PatternDetector, SeriesContext, Status
from .continuation import ContinuationDetector
from .converging import ConvergingDetector
from .parallel import ParallelDetector
from .reversals import ReversalDetector
from .shared import iou

GEOMETRY_SCHEMA_VERSION = "1.1"
PRICE_BASIS = "provider OHLC, split/dividend adjusted (yfinance auto_adjust=True)"
TIME_BASIS = "bar start; t = Unix epoch seconds (UTC instant); display in Asia/Kolkata"

DETECTORS: tuple[PatternDetector, ...] = (ParallelDetector(), ConvergingDetector(), ContinuationDetector(), ReversalDetector())
IMPLEMENTED_PATTERNS: tuple[str, ...] = tuple(p for d in DETECTORS for p in d.patterns)
LINE_FAMILIES = ("parallel", "converging", "continuation")
PENNANTS = ("bullish_pennant", "bearish_pennant")


@dataclass
class Detection:
    detection_id: str
    symbol: str
    timeframe: str
    pattern: str
    pattern_name: str
    category: str
    family: str
    status: str
    status_label: str
    status_reason: str | None
    quality: float
    quality_label: str
    quality_components: list[dict[str, Any]]
    formation_start_index: int
    formation_end_index: int
    formation_start: str
    formation_end: str
    formation_length: int
    upper_touches: int | None
    lower_touches: int | None
    width_pct: float
    move_pct: float
    volume_ratio: float | None
    last_close: float
    last_candle_time: str
    metrics: dict[str, Any]
    gates: list[dict[str, Any]]
    events: list[dict[str, Any]]
    geometry: dict[str, Any]
    explanation: list[str]
    config_hash: str
    config_version: str
    developing: bool = False
    archived: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ dedup


def _geom_window(c: Candidate) -> tuple[int, int]:
    """Bars covered by the drawn boundaries (consolidation for flags/pennants)."""
    return (c.region_start if c.region_start is not None else c.start), c.end


def same_geometry(a: Candidate, b: Candidate, frac: float) -> bool:
    """Both boundaries coincide (within frac x mean width) over the overlapping bars."""
    if a.upper is None or a.lower is None or b.upper is None or b.lower is None:
        return False
    (a0, a1), (b0, b1) = _geom_window(a), _geom_window(b)
    lo, hi = max(a0, b0), min(a1, b1)
    if hi < lo:
        return False
    width = 0.5 * (
        float(np.mean([a.upper.at(lo) - a.lower.at(lo), a.upper.at(hi) - a.lower.at(hi)]))
        + float(np.mean([b.upper.at(lo) - b.lower.at(lo), b.upper.at(hi) - b.lower.at(hi)]))
    )
    tol = frac * width
    for i in (lo, hi):
        if abs(a.upper.at(i) - b.upper.at(i)) > tol or abs(a.lower.at(i) - b.lower.at(i)) > tol:
            return False
    return True


def _collapsed(c: Candidate) -> bool:
    return c.family == "converging" and c.rejected and any(g.name == "convergence_ratio_min" and not g.passed for g in c.gates)


def deduplicate(cands: list[Candidate], cfg: DetectionConfig) -> list[Candidate]:
    """Keep one label per formation.

    Candidates are grouped (parallel, converging, continuation, reversal tops,
    reversal bottoms) and ranked by quality (ties to the longer window). A
    candidate is dropped when it overlaps an already kept one with IoU >=
    ``dedup_iou_threshold``, or when it is nested with the same boundaries.
    Genuinely different nested geometries survive and different groups coexist.
    Developing and Forming (provisional) candidates never displace a valid one.
    A converging candidate is dropped when a longer same-geometry candidate
    failed the CR minimum. A Symmetrical Triangle describing the same formation
    as a kept pennant is dropped (the pole makes it a pennant).
    """
    threshold, frac = cfg.dedup_iou_threshold, cfg.dedup_geometry_tolerance_frac
    collapsed = [c for c in cands if _collapsed(c)] if cfg.suppress_collapsed_subwindows else []
    out: list[Candidate] = []
    groups: dict[str, list[Candidate]] = {}
    for c in cands:
        if not c.rejected:
            groups.setdefault(c.dedup_group, []).append(c)
    for _, group in sorted(groups.items()):
        kept: list[Candidate] = []
        valid = [c for c in group if not c.developing and not c.provisional]
        tentative = [c for c in group if c.developing or c.provisional]
        for pool in (valid, tentative):
            for c in sorted(pool, key=lambda c: (-round(c.quality, 9), -c.length, c.start)):
                if any(k.length > c.length and same_geometry(c, k, frac) for k in collapsed):
                    continue
                duplicate = any(iou((c.start, c.end), (k.start, k.end)) >= threshold or same_geometry(c, k, frac) for k in kept)
                if not duplicate:
                    kept.append(c)
        out.extend(kept)
    pennants = [c for c in out if c.pattern in PENNANTS and not c.provisional]
    if pennants:
        out = [
            c
            for c in out
            if c.pattern != "symmetrical_triangle"
            or not any(iou(_geom_window(c), _geom_window(p)) >= threshold or same_geometry(c, p, frac) for p in pennants)
        ]
    return out


# --------------------------------------------------------------- geometry


class _Timeline:
    """Maps absolute bar indices to candle timestamps (and projected ones)."""

    def __init__(self, ctx: SeriesContext, calendar: NSECalendar | None, need_future: int):
        self.ctx = ctx
        self.future: list[pd.Timestamp] = []
        if calendar is not None and need_future > 0 and ctx.n:
            self.future = calendar.project_bar_starts(ctx.times[-1], ctx.timeframe, need_future)

    def max_index(self) -> int:
        return self.ctx.last + len(self.future)

    def ts(self, i: int) -> pd.Timestamp:
        if i <= self.ctx.last:
            return self.ctx.times[i]
        return self.future[i - self.ctx.last - 1]

    def point(self, i: int, price: float) -> dict[str, Any]:
        i = int(i)
        return {"t": epoch_s(self.ts(i)), "i": i, "p": round(float(price), 4), "projected": i > self.ctx.last}


def _line_element(tl: _Timeline, eid: str, role: str, line: Line, i0: int, i1: int, style: str) -> dict[str, Any]:
    return {"type": "line", "id": eid, "role": role, "style": style, "points": [tl.point(i0, line.at(i0)), tl.point(i1, line.at(i1))]}


def _marker(tl: _Timeline, eid: str, role: str, i: int, price: float, label: str = "", **extra: Any) -> dict[str, Any]:
    return {"type": "marker", "id": eid, "role": role, **tl.point(i, price), "label": label, **extra}


def _lifecycle_markers(tl: _Timeline, ctx: SeriesContext, life) -> list[dict[str, Any]]:
    out = []
    if life is None:
        return out
    for ev in life.events:
        if ev.event in ("broke_above", "broke_below"):
            up = ev.event == "broke_above"
            out.append(_marker(tl, ev.event, "breakout_up" if up else "breakout_down", ev.index, ctx.close[ev.index], "Broke Above" if up else "Broke Below"))
        elif ev.event == "invalidated":
            out.append(_marker(tl, "invalidated", "invalidation", ev.index, ctx.close[ev.index], f"Invalidated ({ev.detail.get('reason')})"))
    return out


def _line_geometry(cand: Candidate, ctx: SeriesContext, life, calendar: NSECalendar | None) -> tuple[_Timeline, list[dict[str, Any]]]:
    s, e, last = cand.start, cand.end, ctx.last
    r0 = cand.region_start if cand.region_start is not None else s
    proj_end = last
    if cand.apex_index is not None:
        # Project to the apex bar (rounded) so the apex marker shares its timestamp.
        proj_end = max(last, int(round(cand.apex_index)))
    tl = _Timeline(ctx, calendar, proj_end - last)
    proj_end = min(proj_end, tl.max_index())
    elements: list[dict[str, Any]] = [
        {
            "type": "band",
            "id": "formation",
            "role": "formation",
            "upper": [tl.point(r0, cand.upper.at(r0)), tl.point(e, cand.upper.at(e))],
            "lower": [tl.point(r0, cand.lower.at(r0)), tl.point(e, cand.lower.at(e))],
        },
        _line_element(tl, "upper", "upper_boundary", cand.upper, r0, e, "solid"),
        _line_element(tl, "lower", "lower_boundary", cand.lower, r0, e, "solid"),
    ]
    if proj_end > e:
        elements.append(_line_element(tl, "upper_projection", "upper_boundary", cand.upper, e, proj_end, "dashed"))
        elements.append(_line_element(tl, "lower_projection", "lower_boundary", cand.lower, e, proj_end, "dashed"))
    if cand.pole is not None:
        p0, base, p1, top = cand.pole
        elements.append({"type": "line", "id": "pole", "role": "pole", "style": "solid", "points": [tl.point(p0, base), tl.point(p1, top)]})
        elements.append(_marker(tl, "pole_start", "pole_start", p0, base, "Pole"))
        elements.append(_marker(tl, "pole_end", "pole_end", p1, top, "P_end"))
    touch_idx = {(t.index, t.boundary) for t in cand.touches}
    for t in cand.touches:
        elements.append(_marker(tl, f"touch_{t.boundary}_{t.index}", f"touch_{t.boundary}", t.index, t.price, "T", residual=round(t.residual, 4), tolerance=round(t.tolerance, 4)))
    for i in cand.swing_highs:
        if (i, "upper") not in touch_idx:
            elements.append(_marker(tl, f"swing_high_{i}", "swing_high", i, ctx.high[i]))
    for i in cand.swing_lows:
        if (i, "lower") not in touch_idx:
            elements.append(_marker(tl, f"swing_low_{i}", "swing_low", i, ctx.low[i]))
    if cand.apex_index is not None:
        ai = min(int(round(cand.apex_index)), tl.max_index())
        elements.append(_marker(tl, "apex", "apex", ai, cand.upper.at(cand.apex_index), "Apex", index_exact=round(float(cand.apex_index), 3)))
    return tl, elements


def _reversal_geometry(cand: Candidate, ctx: SeriesContext, life) -> tuple[_Timeline, list[dict[str, Any]]]:
    s, e, last = cand.start, cand.end, ctx.last
    tl = _Timeline(ctx, None, 0)
    top = cand.lifecycle_kind == NECK_DOWN
    neck = cand.lower if top else cand.upper
    extremes = [p for p in cand.pivots if p[0] in ("P1", "P2", "LS", "H", "RS")]
    edge = [tl.point(i, price) for _, i, price in extremes]
    neck_pts = [tl.point(s, neck.at(s)), tl.point(e, neck.at(e))]
    elements: list[dict[str, Any]] = [
        {"type": "band", "id": "formation", "role": "formation", "upper": edge if top else neck_pts, "lower": neck_pts if top else edge},
        _line_element(tl, "neckline", "neckline", neck, s, e, "solid"),
    ]
    if last > e:
        elements.append(_line_element(tl, "neckline_projection", "neckline", neck, e, last, "dashed"))
        if cand.extreme is not None:
            lvl = Line(e, float(cand.extreme), 0.0)
            elements.append(_line_element(tl, "invalidation_level", "invalidation_level", lvl, e, last, "dashed"))
    for label, i, price in cand.pivots:
        role = "peak" if (label in ("P1", "P2", "LS", "H", "RS")) == top else "trough"
        elements.append(_marker(tl, f"pivot_{label}", role, i, price, label))
    if cand.provisional:
        latest = cand.pivots[-1]
        elements.append(_marker(tl, "provisional_pivot", "provisional_pivot", latest[1], latest[2], f"{latest[0]} (unconfirmed)"))
    return tl, elements


def build_geometry(det_id: str, cand: Candidate, ctx: SeriesContext, life, calendar: NSECalendar | None) -> dict[str, Any]:
    if cand.family == "reversal":
        tl, elements = _reversal_geometry(cand, ctx, life)
    else:
        tl, elements = _line_geometry(cand, ctx, life, calendar)
    elements += _lifecycle_markers(tl, ctx, life)
    s, e, last = cand.start, cand.end, ctx.last
    return {
        "schema_version": GEOMETRY_SCHEMA_VERSION,
        "detection_id": det_id,
        "pattern": cand.pattern,
        "timeframe": ctx.timeframe,
        "price_basis": PRICE_BASIS,
        "time_basis": TIME_BASIS,
        "formation": {"start": tl.point(s, ctx.close[s]), "end": tl.point(e, ctx.close[e]), "length": cand.length},
        "last_candle": tl.point(last, ctx.close[last]),
        "lifecycle_model": {
            "kind": cand.lifecycle_kind,
            "extreme": None if cand.extreme is None else round(float(cand.extreme), 4),
            "apex_index": None if cand.apex_index is None else round(float(cand.apex_index), 3),
        },
        "elements": elements,
    }


# ------------------------------------------------------------- explanation


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def explain(cand: Candidate, ctx: SeriesContext, life) -> list[str]:
    spec = SPEC_BY_SLUG[cand.pattern]
    t0, t1 = iso(ctx.times[cand.start]), iso(ctx.times[cand.end])
    cfg = ctx.cfg
    lines = [f"{spec.name} on {ctx.timeframe}: bars {cand.start}-{cand.end} ({cand.length} bars, {t0} to {t1})."]
    if cand.family in ("parallel", "converging"):
        lines += [
            f"The newest {cfg.fit_exclude_last_bars} completed bars are excluded from fitting and used only for status.",
            f"Upper boundary fitted on {len(cand.fit_highs)} of {len(cand.swing_highs)} swing highs (k={cfg.swing_k_structure}); "
            f"lower on {len(cand.fit_lows)} of {len(cand.swing_lows)} swing lows; slopes {cand.upper.slope:.4g} / {cand.lower.slope:.4g} per bar.",
            f"Touches: {cand.upper_touches} upper, {cand.lower_touches} lower (absolute distance within max(0.15 W, 0.25 ATR)).",
        ]
    elif cand.family == "continuation":
        m = cand.metrics
        lines += [
            f"Pole: bars {m['pole_start_index']}-{m['pole_end_index']} ({m['pole_bars']} bars), height {m['pole_height']:.4g} "
            f"= {m['pole_height_atr']:.3g} x ATR at the pole start, {m['pole_move_pct']:.3g}% move.",
            f"Consolidation: {m['consolidation_bars']} bars ending at the latest fitted bar; boundaries fitted on raw bar highs/lows; "
            f"touches {cand.upper_touches} upper / {cand.lower_touches} lower (>= {cfg.continuation_touch_min_gap_bars} bars apart).",
        ]
        if cand.provisional:
            lines.append("Forming: preliminary 3-4 bar consolidation - direction, width and retracement pass; not yet a validated flag or pennant.")
    else:
        names = ", ".join(f"{label} {price:.4g} (bar {i})" for label, i, price in cand.pivots)
        lines += [
            f"Swing pivots (k={cfg.swing_k_reversal}): {names}.",
            "The neckline is the only breakout boundary; the pattern extreme is the invalidation level.",
        ]
        if cand.provisional:
            lines.append(f"Forming: the latest pivot has fewer than {cfg.swing_k_reversal} confirmation bars; it is not treated as confirmed.")
    for g in cand.gates:
        lines.append(f"{'PASS' if g.passed else 'FAIL'}  {g.name} = {_fmt(g.value)} (required {g.threshold})")
    if cand.developing:
        lines.append("Developing: every structural gate passes but the formation is exactly one touch short.")
    if life is not None:
        lines.append(f"Lifecycle: {STATUS_LABELS[Status(life.status)]}" + (f" ({life.reason})" if life.reason else "") + ".")
    lines.append("Quality is a geometric fit score (0-100), not a probability of success.")
    return lines


# ------------------------------------------------------------------- engine


def detection_id(symbol: str, timeframe: str, pattern: str, start: pd.Timestamp, end: pd.Timestamp, cfg_hash: str) -> str:
    raw = f"{symbol}|{timeframe}|{pattern}|{iso(start)}|{iso(end)}|{cfg_hash}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _volume_ratio(ctx: SeriesContext, s: int, e: int) -> float | None:
    window = ctx.volume[s : e + 1]
    window = window[np.isfinite(window)]
    last = ctx.volume[ctx.last]
    if not len(window) or not np.isfinite(last) or window.mean() <= 0:
        return None
    return float(last / window.mean())


def boundary_model(cand: Candidate) -> BoundaryModel:
    return BoundaryModel(cand.upper, cand.lower, cand.apex_index, cand.lifecycle_kind, cand.extreme)


def finalize(cand: Candidate, ctx: SeriesContext, calendar: NSECalendar | None, cfg_hash: str) -> Detection:
    cfg: DetectionConfig = ctx.cfg
    spec = SPEC_BY_SLUG[cand.pattern]
    det_id = detection_id(ctx.symbol, ctx.timeframe, cand.pattern, ctx.times[cand.start], ctx.times[cand.end], cfg_hash)
    life = None
    if cand.provisional:
        status, reason = Status.FORMING.value, None
        events = [{"index": cand.end, "event": "forming", "status": status, "detail": {"provisional": True}}]
    elif cand.developing:
        status, reason = Status.DEVELOPING.value, None
        events = [{"index": cand.end, "event": "developing", "status": status, "detail": {"missing_touches": 1}}]
    else:
        life = evaluate_lifecycle(boundary_model(cand), ctx.close, ctx.atr, cand.end, ctx.last, cfg)
        status, reason = life.status, life.reason
        events = [{"index": ev.index, "event": ev.event, "status": ev.status, "detail": ev.detail} for ev in life.events]
    for ev in events:
        ev["time"] = iso(ctx.times[ev["index"]])
        ev["t"] = epoch_s(ctx.times[ev["index"]])
    metrics = dict(cand.metrics)
    if life is not None:
        metrics.update(
            {"upper_now": life.upper_now, "lower_now": life.lower_now, "breakout_index": life.breakout_index, "breakout_direction": life.breakout_direction}
        )
    metrics = {k: (round(float(v), 6) if isinstance(v, (float, np.floating)) else v) for k, v in metrics.items()}
    line_family = cand.family in LINE_FAMILIES
    return Detection(
        detection_id=det_id,
        symbol=ctx.symbol,
        timeframe=ctx.timeframe,
        pattern=cand.pattern,
        pattern_name=spec.name,
        category=spec.category,
        family=cand.family,
        status=status,
        status_label=STATUS_LABELS[Status(status)],
        status_reason=reason,
        quality=round(cand.quality, 2),
        quality_label=cfg.quality_label(cand.quality),
        quality_components=[c.as_dict() for c in cand.components],
        formation_start_index=cand.start,
        formation_end_index=cand.end,
        formation_start=iso(ctx.times[cand.start]),
        formation_end=iso(ctx.times[cand.end]),
        formation_length=cand.length,
        upper_touches=cand.upper_touches if line_family else None,
        lower_touches=cand.lower_touches if line_family else None,
        width_pct=round(float(cand.metrics.get("width_pct", float("nan"))), 4),
        move_pct=round(float(cand.metrics.get("move_pct", float("nan"))), 4),
        volume_ratio=_volume_ratio(ctx, cand.start, cand.end),
        last_close=float(ctx.close[ctx.last]),
        last_candle_time=iso(ctx.times[ctx.last]),
        metrics=metrics,
        gates=[g.as_dict() for g in cand.gates],
        events=events,
        geometry=build_geometry(det_id, cand, ctx, life, calendar),
        explanation=explain(cand, ctx, life),
        config_hash=cfg_hash,
        config_version=cfg.version,
        developing=cand.developing,
        archived=bool(life.archived) if life else False,
    )


def min_bars_required(cfg: DetectionConfig) -> int:
    shortest = min(min(cfg.parallel_lengths), min(cfg.converging_lengths))
    return shortest + cfg.fit_exclude_last_bars + cfg.atr_period


def detect_series(
    ctx: SeriesContext,
    calendar: NSECalendar | None = None,
    patterns: set[str] | None = None,
    include_developing: bool = True,
) -> list[Detection]:
    """Run every applicable detector on one completed-candle series."""
    if ctx.n < min_bars_required(ctx.cfg):
        return []
    cfg_hash = config_hash(ctx.cfg)
    cands: list[Candidate] = []
    previous = ctx.collect_rejections
    ctx.collect_rejections = True  # rejected candidates inform suppression, then are dropped
    try:
        for det in DETECTORS:
            if patterns and not (set(det.patterns) & patterns):
                if not (det.family == "continuation" and "symmetrical_triangle" in patterns):
                    continue
            for c in det.detect(ctx):
                if c.developing and not include_developing:
                    continue
                cands.append(c)
    finally:
        ctx.collect_rejections = previous
    kept = deduplicate(cands, ctx.cfg)
    if patterns:
        kept = [c for c in kept if c.pattern in patterns]
    dets = [finalize(c, ctx, calendar, cfg_hash) for c in kept]
    dets.sort(key=lambda d: (-d.quality, d.pattern, d.formation_start_index))
    return dets


def boundary_model_from_geometry(geometry: dict[str, Any]) -> BoundaryModel:
    """Rebuild frozen boundaries from a stored geometry payload (never refits)."""
    lines = {e["id"]: e for e in geometry["elements"] if e["type"] == "line"}

    def _line(eid: str) -> Line:
        p0, p1 = lines[eid]["points"]
        return Line(int(p0["i"]), float(p0["p"]), (float(p1["p"]) - float(p0["p"])) / (int(p1["i"]) - int(p0["i"])))

    model = geometry.get("lifecycle_model") or {}
    kind = model.get("kind", BAND)
    if kind == NECK_DOWN:
        return BoundaryModel(None, _line("neckline"), None, kind, model.get("extreme"))
    if kind == NECK_UP:
        return BoundaryModel(_line("neckline"), None, None, kind, model.get("extreme"))
    apex = model.get("apex_index")
    if apex is None:
        apex = next((e.get("index_exact") for e in geometry["elements"] if e.get("role") == "apex"), None)
    return BoundaryModel(_line("upper"), _line("lower"), apex)


def track_detection(det: Detection, ctx: SeriesContext):
    """Evaluate a previously detected pattern on a longer completed series using
    its frozen geometry. ``ctx`` must be the same symbol/timeframe series (the
    detection's bar indices are positions in it)."""
    return track_geometry(det.geometry, det.formation_end_index, ctx)


def track_geometry(geometry: dict[str, Any], formation_end_index: int, ctx: SeriesContext):
    model = boundary_model_from_geometry(geometry)
    return evaluate_lifecycle(model, ctx.close, ctx.atr, formation_end_index, ctx.last, ctx.cfg)


def diagnose_series(ctx: SeriesContext) -> list[Candidate]:
    """All classified candidates, including rejected ones (self-test/tests)."""
    ctx.collect_rejections = True
    try:
        return [c for det in DETECTORS for c in det.detect(ctx)]
    finally:
        ctx.collect_rejections = False
