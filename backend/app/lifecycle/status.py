"""Pattern lifecycle state machine (spec section 10).

``evaluate_lifecycle`` walks completed bars after the formation using *frozen*
geometry - it never refits. The same function serves a single scan and
longer post-detection tracking.

Boundary kinds:

* ``band`` (channels, ranges, triangles, wedges, flags, pennants): upper and
  lower boundaries; breaks in either direction.
* ``neckline_down`` (Double Top, Head & Shoulders): the neckline is the only
  breakout boundary (``lower``); the pattern extreme (highest peak / head) is
  the invalidation level.
* ``neckline_up`` (Double Bottom, Inverse H&S): mirror, neckline = ``upper``.

Rules:

* Broke Above / Below: ``breakout_confirm_closes`` consecutive completed closes
  beyond the boundary by at least ``break_buffer_atr_frac x ATR`` (ATR of the
  bar being evaluated). Geometry is frozen from that moment; tracking continues
  for ``post_breakout_track_bars`` bars, then the detection is archived.
* Invalidated (terminal): ``failed_breakout`` (after a break,
  ``failed_breakout_closes`` consecutive closes back inside), ``apex_passed``
  (converging, no break, bar reaches ``apex_index - apex_invalidation_bars``),
  ``extreme_exceeded`` (reversals, before the neckline break a close beyond the
  extreme by ``extreme_exceeded_atr x ATR``).
* Otherwise Near / Validated. Band: within ``near_boundary_width_frac x W`` of a
  boundary (closest normalised distance wins). Reversals: within
  ``reversal_near_neckline_atr x ATR`` of the neckline on the pattern side. A
  close beyond a boundary that is not yet a confirmed break is reported as Near.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import DetectionConfig
from ..patterns.base import Line, Status

BAND, NECK_DOWN, NECK_UP = "band", "neckline_down", "neckline_up"


@dataclass(frozen=True)
class BoundaryModel:
    upper: Line | None
    lower: Line | None
    apex_index: float | None = None
    kind: str = BAND
    extreme: float | None = None

    @property
    def neckline(self) -> Line | None:
        if self.kind == NECK_DOWN:
            return self.lower
        if self.kind == NECK_UP:
            return self.upper
        return None


@dataclass
class LifecycleEvent:
    index: int
    event: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleResult:
    status: str
    reason: str | None
    events: list[LifecycleEvent]
    breakout_index: int | None = None
    breakout_direction: str | None = None
    archived: bool = False
    upper_now: float | None = None
    lower_now: float | None = None


def classify_position(close: float, upper: float, lower: float, near_frac: float) -> tuple[Status, float]:
    """Status of a close relative to a band (no break confirmation here)."""
    width = upper - lower
    if close > upper:
        return Status.NEAR_UPPER, 0.0
    if close < lower:
        return Status.NEAR_LOWER, 0.0
    if width <= 0:
        return Status.NEAR_UPPER, 0.0
    du = (upper - close) / width
    dl = (close - lower) / width
    near_u, near_l = du <= near_frac, dl <= near_frac
    if near_u and near_l:
        return (Status.NEAR_UPPER, du) if du <= dl else (Status.NEAR_LOWER, dl)
    if near_u:
        return Status.NEAR_UPPER, du
    if near_l:
        return Status.NEAR_LOWER, dl
    return Status.VALIDATED, min(du, dl)


def classify_neckline(close: float, neck: float, atr: float, kind: str, near_atr: float) -> Status:
    """Reversal position relative to its neckline (no break confirmation here)."""
    if kind == NECK_DOWN:  # tops: pattern side is above the neckline
        return Status.NEAR_LOWER if close - neck <= near_atr * atr else Status.VALIDATED
    return Status.NEAR_UPPER if neck - close <= near_atr * atr else Status.VALIDATED


def _position(model: BoundaryModel, j: int, close: float, atr: float, cfg: DetectionConfig) -> Status:
    if model.kind == BAND:
        return classify_position(close, float(model.upper.at(j)), float(model.lower.at(j)), cfg.near_boundary_width_frac)[0]
    return classify_neckline(close, float(model.neckline.at(j)), atr, model.kind, cfg.reversal_near_neckline_atr)


def evaluate_lifecycle(
    model: BoundaryModel,
    close: np.ndarray,
    atr: np.ndarray,
    formation_end: int,
    last_index: int,
    cfg: DetectionConfig,
) -> LifecycleResult:
    """Evaluate status on completed bars ``formation_end .. last_index``.

    ``formation_end`` sets the initial state; break and invalidation checks
    start on the next bar.
    """
    events: list[LifecycleEvent] = []
    status = _position(model, formation_end, float(close[formation_end]), float(atr[formation_end]), cfg)
    events.append(LifecycleEvent(formation_end, "formation_identified", status.value, _levels(model, formation_end)))

    above_run = below_run = inside_run = 0
    broke_at: int | None = None
    direction: str | None = None
    reason: str | None = None
    archived = False
    confirm = cfg.breakout_confirm_closes
    can_break_up = model.kind in (BAND, NECK_UP)
    can_break_down = model.kind in (BAND, NECK_DOWN)

    for j in range(formation_end + 1, last_index + 1):
        c = float(close[j])
        a = float(atr[j])
        buf = cfg.break_buffer_atr_frac * a
        up = float(model.upper.at(j)) if model.upper is not None else None
        lo = float(model.lower.at(j)) if model.lower is not None else None
        if broke_at is None:
            if model.apex_index is not None and j >= model.apex_index - cfg.apex_invalidation_bars:
                status, reason = Status.INVALIDATED, "apex_passed"
                events.append(LifecycleEvent(j, "invalidated", status.value, {"reason": reason, "apex_index": model.apex_index}))
                break
            if model.extreme is not None:
                beyond = c > model.extreme + cfg.extreme_exceeded_atr * a if model.kind == NECK_DOWN else c < model.extreme - cfg.extreme_exceeded_atr * a
                if beyond:
                    status, reason = Status.INVALIDATED, "extreme_exceeded"
                    events.append(LifecycleEvent(j, "invalidated", status.value, {"reason": reason, "extreme": model.extreme, "close": c}))
                    break
            above_run = above_run + 1 if can_break_up and c > up + buf else 0
            below_run = below_run + 1 if can_break_down and c < lo - buf else 0
            if above_run >= confirm or below_run >= confirm:
                direction = "up" if above_run >= confirm else "down"
                status = Status.BROKE_ABOVE if direction == "up" else Status.BROKE_BELOW
                broke_at = j
                events.append(
                    LifecycleEvent(
                        j,
                        "broke_above" if direction == "up" else "broke_below",
                        status.value,
                        {
                            "boundary": up if direction == "up" else lo,
                            "buffer": buf,
                            "close": c,
                            "first_close_beyond_index": j - confirm + 1,
                            "geometry": "frozen",
                        },
                    )
                )
                continue
            new_status = _position(model, j, c, a, cfg)
            if new_status != status:
                events.append(LifecycleEvent(j, "status_change", new_status.value, {"from": status.value, "close": c}))
                status = new_status
        else:
            if model.kind == BAND:
                inside = lo <= c <= up
            elif model.kind == NECK_DOWN:
                inside = c >= lo  # back above the neckline
            else:
                inside = c <= up  # back below the neckline
            inside_run = inside_run + 1 if inside else 0
            if inside_run >= cfg.failed_breakout_closes:
                status, reason = Status.INVALIDATED, "failed_breakout"
                events.append(LifecycleEvent(j, "invalidated", status.value, {"reason": reason, "breakout_index": broke_at}))
                break
            if j - broke_at >= cfg.post_breakout_track_bars:
                archived = True
                events.append(LifecycleEvent(j, "archived", status.value, {"bars_since_breakout": j - broke_at}))
                break

    return LifecycleResult(
        status=status.value,
        reason=reason,
        events=events,
        breakout_index=broke_at,
        breakout_direction=direction,
        archived=archived,
        upper_now=float(model.upper.at(last_index)) if model.upper is not None else None,
        lower_now=float(model.lower.at(last_index)) if model.lower is not None else None,
    )


def _levels(model: BoundaryModel, j: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if model.upper is not None:
        out["upper"] = float(model.upper.at(j))
    if model.lower is not None:
        out["lower"] = float(model.lower.at(j))
    return out
