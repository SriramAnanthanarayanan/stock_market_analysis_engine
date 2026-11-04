"""1 / 3 / 6 / 12-month candles and performance horizons (spec 9). Fixtures, not market data."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.data.calendar import NSECalendar
from app.data.ohlcv import prepare_series
from app.data.periods import performance_horizons, period_end, period_start, resample_months, split_complete
from app.synthetic import random_walk
from app.timeutil import IST
from tests.conftest import HOLIDAYS_SRC
from tests.fixtures_adv import build_adv_app, prepare


@pytest.fixture(scope="module")
def cal():
    return NSECalendar.load(HOLIDAYS_SRC)


def test_period_boundaries():
    assert period_start(date(2026, 5, 17), 1) == date(2026, 5, 1)
    assert period_start(date(2026, 5, 17), 3) == date(2026, 4, 1)
    assert period_start(date(2026, 5, 17), 6) == date(2026, 1, 1)
    assert period_start(date(2026, 8, 3), 6) == date(2026, 7, 1)
    assert period_start(date(2026, 12, 31), 12) == date(2026, 1, 1)


def test_monthly_ohlcv_aggregation_rules(cal):
    f = random_walk(120, cal, seed=2)
    m = resample_months(f, 1)
    for ts, row in m.iterrows():
        days = f[(f.index.year == ts.year) & (f.index.month == ts.month)]
        assert row["open"] == days["open"].iloc[0] and row["close"] == days["close"].iloc[-1]
        assert row["high"] == days["high"].max() and row["low"] == days["low"].min()
        assert row["volume"] == pytest.approx(days["volume"].sum()) and row["source_bars"] == len(days)
        assert row["first_session"] == days.index[0] and row["last_session"] == days.index[-1]
    assert all(ts.day == 1 and ts.hour == 9 and ts.minute == 15 for ts in m.index)


@pytest.mark.parametrize("tf,months", [("3mo", 3), ("6mo", 6), ("12mo", 12)])
def test_quarter_half_year_and_year(cal, tf, months):
    f = random_walk(700, cal, seed=4)
    agg = resample_months(f, months)
    total = 0
    for ts, row in agg.iterrows():
        assert (ts.month - 1) % months == 0
        total += row["source_bars"]
    assert total == len(f)  # every daily candle in exactly one period, nothing added


def test_missing_sessions_are_not_filled_and_volume_never_guessed(cal):
    f = random_walk(80, cal, seed=6)
    gap = f.drop(f.index[30:35])  # five sessions missing in the provider data
    gap.loc[gap.index[:5], "volume"] = np.nan
    m = resample_months(gap, 1)
    assert m["source_bars"].sum() == len(gap)
    f2 = random_walk(40, cal, seed=6)
    f2["volume"] = np.nan
    assert resample_months(f2, 1)["volume"].isna().all()  # no volume known -> missing, not 0


def test_incomplete_current_period_is_split_off(cal):
    f = random_walk(400, cal, seed=8)
    last = f.index[-1]
    mid = f[f.index.month == last.month].index[len(f[f.index.month == last.month]) // 2]
    cutoff = pd.Timestamp(mid.date().isoformat() + " 18:00", tz=IST)
    ps = prepare_series(f, "T", "1mo", cutoff, cal)
    assert ps.frame.index[-1] < pd.Timestamp(date(mid.year, mid.month, 1), tz=IST) + pd.Timedelta(hours=9, minutes=15)
    assert ps.partial is not None and len(ps.partial) == 1
    assert ps.partial["last_session"].iloc[0].date() == mid.date()  # built from completed daily candles only
    # After the period's last trading day closes the candle is complete.
    end = period_end(date(mid.year, mid.month, 1), 1, cal)
    done = prepare_series(f, "T", "1mo", end + pd.Timedelta(minutes=1), cal)
    assert done.frame.index[-1].month == mid.month and (done.partial is None or len(done.partial) == 0)


def test_performance_horizons_use_adjusted_close_and_prior_trading_day(cal):
    f = random_walk(400, cal, seed=10)
    p = performance_horizons(f)
    assert p["basis"].startswith("raw close") and [h["label"] for h in p["horizons"]] == ["1M", "3M", "6M", "12M"]
    one = p["horizons"][0]
    target = f.index[-1] - pd.DateOffset(months=1)
    base = f["close"][f.index <= target].iloc[-1]
    assert one["return_pct"] == pytest.approx((f["close"].iloc[-1] / base - 1) * 100, abs=1e-3)
    g = f.copy()
    g["adj_close"] = g["close"] * 0.9
    assert performance_horizons(g)["basis"] == "dividend-adjusted close"
    short = performance_horizons(f.iloc[-100:])
    assert short["horizons"][-1]["status"] == "insufficient_history"


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def adv(tmp_path_factory, cal):
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        s, w, d = prepare(client, app, cal)
        yield client, s


def test_chart_multi_month_runs_no_detectors(adv):
    client, _ = adv
    for tf in ("1mo", "3mo", "6mo", "12mo"):
        r = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": tf})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["timeframe"] == tf and j["detections"] == [] and j["sr_zones"] == [] and j["swing_points"] == []
        assert j["detector_support"]["price_action_patterns"] is False and j["detector_support"]["indicators"] is True
        assert any("Multi-month candles" in n for n in j["notes"])
        assert j["performance"]["horizons"][0]["label"] == "1M"
    d = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    assert d["detector_support"]["price_action_patterns"] is True and d["detector_support"]["vcp"] is True


def test_chart_max_candles_limits_preview_payload(adv):
    client, _ = adv
    full = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    small = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d", "max_candles": 160}).json()
    assert len(small["candles"]) < len(full["candles"]) and small["candles"][-1] == full["candles"][-1]
    assert small["candles_total"] == full["candles_total"]
    assert client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d", "max_candles": 5}).status_code == 422


def test_indicators_and_technical_on_multi_month(adv):
    client, _ = adv
    ind = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "3mo"}).json()
    assert ind["validated_timeframe"] is False and len(ind["times"]) == ind["bars"]
    tech = client.get("/api/technical", params={"symbol": "RWALK", "timeframe": "6mo"}).json()
    assert tech["marubozu"] == [] and tech["divergences"] == [] and tech["ema_crosses"] == []
    assert any("multi-month" in n for n in tech["notes"])
    assert client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "2mo"}).status_code == 422


def test_index_chart_multi_month(adv):
    client, _ = adv
    r = client.get("/api/index/chart", params={"code": "NIFTY 50", "timeframe": "12mo"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["sr_zones"] == [] and len(j["candles"]) >= 5
    assert all(pd.Timestamp(c["t"], unit="s", tz="UTC").tz_convert(IST).month == 1 for c in j["candles"])
