"""VCP API: scanner rows from the daily scan, Stage Analysis context filter,
per-symbol payload with geometry (fixtures, not market data)."""

import pytest

from app.data.calendar import NSECalendar
from tests.conftest import HOLIDAYS_SRC
from tests.fixtures_adv import build_adv_app, prepare


@pytest.fixture(scope="module")
def adv(tmp_path_factory):
    cal = NSECalendar.load(HOLIDAYS_SRC)
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        s, w, d = prepare(client, app, cal)
        yield client, s, w, d


def test_vcp_scanner_lists_the_breakout_with_stage_context(adv):
    client, s, w, d = adv
    r = client.get("/api/scan/vcp").json()
    assert r["scan"]["scan_id"] == d["scan_id"] and r["stage_scan"]["scan_id"] == w["scan_id"]
    rw = [x for x in r["items"] if x["symbol"] == "RWALK"]
    assert len(rw) == 1
    v = rw[0]
    assert v["status"] == "breakout" and v["qualified"] and v["contractions"] == 3 and v["quality"] > 60
    assert v["depths_pct"][0] > v["depths_pct"][1] > v["depths_pct"][2]
    assert v["stage"] is not None and v["stage"]["stage_label"] and v["trend_context_ok"] is True
    assert "geometry" not in v and v["quality_components"] and v["gates"] and v["explanation"]


def test_vcp_stage_filter_never_changes_the_vcp(adv):
    client, s, _, _ = adv
    st = client.get("/api/scan/vcp").json()["items"][0]["stage"]["stage"]
    keep = client.get("/api/scan/vcp", params={"exclude_stages": "insufficient_history"}).json()["items"]
    drop = client.get("/api/scan/vcp", params={"exclude_stages": st}).json()["items"]
    assert not [x for x in drop if x["symbol"] == "RWALK"]
    same = [x for x in keep if x["symbol"] == "RWALK"] or [x for x in client.get("/api/scan/vcp").json()["items"] if x["symbol"] == "RWALK"]
    assert same[0]["status"] == "breakout"  # the filter hides it or not; it never alters status / score
    s2 = client.get("/api/scan/vcp", params={"stage": "stage2"}).json()["items"]
    assert all(x["stage"] and x["stage"]["stage"] in ("stage2_confirmed", "early_stage2") for x in s2)


def test_vcp_status_filters_and_validation(adv):
    client, _, _, _ = adv
    assert client.get("/api/scan/vcp", params={"status": "breakout"}).json()["count"] >= 1
    assert client.get("/api/scan/vcp", params={"status": "near_pivot"}).json()["count"] == 0
    assert client.get("/api/scan/vcp", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/scan/vcp", params={"min_quality": 99.9}).json()["count"] == 0


def test_vcp_symbol_payload_from_scan(adv):
    client, _, _, d = adv
    r = client.get("/api/vcp/symbol", params={"symbol": "RWALK"}).json()
    assert r["source"] == "scan" and r["vcp"]["status"] == "breakout"
    g = r["vcp"]["geometry"]
    assert g["schema_version"] == "vcp-1.0" and len(g["contractions"]) == 3 and g["pivot"]["p"] == r["vcp"]["pivot"]
    assert [c["label"] for c in r["vcp"]["contraction_list"]] == ["T1", "T2", "T3"]
    assert all(c["depth_pct"] > 0 and c["bars"] > 0 for c in r["vcp"]["contraction_list"])
    # geometry timestamps exist in the chart candles (the frontend never re-detects)
    chart = client.get("/api/chart", params={"symbol": "RWALK", "timeframe": "1d"}).json()
    ts = {c["t"] for c in chart["candles"]}
    assert g["pivot"]["t"] in ts and all(c["t_high"] in ts and c["t_low"] in ts for c in g["contractions"])
    none = client.get("/api/vcp/symbol", params={"symbol": "FCHAN"}).json()
    assert none["vcp"] is None or none["vcp"]["status"]
    assert client.get("/api/vcp/symbol", params={"symbol": "NOPE"}).status_code == 404


def test_vcp_evidence_published(adv):
    client, _, _, _ = adv
    ev = client.get("/api/evidence", params={"symbol": "RWALK", "source": "vcp"}).json()["items"]
    assert len(ev) == 1 and ev[0]["signal_type"] == "vcp_breakout" and ev[0]["category"] == "setup" and ev[0]["quality"] > 0
