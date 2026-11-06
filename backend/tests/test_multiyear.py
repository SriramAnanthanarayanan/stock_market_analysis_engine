"""Multi-year breakouts (docs/multi-year-breakouts.md): synthetic fixtures, not market data."""

from __future__ import annotations

import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.config import SignalConfig
from app.indicators.core import atr_wilder
from app.signals.multiyear import compact, multiyear_breakouts, recent
from app.synthetic import random_walk
from app.synthetic_multiyear import DEFAULT_SEGMENTS, MultiYearSpec, multiyear_frame, old_high

CFG = SignalConfig()


def detect(f: pd.DataFrame, cfg: SignalConfig = CFG, bench: pd.Series | None = None):
    h, l, c = (f[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    return multiyear_breakouts(f, atr_wilder(h, l, c, cfg.atr_period), cfg, "MY", "hash", bench)


def frame(calendar, segments=DEFAULT_SEGMENTS, tail=None, start=50.0) -> pd.DataFrame:
    spec = MultiYearSpec(segments=segments, start=start)
    if tail is not None:
        spec.tail = tail
    return multiyear_frame(spec, calendar)


def test_ten_year_base_breakout(calendar):
    ev = detect(frame(calendar))
    assert len(ev) == 1
    e = ev[0]
    assert e["level"] == pytest.approx(old_high(MultiYearSpec())) and e["all_time_high_in_data"]
    assert 9.5 <= e["base_years"] <= 10.5 and e["level_years"] <= e["base_years"]
    assert e["zone_tests"] == 4  # the old high + three rallies back into the zone
    assert e["volume_ratio"] == pytest.approx(4.0, rel=0.05)
    assert e["status"] == "holding" and e["age_bars"] == 0 and not e["rebreak"]
    assert e["breakout_pct"] == pytest.approx((104.0 / 101.0 - 1) * 100, abs=0.01)
    assert e["base_depth_pct"] > 30
    assert any("years below this resistance" in x for x in e["explanation"])


def test_any_number_of_years(calendar):
    f = frame(calendar)
    assert len(detect(f, replace(CFG, multiyear_min_years=5.0))) == 1
    assert len(detect(f, replace(CFG, multiyear_min_years=7.25))) == 1
    assert detect(f, replace(CFG, multiyear_min_years=12.0)) == []  # a ~10-year base is not a 12-year breakout


def test_uptrend_new_highs_are_not_multi_year_breakouts(calendar):
    segs = []
    p = 50.0
    for _ in range(16):  # 8 years of advances with short pullbacks: every high is a few weeks old
        segs += [(p * 1.12, 100, 2.0, 1e6), (p * 1.12 * 0.95, 25, 2.0, 1e6)]
        p = p * 1.12 * 0.95
    f = frame(calendar, tuple(segs), tail=[(p * 1.2, 2.0, 1e6)])
    assert detect(f) == []


def test_continuation_after_the_breakout_is_one_event(calendar):
    tail = [(104.0, 3.0, 2.0e6)] + [(104.0 + k * 0.8, 1.5, 1.0e6) for k in range(1, 30)]
    ev = detect(frame(calendar, tail=tail))
    assert len(ev) == 1 and ev[0]["age_bars"] == 29 and ev[0]["status"] == "holding"
    assert ev[0]["return_since_pct"] > 0 and ev[0]["max_gain_pct"] >= ev[0]["return_since_pct"]


def test_failed_breakout_and_re_break(calendar):
    tail = [(104.0, 3.0, 2.0e6)] + [(97.0, 1.5, 0.6e6)] * 8  # 8 closes back below the old high: failed
    f = frame(calendar, tail=tail)
    ev = detect(f)
    assert len(ev) == 1 and ev[0]["status"] == "failed" and ev[0]["failed_time"] is not None
    tail2 = tail + [(97.0 + k * 0.4, 1.2, 0.6e6) for k in range(1, 30)] + [(110.0, 3.0, 3.0e6)]
    ev2 = detect(frame(calendar, tail=tail2))
    assert len(ev2) == 2
    assert ev2[0]["status"] == "failed" and ev2[1]["rebreak"] and ev2[1]["rebreak_of_time"] == ev2[0]["time"]
    assert ev2[1]["base_years"] >= ev2[0]["base_years"]  # the failed attempt's spike stays part of the original base


def test_level_below_an_older_higher_peak(calendar):
    # an old peak at ~150, a decline through 104 about nine years ago, a lower peak (~101) and a
    # long base, then a close at 104: not an all-time high, but the highest price in ~9 years
    segs = ((150.0, 120, 2.0, 1e6), (60.0, 250, 2.0, 1e6), (100.0, 250, 2.0, 1e6), (65.0, 600, 2.0, 1e6), (99.0, 1300, 1.5, 0.6e6))
    f = frame(calendar, segs, start=100.0)
    ev = detect(f)
    assert len(ev) == 1
    e = ev[0]
    assert not e["all_time_high_in_data"] and e["highest_since_time"] is not None
    # the broken level is the highest high since the price was last at 104 (a candle of the old
    # decline, above the ~101 swing high, which is one of the resistance tests)
    assert 101.0 < e["level"] < 104.0 and e["zone_tests"] >= 2
    assert e["years_since_higher"] >= e["base_years"] > 8.5


def test_resistance_zone_vs_exact_high(calendar):
    # a marginal poke (~1 %) above the old high in the middle of the base
    segs = DEFAULT_SEGMENTS[:3] + ((101.0, 20, 2.0, 0.8e6), (75.0, 400, 2.0, 0.7e6), (99.0, 600, 1.2, 0.5e6))
    f = frame(calendar, segs)
    zone = detect(f)[0]
    strict = detect(f, replace(CFG, multiyear_zone_pct=0.0))[0]
    assert zone["level"] == strict["level"] > old_high(MultiYearSpec())  # the poke is the resistance
    assert zone["base_years"] > strict["base_years"] + 3  # the zone keeps the clock from the old high
    assert strict["base_years"] == pytest.approx(strict["level_years"], abs=0.01)


def test_no_look_ahead(calendar):
    tail = [(104.0, 3.0, 2.0e6)] + [(104.0 + k, 1.5, 1.0e6) for k in range(1, 40)]
    f = frame(calendar, tail=tail)
    full = detect(f)
    for k in (len(f) - 40, len(f) - 39, len(f) - 10):
        part = detect(f.iloc[:k])
        assert [(e["time"], e["level"], e["base_years"]) for e in part] == [(e["time"], e["level"], e["base_years"]) for e in full if e["index"] < k]
    assert detect(f.iloc[: len(f) - 40]) == []  # the candle before the breakout knows nothing about it


def test_buffer_and_missing_volume(calendar):
    f = frame(calendar)
    assert detect(f, replace(CFG, multiyear_buffer_atr=5.0)) == []  # 104 is ~1.9 ATR above the level
    g = f.copy()
    g["volume"] = np.nan
    assert detect(g)[0]["volume_ratio"] is None  # never guessed


def test_forward_returns_and_benchmark(calendar):
    tail = [(104.0, 3.0, 2.0e6)] + [(104.0 + k * 0.2, 1.0, 1.0e6) for k in range(1, 300)]
    f = frame(calendar, tail=tail)
    bench = pd.Series(np.linspace(1000.0, 1300.0, len(f)), index=f.index)
    e = detect(f, bench=bench)[0]
    fw = {x["months"]: x for x in e["forward"]["horizons"]}
    t0 = pd.Timestamp(e["time"])
    k = int(f.index.searchsorted(t0 + pd.DateOffset(months=12), side="right")) - 1
    assert fw[12]["status"] == "ok" and fw[12]["return_pct"] == pytest.approx((f["close"].iloc[k] / 104.0 - 1) * 100, abs=0.01)
    b0 = bench.loc[t0]
    assert fw[12]["benchmark_pct"] == pytest.approx((bench.iloc[k] / b0 - 1) * 100, abs=0.01)
    assert fw[12]["excess_pct"] == pytest.approx(fw[12]["return_pct"] - fw[12]["benchmark_pct"], abs=0.02)
    assert fw[24]["status"] == "pending" and fw[24]["return_pct"] is None  # never extrapolated
    assert e["forward"]["mfe_12m_pct"] >= fw[12]["return_pct"] and e["forward"]["window_complete_12m"]


def test_recent_and_compact(calendar):
    tail = [(104.0, 3.0, 2.0e6)] + [(105.0, 1.5, 1.0e6)] * 10
    f = frame(calendar, tail=tail)
    ev = detect(f)
    assert recent(ev, len(f), 5) == [] and recent(ev, len(f), 20) == ev
    c = compact(ev[0])
    assert c["base_years"] == ev[0]["base_years"] and "forward" in c and "explanation" not in c


def test_random_walks_and_speed(calendar):
    for seed in range(5):
        f = random_walk(7700, calendar, seed=seed)
        t0 = time.perf_counter()
        ev = detect(f)
        assert time.perf_counter() - t0 < 1.0  # ~30 years of daily candles
        for e in ev:  # every reported base is long enough and the close really cleared the level
            assert e["base_years"] >= CFG.multiyear_min_years and e["level"] < e["close"]
            assert e["base_start_time"] <= e["level_time"] < e["time"]


def test_spec_defaults():
    assert (CFG.multiyear_min_years, CFG.multiyear_zone_pct, CFG.multiyear_buffer_atr, CFG.multiyear_fail_closes) == (1.0, 3.0, 0.0, 5)
    assert CFG.multiyear_horizons_months == (1, 3, 6, 12, 24, 36) and CFG.multiyear_benchmark == "NIFTY 50"


# ------------------------------------------------------------------ scan + API
@pytest.fixture(scope="module")
def my_app(tmp_path_factory):
    from fastapi.testclient import TestClient

    from app.data.calendar import NSECalendar
    from app.main import create_app
    from app.synthetic_multiyear import BREAKOUT
    from tests.conftest import FNO_CSV, HOLIDAYS_SRC, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings
    from tests.fixtures_e2 import FakeHistory, catalogue
    from tests.test_api import refresh, wait_scan

    cal = NSECalendar.load(HOLIDAYS_SRC)
    frames = fixture_frames(cal)
    # MULTI10: ~10-year base, breakout 3 sessions ago and holding
    frames[("MULTI10", "1d")] = multiyear_frame(MultiYearSpec(tail=BREAKOUT + [(105.0, 1.5, 1.0e6)] * 3), cal)
    # MULTI2: an older ~8-year breakout followed by an advance (history + forward returns only)
    old = MultiYearSpec(segments=DEFAULT_SEGMENTS[:5], tail=BREAKOUT + [(104.0 + k * 0.1, 1.5, 1.0e6) for k in range(1, 700)])
    frames[("MULTI2", "1d")] = multiyear_frame(old, cal)
    app = create_app(make_settings(tmp_path_factory.mktemp("multiyear")), provider=FixtureProvider(frames, fail={"FAIL1"}), background=False, index_history=FakeHistory(cal))
    client = TestClient(app)
    with client:
        s = app.state.services
        extra = "Multi Year Ten Ltd.,Synthetic,MULTI10,EQ,INE000000009\nMulti Year Two Ltd.,Synthetic,MULTI2,EQ,INE000000010\n"
        s.universe.refresh_nifty500(text=NIFTY_CSV + extra, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        s.indices.refresh_catalogue(catalogue(cal), min_rows=5)
        s.indices.refresh_all()
        d = wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
        assert d["state"] == "completed"
        yield client, s, d


def test_scanner_any_number_of_years(my_app):
    client, _, _ = my_app
    r = client.get("/api/scan/multiyear-breakouts").json()  # default: >= 5 years, last 20 sessions
    rows = {x["symbol"]: x for x in r["items"]}
    assert "MULTI10" in rows and "MULTI2" not in rows  # MULTI2's breakout is ~2.8 years old
    m = rows["MULTI10"]
    assert 9.5 <= m["base_years"] <= 10.5 and m["age_bars"] == 3 and m["status"] == "holding" and m["all_time_high_in_data"]
    assert m["company"] == "Multi Year Ten Ltd." and m["past_events"] == [] and m["explanation"]
    for years, present in ((7.5, True), (10.4, False), (15, False), (2, True)):
        got = client.get("/api/scan/multiyear-breakouts", params={"min_years": years}).json()["items"]
        assert ("MULTI10" in {x["symbol"] for x in got}) is present, years
    assert client.get("/api/scan/multiyear-breakouts", params={"max_age": 2}).json()["count"] == 0
    assert client.get("/api/scan/multiyear-breakouts", params={"status": "failed"}).json()["count"] == 0
    assert client.get("/api/scan/multiyear-breakouts", params={"status": "bogus"}).status_code == 422
    allold = client.get("/api/scan/multiyear-breakouts", params={"min_years": 5, "max_age": 1000}).json()["items"]
    assert {x["symbol"] for x in allold} >= {"MULTI10"}


def test_symbol_history_and_event_study(my_app):
    client, _, _ = my_app
    h = client.get("/api/multiyear/symbol", params={"symbol": "MULTI2"}).json()
    assert h["source"] == "scan" and len(h["events"]) == 1 and h["events"][0]["base_years"] > 7
    st = client.get("/api/multiyear/study", params={"min_years": 5}).json()
    assert st["events_total"] >= 2 and st["stocks"] >= 2 and st["caveats"]
    by = {x["months"]: x for x in st["summary"]}
    assert by[12]["n"] >= 1 and by[12]["median"] is not None and by[12]["pending"] >= 1  # MULTI10's 12 months are still ahead
    two = next(x for x in st["items"] if x["symbol"] == "MULTI2")
    assert next(z for z in two["forward"]["horizons"] if z["months"] == 12)["return_pct"] > 0
    assert client.get("/api/multiyear/study", params={"min_years": 12}).json()["events_total"] == 0
    assert client.get("/api/multiyear/symbol", params={"symbol": "NOPE"}).status_code == 404


def test_evidence_confluence_and_old_scans(my_app):
    from sqlalchemy import update

    from app import database as db

    client, s, d = my_app
    ev = client.get("/api/evidence", params={"symbol": "MULTI10", "source": "multiyear"}).json()["items"]
    kinds = {(e["kind"], e["signal_type"]) for e in ev}
    assert ("event", "multiyear_breakout") in kinds and ("state", "multiyear_history") in kinds
    cf = client.get("/api/confluence/symbol", params={"symbol": "MULTI10"}).json()
    bull = cf["bullish"]
    trig = next(c for c in bull["explanation"] if c["key"] == "trigger")
    assert any(e["source"] == "multiyear" and e["status"] in ("used", "overlapping", "bonus") for e in trig["evidence"])
    assert bull["summary"]["multiyear"]["years"] > 9 and bull["summary"]["multiyear"]["meets_screen"]
    screen = client.get("/api/confluence", params={"screen": "multiyear_breakout", "include_unranked": "true"}).json()
    assert "MULTI10" in {x["symbol"] for x in screen["items"]}
    with s.engine.begin() as conn:
        conn.execute(update(db.scans).where(db.scans.c.scan_id == d["scan_id"]).values(signal_config_version="1.1.0"))
    try:
        r = client.get("/api/scan/multiyear-breakouts").json()
        assert r["count"] == 0 and "before the multi-year breakout scanner existed" in r["note"]
    finally:
        with s.engine.begin() as conn:
            conn.execute(update(db.scans).where(db.scans.c.scan_id == d["scan_id"]).values(signal_config_version=s.signal_cfg.version))
