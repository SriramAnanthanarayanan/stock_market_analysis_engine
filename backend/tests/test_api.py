"""API tests against the deterministic fixture provider (results are fixtures, not market data)."""

import threading
import time
from datetime import date, datetime, time as dtime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import database as db
from app.main import create_app
from app.models import ChartResponse, Geometry
from app.timeutil import IST
from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings


def refresh(client, **body):
    r = client.post("/api/scan/refresh", json={"universe": "all", "timeframe": "1d", **body})
    assert r.status_code == 200, r.text
    return r.json()


def wait_scan(client, scan_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/scan/status/{scan_id}").json()
        if st["state"] not in ("queued", "running"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"scan {scan_id} did not finish")


def scan_and_get(client, **params):
    st = wait_scan(client, refresh(client, **{k: v for k, v in params.items() if k in ("timeframe", "asof", "universe")})["scan_id"])
    q = {"universe": params.get("universe", "all"), "timeframe": params.get("timeframe", "1d")}
    if "asof" in params:
        q["asof"] = params["asof"]
    return st, client.get("/api/scan", params=q).json()


def test_health_reports_components(client_env):
    client, s, _, _ = client_env
    h = client.get("/api/health").json()
    assert h["database"]["status"] == "ok" and h["database"]["schema_version"] >= 1
    assert h["universe"]["nifty500"]["last_success_rows"] == 5 and h["universe"]["fno"]["snapshots"] == 1
    assert h["app"]["config_hash"] == s.config_hash and h["app"]["phase"] == "P1"
    for key in ("market_data", "last_scan", "scheduler", "active_scans", "calendar"):
        assert key in h
    assert h["status"] in ("ok", "degraded")


def test_scan_runs_persists_and_records_config(client_env):
    client, s, _, _ = client_env
    st, res = scan_and_get(client)
    assert st["state"] == "completed" and st["total_symbols"] == 5
    assert st["failed_symbols"] == 1 and st["successful_symbols"] == 4  # FAIL1: partial download success
    assert res["scan"]["scan_id"] == st["scan_id"] and res["scan"]["config_version"] == s.cfg.version
    by_symbol: dict[str, set[str]] = {}
    for m in res["matches"]:
        by_symbol.setdefault(m["symbol"], set()).add(m["pattern"])
    assert "falling_channel" in by_symbol["FCHAN"]
    assert "ascending_triangle" in by_symbol["ATRI"]  # a flat top can also be a valid Double Top (groups coexist)
    assert "rectangle" in by_symbol["RECT"]
    assert all(m["config_hash"] == s.config_hash for m in res["matches"])
    q = [m["quality"] for m in res["matches"]]
    assert q == sorted(q, reverse=True)
    assert res["symbol_counts"] == {"ok": 4, "no_data": 1}
    with s.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.config_versions).where(db.config_versions.c.config_hash == s.config_hash)).scalar() == 1
        assert conn.execute(select(func.count()).select_from(db.download_errors).where(db.download_errors.c.symbol == "FAIL1")).scalar() >= 1
        assert conn.execute(select(func.count()).select_from(db.pattern_geometry)).scalar() == conn.execute(select(func.count()).select_from(db.detections)).scalar()


def test_universe_segments_and_filters(client_env):
    client, _, _, _ = client_env
    scan_and_get(client)
    get = lambda **p: client.get("/api/scan", params={"timeframe": "1d", **p}).json()  # noqa: E731
    assert {m["symbol"] for m in get(universe="fno")["matches"]} <= {"FCHAN", "ATRI"}
    assert all(m["fno_status"] == "YES" for m in get(universe="fno")["matches"])
    assert all(m["fno_status"] == "NO" for m in get(universe="non_fno")["matches"])
    assert {m["pattern"] for m in get(universe="all", pattern="rectangle")["matches"]} == {"rectangle"}
    assert {m["category"] for m in get(universe="all", category="triangles_wedges")["matches"]} <= {"triangles_wedges"}
    assert all(m["quality"] >= 90 for m in get(universe="all", min_quality=90)["matches"])
    statuses = {m["status"] for m in get(universe="all", status="validated,near_upper,near_lower")["matches"]}
    assert statuses <= {"validated", "near_upper", "near_lower"}
    flagged = get(universe="all", pattern="bullish_flag,double_top")
    assert {m["pattern"] for m in flagged["matches"]} <= {"bullish_flag", "double_top"}


@pytest.mark.parametrize(
    "params",
    [
        {"timeframe": "2h"},
        {"universe": "nasdaq"},
        {"min_quality": 101},
        {"pattern": "cup_and_handle"},
        {"status": "buy"},
        {"asof": "2999-01-01"},
        {"asof": "not-a-date"},
    ],
)
def test_invalid_scan_parameters_are_422(client_env, params):
    client, _, _, _ = client_env
    r = client.get("/api/scan", params=params)
    assert r.status_code == 422
    assert "Traceback" not in r.text


def test_invalid_refresh_and_chart_requests(client_env):
    client, _, _, _ = client_env
    assert client.post("/api/scan/refresh", json={"timeframe": "1d", "patterns": ["cup_and_handle"]}).status_code == 422
    assert client.post("/api/scan/refresh", json={"timeframe": "1d", "category": "harmonics"}).status_code == 422
    assert client.post("/api/scan/refresh", json={"timeframe": "1d", "unexpected": 1}).status_code == 422
    assert client.get("/api/chart", params={"symbol": "BAD SYMBOL!", "timeframe": "1d"}).status_code == 422
    assert client.get("/api/chart", params={"symbol": "NOPE", "timeframe": "1d"}).status_code == 404
    assert client.get("/api/history", params={"date_from": "2026-10-10", "date_to": "2026-10-01"}).status_code == 422


def test_scan_locking_prevents_identical_overlap(client_env):
    client, _, provider, _ = client_env
    gate = threading.Event()
    original = provider.fetch

    def slow(*a, **k):
        gate.wait(10)
        return original(*a, **k)

    provider.fetch = slow
    a = refresh(client)
    b = refresh(client)
    other = refresh(client, timeframe="1w")
    assert a["created"] and not b["created"] and a["scan_id"] == b["scan_id"]
    assert other["scan_id"] != a["scan_id"]
    gate.set()
    wait_scan(client, a["scan_id"])
    wait_scan(client, other["scan_id"])
    again = refresh(client)
    assert again["created"] and again["scan_id"] != a["scan_id"]
    wait_scan(client, again["scan_id"])


def _candles_by_index(chart):
    return {c["i"]: c for c in chart["candles"]}


def test_chart_geometry_aligns_with_candles(client_env):
    client, _, _, _ = client_env
    _, res = scan_and_get(client)
    m = next(x for x in res["matches"] if x["symbol"] == "FCHAN")
    r = client.get("/api/chart", params={"symbol": "FCHAN", "timeframe": "1d", "pattern_id": m["pattern_id"]})
    assert r.status_code == 200, r.text
    chart = ChartResponse.model_validate(r.json()).model_dump()
    assert chart["source"] == "scan" and chart["selected_detection_id"] == m["pattern_id"]
    by_i = _candles_by_index(chart)
    det = next(d for d in chart["detections"] if d["pattern_id"] == m["pattern_id"])
    assert len(chart["candles"]) >= min(max(det["formation_length"] + 60, 160), len(by_i))
    geo = det["geometry"]
    assert geo["formation"]["start"]["i"] in by_i
    for el in geo["elements"]:
        pts = el.get("points") or (el.get("upper", []) + el.get("lower", [])) or [el]
        for p in pts:
            if not p["projected"]:
                assert by_i[p["i"]]["t"] == p["t"]
        if el["type"] == "marker" and el["role"] == "touch_upper":
            assert el["p"] == pytest.approx(by_i[el["i"]]["h"], abs=1e-3)
        if el["type"] == "marker" and el["role"] == "touch_lower":
            assert el["p"] == pytest.approx(by_i[el["i"]]["l"], abs=1e-3)
    assert det["lifecycle"] and det["lifecycle"][0]["event"] == "formation_identified"
    assert det["quality_components"] and det["gates"] and det["explanation"]
    assert isinstance(chart["sr_zones"], list) and "support/resistance" in chart["sr_zones_note"]


def test_chart_future_bars_give_projections_their_bar_spacing(client_env, calendar):
    client, _, _, _ = client_env
    _, res = scan_and_get(client)
    pid = next(x["pattern_id"] for x in res["matches"] if x["symbol"] == "ATRI")
    chart = client.get("/api/chart", params={"symbol": "ATRI", "timeframe": "1d", "pattern_id": pid}).json()
    last_i = chart["candles"][-1]["i"]
    fut = chart["future_bars"]
    projected = [p for d in chart["detections"] for el in d["geometry"]["elements"] if el["type"] == "line" for p in el["points"] if p["projected"]]
    assert projected and fut
    assert [f["i"] for f in fut] == list(range(last_i + 1, max(p["i"] for p in projected) + 1))  # no gaps
    by_i = {f["i"]: f["t"] for f in fut}
    assert all(by_i[p["i"]] == p["t"] for p in projected)
    import pandas as pd

    assert all(calendar.is_trading_day(pd.Timestamp(f["t"], unit="s", tz="UTC").tz_convert("Asia/Kolkata").date()) for f in fut)


def test_chart_pattern_id_must_match_symbol_and_timeframe(client_env):
    client, _, _, _ = client_env
    _, res = scan_and_get(client)
    pid = next(x["pattern_id"] for x in res["matches"] if x["symbol"] == "FCHAN")
    assert client.get("/api/chart", params={"symbol": "ATRI", "timeframe": "1d", "pattern_id": pid}).status_code == 404
    assert client.get("/api/chart", params={"symbol": "FCHAN", "timeframe": "1w", "pattern_id": pid}).status_code == 404
    assert client.get("/api/chart", params={"symbol": "FCHAN", "timeframe": "1d", "pattern_id": "0123456789abcdef"}).status_code == 404


def test_chart_adhoc_when_no_scan_and_timeframe_switch(client_env):
    client, _, _, _ = client_env
    d = client.get("/api/chart", params={"symbol": "RECT", "timeframe": "1d"}).json()
    h = client.get("/api/chart", params={"symbol": "RECT", "timeframe": "1h"}).json()
    assert d["source"] == "adhoc" and d["detections"] and d["timeframe"] == "1d"
    assert h["timeframe"] == "1h" and all(x["geometry"]["timeframe"] == "1h" for x in h["detections"])
    step_d = d["candles"][-1]["t"] - d["candles"][-2]["t"]
    step_h = h["candles"][-1]["t"] - h["candles"][-2]["t"]
    assert step_h == 3600 and step_d >= 86400  # never reuse daily coordinates on an hourly chart


def test_restart_recovery_restores_results(tmp_path, calendar):
    settings = make_settings(tmp_path)
    provider = FixtureProvider(fixture_frames(calendar), fail={"FAIL1"})
    with TestClient(create_app(settings, provider=provider, background=False)) as c1:
        s1 = c1.app.state.services
        s1.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s1.universe.refresh_fno(text=FNO_CSV, source="fixture")
        _, res1 = scan_and_get(c1)
        with s1.engine.begin() as conn:
            conn.execute(
                db.scans.insert().values(
                    scan_id="stale-scan", universe="all", timeframe="1h", config_hash="x", config_version="x",
                    trigger="manual", state="running", phase="detect", created_at=db.now_ist_iso(),
                )
            )
    calls_before = len(provider.calls)
    with TestClient(create_app(settings, provider=provider, background=False)) as c2:
        res2 = c2.get("/api/scan", params={"universe": "all", "timeframe": "1d"}).json()
        assert [m["pattern_id"] for m in res2["matches"]] == [m["pattern_id"] for m in res1["matches"]]
        assert res2["scan"]["scan_id"] == res1["scan"]["scan_id"]
        assert c2.get("/api/scan/status/stale-scan").json()["state"] == "interrupted"
    assert len(provider.calls) == calls_before  # restored from SQLite, no re-scan


def test_historical_scan_is_reproducible_and_never_uses_future_candles(client_env):
    client, _, _, _ = client_env
    asof = "2026-10-15"
    st1, r1 = scan_and_get(client, asof=asof)
    st2, r2 = scan_and_get(client, asof=asof)
    assert st1["scan_id"] != st2["scan_id"]
    key = lambda r: [(m["pattern_id"], m["quality"], m["status"]) for m in r["matches"]]  # noqa: E731
    assert key(r1) == key(r2)
    assert any("survivorship" in w or "UNKNOWN" in w for w in st1["warnings"])
    assert all(m["fno_status"] == "UNKNOWN" for m in r1["matches"])
    end_of_day = int(datetime.combine(date.fromisoformat(asof), dtime(23, 59, 59), tzinfo=IST).timestamp())
    chart = client.get("/api/chart", params={"symbol": "FCHAN", "timeframe": "1d", "asof": asof}).json()
    assert chart["candles"] and max(c["t"] for c in chart["candles"]) <= end_of_day
    assert chart["asof"] == asof


def test_history_and_reference_endpoints(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    hist = client.get("/api/history", params={"symbol": "FCHAN"}).json()
    assert hist["count"] >= 1 and all(i["symbol"] == "FCHAN" for i in hist["items"])
    assert client.get("/api/history", params={"pattern": "rectangle"}).json()["count"] >= 1
    pats = client.get("/api/patterns").json()
    assert len(pats) == 17 and all(p["implemented"] for p in pats)
    cfg = client.get("/api/config").json()
    assert cfg["hash"] == s.config_hash and len(cfg["parameters"]) > 20
    uni = client.get("/api/universe", params={"universe": "all"}).json()
    assert uni["count"] == 5 and uni["fno_counts"]["YES"] == 2
    ov = client.get("/api/overview", params={"universe": "all"}).json()
    assert ov["timeframes"]["1d"]["scan"] is not None


def test_stored_geometry_round_trips_through_schema(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    with s.engine.connect() as conn:
        rows = conn.execute(select(db.pattern_geometry.c.geometry_json)).all()
    assert rows
    for (raw,) in rows:
        geo = Geometry.model_validate_json(raw)
        again = Geometry.model_validate_json(geo.model_dump_json())
        assert again == geo


# ------------------------------------------------------------------ P2


def test_chart_includes_sr_zones_from_scan_and_adhoc(client_env):
    client, s, _, _ = client_env
    scan_and_get(client)
    chart = client.get("/api/chart", params={"symbol": "RECT", "timeframe": "1d"}).json()
    assert chart["source"] == "scan" and chart["sr_zones"]
    for z in chart["sr_zones"]:
        assert z["p_low"] < z["p_high"] and z["side"] in ("above", "below") and z["touches"] >= 2
    assert sum(z["side"] == "above" for z in chart["sr_zones"]) <= 4 and sum(z["side"] == "below" for z in chart["sr_zones"]) <= 4
    with s.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.sr_zones)).scalar() > 0
    adhoc = client.get("/api/chart", params={"symbol": "RECT", "timeframe": "1w"}).json()
    assert adhoc["source"] == "adhoc" and isinstance(adhoc["sr_zones"], list)


def test_migration_v1_to_v2_keeps_existing_rows(tmp_path):
    from sqlalchemy import inspect

    engine = db.make_engine(tmp_path / "old.db")
    with engine.begin() as conn:
        db.schema_version.create(conn)
        db._migration_1(conn)
        conn.execute(db.schema_version.insert().values(version=1, description="v1", applied_at="2026-11-03T10:00:00+05:30"))
        conn.execute(
            db.scans.insert().values(
                scan_id="old", universe="all", timeframe="1d", config_hash="h", config_version="1.0.0",
                trigger="manual", state="completed", created_at="2026-11-03T10:00:00+05:30",
            )
        )
    assert "sr_zones" not in inspect(engine).get_table_names()
    assert db.migrate(engine) == db.SCHEMA_VERSION >= 2
    assert "sr_zones" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.execute(select(db.scans.c.scan_id)).scalar() == "old"
    assert db.migrate(engine) == db.SCHEMA_VERSION  # idempotent


def test_end_to_end_scan_across_all_families(tmp_path, calendar):
    from app.services import Services
    from app.synthetic import POSITIVE, band_series
    from app.synthetic_p2 import P2_POSITIVE, series_for

    fixtures = {"FCH": band_series(POSITIVE["falling_channel"], calendar), "SYM": band_series(POSITIVE["symmetrical_triangle"], calendar)}
    fixtures.update({k.upper()[:8].replace("_", ""): series_for(v, calendar) for k, v in P2_POSITIVE.items()})
    frames = {(sym, "1d"): df for sym, df in fixtures.items()}
    csv_rows = "\n".join(f"{sym} Ltd.,Synthetic,{sym},EQ,INE{i:09d}" for i, sym in enumerate(fixtures))
    s = Services(make_settings(tmp_path), provider=FixtureProvider(frames))
    s.startup(background=False)
    try:
        s.universe.refresh_nifty500(text="Company Name,Industry,Symbol,Series,ISIN Code\n" + csv_rows + "\n", source="fixture")
        from app.scanner import ScanRequest, scan_matches

        scan_id = s.scans.run_now(ScanRequest("all", "1d"))
        found = {m["pattern"] for m in scan_matches(s.engine, scan_id, "all")}
        expected = {"falling_channel", "symmetrical_triangle"} | set(P2_POSITIVE)
        assert expected <= found, expected - found
    finally:
        s.shutdown()
