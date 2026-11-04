import random
from dataclasses import replace

import numpy as np
import pandas as pd

from app.analytics.sr import Pivot, SRZone, _walk, build_zones, cluster_pivots, compute_zones, nearest
from app.config import DEFAULT_CONFIG as CFG
from app.patterns.base import SeriesContext
from app.synthetic import trading_day_index
from app.synthetic_p2 import sr_series


def test_clustering_is_order_independent():
    pts = [Pivot(100 + 0.1 * (i % 7), i * 6, "H" if i % 2 else "L", 2.0) for i in range(20)] + [Pivot(130.0, 7, "H", 2.0)]
    ref = [[(p.price, p.index) for p in c] for c in cluster_pivots(pts, 0.5)]
    for seed in range(5):
        shuffled = pts[:]
        random.Random(seed).shuffle(shuffled)
        assert [[(p.price, p.index) for p in c] for c in cluster_pivots(shuffled, 0.5)] == ref


def test_cluster_join_uses_each_pivots_own_atr():
    a, b = Pivot(100.0, 0, "H", 2.0), Pivot(100.9, 10, "H", 2.0)  # 0.9 <= 0.5 x 2.0
    c = Pivot(102.5, 20, "H", 2.0)  # 2.05 from mean 100.45 > 1.0
    clusters = cluster_pivots([a, b, c], 0.5)
    assert [[p.index for p in cl] for cl in clusters] == [[0, 10], [20]]


def test_zone_needs_two_spaced_touches_and_padding():
    close_pair = [Pivot(100.0, 10, "H", 2.0), Pivot(100.2, 13, "L", 2.0)]  # only 3 bars apart
    assert build_zones(close_pair, CFG) == []
    spaced = [Pivot(100.0, 10, "H", 2.0), Pivot(100.2, 15, "L", 2.0)]
    (z,) = build_zones(spaced, CFG)
    assert z.touches == [10, 15]
    assert np.isclose(z.low, 100.0 - 0.1 * 2.0) and np.isclose(z.high, 100.2 + 0.1 * 2.0)


def test_close_zones_merge_distant_ones_do_not():
    base = [Pivot(99.2, 0, "H", 2.0), Pivot(100.1, 10, "H", 2.0)]  # cluster mean 99.65
    near = [Pivot(100.7, 20, "L", 2.0), Pivot(100.75, 30, "L", 2.0)]  # 1.05 > 1.0 from the mean -> own cluster
    # padded zones: [99.0, 100.3] and [100.5, 100.95] -> 0.2 apart < 0.25 x ATR = 0.5 -> merged
    far = [Pivot(103.0, 40, "L", 2.0), Pivot(103.1, 50, "L", 2.0)]
    zones = build_zones(base + near + far, CFG)
    assert len(zones) == 2
    assert zones[0].touches == [0, 10, 20, 30]


def _ctx(closes, highs=None, lows=None):
    closes = np.asarray(closes, dtype=float)
    highs = closes + 0.5 if highs is None else np.asarray(highs, dtype=float)
    lows = closes - 0.5 if lows is None else np.asarray(lows, dtype=float)
    from app.data.calendar import NSECalendar
    from tests.conftest import HOLIDAYS_SRC

    idx = trading_day_index(len(closes), NSECalendar.load(HOLIDAYS_SRC))
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1.0}, index=idx)
    return SeriesContext("X", "1d", df, CFG)


def _zone(low, high, est):
    return SRZone("z", low, high, [], [0, est], est)


def test_break_flips_role_and_retest_requires_close_on_reclaimed_side():
    # Resistance 99-101; bars 0-19 below; two closes above 101 + buffer; then
    # an intrabar dip into the zone closing below the midpoint (not a retest),
    # then a dip closing above the midpoint (retest).
    closes = [98.0] * 20 + [102.0, 102.5, 101.5, 101.8]
    lows = [c - 0.5 for c in closes]
    lows[22] = 99.5  # touches the zone ...
    closes[22] = 99.8  # ... but closes below the midpoint 100 -> not a retest
    lows[23] = 100.2
    ctx = _ctx(closes, lows=lows)
    z = _zone(99.0, 101.0, 15)
    _walk(z, ctx)
    assert z.role == "support" and z.status == "retested"
    assert z.broken_index == 21 and z.retest_index == 23
    assert [e["event"] for e in z.events] == ["resistance_broken", "retest"]


def test_single_close_beyond_is_not_a_break():
    ctx = _ctx([98.0] * 20 + [102.0, 100.5, 100.4])
    z = _zone(99.0, 101.0, 15)
    _walk(z, ctx)
    assert z.status == "intact" and z.role == "resistance"


def test_compute_zones_on_fixture(calendar):
    ctx = SeriesContext("S", "1d", sr_series(calendar), CFG)
    zones = compute_zones(ctx)
    lower = min(zones, key=lambda z: z.low)
    upper = max(zones, key=lambda z: z.low)
    assert lower.role == "support" and lower.status == "intact" and len(lower.touches) >= 4
    assert upper.role == "support" and upper.status == "retested"  # broken resistance became support
    assert all(z.side == "below" and z.distance_pct > 0 for z in zones)
    assert len({z.zone_id for z in zones}) == len(zones)


def test_nearest_limits_per_side():
    zones = []
    for k in range(6):
        z = _zone(100 + k, 100.5 + k, 0)
        z.side, z.distance_pct = "above", float(k)
        zones.append(z)
        w = _zone(90 - k, 90.5 - k, 0)
        w.side, w.distance_pct = "below", float(k)
        zones.append(w)
    picked = nearest(zones, 4)
    assert sum(z.side == "above" for z in picked) == 4 and sum(z.side == "below" for z in picked) == 4
    assert max(z.distance_pct for z in picked) == 3.0


def test_lookback_limits_pivots(calendar):
    ctx = SeriesContext("S", "1d", sr_series(calendar), replace(CFG, sr_lookback_bars=40))
    assert all(min(z.touches) >= ctx.n - 40 for z in compute_zones(ctx))


def test_merging_does_not_chain_a_congested_area_into_one_band():
    # Six clusters (span 0.9, starting every 1.5 on ATR 2): each splits from the
    # previous one (1.05 > 0.5 ATR from its mean) yet the padded zones are only
    # 0.2 apart (< 0.25 ATR), so every neighbour pair qualifies for merging.
    pts = []
    for k in range(6):
        level = 99.2 + 1.5 * k
        pts += [Pivot(level, 10 * k, "H", 2.0), Pivot(level + 0.9, 10 * k + 5, "L", 2.0)]
    zones = build_zones(pts, CFG)
    assert len(zones) > 1
    assert all(z.width <= CFG.sr_max_zone_width_atr * z.median_atr + 1e-9 for z in zones)
    unlimited = build_zones(pts, replace(CFG, sr_max_zone_width_atr=1e9))
    assert len(unlimited) == 1  # the literal rule alone would chain everything
