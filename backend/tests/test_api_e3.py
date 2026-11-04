"""E3 API: Stage Analysis watchlist / detail and RS Line (fixtures, not market data)."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings
from tests.fixtures_e2 import FakeHistory, catalogue
from tests.test_api import refresh, wait_scan


@pytest.fixture(scope="module")
def e3(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from app.main import create_app
    from tests.conftest import HOLIDAYS_SRC

    cal = NSECalendar.load(HOLIDAYS_SRC)
    settings = make_settings(tmp_path_factory.mktemp("e3"))
    app = create_app(settings, provider=FixtureProvider(fixture_frames(cal)), background=False, index_history=FakeHistory(cal))
    with TestClient(app) as client:
        s = app.state.services
        s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        s.indices.refresh_catalogue(catalogue(cal), min_rows=5)
        s.indices.refresh_all()
        st = wait_scan(client, refresh(client, timeframe="1w")["scan_id"])
        assert st["state"] == "completed"
        yield client, s, st


def test_weekly_scan_persists_stage_results(e3):
    client, s, st = e3
    from app.scanner import get_scan

    sc = get_scan(s.engine, st["scan_id"])
    assert sc["stage_config_version"] == s.stage_cfg.version and sc["stage_config_hash"]
    r = client.get("/api/stage", params={"universe": "all"}).json()
    assert r["scan"]["scan_id"] == st["scan_id"] and r["summary"]["total"] == len(r["items"]) >= 4
    assert all(x["stage"] in ("stage1", "early_stage2", "stage2_confirmed", "other", "insufficient_history", "data_unavailable") for x in r["items"])
    assert [x["rank"] for x in r["items"]] == sorted(x["rank"] for x in r["items"])
    params = {p["name"]: p for p in r["config"]["parameters"]}
    assert params["max_base_range_pct"]["basis"] == "source" and params["prior_decline_min_pct"]["basis"] == "assumption"
    assert r["config"]["sources"] == ["stage_analysis.txt", "rs_line.txt"]


def test_stage_requires_a_weekly_scan(e3):
    client, _, _ = e3
    r = client.get("/api/stage", params={"universe": "all", "asof": "2025-01-10"}).json()
    assert r["count"] == 0 and "1W" in r["note"]


def test_index_stage_table_filters_and_detail(e3):
    client, _, _ = e3
    r = client.get("/api/stage", params={"instrument": "index"}).json()
    by = {x["symbol"]: x for x in r["items"]}
    assert "NIFTY IT" in by and by["NIFTY IT"]["stage"] != "data_unavailable"
    assert by["NIFTY IT"]["metrics"]["rs_length"] == 52 and by["NIFTY IT"]["metrics"]["ema30"] is not None
    for stage in ("other", "stage1"):
        f = client.get("/api/stage", params={"instrument": "index", "stage": stage}).json()
        assert all(x["stage"] == stage for x in f["items"])
    above = client.get("/api/stage", params={"instrument": "index", "rs": "above_zero"}).json()["items"]
    assert all(x["metrics"]["rs_zero_line"] == "above" for x in above)
    d = client.get("/api/stage/symbol", params={"symbol": "NIFTY IT", "instrument": "index"}).json()
    assert d["stage"] == by["NIFTY IT"]["stage"] and d["gates"] and d["reasons"]
    assert len(d["overlay"]["times"]) == len(d["overlay"]["ema30"]) and d["overlay"]["base"]["high"] is not None
    ov = client.get("/api/indices/overview").json()
    assert {x["code"]: x for x in ov["items"]}["NIFTY IT"]["stage"] == d["stage"]  # Index Analysis shows the same stage


def test_rsline_endpoint(e3):
    client, s, _ = e3
    w = client.get("/api/rsline", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1w"}).json()
    assert w["length"] == 52 and w["benchmark"] == "NIFTY 50" and w["status"] == "ok"
    assert len(w["times"]) == len(w["rs"]) == len(w["rs_sma"]) and w["reference"]["label"].startswith("RS-52 reference")
    d = client.get("/api/rsline", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1d"}).json()
    assert d["length"] == 63
    # Same values as the literal formula on the same candles.
    it = s.indices.frame("NIFTY IT", "1d", __import__("app.timeutil", fromlist=["now_ist"]).now_ist())["close"]
    nb = s.indices.frame("NIFTY 50", "1d", __import__("app.timeutil", fromlist=["now_ist"]).now_ist())["close"]
    lit = ((it / it.shift(63)) / (nb / nb.shift(63)) - 1).to_numpy()
    got = np.array([np.nan if x is None else x for x in d["rs"]])
    np.testing.assert_allclose(got[-50:], lit[-50:], atol=6e-7)  # API rounds RS to 6 decimals
    other = client.get("/api/rsline", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1w", "benchmark": "NIFTY 500"}).json()
    assert other["notes"] and other["rs"][-1] != w["rs"][-1]  # documented effect of changing the benchmark
    stock = client.get("/api/rsline", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    assert stock["instrument"] == "stock" and stock["length"] == 63 and stock["alignment"]["exact"] > 0
    assert client.get("/api/rsline", params={"symbol": "NIFTY IT", "instrument": "index", "timeframe": "1h"}).status_code == 404  # no verified intraday


def test_stage_symbol_validation(e3):
    client, _, _ = e3
    assert client.get("/api/stage/symbol", params={"symbol": "NOSUCH"}).status_code == 404
    assert client.get("/api/stage", params={"stage": "stage5"}).status_code == 422
    assert client.get("/api/rsline", params={"symbol": "RWALK", "benchmark": "NIFTY IT"}).status_code == 422
