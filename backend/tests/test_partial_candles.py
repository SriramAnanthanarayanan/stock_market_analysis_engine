"""Current (incomplete) higher-timeframe candles: 1W and 1/3/6/12-month running
periods aggregated from completed daily candles, flagged ``is_complete=false``,
display only (detectors and default indicators use completed candles).
Fixtures, not market data."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.data.calendar import NSECalendar
from app.data.ohlcv import prepare_series
from app.data.periods import partial_record, partial_week, running_period
from app.synthetic import random_walk
from app.timeutil import IST
from tests.conftest import HOLIDAYS_SRC
from tests.fixtures_adv import build_adv_app, prepare

ASOF = "2026-10-28"  # a Wednesday: the week of 26-Oct and the month of October are still running


@pytest.fixture(scope="module")
def cal():
    return NSECalendar.load(HOLIDAYS_SRC)


def _cut(day: str, hhmm: str = "18:00") -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz=IST)


def test_partial_week_ohlcv_aggregation(cal):
    f = random_walk(200, cal, seed=21)  # ends Friday 30-Oct-2026
    daily = prepare_series(f, "T", "1d", _cut(ASOF), cal).frame
    part = partial_week(daily, _cut(ASOF), cal)
    days = daily[daily.index >= pd.Timestamp("2026-10-26", tz=IST)]
    assert len(part) == 1 and len(days) == 3
    r = part.iloc[0]
    assert part.index[0] == pd.Timestamp("2026-10-26 09:15", tz=IST)  # Monday label like every weekly candle
    assert r["open"] == days["open"].iloc[0] and r["close"] == days["close"].iloc[-1]
    assert r["high"] == days["high"].max() and r["low"] == days["low"].min()
    assert r["volume"] == pytest.approx(days["volume"].sum()) and r["source_bars"] == 3
    rec = partial_record(part, "1w", cal)
    assert rec["is_complete"] is False and rec["label"] == "INCOMPLETE" and rec["sessions"] == 3
    assert rec["c"] == daily["close"].iloc[-1] and pd.Timestamp(rec["last_session"]) == daily.index[-1]
    # once Friday's session has closed the week is a regular, complete candle
    done = prepare_series(f, "T", "1d", _cut("2026-10-30", "15:31"), cal).frame
    assert partial_week(done, _cut("2026-10-30", "15:31"), cal).empty


def test_partial_week_never_guesses_volume_and_handles_holiday_monday(cal):
    f = random_walk(200, cal, seed=22)
    daily = prepare_series(f, "T", "1d", _cut(ASOF), cal).frame.copy()
    daily.loc[daily.index[-1], "volume"] = np.nan
    r = partial_week(daily, _cut(ASOF), cal).iloc[0]
    assert r["volume"] == pytest.approx(daily["volume"].iloc[-3:-1].sum())  # known volumes only
    daily.loc[daily.index[-3:], "volume"] = np.nan
    assert np.isnan(partial_week(daily, _cut(ASOF), cal).iloc[0]["volume"])  # none known -> missing, not 0
    # a week whose Monday is not traded: still Monday-labelled, open = first traded day's open
    tue_onward = daily[daily.index != pd.Timestamp("2026-10-26 09:15", tz=IST)]
    p2 = partial_week(tue_onward, _cut(ASOF), cal)
    assert p2.index[0] == pd.Timestamp("2026-10-26 09:15", tz=IST) and p2.iloc[0]["open"] == tue_onward.loc["2026-10-27", "open"].iloc[0]


@pytest.mark.parametrize("tf,start", [("1mo", "2026-10-01"), ("3mo", "2026-10-01"), ("6mo", "2026-07-01"), ("12mo", "2026-01-01")])
def test_running_multi_month_periods(cal, tf, start):
    f = random_walk(400, cal, seed=23)
    daily = prepare_series(f, "T", "1d", _cut(ASOF), cal).frame
    part = running_period(daily, tf, _cut(ASOF), cal)
    days = daily[daily.index >= pd.Timestamp(start, tz=IST)]
    assert len(part) == 1 and part.index[0] == pd.Timestamp(f"{start} 09:15", tz=IST)
    r = part.iloc[0]
    assert r["open"] == days["open"].iloc[0] and r["close"] == days["close"].iloc[-1]
    assert r["high"] == days["high"].max() and r["low"] == days["low"].min()
    assert r["volume"] == pytest.approx(days["volume"].sum()) and r["source_bars"] == len(days)
    rec = partial_record(part, tf, cal)
    assert rec["is_complete"] is False and rec["timeframe"] == tf and pd.Timestamp(rec["period_end"]) > _cut(ASOF)


def test_detectors_never_see_the_partial_candle(cal):
    f = random_walk(400, cal, seed=24)
    wk = f.copy()
    from app.synthetic import weekly_index, with_index

    wk = with_index(f.iloc[-120:].reset_index(drop=True), weekly_index(120, cal))
    ps = prepare_series(wk, "T", "1w", _cut(ASOF), cal)
    daily = prepare_series(f, "T", "1d", _cut(ASOF), cal).frame
    part = partial_week(daily, _cut(ASOF), cal)
    assert ps.frame.index[-1] < part.index[0]  # the running week is not in the detector input
    assert ps.dropped_incomplete == 1


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def adv(tmp_path_factory, cal):
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        s, w, d = prepare(client, app, cal)
        yield client, s


def _chart(client, tf, **kw):
    r = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": tf, "asof": ASOF, **kw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("tf", ["1w", "1mo", "3mo", "6mo", "12mo"])
def test_chart_shows_running_candle_consistent_with_daily(adv, tf):
    client, _ = adv
    daily = _chart(client, "1d")
    j = _chart(client, tf)
    p = j["partial_candle"]
    assert p is not None and p["is_complete"] is False and p["label"] == "INCOMPLETE" and p["timeframe"] == tf
    assert all(c["is_complete"] is True for c in j["candles"])
    assert j["candles"][-1]["t"] < p["t"]
    last = daily["candles"][-1]
    # latest price / time agree across daily, weekly and monthly charts
    assert p["c"] == pytest.approx(last["c"]) and pd.Timestamp(p["last_session"]).timestamp() == last["t"]
    assert p["v"] is None or p["v"] > 0


def test_weekly_chart_detections_use_completed_candles_only(adv):
    client, _ = adv
    j = _chart(client, "1w")
    last_done = j["candles"][-1]["t"]
    for d in j["detections"]:
        assert d["geometry"]["last_candle"]["t"] <= last_done
    assert j["partial_candle"]["t"] > last_done


def test_live_chart_has_no_partial_when_the_period_closed(adv):
    client, _ = adv
    # fixture data ends Friday 30-Oct-2026; today the week of 26-Oct is complete and nothing of the new week traded yet
    j = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1w"}).json()
    assert j["partial_candle"] is None or j["partial_candle"]["t"] > j["candles"][-1]["t"]
    assert client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d", "asof": ASOF}).json()["partial_candle"] is None


def test_indicators_optionally_include_the_partial_candle(adv):
    client, _ = adv
    base = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1w", "asof": ASOF}).json()
    inc = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1w", "asof": ASOF, "include_partial": True}).json()
    p = _chart(client, "1w")["partial_candle"]
    assert base["partial_included"] is False and base["times"][-1] < p["t"]
    assert inc["partial_included"] is True and inc["partial_t"] == p["t"] and inc["times"][-1] == p["t"]
    assert len(inc["times"]) == len(base["times"]) + 1
    assert inc["ema"]["20"][:-1] == base["ema"]["20"]  # completed values never change
    d1 = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1d", "asof": ASOF, "include_partial": True}).json()
    assert d1["partial_included"] is False


def test_index_chart_running_week_and_month(adv):
    client, _ = adv
    daily = client.get("/api/index/chart", params={"code": "NIFTY 50", "timeframe": "1d", "asof": ASOF}).json()
    for tf in ("1w", "1mo"):
        j = client.get("/api/index/chart", params={"code": "NIFTY 50", "timeframe": tf, "asof": ASOF}).json()
        p = j["partial_candle"]
        assert p is not None and p["is_complete"] is False and p["c"] == pytest.approx(daily["candles"][-1]["c"])
        assert all(c["is_complete"] for c in j["candles"]) and j["candles"][-1]["t"] < p["t"]
