import numpy as np

from app.indicators import atr_wilder, swing_points


def _bars(n=60, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    h = c + rng.uniform(0.2, 1.5, n)
    l = c - rng.uniform(0.2, 1.5, n)
    return h, l, c


def test_atr_wilder_hand_calculation():
    h = np.array([10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20.0])
    l = h - 2
    c = h - 1
    atr = atr_wilder(h, l, c, 14)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(h))]
    assert np.isnan(atr[:13]).all()
    assert atr[13] == np.mean(tr[:14])
    assert np.isclose(atr[14], (atr[13] * 13 + tr[14]) / 14)


def test_atr_is_causal():
    h, l, c = _bars()
    full = atr_wilder(h, l, c)
    for n in (20, 35, 59):
        np.testing.assert_allclose(atr_wilder(h[:n], l[:n], c[:n]), full[:n], equal_nan=True)


def test_swing_definition_and_confirmation():
    high = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 9, 9, 2, 1, 1, 8.0])
    sp = swing_points(high, high - 1, 3)
    # Plateau: the first bar qualifies (> previous k, >= next k); the second does not.
    assert list(sp.highs) == [3, 9]
    # The last k bars can never be swing points (not yet confirmed).
    assert all(i <= len(high) - 1 - 3 for i in sp.highs)


def test_swing_low_mirror():
    low = -np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 9, 9, 2, 1, 1, 8.0])
    sp = swing_points(low + 1, low, 3)
    assert list(sp.lows) == [3, 9]


def test_confirmed_swings_do_not_change_with_more_data():
    h, l, _ = _bars(120)
    k = 3
    full = swing_points(h, l, k)
    for n in (40, 80, 110):
        part = swing_points(h[:n], l[:n], k)
        assert set(part.highs) == {i for i in full.highs if i <= n - 1 - k}
        assert set(part.lows) == {i for i in full.lows if i <= n - 1 - k}
