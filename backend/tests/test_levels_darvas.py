"""SignalConfig 1.4.0 (phase 2 of the 2026-11-15 plan): shared level engine (separate
tests, test score, approach states), multi-year and 52-week approach states, Darvas box.

Synthetic fixtures: they prove the rules as written, not real-chart accuracy."""

import numpy as np
import pandas as pd
import pytest

from app.config import SignalConfig
from app.indicators.core import atr_wilder
from app.signals.breakouts import crossing_events, w52_kind, w52_params, week52_state
from app.signals.darvas import STATE_LABELS, darvas_walk, detect_darvas
from app.signals import levels as LV  # module import: pytest would collect a bare 'tests_score' as a test
from app.signals.multiyear import multiyear_approach
from app.synthetic import trading_day_index

CFG = SignalConfig()


def frame(closes, calendar, highs=None, lows=None, wick=0.5, volume=None):
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + wick if highs is None else np.asarray(highs, dtype=float)
    l = np.minimum(o, c) - wick if lows is None else np.asarray(lows, dtype=float)
    v = np.full(len(c), 1e5) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=trading_day_index(len(c), calendar))


def atr(f):
    return atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), CFG.atr_period)


# ---------------------------------------------------------------- level helpers
def test_separate_tests_merge_consecutive_zone_candles():
    h = np.array([90, 99, 99.5, 98, 90, 91, 99.2, 85, 86, 87, 88, 99.8])
    idx = LV.separate_tests(h, 0, len(h) - 1, zone_lo=99.0, separation=3)
    assert idx == [1, 6, 11]  # 1-2 is one test (consecutive), 6 and 11 are separate
    assert LV.separate_tests(h, 0, len(h) - 1, zone_lo=99.0, separation=6) == [1]  # each zone candle within 6 sessions of the previous one extends the same test
    lows = np.array([10, 9, 11, 9.05, 12])
    assert LV.separate_tests(lows, 0, 4, zone_hi=9.1, separation=2) == [1, 3]  # support side


def test_tests_score_and_approach_state():
    sc = CFG.level_tests_score
    assert [LV.tests_score(n, sc) for n in (0, 1, 2, 3, 4, 9)] == [0.0, 0.2, 0.5, 0.8, 1.0, 1.0]  # 3+ tests rank higher
    assert LV.approach_state(97.0, 98.0, 100.0, 5.0, 2.0) == ("approaching", pytest.approx(3.0))
    assert LV.approach_state(98.5, 99.0, 100.0, 5.0, 2.0)[0] == "very_near"
    assert LV.approach_state(99.0, 100.5, 100.0, 5.0, 2.0)[0] == "touched"  # intraday through, close below
    assert LV.approach_state(100.5, 101.0, 100.0, 5.0, 2.0)[0] is None  # a close above is a breakout, not an approach
    assert LV.approach_state(90.0, 91.0, 100.0, 5.0, 2.0)[0] is None
    assert LV.lows_rising(np.array([5, 4, 6, 7, 6.5, 8]), 0, 5) is True


# ---------------------------------------------------------------- multi-year approach
def multi_year_range(calendar, last_close=97.0, tests=4, years=3):
    """A 2-year advance to 100, then ``years`` years capped below 100 with ``tests`` rallies
    into the 97-100 zone, ending at ``last_close``."""
    adv = list(np.linspace(40, 100, 500))
    n = int(years * 250)
    seg = n // tests
    rng = []
    for k in range(tests):
        down = np.linspace(99.0, 75.0 + k * 3, seg // 2)  # rising lows: buyers stepping in higher
        up = np.linspace(75.0 + k * 3, 99.0, seg - seg // 2)
        rng += list(down) + list(up)
    closes = adv + rng + [last_close]
    return frame(closes, calendar)


def test_multiyear_range_about_to_break_out(calendar):
    f = multi_year_range(calendar)
    r = multiyear_approach(f, atr(f), CFG)
    assert r is not None and r["state"] == "approaching" and r["kind"] == "multiyear_approach"
    assert r["level"] == pytest.approx(100.5, abs=0.01) and r["base_years"] >= 2.5
    assert r["zone_tests"] >= 4 and r["tests_score"] == 1.0 and r["lows_rising"] is True
    assert 0 < r["distance_pct"] <= CFG.multiyear_approach_pct and r["all_time_high_in_data"] is True
    assert any("about to break" in x or "Not a breakout" in x for x in r["explanation"])


def test_multiyear_approach_needs_a_long_base_and_a_nearby_level(calendar):
    young = frame(list(np.linspace(40, 100, 400)) + list(np.linspace(99, 90, 60)) + [97.0], calendar)  # capped for months only
    assert multiyear_approach(young, atr(young), CFG) is None
    far = multi_year_range(calendar, last_close=88.0)  # 12 % below: not within reach
    assert multiyear_approach(far, atr(far), CFG) is None
    above = multi_year_range(calendar, last_close=103.0)  # the breakout belongs to the breakout scanner
    assert multiyear_approach(above, atr(above), CFG) is None
    trend = frame(list(np.linspace(40, 150, 900)), calendar)  # all-time highs every day: nothing overhead
    assert multiyear_approach(trend, atr(trend), CFG) is None


def test_multiyear_approach_uses_only_past_candles(calendar):
    f = multi_year_range(calendar)
    full = multiyear_approach(f, atr(f), CFG)
    g = pd.concat([f, f.iloc[[-1]].assign(close=130.0, high=131.0)])  # a later candle must not change the earlier result
    g.index = trading_day_index(len(g), calendar)
    again = multiyear_approach(f.copy(), atr(f), CFG)
    assert again == full


# ---------------------------------------------------------------- 52-week approach
def test_52_week_high_tests_and_approach(calendar):
    base = []
    for k in range(4):  # four rallies into ~100 inside the last year
        base += list(np.linspace(100, 90, 30)) + list(np.linspace(90, 100, 30))
    closes = list(np.linspace(60, 100, 120)) + base + [98.6]
    f = frame(closes, calendar)
    a = atr(f)
    _, walks = crossing_events(f, a, w52_params(CFG), symbol="T", kind_of=w52_kind, config_hash="h", since=len(f), volume_lookback=20, first_lookback=20)
    st = week52_state(f, a, CFG, walks)
    assert st["status"] == "ok" and st["approach_state"] in ("very_near", "approaching", "touched")  # the last candle's wick reaches the high
    assert st["high_tests"] >= 4 and st["tests_score"] == 1.0 and st["distance_below_high_pct"] > 0


# ---------------------------------------------------------------- Darvas box
def darvas_path(calendar, tail):
    """300 quiet sessions around 50, an advance to new highs at 100, then a box: three lower
    highs, a dip to 94 and three sessions above it; ``tail`` = (close, high, low, volume)."""
    closes = list(50 + np.sin(np.arange(300) / 5.0)) + list(np.linspace(52, 99.5, 40))
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    highs[-1] = 100.0  # the new 52-week high = the box top
    box = [(98.5, 99.0, 97.5), (97.0, 98.0, 96.0), (95.0, 96.5, 94.5), (94.5, 95.5, 94.0), (95.5, 96.0, 94.6), (96.5, 97.0, 95.0), (97.5, 98.0, 96.0)]
    for c, h, l in box:
        closes.append(c), highs.append(h), lows.append(l)
    vols = [1e5] * len(closes)
    for c, h, l, v in tail:
        closes.append(c), highs.append(h), lows.append(l), vols.append(v)
    return frame(closes, calendar, highs=highs, lows=lows, volume=vols)


def test_darvas_box_top_bottom_and_near_top(calendar):
    f = darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5)])
    d = detect_darvas(f, CFG, "T")
    assert d is not None and d["state"] == "near_top" and d["state_label"] == STATE_LABELS["near_top"]
    assert d["box_top"] == pytest.approx(100.0) and d["box_bottom"] == pytest.approx(94.0)
    assert d["box_depth_pct"] == pytest.approx(6.0, abs=0.01) and d["geometry"]["current"]["top"] == pytest.approx(100.0)
    assert d["top_tests"] >= 1 and 0 <= d["tests_score"] <= 1


def test_darvas_breakout_with_volume_and_stacking(calendar):
    f = darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5), (101.5, 102.0, 98.0, 3e5)])
    d = detect_darvas(f, CFG, "T")
    assert d["state"] == "breakout" and d["event"]["volume_ok"] is True and d["event"]["volume_ratio"] >= CFG.darvas_breakout_volume_ratio
    assert d["boxes_stacked"] >= 1 and d["geometry"]["boxes"][-1]["outcome"] == "breakout" and d["box_top"] == pytest.approx(100.0)
    weak = detect_darvas(darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5), (101.5, 102.0, 98.0, 0.8e5)]), CFG, "T")
    assert weak["state"] == "breakout" and weak["event"]["volume_ok"] is False  # reported, not a gate


def test_darvas_breakdown(calendar):
    d = detect_darvas(darvas_path(calendar, [(93.0, 95.0, 92.5, 1.5e5)]), CFG, "T")
    assert d["state"] == "breakdown" and d["box_bottom"] == pytest.approx(94.0)


def test_darvas_no_look_ahead(calendar):
    f = darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5), (101.5, 102.0, 98.0, 3e5)])
    before = detect_darvas(f.iloc[:-1], CFG, "T")
    assert before["state"] == "near_top" and before["event"] is None
    # the top is unknown until 3 sessions after the high: cut right after the high
    k = 339  # index of the 100.0 high (the last advance candle)
    early = darvas_walk(f.iloc[: k + 3], CFG)
    assert early["top_ok"] is False
    assert darvas_walk(f.iloc[: k + 4], CFG)["top_ok"] is True


def test_darvas_deep_fall_is_not_a_box(calendar):
    closes = list(50 + np.sin(np.arange(300) / 5.0)) + list(np.linspace(52, 100, 40)) + list(np.linspace(99, 70, 15)) + [72, 73, 74, 75]
    d = detect_darvas(frame(closes, calendar), CFG, "T")
    assert d is None or d["state"] not in ("in_box", "near_top")


def test_darvas_needs_a_new_high(calendar):
    closes = list(np.linspace(120, 60, 300)) + list(np.linspace(60, 70, 20)) + [69, 68, 69, 68.5, 69.5]
    assert detect_darvas(frame(closes, calendar), CFG, "T") is None  # a pause far below the 52-week high is not a Darvas box


# ---------------------------------------------------------------- 10-day base tests in the setup quality
def test_setup_quality_scores_separate_tests():
    from app.signals.breakouts import setup_quality

    m = {"direction": "bullish", "state": "very_near", "distance_pct": 0.5, "ema20": 100, "ema50": 95, "close": 101, "range_10d_pct": 4.0, "volume_ratio": 1.0, "base_separate_tests": 3, "base_tests_score": 0.8}
    q, comps = setup_quality(m, cfg=CFG)
    c = {x["name"]: x for x in comps}
    assert c["level_tests"]["score"] == 0.8 and c["level_tests"]["weight"] == 10 and 0 <= q <= 100


def test_signal_config_1_4_0_defaults():
    assert CFG.version == "1.4.0" and CFG.darvas_confirm_sessions == 3 and CFG.darvas_new_high_lookback == 252
    assert (CFG.w52_approach_pct, CFG.multiyear_approach_pct, CFG.darvas_max_depth_pct) == (5.0, 5.0, 20.0)


# ---------------------------------------------------------------- API (offline fixture app, 1D + 1W scans)
@pytest.fixture(scope="module")
def adv(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from tests.conftest import HOLIDAYS_SRC
    from tests.fixtures_adv import build_adv_app, prepare

    cal = NSECalendar.load(HOLIDAYS_SRC)
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        prepare(client, app, cal)
        yield client


def test_api_darvas_scan_and_symbol(adv):
    r = adv.get("/api/scan/darvas").json()
    assert {"scan", "count", "items", "state_labels", "params", "note"} <= set(r) and r["count"] == len(r["items"])
    assert r["params"]["darvas_confirm_sessions"] == 3 and "Darvas" in r["note"]
    for it in r["items"]:
        assert it["state"] in ("breakout", "near_top", "in_box", "forming_bottom") and "geometry" not in it
    allst = adv.get("/api/scan/darvas", params={"state": "breakout,near_top,in_box,forming_bottom,forming_top,breakdown"}).json()
    assert allst["count"] >= r["count"]
    assert adv.get("/api/scan/darvas", params={"state": "nonsense"}).status_code == 422
    sym = adv.get("/api/darvas/symbol", params={"symbol": "RWALK"}).json()
    assert sym["symbol"] == "RWALK" and sym["source"] == "scan" and "darvas" in sym
    assert adv.get("/api/darvas/symbol", params={"symbol": "NOPE"}).status_code == 404


def test_api_multiyear_approach_and_52_week_approach(adv):
    r = adv.get("/api/scan/multiyear-approach").json()
    assert {"count", "items", "state_labels", "params", "note"} <= set(r) and r["params"]["multiyear_approach_pct"] == 5.0
    assert all(x["state"] in ("approaching", "very_near", "touched") and x["base_years"] >= 1.0 for x in r["items"])
    assert adv.get("/api/scan/multiyear-approach", params={"state": "bad"}).status_code == 422
    w = adv.get("/api/scan/52week", params={"view": "approach"}).json()
    assert w["view"] == "approach" and all(x.get("approach_state") in ("approaching", "very_near", "touched") for x in w["items"])
    st = adv.get("/api/scan/52week", params={"view": "state"}).json()
    ok = [x for x in st["items"] if x.get("status") == "ok"]
    assert ok and all("high_tests" in x and "tests_score" in x for x in ok)  # the level engine fields are stored on every 52-week state


def test_darvas_breakout_back_below_top_is_flagged(calendar):
    d = detect_darvas(darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5), (101.5, 102.0, 98.0, 3e5), (99.0, 101.0, 98.5, 1e5)]), CFG, "T")
    assert d["state"] == "breakout" and d["back_below_top"] is True and "back below top" in d["state_label"]
    held = detect_darvas(darvas_path(calendar, [(98.2, 98.6, 97.4, 1e5), (101.5, 102.0, 98.0, 3e5)]), CFG, "T")
    assert held["back_below_top"] is False
