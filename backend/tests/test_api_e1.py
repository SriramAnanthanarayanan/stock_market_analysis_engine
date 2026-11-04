"""E1 API: indicator series, chart overlays, candlestick / breakout / indicator scanners.

RWALK's daily fixture is replaced by a random walk whose last completed candle
is a bullish Marubozu closing far above the previous 10-day high, so both
scanners have a known positive. Results are fixtures, not market data."""

import numpy as np
import pandas as pd
import pytest

from app.breakouts import daily_reference
from app.config import TIMEFRAME_SOURCE_INTERVAL
from app.data.ohlcv import prepare_series
from app.synthetic import random_walk
from app.technical import indicator_series
from app.timeutil import asof_cutoff, epoch_s, parse_iso
from tests.test_api import refresh, wait_scan


def _crafted(calendar) -> pd.DataFrame:
    f = random_walk(300, calendar, seed=5).copy()
    prev = f.iloc[:-1]
    ref_high = float(prev["high"].iloc[-10:].max())
    o = float(prev["close"].iloc[-1])
    c = ref_high * 1.04
    f.iloc[-1, f.columns.get_loc("open")] = o
    f.iloc[-1, f.columns.get_loc("close")] = c
    f.iloc[-1, f.columns.get_loc("high")] = c + (c - o) * 0.01
    f.iloc[-1, f.columns.get_loc("low")] = o - (c - o) * 0.01
    f.iloc[-1, f.columns.get_loc("volume")] = float(prev["volume"].iloc[-20:].mean()) * 3
    return f


@pytest.fixture
def e1(client_env, calendar):
    client, s, provider, settings = client_env
    provider.frames[("RWALK", "1d")] = _crafted(calendar)
    st = wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
    assert st["state"] == "completed"
    return client, s, st


def test_scan_records_technical_config_and_basis(e1):
    client, s, st = e1
    from app.scanner import get_scan

    sc = get_scan(s.engine, st["scan_id"])
    assert sc["price_basis"] == s.store.price_basis and sc["tech_config_version"] == s.tech_cfg.version and sc["tech_config_hash"]


def test_candlestick_scanner_finds_marubozu_with_filters(e1):
    client, _, _ = e1
    r = client.get("/api/scan/candlesticks", params={"timeframe": "1d"}).json()
    rw = [x for x in r["items"] if x["symbol"] == "RWALK"]
    assert len(rw) == 1 and rw[0]["kind"] == "bullish_marubozu" and rw[0]["age_bars"] == 0
    assert rw[0]["body_pct"] >= 90 and rw[0]["volume_ratio"] == pytest.approx(3.0, rel=0.05) and rw[0]["company"]
    assert not [x for x in client.get("/api/scan/candlesticks", params={"kind": "bearish_marubozu"}).json()["items"] if x["symbol"] == "RWALK"]
    assert not [x for x in client.get("/api/scan/candlesticks", params={"min_range_atr": 40}).json()["items"] if x["symbol"] == "RWALK"]
    assert [x for x in client.get("/api/scan/candlesticks", params={"min_volume_ratio": 2.5}).json()["items"] if x["symbol"] == "RWALK"]
    assert client.get("/api/scan/candlesticks", params={"kind": "doji"}).status_code == 422


def test_breakout_scanner_modes_and_filters(e1):
    client, _, _ = e1
    one = client.get("/api/scan/breakouts", params={"confirm": 1}).json()
    rw = [x for x in one["items"] if x["symbol"] == "RWALK"]
    assert len(rw) == 1
    b = rw[0]
    assert b["kind"] == "breakout_high_10d" and b["status"] == "confirmed" and b["confirm_closes"] == 1
    assert b["close"] > b["prev_high"] and b["breakout_pct"] == pytest.approx(4.0, abs=0.01) and b["distance_atr"] > 0
    assert set(b) >= {"prev_low", "reference_start", "reference_end", "range_atr", "volume_ratio", "prior_level", "time"}
    two = client.get("/api/scan/breakouts", params={"confirm": 2, "status": "all"}).json()
    rw2 = [x for x in two["items"] if x["symbol"] == "RWALK" and x["kind"] == "breakout_high_10d"]
    assert rw2 and rw2[0]["status"] == "pending"  # needs a second completed close
    assert not [x for x in client.get("/api/scan/breakouts", params={"confirm": 2}).json()["items"] if x["symbol"] == "RWALK" and x["kind"] == "breakout_high_10d"]
    assert not [x for x in client.get("/api/scan/breakouts", params={"min_volume_ratio": 5}).json()["items"] if x["symbol"] == "RWALK"]
    assert all(x["kind"] == "breakdown_low_10d" for x in client.get("/api/scan/breakouts", params={"kind": "breakdown_low_10d", "status": "all", "max_age": 5}).json()["items"])
    assert [x for x in client.get("/api/scan/breakouts", params={"universe": "non_fno"}).json()["items"] if x["symbol"] == "RWALK"]  # RWALK is non-F&O
    assert not [x for x in client.get("/api/scan/breakouts", params={"universe": "fno"}).json()["items"] if x["symbol"] == "RWALK"]


def test_indicator_scanner_snapshot_and_filters(e1):
    client, _, _ = e1
    r = client.get("/api/scan/indicators", params={"timeframe": "1d"}).json()
    by = {x["symbol"]: x for x in r["items"]}
    assert {"RWALK", "FCHAN", "ATRI", "RECT"} <= set(by)
    rw = by["RWALK"]
    assert rw["alignment"] in ("bullish_full", "bearish_full", "bullish_partial", "bearish_partial", "mixed", "not_available")
    assert set(rw["ema"]) == {"10", "20", "30", "50", "100", "200"} and rw["rsi"] is not None
    same = client.get("/api/scan/indicators", params={"alignment": rw["alignment"]}).json()["items"]
    assert all(x["alignment"] == rw["alignment"] for x in same) and "RWALK" in {x["symbol"] for x in same}
    above = client.get("/api/scan/indicators", params={"price_vs_ema200": "above"}).json()["items"]
    assert all(x["price_vs_ema"]["200"] == "above" for x in above)
    assert client.get("/api/scan/indicators", params={"cross": "golden"}).status_code == 422


def test_indicator_series_match_backend_and_chart_candles(e1):
    client, s, st = e1
    ch = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    assert ch["data_source"]["price_basis"] == s.store.price_basis and ch["data_source"]["provider"] == "fixture"
    start_t = ch["candles"][0]["t"]
    ind = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1d", "scan_id": ch["scan_id"], "start_t": start_t}).json()
    assert ind["times"] == [c["t"] for c in ch["candles"]]  # identical candles and window
    frame = prepare_series(s.store.load_current("RWALK", "1d"), "RWALK", "1d", parse_iso(ind["data_cutoff"]), s.calendar).frame
    ser = indicator_series(frame, s.tech_cfg)
    first = len(frame) - len(ind["times"])
    for p in ("20", "200"):
        exp = [None if not np.isfinite(v) else round(float(v), 4) for v in ser["ema"][int(p)][first:]]
        assert ind["ema"][p] == exp
    assert ind["rsi"][-1] == round(float(ser["rsi"][-1]), 2) and ind["status"]["ema"]["200"] == "ok"
    custom = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1d", "ema": "9,21", "rsi_period": 7}).json()
    assert set(custom["ema"]) == {"9", "21"} and custom["params"]["rsi_period"] == 7


def test_insufficient_history_is_explicit(e1):
    client, _, _ = e1
    r = client.get("/api/indicators", params={"symbol": "RWALK", "timeframe": "1d", "ema": "400"}).json()
    assert r["status"]["ema"]["400"] == "insufficient_history" and r["latest"]["ema"]["400"] is None  # never zero-filled


def test_technical_overlays_daily_and_intraday_levels(e1):
    client, s, _ = e1
    d = client.get("/api/technical", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    assert any(m["kind"] == "bullish_marubozu" and m["age_bars"] == 0 for m in d["marubozu"])
    assert any(b["kind"] == "breakout_high_10d" and b["age_bars"] == 0 for b in d["daily"]["breakouts"])
    # 1H chart: levels come from completed DAILY candles, never from 10 hourly bars.
    h = client.get("/api/technical", params={"symbol": "FCHAN", "timeframe": "1h"}).json()
    lv = h["daily"]["levels"]["10"]
    daily = prepare_series(s.store.load_current("FCHAN", "1d"), "FCHAN", "1d", parse_iso(h["data_cutoff"]), s.calendar).frame
    ref = daily_reference(daily, 10)
    assert lv["high"] == ref["high"] and lv["low"] == ref["low"] and lv["basis"] == "daily"
    hourly = prepare_series(s.store.load_current("FCHAN", "1h"), "FCHAN", "1h", parse_iso(h["data_cutoff"]), s.calendar).frame
    assert lv["high"] != float(hourly["high"].iloc[-10:].max()) or lv["reference_start"] != hourly.index[-10].isoformat()
    assert h["daily"]["breakouts"] == [] and h["daily"]["latest_daily_candle"].startswith(str(daily.index[-1].date()))


def test_parameter_validation(e1):
    client, _, _ = e1
    assert client.get("/api/indicators", params={"symbol": "RWALK", "ema": "abc"}).status_code == 422
    assert client.get("/api/indicators", params={"symbol": "RWALK", "rsi_period": 1}).status_code == 422
    assert client.get("/api/indicators", params={"symbol": "NOSUCH"}).status_code == 404
    assert client.get("/api/technical", params={"symbol": "RWALK", "confirm": 3}).status_code == 422
    assert client.get("/api/indicators", params={"symbol": "RWALK", "asof": "2999-01-01"}).status_code == 422
    cfg = client.get("/api/config/technical").json()
    names = {p["name"] for p in cfg["parameters"]}
    assert {"marubozu_min_body_ratio", "breakout_buffer_atr", "rsi_period", "div_swing_k"} <= names and all(p["description"] for p in cfg["parameters"])


def test_scanners_before_any_scan(client_env):
    client = client_env[0]
    r = client.get("/api/scan/breakouts").json()
    assert r["count"] == 0 and r["scan"] is None and "No completed scan" in r["note"]
