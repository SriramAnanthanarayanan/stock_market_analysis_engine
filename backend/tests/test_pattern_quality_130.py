"""DetectionConfig 1.3.0: chart-quality rules from the visual review.

H&S / doubles: consecutive extrema (Lo, Mamaysky & Wang 2000), single head,
close-confirmed points, trend extreme, Savin-Weller-Zvingelis restrictions.
Flags: steep pole, no drift with the pole. Wedges/triangles: >= 50% narrowing.
Synthetic paths prove each rule fires for its own reason; they are not a
substitute for chart review or labelled evaluation.
"""

from dataclasses import replace

import numpy as np

from app.config import DEFAULT_CONFIG
from app.patterns.base import SeriesContext
from app.patterns.continuation import ContinuationDetector, find_pole
from app.patterns.reversals import ReversalDetector, _counter_swing, _head_region_swing, _wick
from app.synthetic_p2 import P2_POSITIVE, PathSpec, flag_windows, pivot_bar, series_for


def ctx_of(df, cfg=DEFAULT_CONFIG):
    return SeriesContext("SYN", "1d", df, cfg, collect_rejections=True)


def _cands(df, pattern, cfg=DEFAULT_CONFIG):
    return [c for c in ReversalDetector().detect(ctx_of(df, cfg)) if c.pattern == pattern]


def _failed(c):
    return {g.name for g in c.gates if not g.passed}


# ------------------------------------------------------------- helpers


def test_counter_swing_measures_moves_against_the_leg():
    falling = np.array([100, 90, 80, 95, 70, 60.0])  # rally 80 -> 95 inside a falling leg
    rising = np.array([60, 70, 90, 75, 100.0])  # pull-back 90 -> 75 inside a rising leg
    assert _counter_swing(falling, up=False) == 15
    assert _counter_swing(rising, up=True) == 15
    assert _counter_swing(np.array([100, 90, 80.0]), up=False) == 0


def test_head_region_swing_sees_a_second_peak_above_the_shoulders():
    #            T1  .    .    H    dip  2nd  .   T2
    C = np.array([90, 100, 115, 120, 104, 117, 100, 88.0])
    P = C + 1
    assert _head_region_swing(C, P, 0, 3, 7, level=105) == 13  # 117 - 104 while above 105
    assert _head_region_swing(C, P, 0, 3, 7, level=125) == 0  # nothing above the shoulders


def test_wick_is_distance_to_best_nearby_close():
    C = np.array([10, 11, 12, 11, 10.0])
    P = np.array([10.5, 11.5, 15, 11.5, 10.5])
    assert _wick(C, P, 2, True) == 3  # spike high 15 vs best close 12


# ------------------------------------------------------------- H & S


HS_SECOND_HEAD = PathSpec(points=((0, 860), (24, 1000), (30, 940), (36, 1060), (40, 1008), (43, 1048), (48, 945), (54, 1002), (62, 966)))


def test_second_peak_above_the_shoulders_is_not_a_textbook_head(calendar):
    spec = HS_SECOND_HEAD
    cands = _cands(series_for(spec, calendar), "head_shoulders")
    assert not [c for c in cands if not c.rejected], "a double-headed top must not be reported as H&S"
    ls, h, rs = pivot_bar(spec, 24), pivot_bar(spec, 36), pivot_bar(spec, 54)
    match = [c for c in cands if (c.metrics["ls_index"], c.metrics["head_index"], c.metrics["rs_index"]) == (ls, h, rs)]
    assert match and "single_head" in _failed(match[0])


def test_head_must_top_the_prior_trend(calendar):
    # An earlier high (1100) above the head inside the prior-trend window.
    spec = PathSpec(points=((0, 820), (6, 1100), (14, 880), (24, 1000), (30, 940), (36, 1060), (42, 945), (48, 1002), (56, 966)))
    cands = _cands(series_for(spec, calendar), "head_shoulders")
    assert not [c for c in cands if not c.rejected]
    match = [c for c in cands if c.metrics["head_index"] == pivot_bar(spec, 36) and c.metrics["rs_index"] == pivot_bar(spec, 48)]
    assert match and "head_is_trend_extreme" in _failed(match[0])


def test_positive_hs_passes_every_new_gate(calendar):
    c = [c for c in _cands(series_for(P2_POSITIVE["head_shoulders"], calendar), "head_shoulders") if not c.rejected]
    assert c
    names = {g.name for g in c[0].gates}
    assert {"consecutive_extrema", "single_head", "close_confirmed_peaks", "close_confirmed_neckline", "head_is_trend_extreme", "shoulders_below_head", "leg_balance"} <= names
    assert c[0].metrics["counter_swing_hh"] < DEFAULT_CONFIG.reversal_counter_swing_frac


def test_lopsided_shoulders_rejected_by_tighter_similarity(calendar):
    # RS 6% above LS: allowed before 1.3.0 (5% of price, 0.3 HH), rejected now (4%, 0.2 HH).
    spec = PathSpec(points=((0, 860), (24, 1000), (30, 940), (36, 1100), (42, 945), (48, 1045), (56, 990)))
    cands = _cands(series_for(spec, calendar), "head_shoulders")
    assert not [c for c in cands if not c.rejected]
    match = [c for c in cands if c.metrics["rs_index"] == pivot_bar(spec, 48) and c.metrics["ls_index"] == pivot_bar(spec, 24)]
    assert match and "shoulder_similarity" in _failed(match[0])


# ------------------------------------------------------------- doubles


def test_double_top_with_an_extra_swing_between_the_peaks_is_rejected(calendar):
    spec = PathSpec(points=((0, 900), (25, 1000), (30, 950), (33, 990), (38, 940), (49, 1003), (57, 962)))
    cands = _cands(series_for(spec, calendar), "double_top")
    assert not [c for c in cands if not c.rejected]
    match = [c for c in cands if (c.metrics["p1_index"], c.metrics["p2_index"]) == (pivot_bar(spec, 25), pivot_bar(spec, 49))]
    assert match and "consecutive_extrema" in _failed(match[0])


def test_double_top_below_an_earlier_high_is_rejected(calendar):
    spec = PathSpec(points=((0, 880), (8, 1040), (14, 900), (25, 1000), (37, 940), (49, 1003), (57, 962)))
    cands = _cands(series_for(spec, calendar), "double_top")
    assert not [c for c in cands if not c.rejected]
    match = [c for c in cands if (c.metrics["p1_index"], c.metrics["p2_index"]) == (pivot_bar(spec, 25), pivot_bar(spec, 49))]
    assert match and "peaks_are_trend_extreme" in _failed(match[0])


# ------------------------------------------------------------- flags


def test_slow_pole_fails_steepness(calendar):
    spec = P2_POSITIVE["bullish_flag"]
    df = series_for(spec, calendar)
    w = flag_windows(spec)
    pole = find_pole(ctx_of(df), w["pole_end"], True)
    assert pole.valid
    steep = pole.height / pole.atr / pole.length
    strict = replace(DEFAULT_CONFIG, pole_min_atr_per_bar=steep + 0.1)
    slow = find_pole(ctx_of(df, strict), w["pole_end"], True)
    assert not slow.valid and {g.name for g in slow.gates if not g.passed} == {"pole_steepness"}


def test_flag_drifting_with_the_pole_is_rejected(calendar):
    # Bullish pole, consolidation rising 6% of the pole (with the trend): not a flag.
    spec = replace(P2_POSITIVE["bullish_flag"], cons_upper=(-12.0, -6.0), cons_lower=(-42.0, -36.0))
    ctx = ctx_of(series_for(spec, calendar))
    flags = [c for c in ContinuationDetector().detect(ctx) if c.pattern == "bullish_flag"]
    assert not [c for c in flags if not c.rejected and not c.provisional]
    assert any("flag_movement" in _failed(c) for c in flags)
