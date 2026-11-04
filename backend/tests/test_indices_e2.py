"""E2: index registry, official index data, weekly bars, returns, RRG, API.
Deterministic fixtures (tests/fixtures_e2.py) - not market data."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_INDEX_CONFIG as CFG, IndexConfig
from app.indicators.technical import ema
from app.indices.analysis import calendar_return, relative_performance
from app.indices.data import IndexDataError, parse_history, resample_weekly, verify_against_reference, weekly_from_daily
from app.indices.registry import classify, parse_catalogue
from app.rrg import heading, quadrant, rrg_entry, rrg_series
from app.timeutil import IST
from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings
from tests.fixtures_e2 import FakeHistory, catalogue


@pytest.fixture(scope="module")
def idx_env(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from app.main import create_app
    from tests.conftest import HOLIDAYS_SRC

    calendar = NSECalendar.load(HOLIDAYS_SRC)
    settings = make_settings(tmp_path_factory.mktemp("e2"))
    hist = FakeHistory(calendar)
    app = create_app(settings, provider=FixtureProvider(fixture_frames(calendar)), background=False, index_history=hist)
    with TestClient(app) as client:
        s = app.state.services
        s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        s.indices.refresh_catalogue(catalogue(calendar, mismatch="NIFTY METAL"), min_rows=5)
        s.indices.refresh_all()
        yield client, s, hist


# ---------------------------------------------------------------- registry
def test_catalogue_classification_rules():
    assert classify("INDICES ELIGIBLE IN DERIVATIVES", "NIFTY BANK", "NIFTY BANK")[:1] == ("sector",)
    assert classify("INDICES ELIGIBLE IN DERIVATIVES", "NIFTY 50", "NIFTY 50")[:1] == ("broad",)
    assert classify("STRATEGY INDICES", "NIFTY200 MOMENTUM 30", "NIFTY200MOMENTM30") == ("strategy", "momentum", "equity")
    assert classify("STRATEGY INDICES", "NIFTY ALPHA LOW-VOLATILITY 30", "NIFTY ALPHALOWVOL")[1] == "multi_factor"
    assert classify("STRATEGY INDICES", "NIFTY100 EQUAL WEIGHT", "NIFTY100 EQL WGT")[1] == "equal_weight"
    assert classify("STRATEGY INDICES", "NIFTY50 TR 2X LEVERAGE", "NIFTY50 TR 2X LEV")[2] == "leveraged"
    assert classify("BROAD MARKET INDICES", "INDIA VIX", "INDIA VIX")[2] == "volatility"
    assert classify("FIXED INCOME INDICES", "NIFTY 10 YR BENCHMARK G-SEC", "NIFTY GS 10YR")[0] == "fixed_income"
    assert classify("BROAD MARKET INDICES", "NIFTY SMALLCAP 250", "NIFTY SMLCAP 250")[1] == "smallcap"


def test_catalogue_validation_and_reference_closes(calendar):
    with pytest.raises(Exception):
        parse_catalogue({"data": []})
    items = parse_catalogue(catalogue(calendar), min_rows=5)
    by = {i["code"]: i for i in items}
    assert by["NIFTY200MOMENTM30"]["name"] == "NIFTY200 MOMENTUM 30"  # official name + official symbol kept
    assert by["NIFTY 50"]["reference"]["previous_day"]["close"] > 0
    assert by["INDIA VIX"]["analysis_enabled"] == 0 and by["NIFTY GS 10YR"]["analysis_enabled"] == 0


def test_registry_never_deletes_and_marks_inactive(idx_env, calendar):
    _, s, _ = idx_env
    payload = catalogue(calendar)
    payload["data"] = [d for d in payload["data"] if d["index"] != "NIFTY METAL"]
    res = s.indices.refresh_catalogue(payload, min_rows=5)
    assert res["inactive"] == 1
    metal = s.indices.registry.get("NIFTY METAL")
    assert metal is not None and metal["active"] == 0
    s.indices.refresh_catalogue(catalogue(calendar, mismatch="NIFTY METAL"), min_rows=5)  # restore (module fixture)
    assert s.indices.registry.get("NIFTY METAL")["active"] == 1


# ---------------------------------------------------------------- data
def test_parse_history_timestamps_and_zero_ohlc():
    df = parse_history([
        {"EOD_TIMESTAMP": "30-OCT-2026", "EOD_OPEN_INDEX_VAL": 0, "EOD_HIGH_INDEX_VAL": 0, "EOD_LOW_INDEX_VAL": 0, "EOD_CLOSE_INDEX_VAL": 1234.5, "HIT_TRADED_QTY": None},
        {"EOD_TIMESTAMP": "29-OCT-2026", "EOD_OPEN_INDEX_VAL": 10, "EOD_HIGH_INDEX_VAL": 12, "EOD_LOW_INDEX_VAL": 9, "EOD_CLOSE_INDEX_VAL": 11, "HIT_TRADED_QTY": 5},
    ])
    assert str(df.index[0]) == "2026-10-29 09:15:00+05:30" and df.index.is_monotonic_increasing
    last = df.iloc[-1]
    assert last["open"] == last["high"] == last["low"] == last["close"] == 1234.5  # no fabricated range
    assert np.isnan(last["volume"]) and last["adj_close"] == 1234.5


def test_history_requests_are_split_into_years(calendar):
    from app.indices.data import NseIndexHistory

    h = NseIndexHistory(make_settings_stub())
    calls = []
    h._get = lambda name, a, b: calls.append((a, b)) or []
    h.fetch("NIFTY 50", date(2016, 1, 1), date(2026, 11, 3))
    assert len(calls) == 11 and all((b - a).days <= 364 for a, b in calls)
    assert calls[0][0] == date(2016, 1, 1) and calls[-1][1] == date(2026, 11, 3)
    assert all(calls[i + 1][0] == calls[i][1] + timedelta(days=1) for i in range(len(calls) - 1))


def make_settings_stub():
    class S:
        download_max_attempts = 1
        download_backoff_base_s = 0.0
        index_request_delay_s = 0.0
        index_history_url = "x"
        index_history_page = "x"

    return S()


def test_official_reference_verification(idx_env):
    _, s, _ = idx_env
    ok = s.indices.registry.get("NIFTY IT")
    assert ok["data_status"] == "ok"
    import json

    ver = json.loads(ok["data_verified"])
    assert ver["status"] == "verified" and ver["compared"] == 4
    bad = s.indices.registry.get("NIFTY METAL")
    assert bad["data_status"] == "error" and "reference closes" in bad["data_status_reason"]


def test_incremental_refresh_after_full_download(idx_env):
    _, s, hist = idx_env
    first = [c for c in hist.calls if c[0] == "NIFTY IT"]
    assert first and (max(c[2] for c in first) - min(c[1] for c in first)).days >= 365 * 9  # 10-year full download
    hist.calls.clear()
    mode = s.indices.refresh_one(s.indices.registry.get("NIFTY IT"), force=False, now=datetime.combine(date(2030, 1, 1), time(12), tzinfo=IST))
    assert mode == "incremental" and len(hist.calls) == 1


def test_weekly_bars_complete_weeks_only(idx_env, calendar):
    _, s, _ = idx_env
    daily = s.indices.frame("NIFTY 50", "1d", datetime.now(IST))
    w = resample_weekly(daily)
    wk = w.index[-3]
    days = daily[(daily.index >= wk) & (daily.index < wk + timedelta(days=7))]
    row = w.loc[wk]
    assert row["open"] == days["open"].iloc[0] and row["close"] == days["close"].iloc[-1]
    assert row["high"] == days["high"].max() and row["low"] == days["low"].min()
    assert wk.weekday() == 0 and wk.hour == 9 and wk.minute == 15
    # Mid-week cutoff: the running week is excluded, earlier weeks unchanged.
    last_week = w.index[-1]
    mid = datetime.combine((last_week + timedelta(days=2)).date(), time(12), tzinfo=IST)
    part = weekly_from_daily(daily[daily.index < mid], mid, calendar)
    assert part.index[-1] < last_week
    pd.testing.assert_frame_equal(part, w.loc[part.index])
    # The cached service path gives the same weekly bars as resampling truncated daily data.
    svc_w = s.indices.frame("NIFTY 50", "1w", mid)
    pd.testing.assert_frame_equal(svc_w[["open", "high", "low", "close"]], part[["open", "high", "low", "close"]])
    svc_d = s.indices.frame("NIFTY 50", "1d", mid)
    completed = daily[daily.index + pd.Timedelta(hours=6, minutes=15) <= pd.Timestamp(mid)]  # daily bar ends 15:30 IST
    assert svc_d.index[-1] < pd.Timestamp(mid).normalize() and len(svc_d) == len(completed)  # the running day is excluded


def test_unavailable_intraday_is_explicit(idx_env):
    _, s, _ = idx_env
    with pytest.raises(IndexDataError, match="intraday"):
        s.indices.frame("NIFTY 50", "1h", datetime.now(IST))


def test_intraday_mapping_requires_official_match(idx_env, calendar):
    _, s, _ = idx_env
    from app.providers.base import FetchResult

    daily = s.indices.store.load(index_key("NIFTY 50"), "1d")

    class Prov:
        def fetch(self, symbols, interval, period=None, start=None):
            r = FetchResult()
            for tk in symbols:
                if tk == "^GOOD":
                    r.data[tk] = daily.copy()
                elif tk == "^BAD":
                    r.data[tk] = daily.assign(close=daily["close"] * 1.02)
            return r

    s.indices.candidates = lambda: {"NIFTY 50": ["^BAD", "^GOOD"], "NIFTY IT": ["^BAD"]}
    res = s.indices.verify_intraday(Prov())
    assert res["NIFTY 50"]["status"] == "verified" and res["NIFTY 50"]["ticker"] == "^GOOD"
    assert res["NIFTY IT"]["status"] == "failed"
    assert s.indices.registry.get("NIFTY IT")["intraday_ticker"] is None  # never substituted


def index_key(code):
    from app.indices.data import index_key as k

    return k(code)


# ---------------------------------------------------------------- returns
def test_calendar_returns_and_relative_performance():
    idx = pd.date_range("2025-01-01 09:15", periods=400, freq="D", tz=IST)
    a = pd.DataFrame({"close": np.linspace(100, 200, 400)}, index=idx)
    r = calendar_return(a, "1M")
    base = a[a.index <= a.index[-1] - pd.DateOffset(months=1)]["close"].iloc[-1]
    assert r == pytest.approx((200 / base - 1) * 100, abs=1e-3)
    assert calendar_return(a.iloc[:20], "12M") is None  # history too short: no value
    b = pd.DataFrame({"close": np.linspace(100, 150, 400)}, index=idx)
    rp = relative_performance(a, b, "3M")
    ra, rb = calendar_return(a, "3M"), calendar_return(b, "3M")
    assert rp == pytest.approx(((1 + ra / 100) / (1 + rb / 100) - 1) * 100, abs=1e-3)


# ---------------------------------------------------------------- RRG
def _weekly(values, start="2024-01-01"):
    idx = pd.date_range(start + " 09:15", periods=len(values), freq="7D", tz=IST)
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_rrg_formulas_match_definition():
    rng = np.random.default_rng(3)
    b = _weekly(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))))
    i = _weekly(500 * np.exp(np.cumsum(rng.normal(0.002, 0.025, 120))))
    status, df, info = rrg_series(i, b, CFG)
    assert status == "ok" and info["weeks_aligned"] == 120
    rs = (i / b).to_numpy()
    ratio = 100 * rs / ema(rs, 14)
    mom = 100 * ratio / ema(ratio, 14)
    np.testing.assert_allclose(df["ratio"].to_numpy(), ratio[~np.isnan(mom)], rtol=1e-12)
    np.testing.assert_allclose(df["momentum"].to_numpy(), mom[~np.isnan(mom)], rtol=1e-12)
    assert len(df) == 120 - 13 - 13  # warm-up: two SMA-seeded EMAs of 14


def test_quadrants_heading_and_boundaries():
    assert quadrant(101, 101) == "leading" and quadrant(101, 99) == "weakening"
    assert quadrant(99, 99) == "lagging" and quadrant(99, 101) == "improving"
    assert quadrant(100, 101) == "improving" and quadrant(101, 100) == "weakening"  # 100 belongs to the weak side
    assert heading(1, 1)[1] == "up-right" and heading(-1, 0)[1] == "left" and heading(0, 0)[1] == "unchanged"


def test_rrg_weekly_alignment_and_missing_data():
    b = _weekly(np.linspace(1000, 1200, 80))
    i = _weekly(np.linspace(500, 700, 80))
    i_shift = i.copy()
    i_shift.index = i_shift.index + pd.Timedelta(days=1)  # different week labels: nothing aligns
    st, _, info = rrg_series(i_shift, b, CFG)
    assert st == "data_gap" and info["weeks_aligned"] == 0
    st, _, _ = rrg_series(i, pd.Series(dtype=float), CFG)
    assert st == "data_unavailable"  # missing benchmark data
    gappy = i.drop(i.index[40:43])  # 3 consecutive weeks missing
    st, _, info = rrg_series(gappy, b, CFG)
    assert st == "data_gap" and info["max_gap_weeks"] == 3
    one = i.drop(i.index[40:41])  # one missing week is allowed (not filled, just skipped)
    st, df, info = rrg_series(one, b, CFG)
    assert st == "ok" and i.index[40] not in df.index
    st, _, _ = rrg_series(i.iloc[-20:], b, CFG)
    assert st == "insufficient_history"


def test_rrg_trail_order_length_and_asof():
    rng = np.random.default_rng(9)
    b = _weekly(1000 * np.exp(np.cumsum(rng.normal(0, 0.02, 150))))
    i = _weekly(500 * np.exp(np.cumsum(rng.normal(0, 0.03, 150))))
    e = rrg_entry("X", i, b, CFG, 12)
    weeks = [p["t"] for p in e["trail"]]
    assert len(weeks) == 12 and weeks == sorted(weeks) and e["latest"]["t"] == weeks[-1]
    assert e["latest"]["quadrant"] == quadrant(e["trail"][-1]["ratio"], e["trail"][-1]["momentum"])
    # As of 10 weeks earlier: the trail ends 10 weeks earlier, earlier values unchanged (no look-ahead).
    e2 = rrg_entry("X", i.iloc[:-10], b.iloc[:-10], CFG, 12)
    assert e2["latest"]["t"] == weeks[-1] - 10 * 7 * 86400
    full = {p["t"]: p for p in rrg_entry("X", i, b, CFG, 40)["trail"]}
    for p in e2["trail"]:
        assert full[p["t"]]["ratio"] == p["ratio"] and full[p["t"]]["momentum"] == p["momentum"]


# ---------------------------------------------------------------- API
def test_index_registry_api(idx_env):
    client, _, _ = idx_env
    r = client.get("/api/indices").json()
    assert r["count"] == 11 and r["counts"]["sector"]["total"] == 3  # BANK (derivatives group) + IT + METAL
    assert {x["code"] for x in client.get("/api/indices", params={"category": "strategy"}).json()["items"]} >= {"NIFTY200MOMENTM30"}
    assert client.get("/api/indices", params={"category": "nonsense"}).status_code == 422
    d = client.get("/api/indices/NIFTY IT").json()
    assert d["name"] == "NIFTY IT" and d["data_status"] == "ok" and d["intraday_status"] in (None, "unavailable", "failed")
    assert client.get("/api/indices/NOT AN INDEX").status_code == 404


def test_index_chart_and_indicators_for_indices(idx_env):
    client, _, _ = idx_env
    ch = client.get("/api/index/chart", params={"code": "NIFTY IT", "timeframe": "1d"}).json()
    assert ch["candles"] and ch["data_source"]["price_basis"] == "index_price_return" and "official" in ch["data_source"]["provider"]
    wk = client.get("/api/index/chart", params={"code": "NIFTY IT", "timeframe": "1w"}).json()
    assert all(pd.Timestamp(c["t"], unit="s", tz="UTC").tz_convert(IST).weekday() == 0 for c in wk["candles"])
    h = client.get("/api/index/chart", params={"code": "NIFTY IT", "timeframe": "1h"})
    assert h.status_code == 404 and h.json()["detail"]["status"] == "data_unavailable"  # never manufactured
    na = client.get("/api/index/chart", params={"code": "INDIA VIX", "timeframe": "1d"})
    assert na.status_code == 422 and na.json()["detail"]["status"] == "not_applicable"
    ind = client.get("/api/indicators", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1d", "start_t": ch["candles"][0]["t"]}).json()
    assert ind["times"] == [c["t"] for c in ch["candles"]] and ind["instrument"] == "index" and ind["price_basis"] == "index_price_return"
    tech = client.get("/api/technical", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1w"}).json()
    assert tech["daily"]["levels"]["10"]["basis"] == "daily"
    found = client.get("/api/symbols", params={"q": "nifty it"}).json()["items"]
    assert any(x["kind"] == "index" and x["symbol"] == "NIFTY IT" for x in found)


def test_rrg_api_benchmarks_and_persistence(idx_env):
    client, s, _ = idx_env
    r = client.get("/api/rrg").json()
    assert r["benchmark"] == "NIFTY 500" and "not the proprietary JdK" in r["method"]
    by = {e["code"]: e for e in r["items"]}
    assert "NIFTY 500" not in by  # the benchmark is not plotted against itself
    assert by["NIFTY IT"]["status"] == "ok" and len(by["NIFTY IT"]["trail"]) == CFG.rrg_trail_weeks
    assert by["NIFTY GAPPY"]["status"] == "data_gap"
    assert by["NIFTY IND DEFENCE"]["status"] in ("ok", "insufficient_history")
    r50 = client.get("/api/rrg", params={"benchmark": "NIFTY 50", "trail": 4}).json()
    it50 = {e["code"]: e for e in r50["items"]}["NIFTY IT"]
    assert len(it50["trail"]) == 4 and it50["latest"]["ratio"] != by["NIFTY IT"]["latest"]["ratio"]  # benchmark switch changes RS
    assert client.get("/api/rrg", params={"benchmark": "NIFTY IT"}).status_code == 422
    old = client.get("/api/rrg", params={"asof": "2026-06-30"}).json()
    assert old["benchmark_last_week"] < r["benchmark_last_week"]
    from sqlalchemy import func, select

    from app import database as db

    with s.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.rrg_runs)).scalar() >= 3
    cat = client.get("/api/rrg", params={"category": "sector"}).json()
    assert cat["items"] and all(e["category"] == "sector" for e in cat["items"])


def test_index_overview_api(idx_env):
    client, _, _ = idx_env
    r = client.get("/api/indices/overview").json()
    by = {x["code"]: x for x in r["items"]}
    it = by["NIFTY IT"]
    assert set(it["returns"]) == {"1W", "1M", "3M", "6M", "1Y"} and it["rsi"] is not None and it["ema200"] in ("above", "below")
    assert it["rs_vs_benchmark"] is not None and it["rrg_quadrant"] in ("leading", "weakening", "lagging", "improving")
    assert by["NIFTY IND DEFENCE"]["returns"]["1Y"] is None  # launched < 1 year ago: no value invented
    assert by["INDIA VIX"]["analysis_enabled"] is False and "returns" not in by["INDIA VIX"]
    wk = client.get("/api/indices/overview", params={"timeframe": "1w"}).json()
    assert {x["code"]: x for x in wk["items"]}["NIFTY IT"]["rsi"] != it["rsi"]
