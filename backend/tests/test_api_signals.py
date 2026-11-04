"""Advanced scanner API (P1): fresh breakouts, Volume Shocker, 52-week, evidence.

RWALK's daily fixture ends with a crafted bullish Marubozu that closes 4 % above
the previous 10-day high on 3x volume (tests/test_api_e1.py), so the fresh
breakout, the volume shock and the Marubozu are all on the same candle.
Fixtures, not market data."""

import pytest
from sqlalchemy import update

from app import database as db
from app.config import SignalConfig
from tests.test_api import refresh, wait_scan
from tests.test_api_e1 import _crafted


@pytest.fixture
def p1(client_env, calendar):
    client, s, provider, settings = client_env
    provider.frames[("RWALK", "1d")] = _crafted(calendar)
    st = wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
    assert st["state"] == "completed"
    return client, s, st


def test_scan_records_signal_and_vcp_config(p1):
    client, s, st = p1
    from app.scanner import get_scan

    sc = get_scan(s.engine, st["scan_id"])
    assert sc["signal_config_hash"] == s.signal_config_hash and sc["signal_config_version"] == s.signal_cfg.version
    assert sc["vcp_config_hash"] == s.vcp_config_hash


def test_fresh_breakout_scanner_default_is_todays_fresh_close(p1):
    client, _, _ = p1
    r = client.get("/api/scan/fresh-breakouts").json()
    rw = [x for x in r["items"] if x["symbol"] == "RWALK"]
    assert len(rw) == 1
    e = rw[0]
    assert e["direction"] == "bullish" and e["age_bars"] == 0 and e["confirmation_status"] == "confirmed"
    assert e["breakout_pct"] == pytest.approx(4.0, abs=0.05) and e["volume_ratio"] == pytest.approx(3.0, rel=0.05)
    assert e["event_id"] and e["company"] and e["fno_status"] in ("YES", "NO", "UNKNOWN")
    assert e["first_in_lookback_n"] in (True, False, None) and r["lookback_n"] == 20
    # direction / strength / 2-close filters
    assert not [x for x in client.get("/api/scan/fresh-breakouts", params={"direction": "bearish"}).json()["items"] if x["symbol"] == "RWALK"]
    assert not [x for x in client.get("/api/scan/fresh-breakouts", params={"min_atr": 40}).json()["items"] if x["symbol"] == "RWALK"]
    pend = client.get("/api/scan/fresh-breakouts", params={"confirm": 2, "status": "pending"}).json()
    assert [x for x in pend["items"] if x["symbol"] == "RWALK"][0]["confirmation_status"] == "pending"
    assert not [x for x in client.get("/api/scan/fresh-breakouts", params={"confirm": 2}).json()["items"] if x["symbol"] == "RWALK"]
    older = client.get("/api/scan/fresh-breakouts", params={"max_age": 60}).json()
    assert older["count"] >= r["count"]


def test_first_breakout_filter_and_custom_n(p1):
    client, _, _ = p1
    base = client.get("/api/scan/fresh-breakouts", params={"max_age": 60}).json()["items"]
    first = client.get("/api/scan/fresh-breakouts", params={"max_age": 60, "first_only": True}).json()["items"]
    assert all(x["first_in_lookback_n"] is True for x in first) and len(first) <= len(base)
    n5 = client.get("/api/scan/fresh-breakouts", params={"max_age": 60, "first_only": True, "lookback_n": 5}).json()
    assert n5["lookback_n"] == 5 and len(n5["items"]) >= len(first)


def test_volume_shocker_scanner_and_filters(p1):
    client, _, _ = p1
    r = client.get("/api/scan/volume-shocks").json()
    rw = [x for x in r["items"] if x["symbol"] == "RWALK"]
    assert len(rw) == 1 and rw[0]["direction"] == "bullish" and rw[0]["rvol"] >= 2.5 and rw[0]["status"] == "shock"
    assert r["min_rvol"] == 2.0 and r["rvol_options"] == [1.5, 2.0, 3.0, 5.0]
    assert not [x for x in client.get("/api/scan/volume-shocks", params={"min_rvol": 5}).json()["items"] if x["symbol"] == "RWALK"]
    assert not [x for x in client.get("/api/scan/volume-shocks", params={"direction": "bearish"}).json()["items"] if x["symbol"] == "RWALK"]
    # Liquidity: RWALK trades ~1e8 rupees a day; a 1e9 floor removes it, liquidity=false keeps it.
    assert not [x for x in client.get("/api/scan/volume-shocks", params={"min_traded_value": 1e9}).json()["items"] if x["symbol"] == "RWALK"]
    assert [x for x in client.get("/api/scan/volume-shocks", params={"min_traded_value": 1e9, "liquidity": False}).json()["items"] if x["symbol"] == "RWALK"]


def test_52_week_scanner_views(p1):
    client, _, _ = p1
    st = client.get("/api/scan/52week", params={"view": "state", "include_insufficient": True}).json()
    by = {x["symbol"]: x for x in st["items"]}
    assert by["RWALK"]["status"] == "ok" and by["RWALK"]["high_52w"] > 0  # 300 daily candles
    assert any(x["status"] == "insufficient_history" for x in st["items"]) and st["insufficient_history"] >= 1
    assert by["RWALK"]["official"]["status"] == "unavailable"
    assert all(x["status"] == "ok" for x in client.get("/api/scan/52week", params={"view": "state"}).json()["items"])
    ev = client.get("/api/scan/52week", params={"max_age": 20}).json()
    assert "Fewer than 252" in ev["note"] and ev["view"] == "events"
    for x in ev["items"]:
        assert x["kind"].startswith("week52_") and x["state"]["status"] == "ok"


def test_evidence_endpoint_normalizes_every_scanner(p1):
    client, _, _ = p1
    r = client.get("/api/evidence", params={"symbol": "RWALK"}).json()
    sources = {x["source"] for x in r["items"]}
    assert {"fresh_breakout", "volume_shock", "marubozu", "week52", "ema_alignment"} <= sources
    for x in r["items"]:
        for k in ("evidence_id", "symbol", "signal_type", "category", "timeframe", "status", "lifecycle", "config_version", "config_hash", "metrics"):
            assert k in x
    maru = [x for x in r["items"] if x["source"] == "marubozu"]
    assert maru[0]["category"] == "volume_candle" and maru[0]["valid_until"] and "fresh for" in maru[0]["expiry_rule"]
    # Evidence re-labels what the Marubozu scanner stored; it never detects again.
    from sqlalchemy import select

    _, s, st = p1
    with s.engine.connect() as conn:
        stored = conn.execute(select(db.tech_signals.c.signal_time).where((db.tech_signals.c.scan_id == st["scan_id"]) & (db.tech_signals.c.symbol == "RWALK") & (db.tech_signals.c.category == "candlestick"))).all()
    assert sorted(x["signal_time"] for x in maru) == sorted(t for (t,) in stored)


def test_old_scan_without_signals_says_so(p1):
    client, s, st = p1
    with s.engine.begin() as conn:
        conn.execute(update(db.scans).where(db.scans.c.scan_id == st["scan_id"]).values(signal_config_hash=None))
    for path in ("/api/scan/fresh-breakouts", "/api/scan/volume-shocks", "/api/scan/52week", "/api/evidence"):
        r = client.get(path).json()
        assert r["count"] == 0 and "before the advanced scanners" in r["note"]


def test_no_scan_and_bad_params(client_env):
    client, _, _, _ = client_env
    assert client.get("/api/scan/fresh-breakouts").json()["count"] == 0
    assert client.get("/api/scan/fresh-breakouts", params={"confirm": 3}).status_code == 422
    assert client.get("/api/scan/volume-shocks", params={"timeframe": "2d"}).status_code == 422
    assert client.get("/api/scan/52week", params={"view": "nope"}).status_code == 422


def test_config_endpoints_document_every_parameter(client_env):
    client, s, _, _ = client_env
    for path, cfg in (("/api/config/signals", s.signal_cfg), ("/api/config/vcp", s.vcp_cfg), ("/api/config/confluence", s.confluence_cfg)):
        r = client.get(path).json()
        assert r["version"] == cfg.version and r["parameters"] and all(p["description"] for p in r["parameters"])
    assert client.get("/api/config/signals").json()["detector_timeframes"]["vcp"] == ["1d"]


def test_scheduler_rescans_when_signal_config_changes(client_env):
    client, s, _, _ = client_env
    from app.scheduler import ScanScheduler

    wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
    sch = ScanScheduler(s)
    need, why = sch.needs_scan("1d")
    s.signal_cfg = SignalConfig(vs_rvol_threshold=2.5)
    need2, why2 = sch.needs_scan("1d")
    assert need2 and "signal scanner configuration changed" in why2


def test_volume_shocker_on_weekly_and_hourly_scans(client_env):
    client, _, _, _ = client_env
    for tf in ("1w", "1h"):
        st = wait_scan(client, refresh(client, timeframe=tf)["scan_id"])
        assert st["state"] == "completed"
        r = client.get("/api/scan/volume-shocks", params={"timeframe": tf, "liquidity": False}).json()
        assert r["scan"]["timeframe"] == tf and "count" in r
