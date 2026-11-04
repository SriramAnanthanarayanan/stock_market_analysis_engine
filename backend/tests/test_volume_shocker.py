"""Volume Shocker (spec 4 / 15.2). Synthetic fixtures: constant reference volume
with one known shock candle, so every ratio is checkable by hand."""

import numpy as np
import pandas as pd
import pytest

from app.config import SignalConfig
from app.data.ohlcv import prepare_series
from app.indicators.core import atr_wilder
from app.signals.volume import classify_direction, volume_shocks
from app.synthetic import intraday_index
from app.timeutil import IST
from tests.fixtures_e1 import frame_from

CFG = SignalConfig()


def daily(calendar, n=80, base_vol=1e5, last_vol=2e5, last=(100.0, 104.2, 99.8, 104.0), vols=None):
    c = np.full(n, 100.0)
    o = c.copy()
    h = c + 1.0
    l = c - 1.0
    o[-1], h[-1], l[-1], c[-1] = last
    v = np.full(n, base_vol) if vols is None else np.asarray(vols, dtype=float)
    v[-1] = last_vol
    return frame_from(o, h, l, c, calendar, v)


def shocks(f, timeframe="1d", cfg=CFG, since=None):
    atr = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
    return volume_shocks(f, timeframe, atr, None, None, cfg, since=len(f) - 1 if since is None else since)


@pytest.mark.parametrize("mult,status", [(1.5, "elevated"), (2.0, "shock"), (3.0, "shock"), (5.0, "shock")])
def test_rvol_thresholds(calendar, mult, status):
    s = shocks(daily(calendar, last_vol=1e5 * mult))
    assert len(s) == 1
    assert s[0]["rvol"] == pytest.approx(mult) and s[0]["status"] == status
    assert s[0]["avg_volume"] == pytest.approx(1e5)  # current candle excluded from its own average


def test_below_smallest_threshold_is_not_stored(calendar):
    assert shocks(daily(calendar, last_vol=1.4e5)) == []


def test_bullish_bearish_and_neutral_candles(calendar):
    bull = shocks(daily(calendar, last=(100.0, 104.2, 99.8, 104.0), last_vol=3e5))[0]
    assert bull["direction"] == "bullish" and bull["kind"] == "volume_shock_bullish"
    bear = shocks(daily(calendar, last=(104.0, 104.2, 99.8, 100.0), last_vol=3e5))[0]
    assert bear["direction"] == "bearish"
    neutral = shocks(daily(calendar, last=(100.0, 104.0, 96.0, 100.4), last_vol=3e5))[0]
    assert neutral["direction"] == "neutral"  # high volume, no meaningful directional close
    assert classify_direction(100, 104, 96, 103.9, CFG)[0] == "bullish"  # body 48.75 % of the range, close location 0.99
    assert classify_direction(100, 104, 96, 100.8, CFG)[0] == "neutral"  # body 10 % of the range: no meaningful close
    assert classify_direction(103.9, 104, 96, 96.5, CFG)[0] == "bearish"
    assert classify_direction(100, 100, 100, 100, CFG)[0] == "neutral"


def test_median_max_percentile_and_zscore(calendar):
    n = 80
    vols = np.full(n, 1e5)
    vols[-61:-1] = np.linspace(1e5, 2e5, 60)  # previous 60 candles rising linearly
    f = daily(calendar, n=n, vols=vols, last_vol=4e5)
    s = shocks(f)[0]
    ref20 = vols[-21:-1]
    assert s["avg_volume"] == pytest.approx(ref20.mean(), rel=1e-6)
    assert s["median_volume"] == pytest.approx(np.median(ref20), rel=1e-6)
    assert s["max_volume"] == pytest.approx(ref20.max(), rel=1e-6)
    assert s["percentile_60"] == pytest.approx(100.0)
    assert s["p90_60"] == pytest.approx(np.percentile(vols[-61:-1], 90), rel=1e-6) and s["above_percentile"] is True
    assert s["zscore"] == pytest.approx((4e5 - ref20.mean()) / ref20.std(ddof=1), rel=1e-4)
    assert s["rvol_50"] == pytest.approx(4e5 / vols[-51:-1].mean(), rel=1e-4)
    assert s["traded_value"] == pytest.approx(104.0 * 4e5)


def test_percentile_condition_is_optional(calendar):
    vols = np.full(80, 1e5)
    vols[-30] = 9e5  # one huge candle in the previous 60 keeps P90 low but ...
    f = daily(calendar, vols=vols, last_vol=2.1e5)
    loose = shocks(f)[0]
    assert loose["status"] == "shock"
    strict_cfg = SignalConfig(vs_require_percentile=True)
    vols2 = np.full(80, 1e5)
    vols2[-61:-21] = 1e6  # 40 heavy candles earlier in the 60-candle window, a quiet last 20
    f2 = daily(calendar, vols=vols2, last_vol=2.5e5)
    s2 = shocks(f2, cfg=strict_cfg)[0]
    assert s2["rvol"] >= 2.0 and s2["above_percentile"] is False and s2["status"] == "elevated"


def test_missing_volume_is_never_guessed(calendar):
    f = daily(calendar, last_vol=5e5)
    f.iloc[-1, f.columns.get_loc("volume")] = np.nan
    assert shocks(f) == []  # the candle itself has no volume
    f2 = daily(calendar, last_vol=5e5)
    f2.iloc[-15:-1, f2.columns.get_loc("volume")] = np.nan  # 14 of the 20 references missing
    assert shocks(f2) == []


def test_zero_volume_candle_is_never_a_shock_and_zeros_count_in_reference(calendar):
    f = daily(calendar, last_vol=0.0)
    assert shocks(f) == []
    vols = np.full(80, 1e5)
    vols[-11:-1] = 0.0  # ten no-trade sessions in the reference
    s = shocks(daily(calendar, vols=vols, last_vol=1e5))[0]
    assert s["avg_volume"] == pytest.approx(5e4) and s["rvol"] == pytest.approx(2.0)


def test_low_liquidity_metric_present(calendar):
    s = shocks(daily(calendar, base_vol=5e4, last_vol=1.5e5))[0]
    assert s["avg_traded_value"] == pytest.approx(100.0 * 5e4)  # close x volume of the reference candles
    assert s["avg_traded_value"] < CFG.vs_min_avg_traded_value["1d"]  # illiquid by the default rule (filtered in the API)


def test_intraday_uses_same_time_of_day_reference(calendar):
    n = 7 * 30
    idx = intraday_index(n, calendar)
    v = np.full(n, 1e5)
    tod = np.array([(t.hour, t.minute) for t in idx])
    v[(tod[:, 0] == 9)] = 8e5  # the first hour is always heavy
    v[-7] = 8e5 * 2.5  # today's first hour: 2.5x the previous first hours
    v[-1] = 1e5 * 1.2  # today's last hour: ordinary
    c = np.full(n, 100.0)
    f = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": v}, index=idx)
    s = shocks(f, timeframe="1h", since=n - 7)
    first = [x for x in s if x["index"] == n - 7]
    assert first and first[0]["rvol"] == pytest.approx(2.5) and first[0]["avg_volume"] == pytest.approx(8e5)
    assert first[0]["reference"].startswith("same time-of-day")
    # Against all previous hours the first hour would look like a 6x+ shock; same-slot it is 2.5x.
    assert all(x["index"] != n - 1 for x in s)


def test_partial_session_daily_candle_is_excluded(calendar):
    f = daily(calendar, last_vol=4e5)
    last_day = f.index[-1].date()
    ps = prepare_series(f, "TEST", "1d", pd.Timestamp(f"{last_day} 11:00", tz=IST), calendar)
    assert len(ps.frame) == len(f) - 1
    assert shocks(ps.frame) == []
