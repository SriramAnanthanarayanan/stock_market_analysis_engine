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
    deep, _ = run(calendar, segments=((118.0, 5, 6.0, 4e6), (52.0, 15, 5.0, 2.5e6), (60.0, 15, 3.0, 1.5e6)))
    assert deep["status"] in ("invalidated", "ipo_low_broken") and deep["status"] != "base_validated"  # > 50 % deep (1.1.0)
    # 1.1.0: a ~41 % deep IPO base is still a base (TEGA-type), scored lower on depth
    d41, _ = run(calendar, segments=((118.0, 5, 6.0, 4e6), (72.0, 15, 5.0, 2.5e6), (80.0, 15, 3.0, 1.5e6)))
    assert d41["status"] not in ("invalidated",) and d41["base"]["depth_pct"] > 35
    assert {c["name"]: c for c in d41["quality_components"]}["depth"]["score"] < 0.6


def test_listing_date_rules(calendar):
    f = ipo_frame(IPOSpec(), calendar)
    assert detect_ipo_base(f, CFG, None, calendar=calendar) is None  # no official date, fallback off
    fb = detect_ipo_base(f, IPOConfig(first_candle_fallback=True), None, calendar=calendar)
    assert fb is not None and fb["listing"]["source"] == "first_candle"
    gap = detect_ipo_base(f, CFG, date(2026, 6, 1), calendar=calendar)  # history starts weeks after listing
    assert gap is None
    old = ipo_frame(IPOSpec(segments=IPOSpec().segments + ((114.0, 500, 1.2, 0.5e6),)), calendar)  # 1.1.0: max_age_bars 500
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
    assert list(STATUS_LABELS.values()) == ["Too Young", "Discovery Phase", "Base Forming", "Base Validated", "Near Pivot", "Breakout", "Failed Breakout", "Invalidated", "Cheat Entry", "IPO Low Broken"]
    assert (CFG.min_age_bars, CFG.max_age_bars, CFG.discovery_bars, CFG.min_base_bars, CFG.max_base_bars) == (25, 500, 20, 20, 250)  # 1.1.0
    assert (CFG.min_base_depth_pct, CFG.max_base_depth_pct, CFG.min_resistance_touches, CFG.near_pivot_pct, CFG.breakout_volume_ratio, CFG.failed_breakout_bars) == (8, 50, 2, 3, 1.5, 5)


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
    assert r["listing_dates_loaded"] == 1 and r["scan"]["ipo_config_version"] == CFG.version
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
    assert cfg["version"] == CFG.version and {p["name"] for p in cfg["parameters"]} >= {"min_age_bars", "breakout_volume_ratio"}
    ev = client.get("/api/evidence", params={"symbol": "RECT", "source": "ipo_base"}).json()["items"]
    assert len(ev) == 1 and ev[0]["signal_type"] == "ipo_breakout" and ev[0]["category"] == "setup"


def test_ipo_scanner_issue_price(ipo_app):
    """Issue price from NSE past public issues (display only): matched when the IPO
    listing date is the listing the detector evaluated."""
    client, s = ipo_app
    x = client.get("/api/scan/ipo-base").json()["items"][0]
    assert x["issue_price"] is None and x["vs_issue_pct"] is None  # no past-issues list loaded yet
    lst = x["listing"]["date"]
    nse = date.fromisoformat(lst).strftime("%d-%b-%Y").upper()
    payload = [{"symbol": "RECT", "companyName": "Rect Ltd", "securityType": "Equity", "issuePrice": "80", "listingDate": nse, "priceRange": "Rs.76 to Rs.80"}]
    equity = f"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nRECT,Rect Ltd,EQ,{nse}\n"
    assert s.universe.refresh_recent_ipos(payload=payload, equity_text=equity, source="fixture", min_rows=1, equity_min_rows=1)["status"] == "ok"
    x = client.get("/api/scan/ipo-base").json()["items"][0]
    assert x["issue_price"] is None and "not checked" in x["issue_price_note"]  # split / bonus history unknown yet

    class NoSplits:
        name = "fake"

        def splits(self, symbol):
            return {}

    assert s.universe.refresh_ipo_splits(NoSplits(), pause_s=0)["checked"] in (0, 1)  # RECT may be older than the 3-year window
    s.universe.refresh_ipo_splits(NoSplits(), symbols={"RECT"}, pause_s=0)
    x = client.get("/api/scan/ipo-base").json()["items"][0]
    assert x["issue_price"] == 80.0 and x["issue_price_raw"] == 80.0 and x["ipo_security_type"] == "Equity"
    assert x["vs_issue_pct"] == pytest.approx(round((x["last_close"] / 80.0 - 1) * 100, 2))
    one = client.get("/api/ipo/symbol", params={"symbol": "RECT"}).json()
    assert one["ipo"]["issue_price"] == 80.0 and one["listing_date_source"] == "nse_ipo" and one["listing_date"] == lst



# ---------------------------------------------------------------- IPOConfig 1.1.0 (reference decks, phase 3)
def test_ipo_low_broken(calendar):
    """Paytm / Cartrade-type: the close falls below the lowest low of the listing period."""
    r, _ = run(calendar, segments=((118.0, 5, 6.0, 4e6), (100.0, 15, 5.0, 2.5e6), (84.0, 30, 3.0, 1.0e6)))
    assert r["status"] == "ipo_low_broken" and r["status_label"] == "IPO Low Broken"
    assert r["ipo_low"]["first_close_below"] is not None and r["ipo_low"]["closes_below"] >= 1
    ok, _ = run(calendar)
    assert ok["ipo_low"]["first_close_below"] is None and ok["status"] != "ipo_low_broken"


def test_cheat_entry_inside_the_base(calendar):
    """A tight sub-base well below the IPO high, broken on volume: early (cheat) entry."""
    segs = ((120.0, 5, 6.0, 4e6), (95.0, 15, 5.0, 2.5e6), (112.0, 10, 3.0, 1.5e6), (112.0, 15, 1.0, 1.0e6))
    r, _ = run(calendar, segments=segs, tail=[(115.0, 1.5, 4.0e6)])  # ~1.9x the 50-session average (listing pop included)
    assert r["status"] == "cheat_entry" and r["cheat_entry"]["volume_ratio"] >= CFG.breakout_volume_ratio
    assert r["cheat_entry"]["level"] < r["pivot"] * (1 - CFG.cheat_min_below_pct / 100)
    weak, _ = run(calendar, segments=segs, tail=[(115.0, 1.5, 1.0e6)])  # no volume: not a cheat entry
    assert weak["status"] != "cheat_entry"
    # a tight area near the base LOW broken on volume is a bounce, not a base within the base
    low = ((120.0, 5, 6.0, 4e6), (95.0, 15, 5.0, 2.5e6), (99.0, 10, 3.0, 1.5e6), (99.0, 15, 1.0, 1.0e6))
    bounce, _ = run(calendar, segments=low, tail=[(102.0, 1.5, 4.0e6)])
    assert bounce["status"] != "cheat_entry"


def test_breakout_retest_held(calendar):
    r, _ = run(calendar, tail=BREAKOUT + [(124.0, 4.0, 1.0e6)])
    assert r["status"] == "breakout" and r["retest"] is not None
    plain, _ = run(calendar, tail=BREAKOUT)
    assert plain["retest"] is None


def test_ipo_1_1_0_defaults():
    assert (CFG.max_age_bars, CFG.max_base_bars, CFG.max_base_depth_pct, CFG.deep_base_pct) == (500, 250, 50.0, 35.0)
