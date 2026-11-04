"""E1: Marubozu candlestick detection and N-day daily breakouts."""

from datetime import datetime, time

import numpy as np
import pandas as pd
import pytest

from app.breakouts import breakout_events, daily_reference, reference_levels
from app.candlesticks import classify_marubozu, marubozu_events, prior_volume_ratio
from app.config import DEFAULT_TECH_CONFIG as T, TechnicalConfig
from app.data.ohlcv import prepare_series
from app.indicators.core import atr_wilder
from app.timeutil import IST
from tests.fixtures_e1 import frame_from


def one(o, h, l, c, cfg=T):
    return int(classify_marubozu([o], [h], [l], [c], cfg)[0])


# ---------------------------------------------------------------- Marubozu rules
def test_bullish_and_bearish_marubozu():
    assert one(100, 110.2, 99.8, 110) == 1  # body 96 %, wicks 1.9 % each
    assert one(110, 110.2, 99.8, 100) == -1


def test_large_body_but_excessive_wick_is_not_marubozu():
    assert one(100, 111, 100, 110) == 0  # body 90.9 % but upper wick 9.1 %
    assert one(100, 110, 99, 110) == 0  # lower wick 9.1 %


def test_body_threshold_is_applied_independently():
    # body 92 %, wicks 4 % each: passes the defaults, fails a 95 % body requirement
    assert one(100, 109.6, 99.6, 109.2) == 1
    assert one(100, 109.6, 99.6, 109.2, TechnicalConfig(marubozu_min_body_ratio=0.95)) == 0


def test_zero_range_missing_and_invalid_ohlc_excluded():
    assert one(100, 100, 100, 100) == 0  # zero range
    assert one(np.nan, 110.2, 99.8, 110) == 0  # missing open
    assert one(100, 109, 99.8, 110) == 0  # high below close: invalid record
    assert one(100, 110.2, 100.5, 110) == 0  # low above open: invalid record
    assert one(100, 100, 100, 100.0, TechnicalConfig(marubozu_min_range=-1.0)) == 0  # doji has no body anyway


def _candles(calendar, last=None, n=30, vol=None):
    o = np.full(n, 100.0)
    c = np.full(n, 100.5)
    h = np.full(n, 101.5)
    l = np.full(n, 99.0)
    if last is not None:
        o[-1], h[-1], l[-1], c[-1] = last
    return frame_from(o, h, l, c, calendar, v=vol)


def test_marubozu_metrics_and_volume_is_supporting_only(calendar):
    vol = np.full(30, 1e5)
    vol[-1] = 2e4  # low volume does not disqualify
    f = _candles(calendar, last=(100, 110.2, 99.8, 110), vol=vol)
    atr = atr_wilder(f["high"], f["low"], f["close"], T.atr_period)
    evs = marubozu_events(f, atr, T, "1d")
    assert len(evs) == 1
    e = evs[0]
    assert e["kind"] == "bullish_marubozu" and e["age_bars"] == 0 and e["timeframe"] == "1d"
    assert e["body_pct"] == pytest.approx(10 / 10.4 * 100, abs=0.01)
    assert e["upper_wick_pct"] == pytest.approx(0.2 / 10.4 * 100, abs=0.01) and e["lower_wick_pct"] == pytest.approx(0.2 / 10.4 * 100, abs=0.01)
    assert e["range"] == pytest.approx(10.4) and e["range_atr"] == pytest.approx(10.4 / atr[-1], abs=1e-3)
    assert e["volume_ratio"] == pytest.approx(0.2, abs=1e-3)  # vs the previous 20 completed candles
    assert e["time"].startswith(str(f.index[-1].date()))


def test_volume_ratio_never_guessed_from_missing_volume():
    v = np.full(25, np.nan)
    v[-1] = 1e5
    assert prior_volume_ratio(v, 24, 20) is None
    v[4:24] = 5e4
    assert prior_volume_ratio(v, 24, 20) == pytest.approx(2.0)


def test_incomplete_candle_is_never_classified(calendar):
    f = _candles(calendar, last=(100, 110.2, 99.8, 110))
    day = f.index[-1].date()
    during = datetime.combine(day, time(12, 0), tzinfo=IST)  # session still open
    ps = prepare_series(f, "SYN", "1d", during, calendar)
    assert len(ps.frame) == len(f) - 1 and ps.dropped_incomplete == 1
    atr = atr_wilder(ps.frame["high"], ps.frame["low"], ps.frame["close"], 14)
    assert marubozu_events(ps.frame, atr, T, "1d") == []
    after = datetime.combine(day, time(16, 0), tzinfo=IST)
    ps2 = prepare_series(f, "SYN", "1d", after, calendar)
    atr2 = atr_wilder(ps2.frame["high"], ps2.frame["low"], ps2.frame["close"], 14)
    assert len(marubozu_events(ps2.frame, atr2, T, "1d")) == 1


# ---------------------------------------------------------------- breakouts
def _daily(calendar, tail_closes, base_n=25):
    """Quiet range (H 101.5 / L 99) then the given closes (O = previous close)."""
    c = np.concatenate([np.full(base_n, 100.5), np.asarray(tail_closes, dtype=float)])
    o = np.concatenate([[100.0], c[:-1]])
    h = np.maximum(o, c) + 1.0
    l = np.minimum(o, c) - 1.0
    h[:base_n], l[:base_n] = 101.5, 99.0
    return frame_from(o, h, l, c, calendar)


def _ev(frame, mode=1, n_days=10, cfg=T):
    atr = atr_wilder(frame["high"], frame["low"], frame["close"], cfg.atr_period)
    return breakout_events(frame, atr, n_days, mode, cfg), atr


def test_ten_day_high_breakout_and_current_candle_excluded(calendar):
    f = _daily(calendar, [103.0])
    evs, atr = _ev(f)
    up = [e for e in evs if e["direction"] == "bullish"]
    assert len(up) == 1
    e = up[0]
    assert e["kind"] == "breakout_high_10d" and e["status"] == "confirmed" and e["confirm_closes"] == 1
    assert e["prev_high"] == pytest.approx(101.5)  # the breakout candle's own high (104) is excluded
    assert e["level"] == pytest.approx(101.5) and e["close"] == 103.0
    assert e["breakout_pct"] == pytest.approx((103 / 101.5 - 1) * 100, abs=1e-3)
    assert e["distance_atr"] == pytest.approx((103 - 101.5) / atr[-1], abs=1e-3)
    assert e["reference_end"].startswith(str(f.index[-2].date())) and e["reference_start"].startswith(str(f.index[-11].date()))
    assert e["age_bars"] == 0 and e["first_of_run"] is True


def test_ten_day_low_breakdown(calendar):
    f = _daily(calendar, [97.0])
    evs, _ = _ev(f)
    dn = [e for e in evs if e["direction"] == "bearish"]
    assert len(dn) == 1 and dn[0]["kind"] == "breakdown_low_10d" and dn[0]["prev_low"] == pytest.approx(99.0)


def test_break_buffer_blocks_marginal_close(calendar):
    f = _daily(calendar, [101.55])  # above 101.5 but not by 0.1 x ATR
    evs, atr = _ev(f)
    assert 0.05 < 0.1 * atr[-1]
    assert not [e for e in evs if e["direction"] == "bullish"]
    assert [e for e in _ev(f, cfg=TechnicalConfig(breakout_buffer_atr=0.0))[0] if e["direction"] == "bullish"]


def test_two_close_confirmation_failed_and_pending(calendar):
    ok, _ = _ev(_daily(calendar, [103.0, 102.6]), mode=2)
    up = [e for e in ok if e["direction"] == "bullish"]
    assert len(up) == 1 and up[0]["status"] == "confirmed" and up[0]["confirm_closes"] == 2
    assert up[0]["first_close_index"] == up[0]["index"] - 1 and up[0]["level"] == pytest.approx(101.5)
    failed, _ = _ev(_daily(calendar, [103.0, 100.8]), mode=2)  # second close back inside: false breakout
    up = [e for e in failed if e["direction"] == "bullish"]
    assert len(up) == 1 and up[0]["status"] == "failed"
    pending, _ = _ev(_daily(calendar, [103.0]), mode=2)
    up = [e for e in pending if e["direction"] == "bullish"]
    assert len(up) == 1 and up[0]["status"] == "pending" and up[0]["age_bars"] == 0
    # one-close mode: both closes are breakouts, only the first starts a run
    one_mode, _ = _ev(_daily(calendar, [103.0, 104.5]), mode=1)
    runs = [e["first_of_run"] for e in one_mode if e["direction"] == "bullish"]
    assert runs == [True, False]


def test_insufficient_daily_history(calendar):
    f = _daily(calendar, [103.0], base_n=9)  # 10 candles: no full previous-10 window for the last one
    rh, rl = reference_levels(f["high"], f["low"], 10)
    assert np.isnan(rh).all() and np.isnan(rl).all()
    assert _ev(f)[0] == []
    assert daily_reference(f.iloc[:9], 10) is None


def test_reference_window_counts_trading_candles_not_calendar_days(calendar):
    f = _daily(calendar, [103.0])
    e = [x for x in _ev(f)[0] if x["direction"] == "bullish"][0]
    span_days = (pd.Timestamp(e["reference_end"]) - pd.Timestamp(e["reference_start"])).days
    assert span_days >= 13  # 10 trading candles span weekends/holidays


def test_historical_asof_breakouts_do_not_change_with_later_candles(calendar):
    f = _daily(calendar, [103.0, 99.5, 100.2, 96.0, 100.0, 104.0])
    full, _ = _ev(f)
    for m in range(26, len(f) + 1):
        part, _ = _ev(f.iloc[:m])
        assert part == [e | {"age_bars": e["age_bars"] - (len(f) - m)} for e in full if e["index"] < m]


def test_daily_reference_levels_for_intraday_overlay(calendar):
    f = _daily(calendar, [103.0])
    ref = daily_reference(f, 10)
    assert ref["high"] == pytest.approx(float(f["high"].iloc[-10:].max())) and ref["low"] == pytest.approx(float(f["low"].iloc[-10:].min()))
    assert ref["reference_end"].startswith(str(f.index[-1].date())) and ref["basis"] == "daily"
