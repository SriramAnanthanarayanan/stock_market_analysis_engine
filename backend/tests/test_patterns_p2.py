"""P2 detector tests: flags, pennants, double tops/bottoms, H&S, precedence.

Synthetic examples verify the formulas, not real-chart accuracy.
"""

from dataclasses import replace

import numpy as np
import pytest

from app.config import DEFAULT_CONFIG
from app.models import Geometry
from app.patterns.base import Line, SeriesContext
from app.patterns.engine import deduplicate, detect_series, diagnose_series, track_detection
from app.synthetic import POSITIVE, band_series
from app.synthetic_p2 import (
    DOUBLE_TOP_BROKE,
    DOUBLE_TOP_FORMING,
    FLAG_FORMING,
    P2_INVALIDATION,
    P2_NEAR_MISS,
    P2_NEGATIVE,
    P2_POSITIVE,
    SLOW_DRIFT,
    FlagSpec,
    flag_windows,
    formation_end_bar,
    series_for,
)
from app.timeutil import epoch_s
from tests.test_patterns import _cand

P2_PATTERNS = tuple(P2_POSITIVE)
FLAGS = ("bullish_flag", "bearish_flag", "bullish_pennant", "bearish_pennant")


def ctx_of(df):
    return SeriesContext("SYN", "1d", df, DEFAULT_CONFIG)


def run(df, cal, **kw):
    return detect_series(ctx_of(df), cal, **kw)


# ---------------------------------------------------- four cases per pattern


@pytest.mark.parametrize("slug", P2_PATTERNS)
def test_positive_example_detected(slug, calendar):
    spec = P2_POSITIVE[slug]
    dets = [d for d in run(series_for(spec, calendar), calendar) if d.pattern == slug]
    assert dets, f"{slug} not detected"
    d = dets[0]
    assert d.status in ("validated", "near_upper", "near_lower"), d.status
    assert d.formation_end_index == formation_end_bar(slug, spec)
    assert 0 <= d.quality <= 100
    assert all(0 <= c["score"] <= 1 for c in d.quality_components)


@pytest.mark.parametrize("slug", P2_PATTERNS)
def test_negative_example_not_reported(slug, calendar):
    _, spec = P2_NEGATIVE[slug]
    assert all(d.pattern != slug for d in run(series_for(spec, calendar), calendar))


@pytest.mark.parametrize("slug", P2_PATTERNS)
def test_near_miss_rejected_for_the_right_reason(slug, calendar):
    _, spec, expected = P2_NEAR_MISS[slug]
    ctx = ctx_of(series_for(spec, calendar))
    assert not [d for d in detect_series(ctx, calendar) if d.pattern == slug and d.status not in ("developing", "forming")]
    end = formation_end_bar(slug, spec)
    if isinstance(spec, FlagSpec):
        cons = flag_windows(spec)["cons_start"]
        diag = [c for c in diagnose_series(ctx) if c.pattern == slug and c.region_start == cons and c.end == end]
    else:
        diag = [c for c in diagnose_series(ctx) if c.pattern == slug and c.end == end]
    failed = {g.name for c in diag for g in c.gates if not g.passed}
    assert expected in failed, failed


@pytest.mark.parametrize("slug", P2_PATTERNS)
def test_invalidated_example_with_frozen_geometry(slug, calendar):
    _, spec, reason = P2_INVALIDATION[slug]
    df = series_for(spec, calendar)
    end = formation_end_bar(slug, spec)
    cut = end + 1 + (DEFAULT_CONFIG.fit_exclude_last_bars if isinstance(spec, FlagSpec) else DEFAULT_CONFIG.swing_k_reversal + 3)
    first = [d for d in run(df.iloc[:cut], calendar) if d.pattern == slug and d.status not in ("developing", "forming")]
    assert first, f"{slug} not detected as of the formation end"
    life = track_detection(first[0], ctx_of(df))
    assert (life.status, life.reason) == ("invalidated", reason)


# ------------------------------------------------------------ provisional


def test_flag_with_three_or_four_bar_consolidation_is_forming(calendar):
    for n in (3, 4):
        dets = [d for d in run(series_for(replace(FLAG_FORMING, cons_bars=n), calendar), calendar) if d.family == "continuation"]
        assert dets and all(d.status == "forming" for d in dets), [d.status for d in dets]
        assert dets[0].metrics["provisional"] is True


def test_unconfirmed_pivot_only_forming(calendar):
    dets = [d for d in run(series_for(DOUBLE_TOP_FORMING, calendar), calendar) if d.pattern == "double_top"]
    assert dets and dets[0].status == "forming" and dets[0].metrics["provisional_pivot"] is True
    roles = {e["role"] for e in dets[0].geometry["elements"]}
    assert "provisional_pivot" in roles


def test_double_top_breaks_below_neckline(calendar):
    d = [x for x in run(series_for(DOUBLE_TOP_BROKE, calendar), calendar) if x.pattern == "double_top"][0]
    assert d.status == "broke_below"
    assert any(e["role"] == "breakout_down" for e in d.geometry["elements"])


def test_no_pole_no_flag(calendar):
    dets = run(series_for(SLOW_DRIFT, calendar), calendar)
    assert not [d for d in dets if d.family == "continuation"]


def test_pole_rules(calendar):
    from app.patterns.continuation import find_pole

    df = series_for(P2_POSITIVE["bullish_flag"], calendar)
    w = flag_windows(P2_POSITIVE["bullish_flag"])
    pole = find_pole(ctx_of(df), w["pole_end"], True)
    assert pole.valid and pole.start == w["pole_start"] and 3 <= pole.length <= 15
    assert pole.height >= 3 * pole.atr and pole.directional >= 0.6
    weak = series_for(replace(P2_POSITIVE["bullish_flag"], height=12.0, cons_upper=(-1, -2), cons_lower=(-5, -6)), calendar)
    weak_pole = find_pole(ctx_of(weak), w["pole_end"], True)
    assert weak_pole is not None and not weak_pole.valid
    assert {g.name for g in weak_pole.gates if not g.passed} >= {"pole_move_pct"}


def test_raw_bar_touches_are_spaced():
    from app.patterns.continuation import raw_touches

    class Ctx:
        cfg = DEFAULT_CONFIG
        high = np.array([10.0, 10.0, 10.0, 9.0, 10.0])
        low = np.array([8.0, 8.5, 8.6, 8.0, 8.9])
        atr = np.full(5, 0.4)

    t = raw_touches(Ctx(), Line(0, 10.0, 0.0), Line(0, 8.0, 0.0), 0, 4)
    uppers = [x.index for x in t if x.boundary == "upper"]
    assert uppers == [0, 2, 4]  # bar 1 is only one bar after bar 0


# ------------------------------------------------------------ precedence


def test_pennant_beats_symmetrical_triangle_on_same_formation():
    pennant = _cand("bullish_pennant", 40, 79, (0, 0), (0, 0), 60, family="continuation")
    pennant.region_start = 68  # consolidation 68-79 after a pole from bar 40
    pennant.upper, pennant.lower = Line(68, 1098, -0.8), Line(68, 1064, 0.8)
    same = _cand("symmetrical_triangle", 50, 79, (1098 + 0.8 * 18, -0.8), (1064 - 0.8 * 18, 0.8), 80, family="converging")
    distinct = _cand("symmetrical_triangle", 0, 79, (1300, -2.0), (800, 2.5), 70, family="converging")
    kept = deduplicate([pennant, same, distinct], DEFAULT_CONFIG)
    assert pennant in kept and same not in kept and distinct in kept


def test_flag_inside_channel_coexists(calendar):
    chan = _cand("falling_channel", 30, 149, (1200, -1.5), (1100, -1.5), 90)
    flag = _cand("bullish_flag", 120, 149, (1000, -0.5), (975, -0.5), 70, family="continuation")
    flag.region_start = 135
    assert set(c.pattern for c in deduplicate([chan, flag], DEFAULT_CONFIG)) == {"falling_channel", "bullish_flag"}


def test_forming_never_displaces_valid():
    valid = _cand("bullish_flag", 59, 77, (1098, -1.0), (1068, -1.0), 50, family="continuation")
    forming = _cand("bullish_flag", 59, 75, (1098, -1.0), (1068, -1.0), 95, family="continuation")
    forming.provisional = True
    assert deduplicate([valid, forming], DEFAULT_CONFIG) == [valid]


def test_tops_and_bottoms_dedup_separately():
    top = _cand("double_top", 60, 90, (0, 0), (0, 0), 80, family="reversal")
    hs = _cand("head_shoulders", 58, 90, (0, 0), (0, 0), 70, family="reversal")
    bottom = _cand("double_bottom", 60, 90, (0, 0), (0, 0), 60, family="reversal")
    top.group = hs.group = "reversal_top"
    bottom.group = "reversal_bottom"
    for c in (top, hs, bottom):
        c.upper = c.lower = None
    kept = deduplicate([top, hs, bottom], DEFAULT_CONFIG)
    assert top in kept and bottom in kept and hs not in kept


# --------------------------------------------------------------- geometry


@pytest.mark.parametrize("slug", P2_PATTERNS)
def test_p2_geometry_matches_candles_and_lifecycle_model(slug, calendar):
    df = series_for(P2_POSITIVE[slug], calendar)
    ctx = ctx_of(df)
    d = [x for x in detect_series(ctx, calendar) if x.pattern == slug][0]
    geo = Geometry.model_validate(d.geometry)
    assert geo.schema_version == "1.1" and geo.lifecycle_model is not None
    for el in geo.elements:
        pts = el.points if el.type == "line" else (el.upper + el.lower if el.type == "band" else [el])
        for p in pts:
            if not p.projected:
                assert p.t == epoch_s(ctx.times[p.i])
        if el.type == "marker" and el.role in ("peak", "pole_end") and slug in ("double_top", "head_shoulders", "bullish_flag", "bullish_pennant"):
            assert el.p == pytest.approx(ctx.high[el.i], abs=1e-3)
        if el.type == "marker" and el.role == "trough" and slug in ("double_top", "head_shoulders"):
            assert el.p == pytest.approx(ctx.low[el.i], abs=1e-3)
    roles = {e.role for e in geo.elements}
    if slug in FLAGS:
        assert {"pole", "upper_boundary", "lower_boundary"} <= roles
    else:
        assert {"neckline", "peak", "trough"} <= roles
    # Frozen geometry reproduces the engine's own lifecycle verdict.
    life = track_detection(d, ctx)
    assert life.status == d.status


def test_reversal_no_future_leakage(calendar):
    df = series_for(P2_POSITIVE["double_top"], calendar)
    n = len(df)
    base = [(d.detection_id, d.status, d.quality) for d in run(df.iloc[: n - 2], calendar)]
    wild = df.copy()
    wild.iloc[n - 2 :, :4] *= 1.4
    again = [(d.detection_id, d.status, d.quality) for d in run(wild.iloc[: n - 2], calendar)]
    assert base == again


def test_all_seventeen_patterns_detectable(calendar):
    found = set()
    for slug, spec in POSITIVE.items():
        found |= {d.pattern for d in run(band_series(spec, calendar), calendar) if d.pattern == slug}
    for slug, spec in P2_POSITIVE.items():
        found |= {d.pattern for d in run(series_for(spec, calendar), calendar) if d.pattern == slug}
    from app.patterns.base import CATALOGUE

    assert found == {p.slug for p in CATALOGUE}
