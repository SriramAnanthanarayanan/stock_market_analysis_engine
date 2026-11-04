"""P3 API: labels, evaluation, tracks, outcomes and exports (fixture data)."""

import json

import pandas as pd

from app.synthetic import POSITIVE, pattern_window
from tests.test_api import scan_and_get

ASOF = "2026-10-30"


def candle_t(s, symbol, i):
    df = s.store.load(symbol, "1d")
    return int(pd.Timestamp(df.index[i]).timestamp())


def label_body(s, symbol="FCHAN", slug="falling_channel", **over):
    a, b = pattern_window(POSITIVE[slug])
    body = {
        "symbol": symbol, "timeframe": "1d", "pattern": slug, "verdict": "valid", "asof": ASOF,
        "formation_start_t": candle_t(s, symbol, a), "formation_end_t": candle_t(s, symbol, b),
        "upper_line": [{"t": candle_t(s, symbol, a), "p": 1100.0}, {"t": candle_t(s, symbol, b), "p": 990.0}],
        "swing_points": [{"t": candle_t(s, symbol, a + 3), "p": 1012.0, "kind": "low"}],
        "dataset": "tuning", "notes": "clean channel",
    }
    body.update(over)
    return body


def test_label_crud_validation_and_export(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    r = client.post("/api/labels", json=label_body(s))
    assert r.status_code == 201, r.text
    lab = r.json()
    assert lab["formation_start"].endswith("+05:30") and lab["upper_line"][1]["p"] == 990.0
    bad = [
        label_body(s, pattern="cup_handle"),
        label_body(s, asof="2999-01-01"),
        label_body(s, formation_start_t=label_body(s)["formation_end_t"]),
        label_body(s, formation_end_t=label_body(s)["formation_end_t"] + 1800),  # not a candle timestamp
        label_body(s, verdict="maybe"),
        {**label_body(s), "unexpected": 1},
    ]
    for body in bad:
        assert client.post("/api/labels", json=body).status_code == 422, body
    upd = client.put(f"/api/labels/{lab['label_id']}", json=label_body(s, verdict="near_miss", notes="second look"))
    assert upd.status_code == 200 and upd.json()["verdict"] == "near_miss" and upd.json()["created_at"] == lab["created_at"]
    assert len(client.get("/api/labels", params={"symbol": "fchan"}).json()) == 1
    csv_r = client.get("/api/labels/export", params={"format": "csv"})
    assert csv_r.headers["content-type"].startswith("text/csv") and "attachment" in csv_r.headers["content-disposition"]
    assert csv_r.text.splitlines()[0].startswith("label_id,symbol,timeframe,pattern,verdict")
    js = client.get("/api/labels/export", params={"format": "json"}).json()
    assert js[0]["label_id"] == lab["label_id"]
    assert client.delete(f"/api/labels/{lab['label_id']}").status_code == 204
    assert client.delete(f"/api/labels/{lab['label_id']}").status_code == 404


def test_evaluation_endpoints(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    assert client.post("/api/evaluation/run", json={}).status_code == 422  # no labels yet
    client.post("/api/labels", json=label_body(s))
    client.post("/api/labels", json=label_body(s, symbol="ATRI", slug="ascending_triangle", verdict="false_detection", upper_line=None, swing_points=None))
    run = client.post("/api/evaluation/run", json={"options": {"iou_threshold": 0.5}})
    assert run.status_code == 200, run.text
    res = run.json()
    fc = res["metrics"]["tuning"]["by_pattern"]["falling_channel"]
    assert fc["tp"] == 1 and fc["precision"] == 1.0 and fc["recall"] == 1.0
    assert res["metrics"]["tuning"]["by_pattern"]["ascending_triangle"]["fp"] >= 1
    runs = client.get("/api/evaluation/runs").json()
    assert runs[0]["run_id"] == res["run_id"] and runs[0]["kind"] == "evaluate"
    detail = client.get(f"/api/evaluation/runs/{res['run_id']}").json()
    assert detail["details"]["cases"]
    fp_csv = client.get(f"/api/evaluation/runs/{res['run_id']}/export", params={"cases": "fp"})
    assert fp_csv.status_code == 200 and all(line.startswith("FP") for line in fp_csv.text.splitlines()[1:] if line)
    cmp = client.post("/api/evaluation/compare", json={"b_overrides": {"close_trend_min_r2": 0.999}})
    assert cmp.status_code == 200 and cmp.json()["fn_added"]
    assert client.post("/api/evaluation/compare", json={"b_overrides": {"nope": 1}}).status_code == 422
    sw = client.post("/api/evaluation/sweep", json={"param": "close_trend_min_r2", "values": [0.5, 0.9]})
    assert sw.status_code == 200 and len(sw.json()["rows"]) == 2
    assert client.post("/api/evaluation/sweep", json={"param": "parallel_lengths", "values": [1]}).status_code == 422


def test_tracks_outcomes_and_detection_export(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    trs = client.get("/api/tracks").json()
    assert trs and all(t["state"] in ("active", "frozen", "closed") for t in trs)
    fchan = next(t for t in trs if t["symbol"] == "FCHAN" and t["pattern"] == "falling_channel")
    detail = client.get(f"/api/tracks/{fchan['track_id']}").json()
    assert detail["events"][0]["event"] == "first_detected" and detail["snapshots"]
    assert detail["snapshots"][0]["detection_id"] == fchan["current_detection_id"]
    # second scan of the same data continues the same tracks
    scan_and_get(client)
    again = client.get("/api/tracks", params={"symbol": "FCHAN", "pattern": "falling_channel"}).json()
    assert [t["track_id"] for t in again] == [fchan["track_id"]]
    assert len(client.get(f"/api/tracks/{fchan['track_id']}").json()["snapshots"]) == 2
    assert client.get("/api/tracks", params={"state": "weird"}).status_code == 422
    assert client.get("/api/tracks/0000").status_code == 404
    out = client.get("/api/outcomes/summary").json()
    assert "not success rates" in out["note"] and out["horizons"] == [5, 10, 20]
    csv_r = client.get("/api/export/detections", params={"universe": "all", "timeframe": "1d"})
    assert csv_r.status_code == 200 and "config_version" in csv_r.text.splitlines()[0]
    js = client.get("/api/export/detections", params={"universe": "all", "timeframe": "1d", "format": "json"}).json()
    assert js["detections"] and js["scan"]["config_version"] and all(d["track_id"] for d in js["detections"])
    json.dumps(js)
