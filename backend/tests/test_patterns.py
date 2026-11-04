"""Synthetic detector tests (P1 patterns) and shared-engine geometry tests.

Synthetic examples verify the formulas; they say nothing about real-chart
accuracy (that needs human-labelled data, P3).
"""

from dataclasses import replace

import numpy as np
import pytest

from app.config import DEFAULT_CONFIG
from app.models import Geometry
from app.patterns.base import Line, QualityComponent, SeriesContext, clamp01, quality_score
from app.patterns.engine import (
    deduplicate,
    detect_series,
    diagnose_series,
    track_detection,
)
from app.patterns.shared import anchor_intercept, find_touches, iou, pooled_slope
from app.synthetic import (
    EXPANDING,
    FALLING_CHANNEL_BREAK_ABOVE,
    INVALIDATION,
    NEAR_MISS,
    NEGATIVE,
    POSITIVE,
    BandSpec,
    arch_path,
    band_series,
    path_series,
    pattern_window,
    random_walk,
)
from app.timeutil import epoch_s


P1_PATTERNS = tuple(POSITIVE)  # the nine P1 line patterns (P2 fixtures: test_patterns_p2.py)


def ctx_of(df, tf="1d", cfg=DEFAULT_CONFIG):
    return SeriesContext("SYN", tf, df, cfg)


def run(df, cal, **kw):
    return detect_series(ctx_of(df), cal, **kw)


# ---------------------------------------------------- four cases per pattern


@pytest.mark.parametrize("slug", P1_PATTERNS)
def test_positive_example_detected(slug, calendar):
    spec = POSITIVE[slug]
    dets = [d for d in run(band_series(spec, calendar), calendar) if d.pattern == slug]
    assert dets, f"{slug} not detected"
    d = dets[0]
    assert d.status in ("validated", "near_upper", "near_lower")
    assert d.formation_end_index == pattern_window(spec)[1]
    assert 0 <= d.quality <= 100 and d.quality >= 70


@pytest.mark.parametrize("slug", P1_PATTERNS)
def test_positive_example_with_noise_detected(slug, calendar):
    spec = BandSpec(**{**POSITIVE[slug].__dict__, "noise_frac": 0.015, "seed": 21})
    assert any(d.pattern == slug and not d.developing for d in run(band_series(spec, calendar), calendar))


@pytest.mark.parametrize("slug", P1_PATTERNS)
def test_negative_example_not_reported(slug, calendar):
    _, spec = NEGATIVE[slug]
    assert all(d.pattern != slug for d in run(band_series(spec, calendar), calendar))


@pytest.mark.parametrize("slug", P1_PATTERNS)
def test_near_miss_rejected_for_the_right_reason(slug, calendar):
    _, spec, expected = NEAR_MISS[slug]
    ctx = ctx_of(band_series(spec, calendar))
    reported = [d for d in detect_series(ctx, calendar) if d.pattern == slug]
    assert not any(d.status != "developing" for d in reported), "near miss must never be Validated"
    if expected == "developing":
        assert reported and all(d.status == "developing" for d in reported)
        assert not [d for d in detect_series(ctx, calendar, include_developing=False) if d.pattern == slug]
        return
    s, e = pattern_window(spec)
    diag = [c for c in diagnose_series(ctx) if (c.start, c.end, c.pattern) == (s, e, slug)]
    assert diag, "the full pattern window should be classified (and rejected)"
    assert expected in [g.name for g in diag[0].gates if not g.passed]


@pytest.mark.parametrize("slug", P1_PATTERNS)
def test_invalidated_example_with_frozen_geometry(slug, calendar):
    _, spec, reason = INVALIDATION[slug]
    df = band_series(spec, calendar)
    _, e = pattern_window(spec)
    cut = e + 1 + DEFAULT_CONFIG.fit_exclude_last_bars
    first = [d for d in run(df.iloc[:cut], calendar) if d.pattern == slug and not d.developing]
    assert first
    life = track_detection(first[0], ctx_of(df))
    assert (life.status, life.reason) == ("invalidated", reason)


# ------------------------------------------------- Falling Channel regressions


def _fc(df, cal):
    return [d for d in run(df, cal) if d.pattern == "falling_channel"]


def test_fc_clean_detected(calendar):
    assert _fc(band_series(POSITIVE["falling_channel"], calendar), calendar)


def test_fc_last_two_closes_above_upper_is_broke_above(calendar):
    d = _fc(band_series(FALLING_CHANNEL_BREAK_ABOVE, calendar), calendar)
    assert d and d[0].status == "broke_above"
    assert any(ev["event"] == "broke_above" for ev in d[0].events)


@pytest.mark.parametrize("other", ["falling_wedge", "rectangle", "rising_channel"])
def test_fc_rejects_other_structures(other, calendar):
    assert not _fc(band_series(POSITIVE[other], calendar), calendar)


def test_fc_rejects_arch(calendar):
    assert not _fc(path_series(arch_path(), calendar, wick=4.0, lead=80), calendar)


def test_expanding_formation_is_never_converging(calendar):
    assert not [d for d in run(band_series(EXPANDING, calendar), calendar) if d.family == "converging"]


# ------------------------------------------------------------- shared engine


def test_touch_uses_absolute_distance(calendar):
    df = band_series(POSITIVE["rectangle"], calendar)
    df.iloc[70, df.columns.get_loc("high")] = 1140.0  # far above the 1080 line
    ctx = ctx_of(df)
    up, lo = Line(0, 1080.0, 0.0), Line(0, 1000.0, 0.0)
    assert find_touches(ctx, up, lo, np.array([70]), np.array([], dtype=int)) == []
    df.iloc[70, df.columns.get_loc("high")] = 1078.0
    touches = find_touches(ctx_of(df), up, lo, np.array([70]), np.array([], dtype=int))
    assert len(touches) == 1 and touches[0].residual == pytest.approx(2.0)


def test_pooled_slope_and_anchoring():
    xu, xl = np.array([0.0, 10, 20]), np.array([5.0, 15, 25])
    yu, yl = 100 - 0.5 * xu, 90 - 0.5 * xl
    assert pooled_slope(xu, yu, xl, yl) == pytest.approx(-0.5)
    y = np.array([10.0, 12.5, 11.0])
    x = np.array([0.0, 1, 2])
    a = anchor_intercept(x, y, 1.0, "upper")
    assert np.all(y <= a + 1.0 * x + 1e-12) and np.isclose((y - (a + x)).max(), 0)


def test_break_buffer_is_required(calendar):
    from app.lifecycle import BoundaryModel, evaluate_lifecycle

    close = np.array([50.0] * 5 + [100.05, 100.05])
    atr = np.full(7, 2.0)  # buffer = 0.2
    model = BoundaryModel(Line(0, 100.0, 0.0), Line(0, 0.0, 0.0))
    assert evaluate_lifecycle(model, close, atr, 4, 6, DEFAULT_CONFIG).status == "near_upper"
    close[5:] = 100.25
    assert evaluate_lifecycle(model, close, atr, 4, 6, DEFAULT_CONFIG).status == "broke_above"


def _cand(pattern, s, e, up, lo, q, family="parallel", developing=False):
    from app.patterns.base import Candidate

    return Candidate(
        pattern=pattern, family=family, start=s, end=e, upper=Line(s, up[0], up[1]), lower=Line(s, lo[0], lo[1]),
        touches=[], swing_highs=[], swing_lows=[], fit_highs=[], fit_lows=[], metrics={}, gates=[], components=[],
        quality=q, developing=developing,
    )


def test_dedup_iou_keeps_higher_quality_then_longer():
    a = _cand("rectangle", 60, 149, (1080, 0), (1000, 0), 80)  # L=90
    b = _cand("rectangle", 90, 149, (1100, 0), (990, 0), 85)  # L=60, IoU 0.67, different lines
    assert deduplicate([a, b], DEFAULT_CONFIG) == [b]
    b.quality = 80
    assert deduplicate([a, b], DEFAULT_CONFIG) == [a]  # tie -> longer
    assert iou((60, 149), (90, 149)) == pytest.approx(60 / 90)


def test_dedup_merges_nested_same_geometry_even_across_subtypes():
    long = _cand("horizontal_channel", 30, 149, (1050, 0.3), (970, 0.3), 90)
    short = _cand("rectangle", 110, 149, (1050 + 0.3 * 80, 0.3), (970 + 0.3 * 80, 0.3), 91)  # same lines, IoU 0.33
    assert deduplicate([long, short], DEFAULT_CONFIG) == [short]


def test_dedup_preserves_genuinely_different_nested_patterns():
    long = _cand("falling_channel", 30, 149, (1200, -1.5), (1100, -1.5), 90)  # L=120
    nested = _cand("rectangle", 110, 149, (1040, 0.0), (1020, 0.0), 75)  # L=40 tight range, different lines
    kept = deduplicate([long, nested], DEFAULT_CONFIG)
    assert set(c.pattern for c in kept) == {"falling_channel", "rectangle"}


def test_dedup_families_coexist_and_developing_never_displaces_valid():
    chan = _cand("falling_channel", 60, 149, (1100, -1.2), (1020, -1.2), 70)
    tri = _cand("symmetrical_triangle", 60, 149, (1100, -1.0), (980, 1.0), 60, family="converging")
    dev = _cand("rectangle", 70, 149, (1100, 0.0), (1000, 0.0), 99, developing=True)
    kept = deduplicate([chan, tri, dev], DEFAULT_CONFIG)
    assert chan in kept and tri in kept and dev not in kept


def test_same_window_never_in_both_families(calendar):
    for spec in list(POSITIVE.values()) + [EXPANDING]:
        dets = run(band_series(spec, calendar), calendar)
        windows = {}
        for d in dets:
            windows.setdefault((d.formation_start_index, d.formation_end_index), set()).add(d.family)
        assert all(len(f) == 1 for f in windows.values())


def test_quality_components_are_clamped(calendar):
    comps = [QualityComponent("a", 50, 3.0, clamp01(3.0), "x"), QualityComponent("b", 50, -2.0, clamp01(-2.0), "y")]
    assert [c.score for c in comps] == [1.0, 0.0]
    assert quality_score(comps) == 50.0
    assert clamp01(float("nan")) == 0.0
    for spec in POSITIVE.values():
        for d in run(band_series(spec, calendar), calendar):
            assert all(0.0 <= c["score"] <= 1.0 for c in d.quality_components)
            assert 0 <= d.quality <= 100


def test_geometry_coordinates_match_candles(calendar):
    for slug in ("falling_channel", "symmetrical_triangle"):
        df = band_series(POSITIVE[slug], calendar)
        ctx = ctx_of(df)
        d = [x for x in detect_series(ctx, calendar) if x.pattern == slug][0]
        geo = Geometry.model_validate(d.geometry)  # versioned contract
        assert geo.schema_version == "1.1"
        for el in geo.elements:
            pts = el.points if el.type == "line" else (el.upper + el.lower if el.type == "band" else [el])
            for p in pts:
                if p.projected:
                    assert p.i > ctx.last and p.t > epoch_s(ctx.times[-1])
                else:
                    assert p.t == epoch_s(ctx.times[p.i]), "point timestamp must be the candle at its index"
        lines = {e.id: e for e in geo.elements if e.type == "line"}
        up = lines["upper"].points
        slope = (up[1].p - up[0].p) / (up[1].i - up[0].i)
        for m in (e for e in geo.elements if e.type == "marker" and e.role == "touch_upper"):
            assert m.p == pytest.approx(ctx.high[m.i], abs=1e-3)
            assert abs(up[0].p + slope * (m.i - up[0].i) - m.p) <= m.tolerance + 1e-6
        for m in (e for e in geo.elements if e.type == "marker" and e.role == "touch_lower"):
            assert m.p == pytest.approx(ctx.low[m.i], abs=1e-3)


def test_converging_projection_uses_trading_calendar(calendar):
    df = band_series(POSITIVE["ascending_triangle"], calendar)
    d = [x for x in run(df, calendar) if x.pattern == "ascending_triangle"][0]
    proj = [p for e in d.geometry["elements"] if e["type"] == "line" for p in e["points"] if p["projected"]]
    assert proj, "apex projection expected"
    import pandas as pd

    for p in proj:
        day = pd.Timestamp(p["t"], unit="s", tz="UTC").tz_convert("Asia/Kolkata").date()
        assert calendar.is_trading_day(day)


def test_no_future_leakage(calendar):
    df = band_series(FALLING_CHANNEL_BREAK_ABOVE, calendar)
    n = len(df)
    base = run(df.iloc[: n - 2], calendar)
    wild = df.copy()
    wild.iloc[n - 2 :, :4] *= 1.5
    again = run(wild.iloc[: n - 2], calendar)
    assert [(d.detection_id, d.status, d.quality) for d in base] == [(d.detection_id, d.status, d.quality) for d in again]


def test_detection_ids_are_deterministic_and_config_bound(calendar):
    df = band_series(POSITIVE["falling_channel"], calendar)
    a = run(df, calendar)[0]
    b = run(df, calendar)[0]
    assert a.detection_id == b.detection_id
    c = detect_series(ctx_of(df, cfg=replace(DEFAULT_CONFIG, touch_tol_atr_frac=0.26)), calendar)[0]
    assert c.config_hash != a.config_hash and c.detection_id != a.detection_id


def test_explanation_lists_every_gate(calendar):
    d = run(band_series(POSITIVE["rectangle"], calendar), calendar)[0]
    for g in d.gates:
        assert any(g["name"] in line for line in d.explanation)
    assert any("not a probability" in line for line in d.explanation)


def test_random_walk_detections_are_structurally_valid(calendar):
    # No assertion on count (random data can form patterns) - every reported
    # detection must still satisfy all of its gates.
    for seed in (1, 2, 3):
        for d in run(random_walk(300, calendar, seed=seed), calendar, include_developing=False):
            assert all(g["passed"] for g in d.gates), d.pattern
