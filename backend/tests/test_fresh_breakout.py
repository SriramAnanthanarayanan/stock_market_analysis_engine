"""Fresh 10-day breakout events (spec 3 / 15.1): crossing rules, de-duplication,
re-arming, First Breakout in Lookback, modes and as-of correctness.

Prices are synthetic fixtures (flat ranges with explicit breakout candles);
they prove the rules, not real-chart accuracy."""

import numpy as np
import pandas as pd
import pytest

from app.config import SignalConfig
from app.data.ohlcv import prepare_series
from app.indicators.core import atr_wilder
from app.signals.breakouts import crossing_events, fresh_kind, fresh_params, touch_records
from app.signals.crossing import CrossingParams, crossing_series, first_in_lookback
from app.timeutil import IST
from tests.fixtures_e1 import frame_from

CFG = SignalConfig()


def bars(closes, highs=None, lows=None, wick=1.0, volume=None):
    """Synthetic daily candles: open = previous close; high/low = body +/- wick unless given."""
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + wick if highs is None else np.asarray(highs, dtype=float)
    l = np.minimum(o, c) - wick if lows is None else np.asarray(lows, dtype=float)
    return o, h, l, c, volume


def frame(closes, calendar, **kw):
    o, h, l, c, v = bars(closes, **kw)
    return frame_from(o, h, l, c, calendar, v)


def events(f, cfg=CFG, since=0):
    atr = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), cfg.atr_period)
    evs, walks = crossing_events(
        f, atr, fresh_params(cfg), symbol="TEST", kind_of=lambda d: fresh_kind(d, cfg.fresh_lookback_days), config_hash="h",
        since=since, volume_lookback=cfg.fresh_volume_lookback, first_lookback=cfg.fresh_first_lookback_sessions,
    )
    return evs, walks


def bull(evs):
    return [e for e in evs if e["direction"] == "bullish"]


def bear(evs):
    return [e for e in evs if e["direction"] == "bearish"]


FLAT = [100.0] * 40  # high 101, low 99 with the default wick


def test_fresh_crossing_above_previous_10_day_high(calendar):
    f = frame(FLAT + [103.0, 104.0, 105.0, 106.0], calendar)
    evs, _ = events(f)
    b = bull(evs)
    assert [e["index"] for e in b] == [40]  # later closes above their own windows are not new events
    e = b[0]
    assert e["level"] == pytest.approx(101.0) and e["kind"] == "fresh_breakout_high_10d"
    assert e["close"] == 103.0 and e["breakout_pct"] == pytest.approx((103 / 101 - 1) * 100, abs=1e-3)
    assert e["trigger"] == "armed" and e["confirm_1"] == "confirmed" and e["confirm_2"] == "confirmed"
    assert e["episode_state"] == "active" and e["still_beyond"] is True and e["age_bars"] == 3
    assert e["sessions_since_last_beyond"] is None and e["first_in_lookback"] is True
    assert e["suppressed_crossings"] == 0 and e["event_id"] and len(e["event_id"]) == 16


def test_fresh_crossing_below_previous_10_day_low(calendar):
    f = frame(FLAT + [97.0, 96.0], calendar)
    evs, _ = events(f)
    assert not bull(evs)
    b = bear(evs)
    assert [e["index"] for e in b] == [40] and b[0]["kind"] == "fresh_breakdown_low_10d"
    assert b[0]["level"] == pytest.approx(99.0) and b[0]["distance_atr"] < 0


def test_current_candle_excluded_from_reference(calendar):
    # The breakout candle's own (much higher) high must not enter its reference window.
    o, h, l, c, _ = bars(FLAT + [102.5])
    h[-1] = 120.0
    f = frame_from(o, h, l, c, calendar)
    e = bull(events(f)[0])[0]
    assert e["level"] == pytest.approx(101.0) and e["reference_high"] == pytest.approx(101.0)
    assert e["reference_end"] < e["time"]


def test_previous_bar_judged_on_its_own_window(calendar):
    # Bar 40 breaks out; bar 41 is beyond its own window too (prev bar beyond) -> no event at 41.
    f = frame(FLAT + [103.0, 104.5], calendar)
    _, walks = events(f)
    w = walks["bullish"]
    assert bool(w.beyond[41]) and not bool(w.fresh[41]) and bool(w.fresh[40])


def test_level_that_dropped_when_an_old_high_rolled_out_is_not_a_crossing(calendar):
    closes = [100.0] * 41
    o, h, l, c, _ = bars(closes)
    h[30] = 110.0  # an old spike high inside the window until it rolls out at bar 41
    closes2 = closes + [102.5]
    o2, h2, l2, c2, _ = bars(closes2)
    h2[:41] = h
    f = frame_from(o2, h2, l2, c2, calendar)
    evs, walks = events(f)
    w = walks["bullish"]
    i = 41
    assert w.level[i] == pytest.approx(101.0) and w.level[i - 1] == pytest.approx(110.0)
    assert bool(w.beyond[i]) and not bool(w.prior_clear[i]) and not bool(w.fresh[i])
    assert not bull(evs)
    # Turning the rule off accepts it (configurable).
    loose = SignalConfig(fresh_require_prior_level_clear=False)
    assert [e["index"] for e in bull(events(f, loose)[0])] == [41]


def test_repeated_closes_above_the_same_level_create_no_repeat_events(calendar):
    # Breakout, a 1-close dip back inside (not 3), then a genuine re-crossing within the window.
    f = frame(FLAT + [103.0, 104.0, 100.5, 106.5, 107.0, 107.5], calendar)
    evs, walks = events(f)
    assert [e["index"] for e in bull(evs)] == [40]
    assert bool(walks["bullish"].fresh[43])  # a crossing by the rules ...
    assert bull(evs)[0]["suppressed_crossings"] == 1  # ... suppressed: same episode, not re-armed


def test_rearming_after_three_closes_back_inside(calendar):
    closes = FLAT + [103.0, 100.5, 100.2, 100.4] + [100.0] * 12 + [104.0]
    f = frame(closes, calendar)
    evs, _ = events(f)
    b = bull(evs)
    assert [e["index"] for e in b] == [40, len(closes) - 1]
    assert b[0]["episode_state"] == "reset" and b[1]["trigger"] == "armed"
    assert b[1]["previous_event_time"] == b[0]["time"]


def test_two_closes_back_inside_do_not_rearm(calendar):
    # Only 2 closes back inside, then a new crossing within the lookback: suppressed.
    closes = FLAT + [103.0, 100.5, 100.4, 101.8, 100.6, 100.5, 102.0, 104.4]
    f = frame(closes, calendar)
    evs, walks = events(f)
    b = bull(evs)
    assert [e["index"] for e in b] == [40]
    fresh_later = [int(i) for i in np.flatnonzero(walks["bullish"].fresh) if i > 40]
    assert fresh_later and fresh_later == walks["bullish"].episodes[0].suppressed


def test_rearm_rule_is_configurable(calendar):
    closes = FLAT + [103.0, 100.5, 100.2] + [100.0] * 12 + [104.0]
    f = frame(closes, calendar)
    assert len(bull(events(f, SignalConfig(fresh_rearm_closes=2))[0])) == 2
    assert len(bull(events(f, SignalConfig(fresh_rearm_closes=20, fresh_rearm_on_new_range=False))[0])) == 1


def test_new_range_after_the_event_rearms_without_close_back(calendar):
    # Breakout at 40, run-up, a 12-session range at ~110 (never back below 101), then a break of that range.
    closes = FLAT + [103.0, 106.0, 109.0] + [110.0, 109.5, 110.2, 109.8, 110.1, 109.7, 110.0, 109.6, 110.3, 109.9, 110.0, 109.8] + [113.0]
    f = frame(closes, calendar)
    b = bull(events(f)[0])
    assert [e["index"] for e in b] == [40, len(closes) - 1]
    assert b[1]["trigger"] == "new_range" and b[0]["episode_state"] == "superseded"
    assert len(bull(events(f, SignalConfig(fresh_rearm_on_new_range=False))[0])) == 1


def test_first_breakout_in_last_20_sessions(calendar):
    # A breakout close 15 sessions before the event -> not first in 20, first in 10.
    closes = FLAT + [103.0, 100.5, 100.2, 100.4] + [100.0] * 11 + [104.0]
    f = frame(closes, calendar)
    b = bull(events(f)[0])
    second = b[1]
    assert second["sessions_since_last_beyond"] == len(closes) - 1 - 40
    assert second["first_in_lookback"] is False  # default N = 20
    assert first_in_lookback(second["sessions_since_last_beyond"], second["history_sessions_before"], 10) is True
    assert b[0]["first_in_lookback"] is True


def test_first_in_lookback_needs_enough_history(calendar):
    f = frame([100.0] * 20 + [103.0], calendar)
    e = bull(events(f)[0])[0]
    assert e["history_sessions_before"] < 20 and e["first_in_lookback"] is None
    assert first_in_lookback(None, 25, 20) is True and first_in_lookback(None, 5, 20) is None


def test_intraday_touch_versus_completed_close(calendar):
    o, h, l, c, _ = bars(FLAT + [100.5])
    h[-1] = 102.0  # high crosses 101, close stays inside
    f = frame_from(o, h, l, c, calendar)
    evs, walks = events(f)
    assert not bull(evs)
    t = touch_records(f, walks, lambda d: fresh_kind(d, 10), since=0)
    tb = [x for x in t if x["direction"] == "bullish"]
    assert len(tb) == 1 and tb[0]["close_status"] == "closed_inside" and tb[0]["status"] == "provisional_touch"
    assert tb[0]["kind"] == "fresh_touch_high_10d"


def test_two_close_confirmation_confirmed_failed_pending(calendar):
    ok = bull(events(frame(FLAT + [103.0, 104.0], calendar))[0])[0]
    assert ok["confirm_2"] == "confirmed"
    failed = bull(events(frame(FLAT + [103.0, 100.0], calendar))[0])[0]
    assert failed["confirm_2"] == "failed"
    pending = bull(events(frame(FLAT + [103.0], calendar))[0])[0]
    assert pending["confirm_2"] == "pending" and pending["confirm_2_time"] is None


def test_insufficient_history_produces_nothing(calendar):
    f = frame([100.0] * 8 + [110.0], calendar)
    evs, walks = events(f)
    assert evs == [] and walks["bullish"].first is None


def test_first_candle_already_beyond_is_blocked_until_rearmed():
    p = CrossingParams(3, 0.0, 0.0, True, 2, False)
    # bars 0-2 build the window; bar 3 is the first computable bar and already beyond.
    c = np.array([10, 10, 10, 12, 12.5, 9.5, 9.6, 9.4, 13.0])
    h = c + 0.2
    l = c - 0.2
    atr = np.ones_like(c)
    w = crossing_series(h, l, c, atr, p, "bullish")
    assert w.first == 3 and bool(w.beyond[3]) and w.blocked_until == 7  # two closes inside (5, 6) arm the walk at 7
    assert [e.event for e in w.episodes] == [8] and w.episodes[0].trigger == "blocked_rearmed"


def test_historical_asof_is_unchanged_by_later_candles(calendar):
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.normal(0, 1.2, 400))
    full = frame(closes, calendar)
    evs_full, _ = events(full)
    cut = 300
    evs_cut, _ = events(full.iloc[:cut])
    stable = ("event_id", "index", "level", "close", "trigger", "first_in_lookback", "sessions_since_last_beyond")
    early = [{k: e[k] for k in stable} for e in evs_full if e["index"] < cut]
    assert early == [{k: e[k] for k in stable} for e in evs_cut]
    assert all(e["index"] < cut for e in evs_cut)


def test_longer_history_does_not_change_recent_events(calendar):
    rng = np.random.default_rng(9)
    closes = 100 + np.cumsum(rng.normal(0, 1.0, 600))
    f = frame(closes, calendar)
    recent_full = [(e["event_id"], e["index"] - 300) for e in events(f)[0] if e["index"] >= 450]
    recent_short = [(e["event_id"], e["index"]) for e in events(f.iloc[300:])[0] if e["index"] >= 150]
    assert recent_full and recent_full == recent_short


def test_completed_candles_only_via_prepare_series(calendar):
    # A daily candle of the running session (cutoff 14:00) is dropped before any breakout logic.
    f = frame(FLAT + [100.0, 106.0], calendar)
    last_day = f.index[-1].date()
    cutoff = pd.Timestamp(f"{last_day} 14:00", tz=IST)
    ps = prepare_series(f, "TEST", "1d", cutoff, calendar)
    assert len(ps.frame) == len(f) - 1
    assert not bull(events(ps.frame)[0])
