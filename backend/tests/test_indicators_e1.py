"""E1 indicators: EMA, RSI, ATR, Pine-style helpers, EMA alignment, RSI divergence.

Reference data: StockCharts ChartSchool worked examples (RSI: 14-period on the
33-close sample; EMA: 10-period on the 30-close sample). Values are the
published rounded results."""

import math

import numpy as np
import pandas as pd
import pytest

from app.config import DEFAULT_CONFIG, DEFAULT_TECH_CONFIG as T, TechnicalConfig
from app.indicators.core import atr_wilder
from app.indicators.divergence import current_divergences, find_divergences
from app.indicators.technical import crossover, crossunder, ema, ema_alignment, ema_pandas, falling, rising, rma, rsi, sma
from tests.fixtures_e1 import bearish_divergence_closes, bullish_divergence_closes, closes_frame

RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
RSI_REF = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]
EMA_CLOSES = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39, 22.38, 22.61, 23.36, 24.05, 23.75, 23.83, 23.95, 23.63, 23.82, 23.87, 23.65, 23.19, 23.10, 23.33, 22.68, 23.10, 22.40, 22.17]
EMA_REF = [22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34, 23.43, 23.51, 23.54, 23.47, 23.40, 23.39, 23.26, 23.23, 23.08, 22.92]


# ---------------------------------------------------------------- EMA
def test_ema_matches_reference_data():
    e = ema(EMA_CLOSES, 10)
    assert np.isnan(e[:9]).all()  # warm-up: no value before n bars
    assert e[9] == pytest.approx(np.mean(EMA_CLOSES[:10]))  # seed = SMA of the first n
    assert np.max(np.abs(np.round(e[9:], 2) - np.array(EMA_REF))) <= 0.0101


def test_ema_small_exact_example_and_seed_variants():
    e = ema([1, 2, 3, 4, 5], 3)
    assert np.isnan(e[:2]).all() and e[2:].tolist() == [2.0, 3.0, 4.0]
    p = ema_pandas([1, 2, 3, 4, 5], 3)  # pandas ewm(span=3, adjust=False): first value = first input
    assert p.tolist() == pd.Series([1, 2, 3, 4, 5], dtype=float).ewm(span=3, adjust=False).mean().tolist()
    assert ema([1, 2, 3, 4, 5], 3, seed="first").tolist() == p.tolist()


def test_ema_missing_value_propagates_and_restarts():
    x = [1, 2, 3, np.nan, 5, 6, 7, 8]
    e = ema(x, 3)
    assert np.isnan(e[3])  # never filled with zero or carried forward
    assert np.isnan(e[4]) and np.isnan(e[5]) and e[6] == pytest.approx(6.0)  # restarts from SMA of 3 valid values


# ---------------------------------------------------------------- RSI
def test_rsi_matches_reference_data():
    r = rsi(RSI_CLOSES, 14)
    assert np.isnan(r[:14]).all() and not np.isnan(r[14])  # first value after n changes
    assert np.max(np.abs(np.round(r[14:], 2) - np.array(RSI_REF))) < 0.006


def test_rsi_flat_rising_falling_and_warmup():
    flat = rsi([100.0] * 30, 14)
    assert np.isnan(flat[:14]).all() and (flat[14:] == 50.0).all()  # undefined -> neutral 50 (documented)
    up = rsi(np.arange(30, dtype=float), 14)
    assert (up[14:] == 100.0).all()
    down = rsi(np.arange(30, 0, -1, dtype=float), 14)
    assert (down[14:] == 0.0).all()
    assert np.isnan(rsi([1.0] * 10, 14)).all()  # insufficient history: no value at all


def test_rma_is_wilder_smoothing():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = rma(x, 3)
    assert r[2] == pytest.approx(2.0) and r[3] == pytest.approx((2.0 * 2 + 4) / 3) and r[4] == pytest.approx((r[3] * 2 + 5) / 3)


# ---------------------------------------------------------------- ATR
def test_atr_hand_computed_reference():
    h = [10, 11, 12, 11, 13]
    l = [9, 10, 10, 10, 11]
    c = [9.5, 10.5, 11.5, 10.5, 12.5]
    a = atr_wilder(h, l, c, 3)
    # TR = 1, 1.5, 2, 1.5, 2.5; ATR[2] = mean(1, 1.5, 2); then Wilder recursion
    assert np.isnan(a[:2]).all()
    assert a[2] == pytest.approx(1.5) and a[3] == pytest.approx(1.5) and a[4] == pytest.approx((1.5 * 2 + 2.5) / 3)


def test_single_atr_definition_shared_with_pattern_detection():
    assert T.atr_period == DEFAULT_CONFIG.atr_period


# ---------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("fn", ["ema", "rsi", "atr"])
def test_values_never_depend_on_future_bars(fn, calendar):
    f = closes_frame(bullish_divergence_closes(), calendar)
    h, l, c = (f[k].to_numpy() for k in ("high", "low", "close"))

    def calc(m):
        if fn == "ema":
            return ema(c[:m], 20)
        if fn == "rsi":
            return rsi(c[:m], 14)
        return atr_wilder(h[:m], l[:m], c[:m], 14)

    full = calc(len(c))
    for m in (30, 55, 80):
        np.testing.assert_allclose(calc(m), full[:m], equal_nan=True)


# ---------------------------------------------------------------- Pine helpers
def test_pine_rising_falling_crossover_semantics():
    x = np.array([1, 2, 3, 3, 4, 5, np.nan, 6], dtype=float)
    assert rising(x, 2).tolist() == [False, False, True, False, True, True, False, False]
    assert falling(np.array([5, 4, 3, 3, 2], dtype=float), 2).tolist() == [False, False, True, False, True]
    a = np.array([1, 2, 3, 2, 1], dtype=float)
    b = np.array([2, 2, 2, 2, 2], dtype=float)
    assert crossover(a, b).tolist() == [False, False, True, False, False]  # a[i-1] <= b[i-1] and a[i] > b[i]
    assert crossunder(a, b).tolist() == [False, False, False, False, True]
    s = sma([1, np.nan, 3, 4], 2)
    assert np.isnan(s[:3]).all() and s[3] == 3.5  # a missing value in the window -> no value


# ---------------------------------------------------------------- alignment
def test_ema_alignment_states():
    per, part = T.ema_periods, T.ema_partial_alignment
    bull = {10: 110, 20: 108, 30: 106, 50: 104, 100: 102, 200: 100}
    assert ema_alignment(bull, per, part).state == "bullish_full"
    assert ema_alignment({p: -v for p, v in bull.items()}, per, part).state == "bearish_full"
    partial = {**bull, 10: 101}  # 10 below 20: not full, but 20 > 50 > 200
    assert ema_alignment(partial, per, part).state == "bullish_partial"
    assert ema_alignment({**bull, 50: 120}, per, part).state == "mixed"
    young = {**bull, 200: None}
    al = ema_alignment(young, per, part)
    assert al.state == "not_available" and al.missing == (200,)  # never guessed from fewer EMAs


# ---------------------------------------------------------------- divergence
def _div(frame, cfg=T):
    h, l, c = (frame[k].to_numpy() for k in ("high", "low", "close"))
    return find_divergences(h, l, c, rsi(c, cfg.rsi_period), atr_wilder(h, l, c, cfg.atr_period), cfg), len(c)


def test_bullish_regular_divergence(calendar):
    divs, n = _div(closes_frame(bullish_divergence_closes(), calendar))
    bull = [d for d in divs if d.direction == "bullish"]
    assert len(bull) == 1
    d = bull[0]
    assert d.status == "confirmed" and d.p2.price < d.p1.price and d.p2.rsi > d.p1.rsi
    assert d.confirmed_index == d.p2.i + T.div_swing_k  # confirmed only after k later bars
    assert d.price_diff_atr >= T.div_min_price_diff_atr and d.rsi_diff >= T.div_min_rsi_diff
    assert all(g["passed"] for g in d.gates)
    assert not [x for x in divs if x.direction == "bearish"]


def test_bearish_regular_divergence(calendar):
    divs, _ = _div(closes_frame(bearish_divergence_closes(), calendar))
    bear = [d for d in divs if d.direction == "bearish"]
    assert len(bear) == 1 and bear[0].status == "confirmed"
    assert bear[0].p2.price > bear[0].p1.price and bear[0].p2.rsi < bear[0].p1.rsi


def test_unconfirmed_pivot_is_developing_never_confirmed(calendar):
    f = closes_frame(bullish_divergence_closes(), calendar)
    full, _ = _div(f)
    p2 = [d for d in full if d.direction == "bullish"][0].p2.i
    k = T.div_swing_k
    for cut in range(p2 + T.div_provisional_min_confirm_bars + 1, p2 + k + 1):  # 1..k-1 later bars
        divs, n = _div(f.iloc[:cut])
        bull = [d for d in divs if d.direction == "bullish"]
        assert bull and all(d.status != "confirmed" for d in bull), f"confirmed early at cut {cut}"
        assert any(d.status == "developing" and d.p2.i == p2 and d.confirmed_index is None for d in bull)
    divs, _ = _div(f.iloc[: p2 + k + 1])  # the confirming bar exists now
    assert any(d.status == "confirmed" and d.p2.i == p2 for d in divs)


def test_divergence_invalidated_by_close_beyond_second_pivot(calendar):
    c = bullish_divergence_closes()
    f = closes_frame(c, calendar)
    p2 = [d for d in _div(f)[0] if d.direction == "bullish"][0].p2.i
    crash = np.concatenate([c[: p2 + 7], np.linspace(c[p2 + 6], c[p2] - 40, 6)])
    divs, _ = _div(closes_frame(crash, calendar))
    d = [x for x in divs if x.direction == "bullish" and x.p2.i == p2][0]
    assert d.status == "invalidated" and d.invalidated_index > d.p2.i


def test_divergence_thresholds_are_respected(calendar):
    f = closes_frame(bullish_divergence_closes(), calendar)
    strict = TechnicalConfig(div_min_rsi_diff=50.0)
    assert not [d for d in _div(f, strict)[0] if d.direction == "bullish"]
    far = TechnicalConfig(div_min_price_diff_atr=5.0)
    assert not [d for d in _div(f, far)[0] if d.direction == "bullish"]
    close_pivots = TechnicalConfig(div_max_pivot_separation=10)
    assert not [d for d in _div(f, close_pivots)[0] if d.direction == "bullish"]


def test_current_divergences_age_filter(calendar):
    divs, n = _div(closes_frame(bullish_divergence_closes(), calendar))
    live = current_divergences(divs, n - 1, T.div_scan_max_age_bars)
    assert any(d.direction == "bullish" for d in live)
    assert not current_divergences(divs, n - 1 + 100, T.div_scan_max_age_bars)
