"""E3: RS Line (rs_line.txt) and Stage Analysis (stage_analysis.txt) translation tests.

1. A bar-by-bar emulation of the Pine script (Pine series semantics: history
   operator, na propagation, ta.sma, ta.rising/ta.falling) is compared with the
   vectorised Python implementation.
2. The literal pandas formulas written in stage_analysis.txt are compared with
   the engine.
3. Deterministic weekly scenarios exercise every stage rule, its boundaries, the
   current-candle exclusion, missing data, insufficient history and as-of
   correctness. Synthetic data - not market data."""

import math
from datetime import datetime, time

import numpy as np
import pandas as pd
import pytest

from app.config import DEFAULT_STAGE_CONFIG as CFG, StageConfig
from app.stage.analysis import compute_stage_series, evaluate, rank_key
from app.stage.rs_line import align_benchmark, compute_rs_line, rs_length_for
from app.timeutil import IST

NA = float("nan")


# ---------------------------------------------------------------- Pine emulation
class PineSeries:
    """Minimal Pine series: append one value per bar, x[n] = value n bars ago (na if unavailable)."""

    def __init__(self):
        self.v: list[float] = []

    def push(self, x: float) -> None:
        self.v.append(x)

    def __getitem__(self, n: int) -> float:
        i = len(self.v) - 1 - n
        return self.v[i] if i >= 0 else NA


def na(x: float) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def pine_sma(series: PineSeries, length: int) -> float:
    vals = [series[i] for i in range(length)]
    return NA if any(na(x) for x in vals) else sum(vals) / length


def pine_rising(series: PineSeries, length: int) -> bool:
    cur = series[0]
    prev = [series[i] for i in range(1, length + 1)]
    return not na(cur) and not any(na(p) for p in prev) and all(cur > p for p in prev)


def pine_falling(series: PineSeries, length: int) -> bool:
    cur = series[0]
    prev = [series[i] for i in range(1, length + 1)]
    return not na(cur) and not any(na(p) for p in prev) and all(cur < p for p in prev)


def run_pine_rs(close, comp, length, base=5, lengthRSMA=50, lengthPriceSMA=50):
    """rs_line.txt executed bar by bar (L29-L47)."""
    baseSymbol, comparativeSymbol, res, sma_res, sma_symb = (PineSeries() for _ in range(5))
    out = {k: [] for k in ("res", "angle0", "sma_res", "ma_rising", "ma_falling", "pos_div", "neg_div")}
    for c, b in zip(close, comp):
        baseSymbol.push(c)
        comparativeSymbol.push(b)
        try:
            r = baseSymbol[0] / baseSymbol[length] / (comparativeSymbol[0] / comparativeSymbol[length]) - 1  # L29
        except ZeroDivisionError:
            r = NA
        r = NA if na(r) else r
        res.push(r)
        y0 = res[0] - res[base]  # L36
        angle0 = math.atan(y0 / base) if not na(y0) else NA  # L37
        sma_res.push(pine_sma(res, lengthRSMA))  # L39
        sma_symb.push(pine_sma(baseSymbol, lengthPriceSMA))  # L41
        pos_div = pine_rising(sma_symb, 3) and not na(sma_symb[0]) and baseSymbol[0] >= sma_symb[0]  # L42
        neg_div = pine_falling(sma_symb, 3) and not na(sma_symb[0]) and baseSymbol[0] < sma_symb[0]  # L43
        out["res"].append(r)
        out["angle0"].append(angle0)
        out["sma_res"].append(sma_res[0])
        out["ma_rising"].append(pine_rising(sma_res, 3))  # L46
        out["ma_falling"].append(pine_falling(sma_res, 3))  # L47
        out["pos_div"].append(pos_div)
        out["neg_div"].append(neg_div)
    return {k: np.array(v, dtype=float if k in ("res", "angle0", "sma_res") else bool) for k, v in out.items()}


@pytest.mark.parametrize("length", [52, 63, 123])
def test_rs_line_matches_bar_by_bar_pine_emulation(length):
    rng = np.random.default_rng(length)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    comp = 1000 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    comp[200] = np.nan  # a missing benchmark value propagates as na
    pine = run_pine_rs(close, comp, length)
    py = compute_rs_line(close, comp, length, CFG)
    np.testing.assert_allclose(py.res, pine["res"], rtol=1e-12, equal_nan=True)
    np.testing.assert_allclose(py.angle, pine["angle0"], rtol=1e-12, equal_nan=True)
    np.testing.assert_allclose(py.sma_res, pine["sma_res"], rtol=1e-10, equal_nan=True)
    for k_py, k_pine in (("ma_rising", "ma_rising"), ("ma_falling", "ma_falling"), ("pos_div", "pos_div"), ("neg_div", "neg_div")):
        assert np.array_equal(getattr(py, k_py), pine[k_pine]), k_py
    assert pine["ma_rising"].any() and pine["ma_falling"].any() and pine["pos_div"].any() and pine["neg_div"].any()  # all branches exercised


def test_rs_formula_is_the_literal_stage_spec_formula():
    rng = np.random.default_rng(1)
    s = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 200))))
    nf = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, 200))))
    literal = ((s / s.shift(52)) / (nf / nf.shift(52))) - 1  # stage_analysis.txt s.4A
    py = compute_rs_line(s.to_numpy(), nf.to_numpy(), 52, CFG)
    np.testing.assert_allclose(py.res, literal.to_numpy(), rtol=1e-12, equal_nan=True)
    np.testing.assert_allclose(py.sma_res, pd.Series(py.res).rolling(50).mean().to_numpy(), rtol=1e-10, equal_nan=True)  # s.4C


def test_rs_lengths_per_timeframe_follow_the_setup():
    assert rs_length_for("1w", CFG) == 52 and rs_length_for("1d", CFG) == 63  # stage_analysis.txt s.4
    assert rs_length_for("1h", CFG) == rs_length_for("4h", CFG) == 123  # rs_line.txt input default
    assert CFG.benchmark == "NIFTY 50" and CFG.rs_trend_base == 5 and CFG.rs_ma_length == 50 and CFG.price_sma_length == 50


def test_benchmark_alignment_pine_gaps_off():
    idx = pd.date_range("2024-01-01 09:15", periods=10, freq="7D", tz=IST)
    bench = pd.Series(np.arange(10, dtype=float) + 100, index=idx)
    al = align_benchmark(idx, bench, 2)
    assert al.exact == 10 and al.carried == 0 and np.array_equal(al.values, bench.to_numpy())
    gap1 = bench.drop(idx[4])  # one missing benchmark bar: previous value carried (gaps_off)
    al = align_benchmark(idx, gap1, 2)
    assert al.values[4] == bench.iloc[3] and al.carried == 1
    gap3 = bench.drop(idx[4:7])  # 3 consecutive: beyond the cap the RS is missing, never invented
    al = align_benchmark(idx, gap3, 2)
    assert al.values[4] == al.values[5] == bench.iloc[3] and np.isnan(al.values[6]) and al.missing == 1
    extra = bench.copy()
    extra.loc[idx[0] + pd.Timedelta(days=3)] = 999.0  # benchmark bar the stock lacks: skipped (stock timeline)
    al = align_benchmark(idx, extra.sort_index(), 2)
    assert 999.0 not in al.values


# ---------------------------------------------------------------- weekly scenarios
def weekly(closes, volume=None, start="2023-01-02"):
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.date_range(start + " 09:15", periods=len(c), freq="7D", tz=IST)
    v = np.full(len(c), 1e5) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": v}, index=idx)


def flat_bench(n, start="2023-01-02", drift=0.0):
    idx = pd.date_range(start + " 09:15", periods=n, freq="7D", tz=IST)
    return pd.Series(1000.0 * np.exp(drift * np.arange(n)), index=idx)


def base_path(final, base_tail=None):
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 105, 21)[1:]
    base = 115 + 10 * np.sin(np.arange(60) / 3.0)  # oscillates 105..125
    if base_tail is not None:
        base[-len(base_tail):] = base_tail
    return np.concatenate([up, down, base, np.atleast_1d(final)])


def test_stage2_confirmed_breakout_with_volume_and_positive_rs():
    c = base_path(135.0)
    v = np.full(len(c), 1e5)
    v[-1] = 1e6
    r = evaluate(weekly(c, v), flat_bench(len(c)), CFG)
    m = r["metrics"]
    assert r["stage"] == "stage2_confirmed", r["reasons"]
    assert m["close"] > m["base_high"] and m["volume_expansion"] and m["rs"] > 0 and m["base_range_pct"] <= 30
    # current candle excluded from the previous base resistance (s.10 rule 6); 1.4.0: the base is the
    # variable-length stretch reported in base_start_week .. the previous week
    w = weekly(c, v)
    start = w.index.get_loc(pd.Timestamp(m["base_start_week"]))
    assert m["base_high"] == pytest.approx(float(w["high"].iloc[start:-1].max()), abs=0.01)  # metrics rounded to 2 dp
    assert m["base_duration_weeks"] == len(w) - 1 - start and m["base_duration_weeks"] >= 52
    fixed = evaluate(w, flat_bench(len(c)), StageConfig(base_mode="fixed"))["metrics"]
    assert fixed["base_high"] == pytest.approx(float(w["high"].iloc[-53:-1].max()), abs=0.01)  # s.2 literal mode kept
    assert r["stage_since"] == weekly(c, v).index[-1].isoformat() and r["previous_stage"] != "stage2_confirmed"


def test_breakout_without_volume_is_not_confirmed():
    c = base_path(135.0)
    r = evaluate(weekly(c), flat_bench(len(c)), CFG)
    assert r["stage"] != "stage2_confirmed"
    assert any(g["name"] == "volume_support" and g["passed"] is False for g in r["gates"])


def test_early_stage2_candidate():
    tail = np.linspace(106, 122, 14)  # rising inside the base: rising 30W EMA, improving RS
    c = base_path(123.0, tail)
    r = evaluate(weekly(c), flat_bench(len(c)), CFG)
    m = r["metrics"]
    assert r["stage"] == "early_stage2", r["reasons"]
    assert m["ema30_direction"] == "rising" and m["close"] > m["ema30"] and m["rs"] > 0 and m["rs_trend"] == "improving"
    assert 0 <= m["distance_to_breakout_pct"] <= 5


def test_stage1_watchlist_even_with_negative_rs():
    c = base_path(115.0)
    bench = flat_bench(len(c), drift=0.01)  # benchmark rising strongly: stock RS negative
    r = evaluate(weekly(c), bench, CFG)
    m = r["metrics"]
    assert r["stage"] == "stage1", r["reasons"]
    assert m["ema30_position"] == "testing" and m["rs"] < 0  # s.6: not rejected for negative RS


def test_wide_range_is_not_a_base():
    up = np.linspace(100, 200, 60)
    wide = 120 + 30 * np.sin(np.arange(80) / 3.0)  # 90..150: > 30 % range
    c = np.concatenate([up, wide])
    r = evaluate(weekly(c), flat_bench(len(c)), CFG)
    # 1.4.0 variable base: no stretch inside the 30 % band lasts the minimum 26 weeks
    assert r["stage"] == "other" and r["metrics"]["base_duration_weeks"] < CFG.min_base_weeks
    assert next(g for g in r["gates"] if g["name"] == "base_range")["passed"] is False
    fixed = evaluate(weekly(c), flat_bench(len(c)), StageConfig(base_mode="fixed"))
    assert fixed["stage"] == "other" and fixed["metrics"]["base_range_pct"] > 30


def test_boundaries_inclusive():
    c = base_path(115.0)
    fx = StageConfig(base_mode="fixed")  # s.2 literal window: the range threshold is the boundary
    ss = compute_stage_series(weekly(c), flat_bench(len(c)), fx)
    i = len(c) - 1
    exact = StageConfig(base_mode="fixed", max_base_range_pct=float(ss.base_range_pct[i]), ema_testing_band_pct=abs(float(ss.ema_dist_pct[i])))
    ss2 = compute_stage_series(weekly(c), flat_bench(len(c)), exact)
    assert ss2.conditions["range_ok"][i] and ss2.conditions["testing"][i]  # <= on the boundary
    tighter = StageConfig(base_mode="fixed", max_base_range_pct=float(ss.base_range_pct[i]) - 1e-6)
    assert not compute_stage_series(weekly(c), flat_bench(len(c)), tighter).conditions["range_ok"][i]
    # variable mode (1.4.0): the minimum base length is the boundary (>=)
    sv = compute_stage_series(weekly(c), flat_bench(len(c)), CFG)
    d = int(sv.base_weeks[i])
    assert compute_stage_series(weekly(c), flat_bench(len(c)), StageConfig(min_base_weeks=d)).conditions["range_ok"][i]
    assert not compute_stage_series(weekly(c), flat_bench(len(c)), StageConfig(min_base_weeks=d + 1)).conditions["range_ok"][i]


def test_prior_decline_assumption_is_flagged_and_configurable():
    c = base_path(115.0)
    r = evaluate(weekly(c), flat_bench(len(c)), CFG)
    g = next(x for x in r["gates"] if x["name"] == "prior_decline")
    assert g["basis"] == "assumption" and g["passed"] is True
    strict = evaluate(weekly(c), flat_bench(len(c)), StageConfig(prior_decline_min_pct=60.0))
    assert strict["stage"] == "other"


def test_insufficient_history():
    c = base_path(115.0)[-90:]
    r = evaluate(weekly(c), flat_bench(len(c)), CFG)
    assert r["stage"] == "insufficient_history" and "completed weeks" in r["reasons"][0]


def test_missing_benchmark_weeks_make_rs_missing_not_invented():
    c = base_path(115.0)
    bench = flat_bench(len(c))
    gappy = bench.drop(bench.index[-4:-1])  # last weeks missing beyond the carry cap
    r = evaluate(weekly(c), gappy, CFG)
    al = r["metrics"]["benchmark_alignment"]
    assert al["carried"] == 2 and al["missing"] == 1


def test_asof_history_never_changes_with_later_weeks():
    c = base_path(135.0)
    v = np.full(len(c), 1e5)
    v[-1] = 1e6
    full = compute_stage_series(weekly(c, v), flat_bench(len(c)), CFG)
    for m in (110, 125, len(c) - 1):
        part = compute_stage_series(weekly(c[:m], v[:m]), flat_bench(len(c))[:m], CFG)
        assert list(part.stage) == list(full.stage[:m])  # classification of every earlier week unchanged
        np.testing.assert_allclose(part.ema30, full.ema30[:m])


def test_incomplete_week_is_excluded(calendar):
    from app.data.ohlcv import prepare_series

    c = base_path(135.0)
    end = pd.Timestamp("2026-10-26 09:15", tz=IST)
    idx = pd.date_range(end=end, periods=len(c), freq="7D", tz=IST)
    w = weekly(c)
    w.index = idx
    mid = datetime(2026, 10, 28, 12, 0, tzinfo=IST)  # Wednesday of the last week
    ps = prepare_series(w, "SYN", "1w", mid, calendar)
    assert ps.frame.index[-1] < idx[-1] and ps.dropped_incomplete == 1


def test_ranking_order():
    a = rank_key("early_stage2", {"rs": 0.1, "rs_trend_angle": 0.01, "rs_sma_direction": "rising", "ema30_direction": "rising", "base_range_pct": 20, "volume_expansion_ratio": 1.2}, CFG)
    b = rank_key("early_stage2", {"rs": 0.2, "rs_trend_angle": 0.0, "rs_sma_direction": "falling", "ema30_direction": "rising", "base_range_pct": 25, "volume_expansion_ratio": 1.0}, CFG)
    c = rank_key("stage2_confirmed", {"rs": 0.0}, CFG)
    d = rank_key("other", {"rs": 0.9}, CFG)
    assert sorted([a, b, c, d]) == [b, a, c, d]  # stage first (1.1.0: Early Stage 2 > Stage 2 Confirmed), then higher RS


# ---------------------------------------------------------------- user entry setup (confluence)
def setup_scenario(final=123.0, decline_vol=3e5, base_vol=1e5, recent_vol=1.6e5):
    """Decline on heavy volume, quiet 60-week base, rising into the base high on rising volume."""
    tail = np.linspace(106, 122, 14)
    c = base_path(final, tail)
    v = np.full(len(c), base_vol)
    v[60:80] = decline_vol  # the decline phase
    v[:60] = decline_vol
    v[-4:] = recent_vol
    return weekly(c, v), flat_bench(len(c))


def test_entry_setup_all_checks_pass():
    w, b = setup_scenario()
    r = evaluate(w, b, CFG)
    m = r["metrics"]
    setup = [g for g in r["gates"] if g["stage"] == "setup"]
    assert m["setup_met"] is True and all(g["passed"] for g in setup), [(g["name"], g["value"]) for g in setup if not g["passed"]]
    assert {g["name"] for g in setup} == {"major_decline", "long_base", "price_above_ema", "rs_positive", "near_or_breaking_out", "volume_high_vs_base"}
    assert m["base_duration_weeks"] >= 52 and m["volume_pickup_ratio"] >= 1.5 and m["setup_checks"] == 6
    assert r["reasons"][0].startswith("ENTRY SETUP")


@pytest.mark.parametrize(
    "kwargs, failing",
    [
        ({"recent_vol": 1.3e5}, "volume_high_vs_base"),  # higher, but not very high (< 1.5x the sideways base)
        ({"recent_vol": 0.5e5}, "volume_high_vs_base"),  # no volume into the breakout
        ({"final": 100.0}, "price_above_ema"),  # below the 30W EMA
    ],
)
def test_entry_setup_fails_on_any_single_check(kwargs, failing):
    w, b = setup_scenario(**kwargs)
    m = evaluate(w, b, CFG)["metrics"]
    assert m["setup_met"] is False and failing in m["setup_failed"]


def test_entry_setup_needs_positive_rs():
    w, b = setup_scenario()
    strong_bench = flat_bench(len(w), drift=0.01)
    m = evaluate(w, strong_bench, CFG)["metrics"]
    assert m["setup_met"] is False and "rs_positive" in m["setup_failed"]


def test_true_base_length_is_not_capped_at_52_weeks():
    from app.stage.analysis import base_length

    h = np.r_[np.linspace(200, 120, 30), np.full(150, 110.0) + np.sin(np.arange(150)) * 5]
    l = h * 0.98
    assert base_length(h, l, len(h) - 1, 30.0) >= 140


def test_setups_rank_first_and_by_base_length():
    long = rank_key("other", {"setup_met": True, "base_duration_weeks": 120, "rs": 0.01}, CFG)
    short = rank_key("early_stage2", {"setup_met": True, "base_duration_weeks": 60, "rs": 0.5}, CFG)
    plain = rank_key("early_stage2", {"setup_met": False, "base_duration_weeks": 200, "rs": 0.9}, CFG)
    assert sorted([plain, short, long]) == [long, short, plain]


def test_decline_volume_is_ignored_and_base_volume_trend_reported():
    w, b = setup_scenario(decline_vol=0.5e5)  # quiet crash: no longer part of the setup
    m = evaluate(w, b, CFG)["metrics"]
    assert m["setup_met"] is True and m["base_volume_trend"] is not None



# ---------------------------------------------------------------- 1.4.0 variable-length base (user request 2026-11-15)
def test_variable_base_finds_a_base_younger_than_the_fixed_window():
    """A 35-week base right after a decline: the fixed 52-week window reaches back into the
    decline (range > 30 %); the variable base finds it when 26 weeks are enough (1.4.0), and
    1.5.0's 52-week minimum (user, 2026-11-16) rejects it as too short."""
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 105, 21)[1:]
    base = 115 + 8 * np.sin(np.arange(35) / 3.0)
    c = np.concatenate([up, down, base, [118.0]])
    w, b = weekly(c), flat_bench(len(c))
    fixed = evaluate(w, b, StageConfig(base_mode="fixed", max_base_range_pct=30.0))
    assert next(g for g in fixed["gates"] if g["name"] == "base_range")["passed"] is False
    var = evaluate(w, b, StageConfig(min_base_weeks=26, max_base_range_pct=30.0))
    g = next(g for g in var["gates"] if g["name"] == "base_range")
    assert g["passed"] is True and g["basis"] == "user"
    m = var["metrics"]
    assert 26 <= m["base_duration_weeks"] <= 40 and m["base_range_pct"] <= 30 and m["base_mode"] == "variable"
    assert m["prior_decline_pct"] >= CFG.prior_decline_min_pct  # measured before the base start
    now = evaluate(w, b, CFG)  # 1.5.0 default: a base needs 52 weeks
    assert next(g for g in now["gates"] if g["name"] == "base_range")["passed"] is False and now["stage"] == "other"


def test_variable_base_drops_the_entry_leg_of_the_decline():
    c = base_path(115.0)
    w = weekly(c)
    m = evaluate(w, flat_bench(len(c)), CFG)["metrics"]
    base_only = w["high"].iloc[80:-1].max()  # the oscillating range, not the decline weeks
    assert m["base_high"] == pytest.approx(float(base_only), abs=0.01)
    assert m["base_high"] < 130  # the decline weeks that still fit the 30 % band (up to ~135) are not the resistance


def test_variable_bases_match_a_brute_force_search():
    from app.stage.analysis import variable_bases

    rng = np.random.default_rng(7)
    h = 100 * np.exp(np.cumsum(rng.normal(0, 0.04, 400)))
    l = h * (1 - rng.uniform(0.0, 0.06, 400))
    bh, bl, st = variable_bases(h, l, 30.0, 520)
    for e in range(0, 400, 7):
        s = e
        while s - 1 >= 0 and (max(h[s - 1 : e + 1]) - min(l[s - 1 : e + 1])) / min(l[s - 1 : e + 1]) * 100 <= 30.0:
            s -= 1
        if (h[e] - l[e]) / l[e] * 100 > 30.0:
            assert st[e] == e + 1
            continue
        t = s
        while t < e and h[t] > max(h[t + 1 : e + 1]):
            t += 1
        assert st[e] == t and bh[e] == pytest.approx(max(h[t : e + 1])) and bl[e] == pytest.approx(min(l[t : e + 1]))


def test_variable_base_uses_no_later_weeks():
    c = base_path(135.0)
    v = np.full(len(c), 1e5)
    v[-1] = 1e6
    full = compute_stage_series(weekly(c, v), flat_bench(len(c)), CFG)
    for m in (100, 130, len(c) - 1):
        part = compute_stage_series(weekly(c[:m], v[:m]), flat_bench(len(c))[:m], CFG)
        np.testing.assert_array_equal(part.base_weeks, full.base_weeks[:m])
        np.testing.assert_allclose(part.base_high, full.base_high[:m], equal_nan=True)


def test_one_huge_week_means_no_base_not_missing_history():
    c = base_path(115.0)
    w = weekly(c)
    w.iloc[-2, w.columns.get_loc("high")] = w["low"].iloc[-2] * 1.5  # the previous week alone spans 50 %
    r = evaluate(w, flat_bench(len(c)), CFG)
    assert r["stage"] == "other" and r["metrics"]["base_duration_weeks"] == 0


# ---------------------------------------------------------------- 1.5.0 (user, 2026-11-16): 52-week base, 30-40 % band, targets
def test_1_5_0_defaults_are_the_users_choice():
    assert (CFG.min_base_weeks, CFG.max_base_range_pct, CFG.tight_base_range_pct) == (52, 40.0, 30.0)
    assert CFG.ema_period == 30 and CFG.rs_length_weekly == 52  # 30-week EMA and the RS Line are unchanged


def test_tight_and_wide_bases_and_targets():
    c = base_path(115.0)  # 60-week base oscillating 105..125 (~21 %): tight
    m = evaluate(weekly(c), flat_bench(len(c)), CFG)["metrics"]
    assert m["base_tightness"] == "tight" and m["base_duration_weeks"] >= 52 and m["tight_base_weeks"] >= 52  # also a 52-week base inside 30 %
    hi, lo, close = m["base_high"], m["base_low"], m["close"]
    assert m["target_base_height"] == pytest.approx(hi + (hi - lo), abs=0.02)
    assert m["target_base_height_pct"] == pytest.approx(((hi + (hi - lo)) / close - 1) * 100, abs=0.05)
    peak = m["swing_rule_peak"]
    assert peak is not None and peak > hi and m["target_swing_rule"] == pytest.approx(peak + (peak - lo), abs=0.02)
    # a 35 % wide base: inside the 40 % band, labelled wide
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 100, 21)[1:]
    wide = 112 + 15 * np.sin(np.arange(60) / 3.0)  # ~97..127 with wicks: ~33 %
    c2 = np.concatenate([up, down, wide, [115.0]])
    m2 = evaluate(weekly(c2), flat_bench(len(c2)), CFG)["metrics"]
    assert 30 < m2["base_range_pct"] <= 40 and m2["base_tightness"] == "wide" and m2["tight_base_weeks"] < 52


def test_no_targets_without_a_valid_base():
    up = np.linspace(100, 200, 60)
    wide = 120 + 30 * np.sin(np.arange(80) / 3.0)
    c = np.concatenate([up, wide])
    m = evaluate(weekly(c), flat_bench(len(c)), CFG)["metrics"]
    assert m["base_tightness"] is None and m["target_base_height"] is None and m["target_swing_rule"] is None
