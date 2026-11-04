"""Labels, matching, precision/recall, comparison and sweeps (P3).

Ground truth comes from the synthetic fixtures' known pattern windows, never
from the detector's own output.
"""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from app.config import DEFAULT_CONFIG
from app.evaluation.engine import ConfigOverrideError, EvalOptions, Evaluator, make_variant
from app.evaluation.labels import LabelStore, default_dataset
from app.synthetic import POSITIVE, pattern_window
from app.timeutil import iso

ASOF = "2026-10-30"  # fixture series end


@pytest.fixture
def env(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN", "ATRI", "RECT"], "1d")
    return s, LabelStore(s.engine, s.settings.validation_modulus), Evaluator(s)


def window_times(s, symbol, slug):
    df = s.store.load(symbol, "1d")
    a, b = pattern_window(POSITIVE[slug])
    return iso(df.index[a]), iso(df.index[b])


def label(store, s, symbol, slug, verdict="valid", pattern=None, dataset="tuning", shift=0):
    start, end = window_times(s, symbol, slug)
    if shift:
        df = s.store.load(symbol, "1d")
        a, b = pattern_window(POSITIVE[slug])
        start, end = iso(df.index[a + shift]), iso(df.index[b])
    return store.create(
        {"symbol": symbol, "timeframe": "1d", "pattern": pattern or slug, "verdict": verdict, "asof": ASOF,
         "formation_start": start, "formation_end": end, "dataset": dataset, "notes": "test"}
    )


def test_label_crud_and_default_split(env):
    s, store, _ = env
    lab = label(store, s, "FCHAN", "falling_channel", dataset=None)
    assert lab["dataset"] == default_dataset("FCHAN", s.settings.validation_modulus)
    upd = store.update(lab["label_id"], {**lab, "verdict": "near_miss", "notes": "edited"})
    assert upd["verdict"] == "near_miss" and upd["notes"] == "edited" and upd["created_at"] == lab["created_at"]
    assert [x["label_id"] for x in store.list(symbol="FCHAN")] == [lab["label_id"]]
    assert store.delete(lab["label_id"]) and store.get(lab["label_id"]) is None


def test_default_split_is_deterministic_and_about_one_in_five():
    syms = [f"S{i}" for i in range(2000)]
    split = [default_dataset(x, 5) for x in syms]
    assert split == [default_dataset(x, 5) for x in syms]
    assert 0.15 < split.count("validation") / len(split) < 0.25


def test_precision_recall_and_cases(env):
    s, store, ev = env
    tp = label(store, s, "FCHAN", "falling_channel")  # real falling channel -> TP
    fn = label(store, s, "RECT", "rectangle", pattern="rising_channel")  # no such pattern -> FN
    fd = label(store, s, "ATRI", "ascending_triangle", verdict="false_detection")  # detector output judged wrong -> FP
    res = ev.evaluate(store.list(), DEFAULT_CONFIG)
    m = res["metrics"]["tuning"]
    tri = m["by_pattern"]["ascending_triangle"]
    assert m["by_pattern"]["falling_channel"]["tp"] == 1
    assert m["by_pattern"]["rising_channel"]["fn"] == 1
    assert tri["fp"] >= 1 and tri["precision"] == 0.0
    kinds = {(c["type"], c["label_id"]) for c in res["cases"]}
    assert ("TP", tp["label_id"]) in kinds and ("FN", fn["label_id"]) in kinds and ("FP", fd["label_id"]) in kinds
    # The rectangle the detector found on RECT is out of scope (only rising_channel was labelled there) ...
    assert not any(c["symbol"] == "RECT" and c["pattern"] == "rectangle" for c in res["cases"])
    # ... unless every pattern type on a labelled chart counts.
    wide = ev.evaluate(store.list(), DEFAULT_CONFIG, EvalOptions(fp_scope="all"))
    assert any(c["symbol"] == "RECT" and c["pattern"] == "rectangle" and c["type"] == "FP" for c in wide["cases"])


def test_iou_threshold_controls_matching(env):
    s, store, ev = env
    label(store, s, "FCHAN", "falling_channel", shift=60)  # label covers only the last third of the formation
    strict = ev.evaluate(store.list(), DEFAULT_CONFIG)["metrics"]["all"]["by_pattern"]["falling_channel"]
    loose = ev.evaluate(store.list(), DEFAULT_CONFIG, EvalOptions(iou_threshold=0.2))["metrics"]["all"]["by_pattern"]["falling_channel"]
    assert strict["fn"] == 1 and strict["tp"] == 0
    assert loose["tp"] == 1


def test_each_label_matched_once_extra_detection_is_duplicate(env, monkeypatch):
    s, store, ev = env
    lab = label(store, s, "FCHAN", "falling_channel")
    a, b = pattern_window(POSITIVE["falling_channel"])
    mk = lambda i, q, s0: SimpleNamespace(detection_id=f"d{i}", pattern="falling_channel", status="validated", quality=q,  # noqa: E731
                                          formation_start_index=s0, formation_end_index=b, formation_start="x", formation_end="y")
    monkeypatch.setattr("app.evaluation.engine.detect_series", lambda ctx, cal: [mk(1, 80.0, a), mk(2, 90.0, a + 5)])
    res = ev.evaluate(store.list(), DEFAULT_CONFIG)
    tp = [c for c in res["cases"] if c["type"] == "TP"]
    dup = [c for c in res["cases"] if c.get("reason") == "duplicate"]
    assert len(tp) == 1 and tp[0]["detection_id"] == "d1" and tp[0]["label_id"] == lab["label_id"]  # best IoU wins
    assert len(dup) == 1 and dup[0]["detection_id"] == "d2"
    assert res["metrics"]["all"]["overall"]["all"]["duplicates"] == 1


def test_unusable_labels_are_skipped_not_guessed(env):
    s, store, ev = env
    store.create({"symbol": "FCHAN", "timeframe": "1d", "pattern": "falling_channel", "verdict": "valid", "asof": ASOF,
                  "formation_start": "2026-10-31T09:15:00+05:30", "formation_end": "2026-11-02T09:15:00+05:30", "dataset": "tuning"})
    store.create({"symbol": "NOPE", "timeframe": "1d", "pattern": "rectangle", "verdict": "valid", "asof": ASOF,
                  "formation_start": "2026-10-01T09:15:00+05:30", "formation_end": "2026-10-20T09:15:00+05:30", "dataset": "tuning"})
    res = ev.evaluate(store.list(), DEFAULT_CONFIG)
    assert len(res["skipped"]) == 2 and not res["cases"]


def test_validation_set_reported_separately(env):
    s, store, ev = env
    label(store, s, "FCHAN", "falling_channel", dataset="validation")
    label(store, s, "RECT", "rectangle", dataset="tuning")
    m = ev.evaluate(store.list(), DEFAULT_CONFIG)["metrics"]
    assert m["validation"]["by_pattern"]["falling_channel"]["tp"] == 1 and "falling_channel" not in m["tuning"]["by_pattern"]
    assert m["tuning"]["by_pattern"]["rectangle"]["tp"] == 1


def test_compare_lists_added_and_removed_errors(env):
    s, store, ev = env
    lab = label(store, s, "FCHAN", "falling_channel")
    strict = make_variant(DEFAULT_CONFIG, {"close_trend_min_r2": 0.999})
    cmp = ev.compare(store.list(), DEFAULT_CONFIG, strict)
    assert [c["label_id"] for c in cmp["fn_added"]] == [lab["label_id"]]
    assert cmp["diff"]["tuning"]["falling_channel|1d"]["recall_delta"] == -1.0
    assert "no configuration is changed" in cmp["note"]


def test_sweep_reports_every_value_without_choosing(env):
    s, store, ev = env
    label(store, s, "FCHAN", "falling_channel")
    sw = ev.sweep(store.list(), DEFAULT_CONFIG, "close_trend_min_r2", [0.5, 0.999])
    assert [r["value"] for r in sw["rows"]] == [0.5, 0.999]
    assert [r["is_current"] for r in sw["rows"]] == [True, False]
    assert sw["rows"][0]["tuning"]["recall"] == 1.0 and sw["rows"][1]["tuning"]["recall"] == 0.0
    assert "does not select" in sw["note"] and "best" not in sw


def test_make_variant_validation():
    v = make_variant(DEFAULT_CONFIG, {"converging_cr_max": "0.65", "pole_min_bars": 4.0})
    assert v.converging_cr_max == 0.65 and v.pole_min_bars == 4 and "+variant." in v.version
    assert make_variant(DEFAULT_CONFIG, {}) is DEFAULT_CONFIG
    for bad in ({"no_such_param": 1}, {"version": "9"}, {"parallel_lengths": [10]}, {"pole_min_bars": 3.5}, {"suppress_collapsed_subwindows": 1}):
        with pytest.raises(ConfigOverrideError):
            make_variant(DEFAULT_CONFIG, bad)
    assert replace(DEFAULT_CONFIG).version == DEFAULT_CONFIG.version  # base untouched


def test_evaluation_runs_persist(env):
    s, store, ev = env
    label(store, s, "FCHAN", "falling_channel")
    res = ev.evaluate(store.list(), DEFAULT_CONFIG)
    run_id = ev.save_run("evaluate", DEFAULT_CONFIG, None, res["label_count"], res["metrics"], {"cases": res["cases"]})
    from sqlalchemy import select

    from app import database as db

    with s.engine.connect() as conn:
        row = conn.execute(select(db.evaluation_runs).where(db.evaluation_runs.c.run_id == run_id)).mappings().first()
    assert row["kind"] == "evaluate" and row["label_count"] == 1 and row["config_hash"]
    assert date.fromisoformat(row["created_at"][:10])


def test_degenerate_variants_never_crash_scoring(calendar):
    from app.patterns.base import SeriesContext
    from app.patterns.engine import detect_series
    from app.synthetic import band_series

    df = band_series(POSITIVE["falling_channel"], calendar)
    for over in ({"close_trend_min_r2": 0.9}, {"converging_cr_min": 0.6}, {"parallel_min_containment": 1.0}, {"quality_size_atr_full": 0.0}):
        cfg = make_variant(DEFAULT_CONFIG, over)
        for d in detect_series(SeriesContext("S", "1d", df, cfg), calendar):
            assert 0 <= d.quality <= 100 and all(0 <= c["score"] <= 1 for c in d.quality_components)
