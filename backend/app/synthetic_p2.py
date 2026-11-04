"""Deterministic synthetic fixtures for the P2 detectors (flags, pennants,
double tops/bottoms, head & shoulders) and support/resistance zones.

As with ``synthetic.py``: these prove the code follows the formulas, not that
the rules match real charts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from .data.calendar import NSECalendar
from .synthetic import DEFAULT_END, _frame, path_series, trading_day_index, zigzag_path


# --------------------------------------------------------------- flags


@dataclass(frozen=True)
class FlagSpec:
    """Pole + consolidation, described in the bullish frame (bear = mirror image).

    Consolidation boundaries are offsets from the pole top at the first and last
    consolidation bar."""

    bull: bool = True
    base: float = 1000.0
    height: float = 100.0
    pole_bars: int = 8
    cons_bars: int = 10
    cons_upper: tuple[float, float] = (-2.0, -12.0)
    cons_lower: tuple[float, float] = (-32.0, -42.0)
    half_period: int = 2
    wick: float = 2.0
    lead: int = 60
    post: tuple[float, ...] | None = None  # band positions of post closes (0 = lower, 1 = upper)
    n_post: int = 3
    cons_volume: float = 60_000.0
    pole_volume: float = 150_000.0


def flag_series(spec: FlagSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    b, h, w = spec.base, spec.height, spec.wick
    rng = np.random.default_rng(3)
    o: list[float] = []
    hi: list[float] = []
    lo: list[float] = []
    cl: list[float] = []
    vol: list[float] = []

    def bar(op: float, c: float, hw: float = w, high: float | None = None, low: float | None = None, v: float = 100_000.0) -> None:
        o.append(op)
        cl.append(c)
        hi.append(max(op, c) + hw if high is None else high)
        lo.append(min(op, c) - hw if low is None else low)
        vol.append(v)

    # Quiet lead-in; its last bar carries the lowest low (deterministic pole start).
    lead_close = b + 1.0 * np.sin(np.arange(spec.lead) / 2.0) + rng.normal(0, 0.2, spec.lead)
    prev = lead_close[0]
    for i, c in enumerate(lead_close):
        bar(prev, c, hw=1.5, v=100_000.0)
        prev = c
    lo[-1] = min(lo) - 1.0
    top = b + h
    # Pole: every close above the previous one; P_end high = top exactly.
    for k in range(1, spec.pole_bars + 1):
        c = b + (h - w) * k / spec.pole_bars
        bar(prev, c, v=spec.pole_volume)
        prev = c
    hi[-1] = top
    # Consolidation zig-zag touching both boundaries (raw bars).
    n_c = spec.cons_bars
    n_post = len(spec.post) if spec.post is not None else spec.n_post
    xs = np.arange(n_c + n_post, dtype=float)
    up = top + spec.cons_upper[0] + (spec.cons_upper[1] - spec.cons_upper[0]) * xs / max(1, n_c - 1)
    dn = top + spec.cons_lower[0] + (spec.cons_lower[1] - spec.cons_lower[0]) * xs / max(1, n_c - 1)
    width = up - dn
    period = 2 * spec.half_period
    g = (xs % period) / period
    tri = np.abs(2 * g - 1)  # 1 = upper touch, 0 = lower touch
    hw = min(w, 0.3 * float(width[:n_c].min()))
    mid = dn + hw + tri * (width - 2 * hw)
    for j in range(n_c + n_post):
        if j >= n_c and spec.post is not None:
            c = dn[j] + spec.post[j - n_c] * width[j]
            bar(prev, c, hw=0.3 * hw, v=spec.cons_volume)
        else:
            c = float(np.clip(mid[j] + 0.3 * hw * (1 if g[j] >= 0.5 else -1), mid[j] - hw, mid[j] + hw))
            bar(prev, c, high=mid[j] + hw, low=mid[j] - hw, v=spec.cons_volume)
            o[-1] = float(np.clip(prev, mid[j] - hw, mid[j] + hw))
        prev = c
    arr = {k: np.array(v, dtype=float) for k, v in (("o", o), ("h", hi), ("l", lo), ("c", cl), ("v", vol))}
    arr["h"] = np.maximum(arr["h"], np.maximum(arr["o"], arr["c"]))
    arr["l"] = np.minimum(arr["l"], np.minimum(arr["o"], arr["c"]))
    if not spec.bull:
        arr = {"o": 2 * b - arr["o"], "h": 2 * b - arr["l"], "l": 2 * b - arr["h"], "c": 2 * b - arr["c"], "v": arr["v"]}
    idx = trading_day_index(len(arr["c"]), calendar, end)
    return _frame(idx, arr["o"], arr["h"], arr["l"], arr["c"], arr["v"])


def flag_windows(spec: FlagSpec) -> dict[str, int]:
    pole_end = spec.lead + spec.pole_bars - 1
    return {"pole_start": spec.lead - 1, "pole_end": pole_end, "cons_start": pole_end + 1, "cons_end": pole_end + spec.cons_bars}


# ------------------------------------------------------------- reversals


@dataclass(frozen=True)
class PathSpec:
    points: tuple[tuple[int, float], ...]
    wick: float = 2.0
    lead: int = 40
    mirror_base: float | None = None  # reflect prices around this level (tops -> bottoms)
    volumes: dict[int, float] = field(default_factory=dict)  # bar (relative to the path) -> volume


def reversal_series(spec: PathSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    df = path_series(zigzag_path(list(spec.points)), calendar, wick=spec.wick, end=end, lead=spec.lead)
    for rel, v in spec.volumes.items():
        df.iloc[spec.lead + rel, df.columns.get_loc("volume")] = v
    if spec.mirror_base is not None:
        m = 2 * spec.mirror_base
        df = pd.DataFrame(
            {"open": m - df["open"], "high": m - df["low"], "low": m - df["high"], "close": m - df["close"], "volume": df["volume"]},
            index=df.index,
        )
    return df


def mirrored(spec: PathSpec, base: float = 1000.0) -> PathSpec:
    return replace(spec, mirror_base=base)


def pivot_bar(spec: PathSpec, rel: int) -> int:
    return spec.lead + rel


# ------------------------------------------------------------ catalogue

FLAG_UP = FlagSpec()
PENNANT_UP = FlagSpec(cons_bars=12, cons_upper=(-2.0, -10.0), cons_lower=(-36.0, -28.0), wick=1.5)

DOUBLE_TOP = PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (57, 962)), volumes={25: 200_000.0, 49: 90_000.0})
HEAD_SHOULDERS = PathSpec(points=((0, 860), (24, 1000), (30, 940), (36, 1060), (42, 945), (48, 1002), (56, 966)))

P2_POSITIVE: dict[str, object] = {
    "bullish_flag": FLAG_UP,
    "bearish_flag": replace(FLAG_UP, bull=False),
    "bullish_pennant": PENNANT_UP,
    "bearish_pennant": replace(PENNANT_UP, bull=False),
    "double_top": DOUBLE_TOP,
    "double_bottom": mirrored(DOUBLE_TOP),
    "head_shoulders": HEAD_SHOULDERS,
    "inverse_head_shoulders": mirrored(HEAD_SHOULDERS),
}

SLOW_DRIFT = PathSpec(points=((0, 1000), (40, 1060), (52, 1050), (60, 1056)), wick=2.0)

# Structurally different series that must NOT produce the keyed pattern.
P2_NEGATIVE: dict[str, tuple[str, object]] = {
    "bullish_flag": ("bearish pole + consolidation", replace(FLAG_UP, bull=False)),
    "bearish_flag": ("bullish pole + consolidation", FLAG_UP),
    "bullish_pennant": ("flag (parallel consolidation)", FLAG_UP),
    "bearish_pennant": ("bullish pennant", PENNANT_UP),
    "double_top": ("head & shoulders (unequal peaks)", HEAD_SHOULDERS),
    "double_bottom": ("double top", DOUBLE_TOP),
    "head_shoulders": ("double top (no head)", DOUBLE_TOP),
    "inverse_head_shoulders": ("head & shoulders (tops)", HEAD_SHOULDERS),
}

# Misses by one rule: (label, spec, expected failing gate).
P2_NEAR_MISS: dict[str, tuple[str, object, str]] = {
    "bullish_flag": ("retracement > 50%", replace(FLAG_UP, cons_upper=(-2.0, -28.0), cons_lower=(-40.0, -66.0)), "retracement"),
    "bearish_flag": ("retracement > 50%", replace(FLAG_UP, bull=False, cons_upper=(-2.0, -28.0), cons_lower=(-40.0, -66.0)), "retracement"),
    "bullish_pennant": ("CR 0.69 > 0.60", replace(PENNANT_UP, cons_upper=(-2.0, -7.0), cons_lower=(-34.0, -29.0)), "convergence_ratio_max"),
    "bearish_pennant": ("CR 0.69 > 0.60", replace(PENNANT_UP, bull=False, cons_upper=(-2.0, -7.0), cons_lower=(-34.0, -29.0)), "convergence_ratio_max"),
    "double_top": ("no prior uptrend (prior move < D)", PathSpec(points=((0, 992), (25, 1000), (37, 940), (49, 1003), (57, 962))), "prior_trend"),
    "double_bottom": ("no prior downtrend (prior move < D)", mirrored(PathSpec(points=((0, 992), (25, 1000), (37, 940), (49, 1003), (57, 962)))), "prior_trend"),
    "head_shoulders": ("time symmetry 14/34 < 0.5", PathSpec(points=((0, 860), (24, 1000), (31, 940), (38, 1060), (52, 945), (72, 1002), (80, 966))), "time_symmetry"),
    "inverse_head_shoulders": ("time symmetry 14/34 < 0.5", mirrored(PathSpec(points=((0, 860), (24, 1000), (31, 940), (38, 1060), (52, 945), (72, 1002), (80, 966)))), "time_symmetry"),
}

_FAIL_UP = (0.5, 0.6, 1.4, 1.5, 0.7, 0.6, 0.5)
_FAIL_DOWN = (0.5, 0.4, -0.4, -0.5, 0.3, 0.4, 0.5)

# Detected as of ``asof_rel`` bars after the formation end, then tracked with frozen geometry.
P2_INVALIDATION: dict[str, tuple[str, object, str]] = {
    "bullish_flag": ("break above, 3 closes back inside", replace(FLAG_UP, post=_FAIL_UP), "failed_breakout"),
    "bearish_flag": ("break below, 3 closes back inside", replace(FLAG_UP, bull=False, post=_FAIL_UP), "failed_breakout"),
    "bullish_pennant": ("no break before apex - 3", replace(PENNANT_UP, post=(0.5,) * 16), "apex_passed"),
    "bearish_pennant": ("no break before apex - 3", replace(PENNANT_UP, bull=False, post=(0.5,) * 16), "apex_passed"),
    "double_top": ("close above the higher peak + 0.5 ATR", PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (57, 962), (66, 1030))), "extreme_exceeded"),
    "double_bottom": ("close below the lower trough - 0.5 ATR", mirrored(PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (57, 962), (66, 1030)))), "extreme_exceeded"),
    "head_shoulders": ("neckline break, 3 closes back above", PathSpec(points=((0, 860), (24, 1000), (30, 940), (36, 1060), (42, 945), (48, 1002), (56, 966), (60, 925), (62, 924), (66, 975), (72, 985))), "failed_breakout"),
    "inverse_head_shoulders": ("neckline break, 3 closes back below", mirrored(PathSpec(points=((0, 860), (24, 1000), (30, 940), (36, 1060), (42, 945), (48, 1002), (56, 966), (60, 925), (62, 924), (66, 975), (72, 985)))), "failed_breakout"),
}

# Provisional (Forming) fixtures.
FLAG_FORMING = replace(FLAG_UP, cons_bars=4)
DOUBLE_TOP_FORMING = PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (52, 985)))  # 3 bars after P2
DOUBLE_TOP_BROKE = PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (58, 950), (61, 925)))


def series_for(spec: object, calendar: NSECalendar) -> pd.DataFrame:
    if isinstance(spec, FlagSpec):
        return flag_series(spec, calendar)
    if isinstance(spec, PathSpec):
        return reversal_series(spec, calendar)
    raise TypeError(type(spec))


def formation_end_bar(slug: str, spec: object) -> int:
    """Absolute bar index where the fixture's formation ends (for as-of cuts)."""
    if isinstance(spec, FlagSpec):
        return flag_windows(spec)["cons_end"]
    pts = spec.points
    peak_rel = pts[3][0] if slug in ("double_top", "double_bottom") else pts[5][0]
    return spec.lead + peak_rel


# ------------------------------------------------------------- S/R zones


def sr_series(calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    """Range 950-1050 for 120 bars (repeated pivots at both levels), then a break
    above 1050 that is retested from above, then drift higher."""
    pts = [(0, 1000)]
    x = 0
    for _ in range(5):
        x += 12
        pts.append((x, 1048))
        x += 12
        pts.append((x, 952))
    x += 10
    pts.append((x, 1100))  # breakout above resistance
    x += 8
    pts.append((x, 1056))  # retest of the broken resistance from above
    x += 12
    pts.append((x, 1130))
    return path_series(zigzag_path(pts), calendar, wick=3.0, end=end, lead=20)
