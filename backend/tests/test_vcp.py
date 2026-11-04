"""Volatility Contraction Pattern detector (spec 6 / 15.4).

Synthetic positive and negative bases (app.synthetic_vcp). They prove that the
rules are implemented as configured; real-chart VCP accuracy is NOT claimed
(no labelled examples exist)."""

import numpy as np
import pytest

from app.config import VCPConfig
from app.synthetic import random_walk
from app.synthetic_vcp import VCPSpec, base_start_index, pivot_high, vcp_frame
from app.vcp import detect_vcp
from app.vcp.detector import QUALIFIED_STATUSES, STATUS_LABELS

CFG = VCPConfig()
TWO = dict(depths=(16, 8), recover=(0.99, 0.99), down_bars=(8, 5), up_bars=(8, 5), volumes=(1e5, 0.6e5))
FOUR = dict(depths=(28, 15, 8, 4), recover=(0.99, 0.99, 0.99, 0.985), down_bars=(8, 6, 4, 3), up_bars=(8, 6, 5, 4), volumes=(1e5, 0.8e5, 0.6e5, 0.4e5))


def run(calendar, cfg=CFG, **kw):
    spec = VCPSpec(**kw)
    return detect_vcp(vcp_frame(spec, calendar), cfg, symbol="TEST", config_hash="h"), spec


@pytest.mark.parametrize("kw,n", [(TWO, 2), ({}, 3), (FOUR, 4)])
def test_two_three_and_four_contraction_vcps(calendar, kw, n):
    r, spec = run(calendar, **kw)
    assert r is not None and r["qualified"] and r["status"] in ("near_pivot", "tightening")
    assert r["contractions"] == n and len(r["contraction_list"]) == n
    d = r["depths_pct"]
    assert all(d[i + 1] <= CFG.contraction_ratio_max * d[i] for i in range(n - 1))
    assert r["pivot"] == pytest.approx(pivot_high(spec) + spec.wick, abs=1e-6)  # high of the final contraction
    assert r["base_start"] and r["base_sessions"] >= CFG.min_base_sessions


def test_individual_contraction_measurements(calendar):
    r, spec = run(calendar)
    t1 = r["contraction_list"][0]
    h0 = spec.high + spec.wick
    low = spec.high * (1 - spec.depths[0] / 100) - spec.wick
    assert t1["start_price"] == pytest.approx(h0) and t1["end_price"] == pytest.approx(low)
    assert t1["depth_pct"] == pytest.approx((h0 - low) / h0 * 100, abs=0.01)
    assert t1["bars"] == spec.down_bars[0] and t1["avg_volume"] == pytest.approx(1e5)
    assert t1["volume_vs_avg50"] == pytest.approx(1e5 / 1.2e5, rel=1e-3)  # prior 50 sessions: the advance at 1.2e5
    for c in r["contraction_list"]:
        for k in ("start_time", "end_time", "start_price", "end_price", "depth", "depth_pct", "bars", "avg_volume", "min_volume", "volume_vs_avg50"):
            assert c[k] is not None
    assert r["contraction_list"][-1]["provisional"] in (True, False)


def test_non_contracting_base_is_not_a_vcp(calendar):
    r, _ = run(calendar, depths=(15, 15, 14))
    assert r is None or (r["status"] not in QUALIFIED_STATUSES and not r["qualified"])


def test_expanding_volatility_is_rejected(calendar):
    r, _ = run(calendar, depths=(8, 14), recover=(0.99, 0.99), down_bars=(6, 6), up_bars=(6, 6), volumes=(1e5, 0.8e5))
    assert r is None or r["status"] == "invalidated"


def test_descending_depths_but_rising_volume_is_not_qualified(calendar):
    r, _ = run(calendar, volumes=(0.5e5, 0.7e5, 1.0e5))
    assert r["status"] == "forming" and not r["qualified"]
    assert next(g for g in r["gates"] if g["name"] == "volume_dry_up")["passed"] is False


def test_insufficient_volume_contraction(calendar):
    r, _ = run(calendar, volumes=(1e5, 0.98e5, 0.95e5))
    assert r["status"] == "forming" and "volume dry up" in r["status_reason"]


def test_wide_final_contraction_stays_forming(calendar):
    r, _ = run(calendar, depths=(30, 20, 13), recover=(0.99, 0.99, 0.985), down_bars=(10, 8, 6), up_bars=(10, 8, 6))
    assert r["status"] == "forming" and r["final_depth_pct"] > CFG.final_max_depth_pct
    assert next(g for g in r["gates"] if g["name"] == "tight_final_contraction")["passed"] is False


def test_tight_final_contraction_and_near_pivot(calendar):
    r, _ = run(calendar, depths=(20, 10, 4))
    assert r["final_depth_pct"] <= CFG.final_max_depth_pct and r["status"] == "near_pivot"
    assert 0 <= r["pivot_distance_pct"] <= CFG.near_pivot_pct
    far, _ = run(calendar, recover=(0.99, 0.99, 0.94))
    assert far["status"] == "tightening"


def test_pivot_is_final_contraction_high_not_base_high(calendar):
    r, spec = run(calendar, recover=(0.97, 0.97, 0.985))
    assert r["pivot"] < r["first_high"] and r["pivot"] == pytest.approx(pivot_high(spec) + spec.wick)
    assert r["geometry"]["pivot"]["p"] == r["pivot"]


def test_breakout_with_volume_expansion(calendar):
    spec = VCPSpec()
    r, _ = run(calendar, tail=[(1.02, 2.5e5)])
    assert r["status"] == "breakout" and r["breakout_volume_ratio"] >= CFG.breakout_volume_ratio
    assert r["breakout_age"] == 0 and r["geometry"]["breakout"]["volume_ok"] is True


def test_breakout_without_volume_expansion_is_not_a_breakout(calendar):
    r, _ = run(calendar, tail=[(1.02, 0.6e5)])
    assert r["status"] == "pivot_cross_low_volume" and r["status"] != "breakout"


def test_two_close_confirmation(calendar):
    two = VCPConfig(breakout_confirm_closes=2)
    pending, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5)])
    assert pending["status"] == "breakout_pending"
    ok, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5), (1.03, 1.5e5)])
    assert ok["status"] == "breakout"
    fail, _ = run(calendar, cfg=two, tail=[(1.02, 2.5e5), (0.99, 1.0e5)])
    assert fail["status"] == "failed_breakout"


def test_failed_breakout(calendar):
    r, _ = run(calendar, tail=[(1.02, 2.5e5), (0.97, 0.6e5), (0.965, 0.6e5)])
    assert r["status"] == "failed_breakout" and r["event_time"]


def test_invalidated_after_breakout_below_final_low(calendar):
    r, _ = run(calendar, tail=[(1.02, 2.5e5), (0.92, 1.5e5)])
    assert r["status"] == "invalidated" and "final contraction low" in r["status_reason"]


def test_invalidated_before_breakout(calendar):
    r, _ = run(calendar, tail=[(0.85, 1e5), (0.75, 1e5)])
    assert r["status"] == "invalidated"


def test_old_breakout_is_no_longer_reported(calendar):
    tail = [(1.02, 2.5e5)] + [(1.02 + 0.002 * k, 1.2e5) for k in range(1, CFG.post_breakout_track_sessions + 3)]
    r, _ = run(calendar, tail=tail)
    assert r is None or r["status"] != "breakout" or r["breakout_age"] <= CFG.post_breakout_track_sessions


def test_developing_with_one_contraction(calendar):
    r, _ = run(calendar, depths=(15,), recover=(0.98,), down_bars=(8,), up_bars=(8,), volumes=(1e5,))
    assert r["status"] == "developing" and r["contractions"] == 1 and not r["qualified"]


def test_no_prior_uptrend_no_vcp(calendar):
    r, _ = run(calendar, advance_pct=10.0)
    assert r is None


def test_score_components_and_weights(calendar):
    r, _ = run(calendar)
    comps = {c["name"]: c for c in r["quality_components"]}
    assert set(comps) == set(CFG.weights)
    for c in comps.values():
        assert 0.0 <= c["score"] <= 1.0 and c["weight"] == CFG.weights[c["name"]] and c["formula"]
    expected = 100 * sum(c["weight"] * c["score"] for c in comps.values()) / sum(CFG.weights.values())
    assert r["quality"] == pytest.approx(expected, abs=0.01)
    assert comps["final_tightness"]["raw"] == r["final_depth_pct"]
    wide, _ = run(calendar, depths=(30, 20, 13), recover=(0.99, 0.99, 0.985), down_bars=(10, 8, 6), up_bars=(10, 8, 6))
    assert {c["name"]: c for c in wide["quality_components"]}["final_tightness"]["score"] == 0.0
    weak_vol, _ = run(calendar, volumes=(1e5, 0.98e5, 0.95e5))
    assert {c["name"]: c for c in weak_vol["quality_components"]}["volume_contraction"]["score"] < comps["volume_contraction"]["score"]


def test_geometry_and_explanation(calendar):
    r, spec = run(calendar, tail=[(1.02, 2.5e5)])
    g = r["geometry"]
    assert g["schema_version"] == "vcp-1.0" and len(g["contractions"]) == 3
    for el in g["contractions"]:
        assert el["t_high"] < el["t_low"] and el["p_high"] > el["p_low"] and el["label"].startswith("T")
    assert g["base"]["i_start"] == base_start_index(spec) and g["breakout"]["i"] == g["last_candle"]["i"]
    text = " ".join(r["explanation"])
    assert "Pivot" in text and "T1" in text and "PASS progressive contraction" in text and "ENGINEERING" in text


def test_asof_result_unchanged_by_later_candles_and_stable_id(calendar):
    spec = VCPSpec(tail=[(1.02, 2.5e5), (1.03, 1.5e5), (1.04, 1.2e5)])
    f = vcp_frame(spec, calendar)
    early = detect_vcp(f.iloc[:-3], CFG, symbol="TEST", config_hash="h")
    again = detect_vcp(f.iloc[:-3].copy(), CFG, symbol="TEST", config_hash="h")
    late = detect_vcp(f, CFG, symbol="TEST", config_hash="h")
    assert early["status"] == "near_pivot" and late["status"] == "breakout"
    assert early["vcp_id"] == late["vcp_id"] == again["vcp_id"]  # same base start -> same identity
    assert early["contraction_list"] == again["contraction_list"]


def test_random_walks_rarely_produce_qualified_vcps(calendar):
    qualified = 0
    for seed in range(40):
        r = detect_vcp(random_walk(800, calendar, seed=seed, vol=0.018), CFG, symbol="R")
        qualified += bool(r and r["qualified"])
    assert qualified <= 2


def test_status_labels_cover_every_status():
    assert set(STATUS_LABELS) >= {"developing", "forming", "tightening", "near_pivot", "breakout", "failed_breakout", "invalidated", "pivot_cross_low_volume", "breakout_pending"}


def test_insufficient_history(calendar):
    f = vcp_frame(VCPSpec(), calendar).iloc[:10]
    assert detect_vcp(f, CFG) is None
