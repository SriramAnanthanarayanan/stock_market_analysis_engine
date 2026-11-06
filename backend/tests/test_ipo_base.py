"""IPO Base Breakout scanner: listing -> price discovery -> post-listing base ->
pivot -> breakout on volume (docs/ipo-base.md). Synthetic post-listing paths
(app.synthetic_ipo); they prove the rules as configured, not real-chart accuracy."""

from datetime import date

import pytest

from app.config import IPOConfig, VCPConfig
from app.data.universe import UniverseError, parse_equity_list
from app.ipo import STATUS_LABELS, detect_ipo_base
from app.synthetic_ipo import IPOSpec, base_high, ipo_frame
from app.vcp import detect_vcp

CFG = IPOConfig()
BREAKOUT = [(125.0, 3.0, 3.0e6)]


def run(calendar, cfg=CFG, listing="first", **kw):
    f = ipo_frame(IPOSpec(**kw), calendar)
    lst = f.index[0].date() if listing == "first" else listing
    return detect_ipo_base(f, cfg, lst, symbol="IPO", config_hash="h", calendar=calendar), f


def test_too_young(calendar):
    f = ipo_frame(IPOSpec(), calendar).iloc[:20]
    r = detect_ipo_base(f, CFG, f.index[0].date(), symbol="IPO", calendar=calendar)
    assert r["status"] == "too_young" and r["age_bars"] == 20 and r["listing"]["source"] == "nse_equity_list"
    assert r["geometry"]["listing"]["date"] == f.index[0].date().isoformat()


def test_discovery_phase(calendar):
    r, _ = run(calendar, segments=((110.0, 10, 5.0, 3e6), (125.0, 20, 4.0, 2e6)))
    assert r["status"] == "discovery_phase" and r["age_bars"] == 31
    assert r["discovery"]["bars"] == CFG.discovery_bars and r["discovery"]["complete"] is True


def test_base_forming(calendar):
    r, _ = run(calendar, segments=((118.0, 5, 6.0, 4e6), (95.0, 12, 5.0, 2.5e6), (105.0, 8, 3.0, 1.5e6)))
    assert r["status"] == "base_forming" and "resistance touches" in r["status_reason"]


def test_valid_base_and_pivot(calendar):
    r, f = run(calendar)
    assert r["status"] == "base_validated" and all(g["passed"] for g in r["gates"])
    b = r["base"]
    assert r["pivot"] == pytest.approx(base_high(IPOSpec()), abs=1e-3)  # pivot = base high (resistance)
    assert CFG.min_base_depth_pct <= b["depth_pct"] <= CFG.max_base_depth_pct and CFG.min_base_bars <= b["length"] <= CFG.max_base_bars
    assert len(b["touches"]) >= CFG.min_resistance_touches and b["settle_ratio"] <= CFG.settle_range_ratio
    assert b["volume_dry_up_ratio"] < 1 and 0 <= r["quality"] <= 100 and len(r["quality_components"]) == 6
    g = r["geometry"]
    assert g["listing"]["t"] == int(f.index[0].timestamp()) and g["discovery"]["t_end"] > g["discovery"]["t_start"]
    assert g["base"]["t_start"] < g["base"]["t_end"] and g["pivot"]["p"] == r["pivot"]


def test_near_pivot(calendar):
    r, _ = run(calendar, tail=[(118.5, 1.0, 0.5e6), (119.0, 1.0, 0.5e6)])
    assert r["status"] == "near_pivot" and 0 <= r["pivot_distance_pct"] <= CFG.near_pivot_pct


def test_breakout_with_volume_confirmation(calendar):
    r, f = run(calendar, tail=BREAKOUT)
    assert r["status"] == "breakout" and r["breakout"]["volume_ok"] and r["breakout_volume_ratio"] >= CFG.breakout_volume_ratio
    assert r["breakout"]["t"] == int(f.index[-1].timestamp()) and r["breakout_age"] == 0
    low, _ = run(calendar, tail=[(125.0, 3.0, 0.6e6)])
    assert low["status"] == "near_pivot" and "volume" in low["status_reason"]  # no breakout without volume


def test_failed_breakout_and_invalidation(calendar):
    failed, _ = run(calendar, tail=BREAKOUT + [(119.0, 2.0, 1.0e6)])
    assert failed["status"] == "failed_breakout" and failed["failure"]["t"] > failed["breakout"]["t"]
    deep, _ = run(calendar, segments=((118.0, 5, 6.0, 4e6), (70.0, 15, 5.0, 2.5e6), (80.0, 15, 3.0, 1.5e6)))
    assert deep["status"] == "invalidated" and "deep" in deep["status_reason"]


def test_listing_date_rules(calendar):
    f = ipo_frame(IPOSpec(), calendar)
    assert detect_ipo_base(f, CFG, None, calendar=calendar) is None  # no official date, fallback off
    fb = detect_ipo_base(f, IPOConfig(first_candle_fallback=True), None, calendar=calendar)
    assert fb is not None and fb["listing"]["source"] == "first_candle"
    gap = detect_ipo_base(f, CFG, date(2026, 6, 1), calendar=calendar)  # history starts weeks after listing
    assert gap is None
    old = ipo_frame(IPOSpec(segments=IPOSpec().segments + ((114.0, 400, 1.2, 0.5e6),)), calendar)
    assert detect_ipo_base(old, CFG, old.index[0].date(), calendar=calendar) is None  # older than max_age_bars


def test_no_future_leakage(calendar):
    f = ipo_frame(IPOSpec(tail=BREAKOUT + [(126.0, 2.0, 1.5e6)]), calendar)
    lst = f.index[0].date()
    at_b = detect_ipo_base(f.iloc[:-1], CFG, lst, calendar=calendar)
    before = detect_ipo_base(f.iloc[:-2], CFG, lst, calendar=calendar)
    assert at_b["status"] == "breakout" and at_b["breakout_age"] == 0
    assert before["status"] == "base_validated" and before["breakout"] is None
    assert detect_ipo_base(f, CFG, lst, calendar=calendar)["breakout"]["t"] == at_b["breakout"]["t"]


def test_an_ipo_base_is_not_a_vcp(calendar):
    _, f = run(calendar)
    v = detect_vcp(f, VCPConfig(), symbol="IPO")
    assert v is None or not v["qualified"]


def test_status_labels():
    assert list(STATUS_LABELS.values()) == ["Too Young", "Discovery Phase", "Base Forming", "Base Validated", "Near Pivot", "Breakout", "Failed Breakout", "Invalidated"]
    assert (CFG.min_age_bars, CFG.max_age_bars, CFG.discovery_bars, CFG.min_base_bars, CFG.max_base_bars) == (25, 365, 20, 20, 120)
    assert (CFG.min_base_depth_pct, CFG.max_base_depth_pct, CFG.min_resistance_touches, CFG.near_pivot_pct, CFG.breakout_volume_ratio, CFG.failed_breakout_bars) == (8, 35, 2, 3, 1.5, 5)


def test_parse_equity_list():
    text = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE\nABC,Abc Ltd, EQ, 06-OCT-2008, 10\nXYZ,Xyz Ltd, EQ, 21-Aug-2026, 10\nBAD,Bad, EQ, ?, 10\n"
    out = parse_equity_list(text, min_rows=2)
    assert out == {"ABC": date(2008, 10, 6), "XYZ": date(2026, 8, 21)}
    with pytest.raises(UniverseError):
        parse_equity_list(text, min_rows=5)
    with pytest.raises(UniverseError):
        parse_equity_list("FOO,BAR\n1,2\n", min_rows=1)


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def ipo_app(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from tests.conftest import FNO_CSV, HOLIDAYS_SRC, NIFTY_CSV
    from tests.fixtures_adv import build_adv_app
    from tests.fixtures_e2 import catalogue
    from tests.test_api import refresh, wait_scan

    cal = NSECalendar.load(HOLIDAYS_SRC)
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    f = ipo_frame(IPOSpec(tail=BREAKOUT), cal)
    provider.frames[("RECT", "1d")] = f
    with client:
        s = app.state.services
        s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        lst = f.index[0].strftime("%d-%b-%Y").upper()
        res = s.universe.refresh_listing_dates(text=f"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nRECT,Rect Ltd,EQ,{lst}\nZZZ,Other,EQ,01-JAN-2001\n", min_rows=1)
        assert res["status"] == "ok" and res["rows"] == 1
        s.indices.refresh_catalogue(catalogue(cal), min_rows=5)
        s.indices.refresh_all()
        w = wait_scan(client, refresh(client, timeframe="1w")["scan_id"])
        d = wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
        assert w["state"] == "completed" and d["state"] == "completed"
        yield client, s


def test_ipo_scanner_api(ipo_app):
    client, s = ipo_app
    r = client.get("/api/scan/ipo-base").json()
    assert r["listing_dates_loaded"] == 1 and r["scan"]["ipo_config_version"] == "1.0.0"
    rows = {x["symbol"]: x for x in r["items"]}
    assert set(rows) == {"RECT"}  # only the stock with an official recent listing date
    x = rows["RECT"]
    assert x["status"] == "breakout" and x["breakout"]["volume_ok"] and "geometry" not in x
    assert x["listing"]["source"] == "nse_equity_list" and x["base"]["touches"]
    assert client.get("/api/scan/ipo-base", params={"status": "base_forming"}).json()["count"] == 0
    assert client.get("/api/scan/ipo-base", params={"status": "nope"}).status_code == 422
    one = client.get("/api/ipo/symbol", params={"symbol": "RECT"}).json()
    assert one["source"] == "scan" and one["ipo"]["geometry"]["schema_version"] == "ipo-base-1.0" and one["listing_date_source"] == "nse_equity_list"
    assert client.get("/api/ipo/symbol", params={"symbol": "RWALK"}).json()["ipo"] is None
    cfg = client.get("/api/config/ipo").json()
    assert cfg["version"] == "1.0.0" and {p["name"] for p in cfg["parameters"]} >= {"min_age_bars", "breakout_volume_ratio"}
    ev = client.get("/api/evidence", params={"symbol": "RECT", "source": "ipo_base"}).json()["items"]
    assert len(ev) == 1 and ev[0]["signal_type"] == "ipo_breakout" and ev[0]["category"] == "setup"
