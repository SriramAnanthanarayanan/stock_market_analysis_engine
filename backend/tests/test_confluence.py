"""Confluence score (docs/confluence-engine.md): category normalization, no double
counting, freshness / invalidation, coverage, same-session combination bonus,
bullish and bearish scored separately; snapshot persistence and the API.

Unit tests build evidence rows by hand with the same constructor the scanners'
adapters use (``app.signals.evidence.make``). Fixtures, not market data."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from app.config import KNOWN_CONFLUENCE_CONFIG_HASHES, ConfluenceConfig, config_hash
from app.confluence.engine import band_of, score_symbol, summarize
from app.signals.evidence import make

CFG = ConfluenceConfig()


def day(age: int | None) -> str:
    return f"2026-10-{30 - (age or 0):02d}T00:00:00+05:30"


def ev(source, signal_type, *, category, kind="event", direction="bullish", age=0, status="confirmed", lifecycle="active", metrics=None, quality=None, uid=None, time=None):
    return make(
        symbol="TEST", timeframe="1d", source=source, signal_type=signal_type, category=category, kind=kind, direction=direction,
        signal_time=time or day(age), signal_t=None, age_bars=age, status=status, lifecycle=lifecycle, config_version="1.0.0",
        config_hash="fixture", metrics=metrics or {}, quality=quality, underlying_id=uid,
    )


def fresh(age=0, direction="bullish", dist=1.5, first=True, vol=None, lifecycle="active", uid="fb1"):
    kind = "fresh_breakout_high_10d" if direction == "bullish" else "fresh_breakdown_low_10d"
    return ev("fresh_breakout", kind, category="trigger", direction=direction, age=age, lifecycle=lifecycle, uid=uid, metrics={"distance_atr": dist, "first_in_lookback": first, "volume_ratio": vol, "level": 100.0})


def w52(age=0, direction="bullish", dist=1.5, vol=None, uid="w1"):
    kind = "week52_high_breakout" if direction == "bullish" else "week52_low_breakdown"
    return ev("week52", kind, category="trigger", direction=direction, age=age, uid=uid, metrics={"distance_atr": dist, "volume_ratio": vol, "level": 120.0})


def shock(age=0, direction="bullish", rvol=4.0, status="shock"):
    return ev("volume_shock", f"volume_shock_{direction}", category="volume_candle", direction=direction, age=age, status=status, metrics={"rvol": rvol})


def vstate(status="ok", rvol=1.2):
    return ev("volume_shock", "volume_state", category="context", kind="state", direction="neutral", status=status, metrics={"status": status, "rvol": rvol})


def maru(age=0, direction="bullish", range_atr=2.0):
    return ev("marubozu", f"{direction}_marubozu", category="volume_candle", direction=direction, age=age, metrics={"range_atr": range_atr})


def ema(alignment="bullish_full", p50="above", p200="above", close=100.0):
    d = "bullish" if alignment.startswith("bullish") else "bearish" if alignment.startswith("bearish") else "neutral"
    return ev("ema_alignment", f"ema_{alignment}", category="trend", kind="state", direction=d, status="ok", metrics={"alignment": alignment, "price_vs_ema": {"20": p50, "50": p50, "200": p200}, "close": close})


def pattern(slug, family, quality, direction="bullish", status="forming", breakout_age=None, lifecycle="active", uid=None):
    m = {"pattern": slug, "pattern_name": slug.replace("_", " ").title(), "family": family, "formation_end": day(2)}
    if breakout_age is not None:
        m.update(breakout_age=breakout_age, breakout_time=day(breakout_age))
    return ev("pattern", f"pattern_{slug}", category="setup", kind="formation", direction=direction, age=None, status=status, lifecycle=lifecycle, quality=quality, uid=uid or slug, metrics=m)


def vcp(status="near_pivot", quality=80.0, breakout_age=None, vol=None):
    lifecycle = "invalidated" if status in ("invalidated", "failed_breakout") else "active"
    m = {"status": status, "status_label": status.replace("_", " ").title(), "quality": quality, "base_end": day(3), "breakout_age": breakout_age, "breakout_volume_ratio": vol, "breakout_time": day(breakout_age) if breakout_age is not None else None}
    return ev("vcp", f"vcp_{status}", category="setup", kind="formation", age=breakout_age, status=status, lifecycle=lifecycle, quality=quality, uid="vcp1", metrics=m)


def stage(status="stage2_confirmed", pos="above", dirn="rising"):
    return ev("stage", f"stage_{status}", category="trend", kind="state", status=status, metrics={"stage": status, "stage_label": status, "ema30_position": pos, "ema30_direction": dirn})


def rsline(rs=0.05, trend="improving", sma="rising"):
    return ev("rs_line", "rs_positive" if rs > 0 else "rs_negative", category="relative_strength", kind="state", direction="bullish" if rs > 0 else "bearish", status="ok", metrics={"rs": rs, "rs_trend": trend, "rs_sma_direction": sma})


def rrg(q="leading"):
    return ev("rrg", f"rrg_{q}", category="relative_strength", kind="state", status="ok", metrics={"quadrant": q, "benchmark": "NIFTY 500"})


def cat(res, key):
    return next(c for c in res["categories"] if c["key"] == key)


def statuses(c):
    return {(e["source"], e["status"]) for e in c["evidence"]}


def bull_stack():
    primary = [ema(), vcp("near_pivot", 80.0), fresh(0), shock(0), maru(0), vstate()]
    context = [stage(), rsline(), rrg()]
    return primary, context


# ---------------------------------------------------------------- engine

def test_full_bullish_stack_every_category_and_capped_at_100():
    p, c = bull_stack()
    r = score_symbol(p, c, "bullish", CFG)
    got = {k["key"]: k["score"] for k in r["categories"]}
    assert got == {"trend": 1.0, "setup": 0.8, "trigger": 1.0, "volume_candle": 1.0, "relative_strength": 1.0}
    assert all(k["available"] for k in r["categories"]) and r["coverage"] == 1.0 and r["ranked"]
    # 100 x (20 + 25 x 0.8 + 20 + 25 + 10) / 100 = 95, + 10 combination points, capped at 100
    assert r["base_score"] == 95.0 and r["bonus"] == 10.0 and r["score"] == 100.0
    assert r["combo"]["type"] == "breakout_marubozu_volume" and r["combo"]["session"] == day(0)
    assert r["band"] == "Very High Confluence"
    assert sum(k["points"] for k in r["categories"]) == pytest.approx(r["base_score"], abs=0.02)


def test_score_is_not_a_count_of_positive_scanner_results():
    p, c = bull_stack()
    base = score_symbol(p, c, "bullish", CFG)
    # every row twice (a duplicated signal) -> identical score and signal count
    dup = score_symbol(p + p, c + c, "bullish", CFG)
    assert (dup["score"], dup["base_score"], dup["active_signals"]) == (base["score"], base["base_score"], base["active_signals"])
    assert ("fresh_breakout", "overlapping") in statuses(cat(dup, "trigger"))
    # a second, older 10-day breakout and a second shock add nothing
    more = score_symbol(p + [fresh(2, uid="fb2"), shock(1, rvol=3.0)], c, "bullish", CFG)
    assert cat(more, "trigger")["score"] == cat(base, "trigger")["score"] and cat(more, "volume_candle")["score"] == cat(base, "volume_candle")["score"]
    # three structures of three different families: best + ONE capped diversity bonus
    three = score_symbol([pattern(slug, fam, 60.0, status="validated") for slug, fam in (("bullish_flag", "continuation"), ("double_bottom", "reversal"), ("ascending_triangle", "converging"))], [], "bullish", CFG)
    assert cat(three, "setup")["score"] == pytest.approx(0.6 + 0.15 * 0.6)
    assert sorted(e["status"] for e in cat(three, "setup")["evidence"]) == ["bonus", "overlapping", "used"]


def test_overlapping_patterns_of_one_family_use_the_strongest():
    same = [pattern("bullish_flag", "continuation", 80.0), pattern("bullish_pennant", "continuation", 70.0)]
    r = score_symbol(same, [], "bullish", CFG)
    s = cat(r, "setup")
    assert s["score"] == pytest.approx(0.8 * CFG.forming_factor)
    assert [e["status"] for e in s["evidence"]] == ["used", "overlapping"] and "family" in s["evidence"][1]["note"]
    other = score_symbol(same + [pattern("double_bottom", "reversal", 90.0, status="validated")], [], "bullish", CFG)
    s2 = cat(other, "setup")
    # validated double bottom (0.9) is the best; one continuation pattern adds 0.15 x 0.48; the other overlaps
    assert s2["score"] == pytest.approx(0.9 + 0.15 * 0.48)
    assert [e["status"] for e in s2["evidence"]] == ["used", "bonus", "overlapping"]
    capped = score_symbol([pattern("double_bottom", "reversal", 100.0, status="validated"), pattern("falling_wedge", "converging", 100.0, status="validated")], [], "bullish", CFG)
    assert cat(capped, "setup")["score"] == 1.0  # category capped at 1


def test_marubozu_and_volume_shock_same_session():
    r = score_symbol([shock(0, rvol=2.75), maru(0, range_atr=1.5), vstate()], [], "bullish", CFG)
    vc = cat(r, "volume_candle")
    # V = (2.75 - 1.5) / 2.5 = 0.5 ; C = 0.8 + 0.2 x 0.5 = 0.9 ; 0.6 V + 0.4 C
    assert vc["parts"] == {"volume": 0.5, "candle": 0.9} and vc["score"] == pytest.approx(0.6 * 0.5 + 0.4 * 0.9)
    assert r["combo"]["type"] is None and r["bonus"] == 0.0  # no breakout: no combination bonus
    full = score_symbol([fresh(0), shock(0, rvol=2.75), maru(0, range_atr=1.5), vstate()], [], "bullish", CFG)
    assert full["combo"]["type"] == "breakout_marubozu_volume" and full["bonus"] == CFG.combo_points["breakout_marubozu_volume"]


def test_breakout_plus_volume_and_same_session_rule():
    r = score_symbol([fresh(0), shock(0), vstate()], [], "bullish", CFG)
    assert r["combo"]["type"] == "breakout_volume" and r["bonus"] == CFG.combo_points["breakout_volume"]
    # Marubozu one session earlier is not the breakout session: no three-part combination
    r2 = score_symbol([fresh(0), shock(0), maru(1), vstate()], [], "bullish", CFG)
    assert r2["combo"]["type"] == "breakout_volume"
    # Marubozu + breakout without volume on that session
    r3 = score_symbol([fresh(0), shock(1), maru(0), vstate()], [], "bullish", CFG)
    assert r3["combo"]["type"] == "breakout_marubozu" and r3["bonus"] == CFG.combo_points["breakout_marubozu"]
    # all three on one session, but that session is older than combo_max_age_sessions
    old = CFG.combo_max_age_sessions + 1
    r4 = score_symbol([fresh(old), shock(old), maru(old), vstate()], [], "bullish", CFG)
    assert r4["combo"]["type"] is None and r4["bonus"] == 0.0
    # an elevated (below-threshold) volume candle is not a volume shock
    r5 = score_symbol([fresh(0), shock(0, rvol=1.6, status="elevated"), maru(0), vstate()], [], "bullish", CFG)
    assert r5["combo"]["type"] == "breakout_marubozu"


def test_vcp_near_pivot_is_a_setup_not_a_trigger():
    r = score_symbol([vcp("near_pivot", 80.0), vstate()], [], "bullish", CFG)
    assert cat(r, "setup")["score"] == 0.8 and cat(r, "trigger")["score"] == 0.0 and cat(r, "trigger")["evidence"] == []
    forming = score_symbol([vcp("forming", 80.0)], [], "bullish", CFG)
    assert cat(forming, "setup")["score"] == pytest.approx(0.8 * CFG.forming_factor)
    dev = score_symbol([vcp("developing", 80.0)], [], "bullish", CFG)
    assert cat(dev, "setup")["score"] == 0.0 and cat(dev, "setup")["evidence"][0]["status"] == "overlapping"
    assert cat(score_symbol([vcp("near_pivot", 80.0)], [], "bearish", CFG), "setup")["score"] == 0.0  # bullish structure only


def test_vcp_breakout_counts_as_structure_and_trigger_while_fresh():
    r = score_symbol([vcp("breakout", 75.0, breakout_age=1, vol=2.1)], [], "bullish", CFG)
    assert cat(r, "setup")["score"] == 0.75 and cat(r, "trigger")["score"] == 0.75
    assert cat(r, "trigger")["evidence"][0]["source"] == "vcp"
    stale = score_symbol([vcp("breakout", 75.0, breakout_age=CFG.freshness_sessions["vcp_breakout"] + 1, vol=2.1)], [], "bullish", CFG)
    assert cat(stale, "trigger")["score"] == 0.0 and cat(stale, "trigger")["evidence"][0]["status"] == "expired"
    assert cat(stale, "setup")["score"] == 0.75  # the structure is still valid


def test_10_day_and_52_week_breakouts_are_correlated():
    both = score_symbol([fresh(0, dist=0.0), w52(0, dist=0.0)], [], "bullish", CFG)
    t = cat(both, "trigger")
    # each alone is 0.5; together not 1.0: strongest + 0.15 x the other
    assert t["score"] == pytest.approx(0.5 + 0.15 * 0.5)
    assert sorted(e["status"] for e in t["evidence"]) == ["bonus", "used"]
    strong = score_symbol([fresh(0), w52(0), pattern("ascending_triangle", "converging", 90.0, status="broke_above", breakout_age=0), vcp("breakout", 90.0, breakout_age=0, vol=3.0)], [], "bullish", CFG)
    ts = cat(strong, "trigger")
    assert ts["score"] == 1.0 and ts["points"] <= CFG.weights["trigger"] * 100.0 / sum(CFG.weights[k] for k in ("setup", "trigger", "volume_candle"))
    # breakout volume and the volume shock of the same candle are one measurement
    vol = score_symbol([fresh(0, vol=3.0), shock(0, rvol=3.0), vstate()], [], "bullish", CFG)
    v = cat(vol, "volume_candle")
    assert v["parts"]["volume"] == pytest.approx(0.6) and sorted(e["status"] for e in v["evidence"]) == ["overlapping", "used"]


def test_stale_events_expire_and_are_listed_not_scored():
    p = [fresh(CFG.freshness_sessions["fresh_breakout"] + 1), maru(CFG.freshness_sessions["marubozu"] + 1), shock(CFG.freshness_sessions["volume_shock"] + 1), vstate()]
    r = score_symbol(p, [], "bullish", CFG)
    assert cat(r, "trigger")["score"] == 0.0 and cat(r, "volume_candle")["score"] == 0.0
    assert ("fresh_breakout", "expired") in statuses(cat(r, "trigger"))
    assert {("marubozu", "expired"), ("volume_shock", "expired")} <= statuses(cat(r, "volume_candle"))
    assert all(e["strength"] == 0 for c in r["categories"] for e in c["evidence"] if e["status"] == "expired")
    s = summarize(p, [], "bullish", CFG)
    assert s["fresh_breakout"] is None and s["marubozu"] is None and s["volume_shock"] is None
    # the freshness window is configurable
    wide = replace(CFG, freshness_sessions={**CFG.freshness_sessions, "fresh_breakout": 10})
    assert cat(score_symbol(p, [], "bullish", wide), "trigger")["score"] > 0


def test_invalidated_evidence_is_never_scored_or_shown_as_current():
    p = [pattern("ascending_triangle", "converging", 90.0, status="invalidated", lifecycle="invalidated"), fresh(0, lifecycle="invalidated"), vcp("invalidated", 70.0), vstate()]
    r = score_symbol(p, [], "bullish", CFG)
    assert cat(r, "setup")["score"] == 0.0 and cat(r, "trigger")["score"] == 0.0 and r["combo"]["type"] is None
    assert {("pattern", "invalidated"), ("vcp", "invalidated")} <= statuses(cat(r, "setup"))
    assert ("fresh_breakout", "invalidated") in statuses(cat(r, "trigger"))
    assert r["active_signals"] == 0
    s = summarize(p, [], "bullish", CFG)
    assert s["pattern"] is None and s["fresh_breakout"] is None


def test_missing_data_is_unavailable_not_zero_and_coverage_gates_ranking():
    # no EMA state, no weekly context, latest candle volume unknown
    p = [vcp("near_pivot", 80.0), fresh(0), vstate(status="missing", rvol=None), shock(1)]
    r = score_symbol(p, [], "bullish", CFG)
    av = {k["key"]: k["available"] for k in r["categories"]}
    assert av == {"trend": False, "setup": True, "trigger": True, "volume_candle": False, "relative_strength": False}
    assert all(k["points"] is None for k in r["categories"] if not k["available"])
    assert r["coverage"] == pytest.approx(0.45) and not r["ranked"]
    # normalized over the available categories: 100 x (25 x 0.8 + 20 x 1) / 45
    assert r["base_score"] == pytest.approx(100 * 40 / 45, abs=0.01)
    assert score_symbol(p, [], "bullish", replace(CFG, min_coverage=0.4))["ranked"]
    # no volume_state row at all (e.g. data source without it) -> volume treated as known
    assert cat(score_symbol([shock(0)], [], "bullish", CFG), "volume_candle")["available"]


def test_trend_and_rs_correlated_pairs_get_only_an_agreement_bonus():
    both = score_symbol([ema("bullish_partial")], [stage("early_stage2")], "bullish", CFG)
    t = cat(both, "trend")
    # stage 0.8, EMA 0.7 x 0.7 + 0.3 x 1 = 0.79 -> max 0.8 + agreement 0.1 (not 1.59)
    assert t["score"] == pytest.approx(0.9) and [e["status"] for e in t["evidence"]] == ["used", "bonus"]
    weak = score_symbol([ema("bullish_full")], [stage("stage1")], "bullish", CFG)
    assert cat(weak, "trend")["score"] == 1.0 and cat(weak, "trend")["evidence"][1]["status"] == "overlapping"
    rs = score_symbol([], [rsline(0.02, "flat", "flat"), rrg("improving")], "bullish", CFG)
    assert cat(rs, "relative_strength")["score"] == pytest.approx(0.7)
    rs2 = score_symbol([], [rsline(0.02, "improving", "rising"), rrg("lagging")], "bullish", CFG)
    r2 = cat(rs2, "relative_strength")
    assert r2["score"] == 1.0 and [e["status"] for e in r2["evidence"]] == ["used", "overlapping"]


def test_bearish_is_mirrored_and_scored_separately():
    p = [ema("bearish_full", "below", "below"), pattern("head_shoulders", "reversal", 70.0, direction="bearish", status="broke_below", breakout_age=0), fresh(0, "bearish"), shock(0, "bearish"), maru(0, "bearish"), vstate()]
    c = [stage("other", "below", "falling"), rsline(-0.04, "deteriorating", "falling"), rrg("lagging")]
    bear = score_symbol(p, c, "bearish", CFG)
    bull = score_symbol(p, c, "bullish", CFG)
    assert {k["key"]: k["score"] for k in bear["categories"]} == {"trend": 1.0, "setup": 0.7, "trigger": 1.0, "volume_candle": 1.0, "relative_strength": 1.0}
    assert bear["combo"]["type"] == "breakout_marubozu_volume" and bear["score"] == 100.0 and bear["band"] == "Very High Confluence"
    assert bull["score"] == 0.0 and bull["band"] == "Low Confluence" and bull["combo"]["type"] is None
    assert summarize(p, c, "bearish", CFG)["pattern"]["name"] == "Head Shoulders" and summarize(p, c, "bullish", CFG)["pattern"] is None


def test_bands_and_configurable_weights():
    assert [band_of(x, CFG) for x in (100, 85, 84.99, 70, 69.9, 55, 54.99, 0)] == [
        "Very High Confluence", "Very High Confluence", "High Confluence", "High Confluence", "Moderate Confluence", "Moderate Confluence", "Low Confluence", "Low Confluence"]
    p, c = bull_stack()
    heavy_setup = replace(CFG, weights={"trend": 0, "setup": 100, "trigger": 0, "volume_candle": 0, "relative_strength": 0}, combo_points={})
    r = score_symbol(p, c, "bullish", heavy_setup)
    assert r["base_score"] == 80.0 and r["score"] == 80.0 and r["bonus"] == 0.0
    assert config_hash(heavy_setup) != config_hash(CFG)
    assert KNOWN_CONFLUENCE_CONFIG_HASHES[CFG.version] == config_hash(CFG)


# ---------------------------------------------------------------- snapshots + API

@pytest.fixture(scope="module")
def adv(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from tests.conftest import HOLIDAYS_SRC
    from tests.fixtures_adv import build_adv_app, prepare

    cal = NSECalendar.load(HOLIDAYS_SRC)
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        s, w, d = prepare(client, app, cal)
        yield client, s, w, d, provider


def test_snapshot_is_stored_after_the_scan_and_reused(adv):
    from sqlalchemy import func, select

    from app import database as db

    client, s, w, d, _ = adv
    # the scan builds the snapshot right after it reports "completed": wait for that hook
    deadline = time.monotonic() + 30
    while True:
        with s.engine.connect() as conn:
            runs = conn.execute(select(db.confluence_runs).where(db.confluence_runs.c.primary_scan_id == d["scan_id"])).mappings().all()
        if runs or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert len(runs) == 1 and runs[0]["context_scan_id"] == w["scan_id"] and runs[0]["timeframe"] == "1d"
    r1 = client.get("/api/confluence").json()
    r2 = client.get("/api/confluence", params={"direction": "bearish"}).json()
    assert r1["snapshot"]["snapshot_id"] == runs[0]["snapshot_id"] == r2["snapshot"]["snapshot_id"]
    assert r1["snapshot"]["config_hash"] == KNOWN_CONFLUENCE_CONFIG_HASHES["1.0.0"] and r1["context_scan"]["scan_id"] == w["scan_id"]
    with s.engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(db.confluence_snapshots).where(db.confluence_snapshots.c.snapshot_id == r1["snapshot"]["snapshot_id"])).scalar()
    assert n >= 2 * len({x["symbol"] for x in r1["items"]})  # both directions of every scored symbol
    assert r1["weights"] == CFG.weights and r1["bands"] == CFG.bands and r1["min_coverage"] == CFG.min_coverage


def test_concurrent_requests_build_one_snapshot(adv):
    """A dashboard request that arrives while another builds the snapshot reads the
    stored one instead of building a duplicate."""
    from sqlalchemy import func, select

    from app import database as db
    from app.confluence.service import snapshot

    client, s, w, d, _ = adv
    old_cfg = s.confluence_cfg
    s.confluence_cfg = replace(old_cfg, min_coverage=0.55)  # new inputs key -> nothing stored yet
    try:
        out: list[str] = []
        ts = [threading.Thread(target=lambda: out.append(snapshot(s, "all", "1d", None)["snapshot_id"])) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(set(out)) == 1
        with s.engine.connect() as conn:
            n = conn.execute(select(func.count()).select_from(db.confluence_runs).where(db.confluence_runs.c.config_hash == config_hash(s.confluence_cfg))).scalar()
        assert n == 1
    finally:
        s.confluence_cfg = old_cfg


def test_combination_stock_ranks_first_with_explanation(adv):
    client, *_ = adv
    r = client.get("/api/confluence").json()
    top = r["items"][0]
    assert top["symbol"] == "RWALK" and top["combo"]["type"] == "breakout_marubozu_volume" and top["score"] <= 100.0
    assert top["band"] == "Very High Confluence" and top["ranked"] and top["coverage"] >= CFG.min_coverage
    keys = [c["key"] for c in top["explanation"]]
    assert keys == ["trend", "setup", "trigger", "volume_candle", "relative_strength"]
    used = {(e["source"], e["status"]) for c in top["explanation"] for e in c["evidence"]}
    assert ("vcp", "used") in used and ("marubozu", "used") in used and ("volume_shock", "used") in used
    assert any(st in ("bonus", "overlapping") for _, st in used)  # correlated evidence is shown as capped
    sm = top["summary"]
    assert sm["vcp_status"] == "breakout" and sm["fresh_breakout"]["age"] == 0 and sm["volume_shock"]["rvol"] >= 2 and sm["marubozu"]["age"] == 0
    assert all(r["items"][i]["score"] >= r["items"][i + 1]["score"] for i in range(len(r["items"]) - 1))


def test_screens_filters_and_validation(adv):
    client, *_ = adv
    get = lambda **p: client.get("/api/confluence", params=p)
    combo = get(screen="breakout_marubozu_volume").json()
    assert [x["symbol"] for x in combo["items"]] == ["RWALK"] and combo["direction"] == "bullish"
    assert "RWALK" in [x["symbol"] for x in get(screen="vcp_breakout").json()["items"]]
    assert get(screen="bearish_breakdown").json()["direction"] == "bearish"
    for sc in client.get("/api/confluence/screens").json()["items"]:
        assert get(screen=sc["key"]).status_code == 200 and "test" not in sc
    assert get(screen="nope").status_code == 422
    assert get(min_score=99.99).json()["count"] <= 1
    assert all(x["score"] >= 30 for x in get(min_score=30).json()["items"])
    assert [x["symbol"] for x in get(q="rwa").json()["items"]] == ["RWALK"]
    assert [x["symbol"] for x in get(vcp="breakout").json()["items"]] == ["RWALK"]
    assert "RWALK" in [x["symbol"] for x in get(fresh_breakout=True).json()["items"]]
    assert all(x["summary"]["volume_shock"] for x in get(volume_shock=True).json()["items"])
    assert all(x["band"] == "Very High Confluence" for x in get(band="Very High Confluence").json()["items"])
    allr = get(include_unranked=True).json()["count"]
    assert allr >= get().json()["count"]
    assert len(get(limit=1).json()["items"]) == 1
    stg = get(stage="stage2_confirmed,early_stage2").json()["items"]
    assert all(x["summary"]["stage"] in ("stage2_confirmed", "early_stage2") for x in stg)
    for fno in ("YES", "NO"):
        assert all(x["fno_status"] == fno for x in get(fno=fno, include_unranked=True).json()["items"])
    assert get(direction="sideways").status_code == 422 and get(timeframe="1h").status_code == 422
    assert get(asof="2999-01-01").status_code == 422


def test_bearish_rows_are_separate(adv):
    client, *_ = adv
    bull = {x["symbol"]: x for x in client.get("/api/confluence", params={"include_unranked": True}).json()["items"]}
    bear = {x["symbol"]: x for x in client.get("/api/confluence", params={"include_unranked": True, "direction": "bearish"}).json()["items"]}
    assert set(bull) == set(bear)
    assert all(x["direction"] == "bearish" for x in bear.values())
    assert bear["RWALK"]["score"] < bull["RWALK"]["score"]
    assert (bear["RWALK"]["combo"] or {}).get("type") is None


def test_symbol_endpoint(adv):
    client, *_ = adv
    r = client.get("/api/confluence/symbol", params={"symbol": "rwalk"}).json()
    assert r["symbol"] == "RWALK" and r["bullish"]["direction"] == "bullish" and r["bearish"]["direction"] == "bearish"
    assert r["bullish"]["explanation"] and r["snapshot"]["snapshot_id"]
    assert client.get("/api/confluence/symbol", params={"symbol": "NOPE"}).status_code == 404
    assert client.get("/api/confluence/symbol", params={"symbol": "BAD SYMBOL"}).status_code == 422


def test_refresh_recomputes_from_stored_evidence_without_scanners(adv, monkeypatch):
    client, s, w, d, provider = adv
    before = client.get("/api/confluence").json()
    calls = len(provider.calls)

    def boom(*a, **k):
        raise AssertionError("a scanner ran")

    import app.scanner as scanner_mod
    import app.signals.scan as sigscan

    monkeypatch.setattr(sigscan, "run_signals", boom)
    monkeypatch.setattr(scanner_mod, "run_signals", boom, raising=False)
    r = client.post("/api/confluence/refresh", json={"universe": "all", "timeframe": "1d"}).json()
    assert r["snapshot"]["snapshot_id"] != before["snapshot"]["snapshot_id"]
    after = client.get("/api/confluence").json()
    assert after["snapshot"]["snapshot_id"] == r["snapshot"]["snapshot_id"]  # newest snapshot wins
    assert [(x["symbol"], x["score"]) for x in after["items"]] == [(x["symbol"], x["score"]) for x in before["items"]]  # deterministic
    assert len(provider.calls) == calls  # no data download either


def test_config_change_creates_a_new_snapshot(adv):
    client, s, *_ = adv
    old_cfg = s.confluence_cfg
    try:
        s.confluence_cfg = replace(old_cfg, weights={**old_cfg.weights, "relative_strength": 30})
        r = client.get("/api/confluence").json()
        assert r["snapshot"]["config_hash"] == config_hash(s.confluence_cfg) != config_hash(old_cfg)
        assert r["weights"]["relative_strength"] == 30
    finally:
        s.confluence_cfg = old_cfg
    assert client.get("/api/confluence").json()["snapshot"]["config_hash"] == config_hash(old_cfg)


def test_weekly_snapshot_and_config_endpoint(adv):
    client, s, w, *_ = adv
    r = client.get("/api/confluence", params={"timeframe": "1w", "include_unranked": True}).json()
    assert r["snapshot"]["primary_scan_id"] == w["scan_id"] == r["snapshot"]["context_scan_id"] and r["snapshot"]["timeframe"] == "1w"
    cfg = client.get("/api/config/confluence").json()
    assert cfg["version"] == "1.0.0" and cfg["config_hash"] == KNOWN_CONFLUENCE_CONFIG_HASHES["1.0.0"]
    names = {p["name"] for p in cfg["parameters"]}
    assert {"weights", "bands", "freshness_sessions", "min_coverage", "combo_points"} <= names
    assert len(client.get("/api/confluence/screens").json()["items"]) == 9


def test_no_snapshot_without_a_scan(tmp_path_factory):
    from fastapi.testclient import TestClient

    from app.data.calendar import NSECalendar
    from app.main import create_app
    from tests.conftest import HOLIDAYS_SRC, FixtureProvider, fixture_frames, make_settings
    from tests.fixtures_e2 import FakeHistory

    cal = NSECalendar.load(HOLIDAYS_SRC)
    app = create_app(make_settings(tmp_path_factory.mktemp("cf_empty")), provider=FixtureProvider(fixture_frames(cal)), background=False, index_history=FakeHistory(cal))
    with TestClient(app) as client:
        r = client.get("/api/confluence").json()
        assert r["snapshot"] is None and r["items"] == [] and "Run a 1D scan" in r["note"]
        assert client.get("/api/confluence/symbol", params={"symbol": "RWALK"}).status_code == 404
        assert client.post("/api/confluence/refresh", json={}).status_code == 404
