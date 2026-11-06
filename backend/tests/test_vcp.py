"""Volatility Contraction Pattern detector v2 (successive contractions T1 > T2 > ...).

Synthetic positive and negative bases (app.synthetic_vcp). They prove that the
rules are implemented as configured; real-chart VCP accuracy is NOT claimed
(no labelled examples exist)."""

import numpy as np
import pandas as pd
import pytest

from app.config import VCPConfig
from app.synthetic import POSITIVE, band_series, random_walk, trading_day_index
from app.synthetic_vcp import VCPSpec, base_start_index, pivot_high, vcp_frame
from app.vcp import detect_vcp
from app.vcp.detector import QUALIFIED_STATUSES, STATUS_LABELS

CFG = VCPConfig()
TWO = dict(depths=(16, 8), recover=(0.99, 0.99), down_bars=(8, 5), up_bars=(8, 5), volumes=(1e5, 0.6e5))
FOUR = dict(depths=(28, 15, 8, 4), recover=(0.99, 0.99, 0.99, 0.985), down_bars=(8, 6, 4, 3), up_bars=(8, 6, 5, 4), volumes=(1e5, 0.8e5, 0.6e5, 0.4e5))
FIVE = dict(depths=(25, 16, 10, 6, 4), recover=(0.99,) * 4 + (0.985,), down_bars=(8, 7, 6, 4, 3), up_bars=(8, 7, 6, 5, 4), volumes=(1e5, 0.8e5, 0.6e5, 0.45e5, 0.3e5))
SIX = dict(depths=(30, 20, 14, 9, 6, 4), recover=(0.99,) * 5 + (0.985,), down_bars=(8, 7, 6, 5, 4, 3), up_bars=(8, 7, 6, 5, 4, 4), volumes=(1e5, 0.85e5, 0.7e5, 0.55e5, 0.42e5, 0.3e5))
SPECS = {2: TWO, 3: {}, 4: FOUR, 5: FIVE, 6: SIX}


def run(calendar, cfg=CFG, **kw):
    spec = VCPSpec(**kw)
    return detect_vcp(vcp_frame(spec, calendar), cfg, symbol="TEST", config_hash="h"), spec


def gate(r, name):
    return next(g for g in r["gates"] if g["name"] == name)


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_two_to_six_successive_contractions(calendar, n):
    r, spec = run(calendar, **SPECS[n])
    assert r is not None and r["qualified"] and r["status"] in ("near_pivot", "final_contraction")
    assert r["contractions"] == n and len(r["contraction_list"]) == n
    d = r["depths_pct"]
    assert all(d[i + 1] <= CFG.max_next_contraction_ratio * d[i] for i in range(n - 1))  # decreasing T1 > T2 > ...
    assert all(x >= CFG.min_contraction_pct for x in d) and d[-1] <= CFG.max_final_contraction_pct
    assert r["pivot"] == pytest.approx(pivot_high(spec) + spec.wick, abs=1e-3)  # high of the final contraction (stored to 4 decimals)
    assert CFG.min_base_bars <= r["base_sessions"] <= CFG.max_base_bars
    assert [c["label"] for c in r["contraction_list"]] == [f"T{i + 1}" for i in range(n)]
    assert r["contraction_list"][-1]["final"] is True and not any(c["final"] for c in r["contraction_list"][:-1])


def test_per_contraction_measurements(calendar):
    r, spec = run(calendar)
    t1 = r["contraction_list"][0]
    h0 = spec.high + spec.wick
    low = spec.high * (1 - spec.depths[0] / 100) - spec.wick
    assert t1["high"] == pytest.approx(h0) and t1["low"] == pytest.approx(low)
    assert t1["depth_pct"] == pytest.approx((h0 - low) / h0 * 100, abs=0.01)  # (high - low) / high x 100
    assert t1["bars"] == spec.down_bars[0] and t1["recovery_bars"] == spec.up_bars[0]
    assert t1["avg_volume"] == pytest.approx(1e5) and t1["median_volume"] == pytest.approx(1e5)
    assert t1["volume_vs_avg50"] == pytest.approx(1e5 / 1.2e5, rel=1e-3)  # prior 50 sessions: the advance at 1.2e5
    t2 = r["contraction_list"][1]
    assert t2["volume_vs_prev"] == pytest.approx(0.7, rel=1e-3)
    for c in r["contraction_list"]:
        for k in ("start_time", "end_time", "start_i", "end_i", "high", "low", "depth_pct", "bars", "avg_volume", "median_volume", "volume_vs_avg50", "atr_range"):
            assert c[k] is not None, k
        assert c["start_t"] < c["end_t"] and c["atr_range"] > 0
    assert r["contraction_list"][-1]["recovery_bars"] is None


def test_equal_depth_box_is_not_a_vcp(calendar):
    r, _ = run(calendar, depths=(15, 15, 14))
    assert r is None or (r["status"] not in QUALIFIED_STATUSES and not r["qualified"])


def test_rectangle_only_is_never_a_vcp(calendar):
    rect = band_series(POSITIVE["rectangle"], calendar)
    r = detect_vcp(rect, CFG, symbol="RECT")
    assert r is None or (not r["qualified"] and r["status"] not in QUALIFIED_STATUSES)
    # a pure box after an advance: same-size swings for 80 sessions
    n_adv, n_box = 120, 80
    adv = np.linspace(60, 100, n_adv)
    box = 94 + 6 * np.sin(np.arange(n_box) * 2 * np.pi / 16)
    c = np.concatenate([adv, box])
    o = np.concatenate([[c[0]], c[:-1]])
    f = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3, "close": c, "volume": np.full(len(c), 1e5)}, index=trading_day_index(len(c), calendar))
    r2 = detect_vcp(f, CFG, symbol="BOX")
    assert r2 is None or (not r2["qualified"] and r2["status"] not in QUALIFIED_STATUSES)


@pytest.mark.parametrize("kw", [
    dict(depths=(8, 14), recover=(0.99, 0.99), down_bars=(6, 6), up_bars=(6, 6), volumes=(1e5, 0.8e5)),  # deeper second contraction
    dict(depths=(20, 17, 14), volumes=(1e5, 0.7e5, 0.45e5)),  # shrinking, but only 0.85 x each time
])
def test_invalid_contraction_sequence(calendar, kw):
    r, _ = run(calendar, **kw)
    assert r is None or r["status"] in ("invalidated", "developing") or not gate(r, "progressive_contraction")["passed"]
    assert r is None or not r["qualified"]


def test_volume_dry_up_required(calendar):
    rising, _ = run(calendar, volumes=(0.5e5, 0.7e5, 1.0e5))
    assert rising["status"] == "forming" and not rising["qualified"]
    assert gate(rising, "volume_dry_up")["passed"] is False and gate(rising, "volume_contracting")["passed"] is False
    # contracting vs T1 but final still ~0.9 x the 50-day average: not dried up
    flat, _ = run(calendar, volumes=(1.0e5, 0.95e5, 0.9e5), lead_volume=1.0e5)
    assert gate(flat, "volume_dry_up")["passed"] is False and flat["status"] == "forming"
    assert "volume dry up" in flat["status_reason"]
    dry, _ = run(calendar)
    assert gate(dry, "volume_dry_up")["passed"] is True and gate(dry, "volume_dry_up")["value"] <= CFG.final_volume_ratio
    assert dry["geometry"]["dry_up"]["passed"] is True and dry["metrics"]["final_volume_vs_avg50"] <= CFG.final_volume_ratio


def test_final_contraction_state_and_wide_final(calendar):
    wide, _ = run(calendar, depths=(30, 20, 13), recover=(0.99, 0.99, 0.985), down_bars=(10, 8, 6), up_bars=(10, 8, 6))
    assert wide["status"] == "forming" and wide["final_depth_pct"] > CFG.max_final_contraction_pct
    assert gate(wide, "tight_final_contraction")["passed"] is False
    far, _ = run(calendar, recover=(0.99, 0.99, 0.94))  # qualified, close > 3 % below the pivot
    assert far["status"] == "final_contraction" and far["pivot_distance_pct"] > CFG.near_pivot_pct
    near, _ = run(calendar, depths=(20, 10, 4))
    assert near["status"] == "near_pivot" and 0 <= near["pivot_distance_pct"] <= CFG.near_pivot_pct


def test_pivot_is_final_contraction_high_not_base_high(calendar):
    r, spec = run(calendar, recover=(0.97, 0.97, 0.985))
    assert r["pivot"] < r["first_high"] and r["pivot"] == pytest.approx(pivot_high(spec) + spec.wick)
    assert r["geometry"]["pivot"]["p"] == r["pivot"] and r["geometry"]["pivot"]["t"] == r["contraction_list"][-1]["start_t"]


def test_breakout_requires_volume(calendar):
    r, _ = run(calendar, tail=[(1.02, 2.5e5)])
    assert r["status"] == "breakout" and r["breakout_volume_ratio"] >= CFG.breakout_volume_ratio
    assert r["breakout_age"] == 0 and r["geometry"]["breakout"]["volume_ok"] is True and r["geometry"]["breakout"]["volume"] == 2.5e5
    low, _ = run(calendar, tail=[(1.02, 0.6e5)])
    assert low["status"] == "near_pivot" and low["pivot_crossed_low_volume"] is True  # a close above the pivot without volume is not a breakout


def test_two_close_confirmation(calendar):
    two = VCPConfig(breakout_confirm_closes=2)
    pending, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5)])
    assert pending["status"] == "breakout" and pending["breakout_confirmation"] == "pending"
    ok, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5), (1.03, 1.5e5)])
    assert ok["status"] == "breakout" and ok["breakout_confirmation"] == "two_closes"
    fail, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5), (0.99, 1.0e5)])
    assert fail["status"] == "failed_breakout"


def test_failed_breakout(calendar):
    r, _ = run(calendar, tail=[(1.02, 2.5e5), (0.97, 0.6e5), (0.965, 0.6e5)])
    assert r["status"] == "failed_breakout" and r["event_time"] and r["geometry"]["event"]["status"] == "failed_breakout"


def test_invalidations(calendar):
    after, _ = run(calendar, tail=[(1.02, 2.5e5), (0.92, 1.5e5)])
    assert after["status"] == "invalidated" and "final contraction low" in after["status_reason"]
    before, _ = run(calendar, tail=[(0.85, 1e5), (0.75, 1e5)])
    assert before["status"] == "invalidated"


def test_no_future_leakage(calendar):
    spec = VCPSpec(tail=[(1.02, 2.5e5), (1.03, 1.5e5), (1.04, 1.2e5)])
    f = vcp_frame(spec, calendar)
    full = detect_vcp(f, CFG, symbol="TEST", config_hash="h")
    b = full["breakout_index"]
    at_b = detect_vcp(f.iloc[: b + 1], CFG, symbol="TEST", config_hash="h")
    # the breakout is found on its own candle with only data up to it; the contractions do not change later
    assert at_b["status"] == "breakout" and at_b["breakout_index"] == b and at_b["breakout_age"] == 0
    assert at_b["depths_pct"] == full["depths_pct"] and at_b["pivot"] == full["pivot"] and at_b["vcp_id"] == full["vcp_id"]
    before = detect_vcp(f.iloc[:b], CFG, symbol="TEST", config_hash="h")
    assert before["status"] in ("near_pivot", "final_contraction") and before["breakout_index"] is None
    # the final contraction's low is provisional until k later candles confirm it
    fin_lo = full["contraction_list"][-1]["end_i"]
    early = detect_vcp(f.iloc[: fin_lo + 2], CFG, symbol="TEST", config_hash="h")
    assert early is not None and early["contraction_list"][-1]["provisional"] is True
    # changing candles after a cutoff never changes the result at that cutoff
    g = f.copy()
    g.iloc[b + 1 :, g.columns.get_loc("close")] *= 0.5
    assert detect_vcp(g.iloc[: b + 1], CFG, symbol="TEST", config_hash="h")["quality"] == at_b["quality"]


def test_score_components_and_weights(calendar):
    r, _ = run(calendar)
    comps = {c["name"]: c for c in r["quality_components"]}
    assert set(comps) == {"contraction_progression", "final_contraction", "volume_behaviour", "base_structure", "trend_context", "pivot_quality"}
    assert {k: c["weight"] for k, c in comps.items()} == {"contraction_progression": 30, "final_contraction": 20, "volume_behaviour": 20, "base_structure": 10, "trend_context": 10, "pivot_quality": 10}
    for c in comps.values():
        assert 0.0 <= c["score"] <= 1.0 and c["formula"]
    expected = 100 * sum(c["weight"] * c["score"] for c in comps.values()) / sum(CFG.weights.values())
    assert r["quality"] == pytest.approx(expected, abs=0.01) and 0 <= r["quality"] <= 100
    assert comps["final_contraction"]["raw"] == r["final_depth_pct"]
    wide, _ = run(calendar, depths=(30, 20, 13), recover=(0.99, 0.99, 0.985), down_bars=(10, 8, 6), up_bars=(10, 8, 6))
    assert {c["name"]: c for c in wide["quality_components"]}["final_contraction"]["score"] < comps["final_contraction"]["score"]
    weak_vol, _ = run(calendar, volumes=(1e5, 0.98e5, 0.95e5))
    assert {c["name"]: c for c in weak_vol["quality_components"]}["volume_behaviour"]["score"] < comps["volume_behaviour"]["score"]


def test_geometry_is_a_contraction_structure_not_a_box(calendar):
    r, spec = run(calendar, tail=[(1.02, 2.5e5)])
    g = r["geometry"]
    assert g["schema_version"] == "vcp-2.0" and len(g["contractions"]) == 3
    for el in g["contractions"]:
        assert el["t_high"] < el["t_low"] and el["p_high"] > el["p_low"] and el["label"].startswith("T") and el["depth_pct"] > 0
    assert [el["final"] for el in g["contractions"]] == [False, False, True]
    assert len(g["swings"]) == 6 and {s["kind"] for s in g["swings"]} == {"high", "low"}
    assert g["dry_up"]["t_start"] <= g["dry_up"]["t_end"] and g["dry_up"]["volume_ratio"] is not None
    assert g["base"]["i_start"] == base_start_index(spec) and g["breakout"]["i"] == g["last_candle"]["i"]
    text = " ".join(r["explanation"])
    assert "Pivot" in text and "T1" in text and "PASS progressive contraction" in text and "ENGINEERING" in text


def test_developing_uptrend_and_history(calendar):
    one, _ = run(calendar, depths=(15,), recover=(0.98,), down_bars=(8,), up_bars=(8,), volumes=(1e5,))
    assert one["status"] == "developing" and one["contractions"] == 1 and not one["qualified"]
    assert run(calendar, advance_pct=10.0)[0] is None  # no prior uptrend
    assert detect_vcp(vcp_frame(VCPSpec(), calendar).iloc[:10], CFG) is None


def test_old_breakout_is_no_longer_reported(calendar):
    tail = [(1.02, 2.5e5)] + [(1.02 + 0.002 * k, 1.2e5) for k in range(1, CFG.post_breakout_track_sessions + 3)]
    r, _ = run(calendar, tail=tail)
    assert r is None or r["status"] != "breakout" or r["breakout_age"] <= CFG.post_breakout_track_sessions


def test_random_walks_rarely_produce_qualified_vcps(calendar):
    qualified = 0
    for seed in range(40):
        r = detect_vcp(random_walk(800, calendar, seed=seed, vol=0.018), CFG, symbol="R")
        qualified += bool(r and r["qualified"])
    assert qualified <= 2


def test_status_labels_are_the_spec_states():
    assert list(STATUS_LABELS.values()) == ["Developing", "Forming", "Final Contraction", "Near Pivot", "Breakout", "Failed Breakout", "Invalidated"]


def test_spec_default_thresholds():
    assert (CFG.min_contractions, CFG.max_contractions, CFG.min_base_bars, CFG.max_base_bars) == (2, 6, 15, 120)
    assert (CFG.max_next_contraction_ratio, CFG.min_contraction_pct, CFG.max_final_contraction_pct, CFG.final_volume_ratio) == (0.75, 3.0, 10.0, 0.70)
    assert (CFG.near_pivot_pct, CFG.breakout_volume_ratio) == (3.0, 1.5)
