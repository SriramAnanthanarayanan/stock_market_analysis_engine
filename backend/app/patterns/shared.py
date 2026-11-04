"""Shared geometry for line-based patterns (spec section 8.0).

Coordinates: ``x`` is the bar index inside the formation window (0 at the
window start). ``Line`` objects store the absolute index of x=0.

* Anchored line: OLS slope through the boundary's relevant swing points, then
  the intercept is shifted so the line passes through the extreme swing point
  (upper: max(high - slope*x); lower: min(low - slope*x)).
* Relevant swing points: all swing points of that side inside the fit window,
  refined once - points farther than ``fit_refine_tolerance_mult`` x touch
  tolerance from the initial anchored line are dropped from the *slope*
  estimate (if >= 2 remain). Anchoring always uses every swing point, so the
  line still bounds all of them. Deterministic; documented in pattern-rules.md.
* Parallel fit: pooled OLS (one shared slope, two intercepts), anchored.
* Touch: ``abs(line(x) - price) <= max(0.15 W(x), 0.25 ATR)`` - an absolute
  distance, so a candle far beyond the boundary never counts as a touch.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import DetectionConfig
from .base import Gate, Line, SeriesContext, Touch


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Return (slope, intercept, r2) of y ~ x."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 2:
        raise ValueError("OLS needs at least two points")
    xm, ym = x.mean(), y.mean()
    dx = x - xm
    sxx = float(dx @ dx)
    slope = float(dx @ (y - ym)) / sxx if sxx > 0 else 0.0
    intercept = float(ym - slope * xm)
    resid = y - (intercept + slope * x)
    ss_tot = float(((y - ym) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2


def anchor_intercept(x: np.ndarray, y: np.ndarray, slope: float, side: str) -> float:
    v = np.asarray(y, dtype=float) - slope * np.asarray(x, dtype=float)
    return float(v.max() if side == "upper" else v.min())


def pooled_slope(xu: np.ndarray, yu: np.ndarray, xl: np.ndarray, yl: np.ndarray) -> float:
    """Common slope of two point sets with separate intercepts (pooled OLS)."""
    xu, yu, xl, yl = (np.asarray(a, dtype=float) for a in (xu, yu, xl, yl))
    du, dl = xu - xu.mean(), xl - xl.mean()
    num = float(du @ (yu - yu.mean()) + dl @ (yl - yl.mean()))
    den = float(du @ du + dl @ dl)
    return num / den if den > 0 else 0.0


@dataclass(frozen=True)
class BoundaryFit:
    upper: Line
    lower: Line
    fit_highs: np.ndarray  # absolute indices used for the slope estimate
    fit_lows: np.ndarray


def tolerance(width: np.ndarray | float, atr: np.ndarray | float, cfg: DetectionConfig) -> np.ndarray:
    return np.maximum(cfg.touch_tol_width_frac * np.asarray(width, dtype=float), cfg.touch_tol_atr_frac * np.asarray(atr, dtype=float))


def fit_independent(ctx: SeriesContext, s: int, hi: np.ndarray, lo: np.ndarray, refine: bool = True) -> BoundaryFit:
    cfg = ctx.cfg
    xu, yu = (hi - s).astype(float), ctx.high[hi]
    xl, yl = (lo - s).astype(float), ctx.low[lo]
    bu = ols(xu, yu)[0]
    bl = ols(xl, yl)[0]
    au = anchor_intercept(xu, yu, bu, "upper")
    al = anchor_intercept(xl, yl, bl, "lower")
    keep_u = np.ones(len(hi), dtype=bool)
    keep_l = np.ones(len(lo), dtype=bool)
    if refine:
        mult = cfg.fit_refine_tolerance_mult
        wu = (au + bu * xu) - (al + bl * xu)
        wl = (au + bu * xl) - (al + bl * xl)
        ku = np.abs(au + bu * xu - yu) <= mult * tolerance(wu, ctx.atr[hi], cfg)
        kl = np.abs(al + bl * xl - yl) <= mult * tolerance(wl, ctx.atr[lo], cfg)
        if ku.sum() >= 2 and not ku.all():
            keep_u = ku
            bu = ols(xu[ku], yu[ku])[0]
            au = anchor_intercept(xu, yu, bu, "upper")
        if kl.sum() >= 2 and not kl.all():
            keep_l = kl
            bl = ols(xl[kl], yl[kl])[0]
            al = anchor_intercept(xl, yl, bl, "lower")
    return BoundaryFit(Line(s, au, bu), Line(s, al, bl), hi[keep_u], lo[keep_l])


def fit_parallel(ctx: SeriesContext, s: int, hi: np.ndarray, lo: np.ndarray, fit_hi: np.ndarray, fit_lo: np.ndarray) -> BoundaryFit:
    b = pooled_slope(fit_hi - s, ctx.high[fit_hi], fit_lo - s, ctx.low[fit_lo])
    au = anchor_intercept(hi - s, ctx.high[hi], b, "upper")
    al = anchor_intercept(lo - s, ctx.low[lo], b, "lower")
    return BoundaryFit(Line(s, au, b), Line(s, al, b), fit_hi, fit_lo)


def widths(upper: Line, lower: Line, s: int, e: int) -> tuple[float, float, float]:
    """(W0, W1, W_mean). W is linear in x, so its mean is the endpoint average."""
    w0 = float(upper.at(s) - lower.at(s))
    w1 = float(upper.at(e) - lower.at(e))
    return w0, w1, (w0 + w1) / 2.0


def find_touches(ctx: SeriesContext, upper: Line, lower: Line, hi: np.ndarray, lo: np.ndarray) -> list[Touch]:
    cfg = ctx.cfg
    out: list[Touch] = []
    for idx_arr, line, prices, side in ((hi, upper, ctx.high, "upper"), (lo, lower, ctx.low, "lower")):
        if len(idx_arr) == 0:
            continue
        lv = line.at(idx_arr)
        w = upper.at(idx_arr) - lower.at(idx_arr)
        tol = tolerance(w, ctx.atr[idx_arr], cfg)
        res = np.abs(lv - prices[idx_arr])
        for i, r, t in zip(idx_arr, res, tol):
            if r <= t:
                out.append(Touch(int(i), float(prices[i]), side, float(r), float(t)))
    out.sort(key=lambda t: (t.index, t.boundary))
    return out


def containment(ctx: SeriesContext, upper: Line, lower: Line, start: int, end: int) -> float:
    """Share of bars in [start, end] with high <= upper + s and low >= lower - s."""
    cfg = ctx.cfg
    if end < start:
        return 0.0
    idx = np.arange(start, end + 1)
    up = upper.at(idx)
    lo = lower.at(idx)
    w = up - lo
    slack = np.maximum(cfg.containment_slack_width_frac * w, cfg.containment_slack_atr_frac * ctx.atr[idx])
    inside = (ctx.high[idx] <= up + slack) & (ctx.low[idx] >= lo - slack)
    return float(inside.mean())


def close_trend(ctx: SeriesContext, start: int, end: int) -> tuple[float, float]:
    """(slope, R^2) of close OLS over [start, end]."""
    if end - start + 1 < 3:
        return 0.0, 0.0
    idx = np.arange(start, end + 1)
    slope, _, r2 = ols(idx - start, ctx.close[idx])
    return slope, r2


@dataclass(frozen=True)
class TouchStats:
    count: int
    first_x: int | None
    last_x: int | None
    spread: int
    first_half: bool
    second_half: bool
    mean_residual: float
    mean_residual_ratio: float


def touch_stats(touches: list[Touch], boundary: str, s: int, length: int) -> TouchStats:
    xs = [t.index - s for t in touches if t.boundary == boundary]
    res = [t.residual for t in touches if t.boundary == boundary]
    ratio = [t.residual / t.tolerance for t in touches if t.boundary == boundary and t.tolerance > 0]
    if not xs:
        return TouchStats(0, None, None, 0, False, False, float("nan"), float("nan"))
    half = length / 2.0
    return TouchStats(
        count=len(xs),
        first_x=min(xs),
        last_x=max(xs),
        spread=max(xs) - min(xs),
        first_half=any(x < half for x in xs),
        second_half=any(x >= half for x in xs),
        mean_residual=float(np.mean(res)),
        mean_residual_ratio=float(np.mean(ratio)) if ratio else float("nan"),
    )


def touch_deficit(n_upper: int, n_lower: int, min_upper: int, min_lower: int, min_total: int) -> int:
    """Fewest additional touches needed to satisfy all touch-count gates."""
    need = max(0, min_upper - n_upper) + max(0, min_lower - n_lower)
    return max(need, min_total - n_upper - n_lower, 0)


def touch_gates(
    su: TouchStats,
    sl: TouchStats,
    length: int,
    min_upper: int,
    min_lower: int,
    min_total: int,
    cfg: DetectionConfig,
    require_halves: bool,
) -> list[Gate]:
    total = su.count + sl.count
    gates = [
        Gate("upper_touches", su.count >= min_upper, su.count, f">= {min_upper}", "touch"),
        Gate("lower_touches", sl.count >= min_lower, sl.count, f">= {min_lower}", "touch"),
        Gate("total_touches", total >= min_total, total, f">= {min_total}", "touch"),
    ]
    min_spread = cfg.touch_spread_min_frac * length
    for side, st in (("upper", su), ("lower", sl)):
        group = f"touch_dist_{side}"
        if require_halves:
            gates.append(Gate(f"{side}_touch_in_first_half", st.first_half, st.first_x, f"a touch at x < {length / 2:g}", group))
            gates.append(Gate(f"{side}_touch_in_second_half", st.second_half, st.last_x, f"a touch at x >= {length / 2:g}", group))
        gates.append(Gate(f"{side}_touch_spread", st.spread >= min_spread, st.spread, f">= {min_spread:g} bars (0.4 x L)", group))
    return gates


def classify_validity(gates: list[Gate], su: TouchStats, sl: TouchStats, min_upper: int, min_lower: int, deficit: int) -> str:
    """'valid', 'developing' (exactly one touch short, everything else passes) or 'rejected'."""
    if all(g.passed for g in gates):
        return "valid"
    if deficit != 1:
        return "rejected"
    for g in gates:
        if g.passed:
            continue
        if g.group == "structure":
            return "rejected"
        # Distribution gates of a boundary that is itself short of touches are
        # implied by the missing touch; otherwise they are genuine failures.
        if g.group == "touch_dist_upper" and su.count >= min_upper:
            return "rejected"
        if g.group == "touch_dist_lower" and sl.count >= min_lower:
            return "rejected"
    return "developing"


def iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0]) + 1
    if inter <= 0:
        return 0.0
    union = (a[1] - a[0] + 1) + (b[1] - b[0] + 1) - inter
    return inter / union
