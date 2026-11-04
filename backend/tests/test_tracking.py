"""Cross-scan lifecycle tracking and outcome measurement (P3).

Successive live scans are simulated by growing the completed-candle series one
bar at a time and running detection + tracking on each prefix.
"""

from dataclasses import replace

import pytest
from sqlalchemy import select

from app import database as db
from app.analytics.outcomes import aggregate, compute_outcome
from app.config import DEFAULT_CONFIG
from app.lifecycle.tracking import Tracker, remap_geometry, epoch_index
from app.patterns.base import SeriesContext
from app.patterns.engine import detect_series
from app.synthetic import POSITIVE, BandSpec, band_series, pattern_window
from app.synthetic_p2 import P2_INVALIDATION, PathSpec, series_for
from app.timeutil import iso


@pytest.fixture
def engine(tmp_path):
    e = db.make_engine(tmp_path / "t.db")
    db.migrate(e)
    return e


def ctx_of(df):
    return SeriesContext("SYN", "1d", df, DEFAULT_CONFIG)


def simulate(engine, df, start, calendar, end=None):
    """Run detection + tracking on prefixes df[:start] .. df[:end]; returns per-scan outputs."""
    tracker = Tracker(engine, (2, 5))
    outs = []
    for cut in range(start, (end or len(df)) + 1):
        ctx = ctx_of(df.iloc[:cut])
        dets = [d for d in detect_series(ctx, calendar) if d.pattern == "falling_channel"]
        outs.append((cut, tracker.process(f"scan{cut:04d}", ctx, dets)))
    return outs


def tracks(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(db.pattern_tracks).order_by(db.pattern_tracks.c.first_detected_at)).mappings()]


def events(engine, tid):
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(select(db.track_events.c.event).where(db.track_events.c.track_id == tid).order_by(db.track_events.c.id))]


def test_breakout_freezes_geometry_and_failed_breakout_is_terminal(engine, calendar):
    spec = replace(POSITIVE["falling_channel"], post=[0.5, 0.6, 0.7, 1.3, 1.35, 1.4, 0.7, 0.6, 0.5, 0.5])
    df = band_series(spec, calendar)
    _, e = pattern_window(spec)
    outs = simulate(engine, df, e + 4, calendar)
    trs = [t for t in tracks(engine) if t["pattern"] == "falling_channel"]
    first = trs[0]
    assert first["state"] == "closed" and first["closed_reason"] == "invalidated" and first["status"] == "invalidated"
    assert first["breakout_direction"] == "up" and first["breakout_time"]
    ev = events(engine, first["track_id"])
    assert ev[0] == "first_detected" and "broke_above" in ev and ev[-1] == "invalidated"
    # While frozen, every scan reports the tracked (frozen) pattern - never a refit.
    statuses = []
    for cut, out in outs:
        fc = [d for d in out if d.pattern == "falling_channel"]
        if any(d.extra.get("tracked") for d in fc):
            assert len(fc) == 1, f"refit reported next to the frozen track at cut {cut}"
            statuses.append(fc[0].status)
    assert statuses[0] == "broke_above" and statuses[-1] == "invalidated"
    frozen_geoms = {db.dumps([el for el in d.geometry["elements"] if el["id"] in ("upper", "lower")]) for _, out in outs for d in out if d.extra.get("tracked")}
    assert len(frozen_geoms) == 1  # identical frozen boundaries across scans


FAILED = [0.5, 0.6, 0.7, 1.3, 1.35, 1.4, 0.7, 0.6, 0.5, 0.5]  # break above, three closes back inside, one more bar


def test_invalidated_track_is_never_reactivated_or_duplicated(engine, calendar):
    spec = replace(POSITIVE["falling_channel"], post=FAILED)
    df = band_series(spec, calendar)
    _, e = pattern_window(spec)
    simulate(engine, df, e + 4, calendar)
    before = tracks(engine)
    closed = {t["track_id"] for t in before if t["state"] == "closed" and t["closed_reason"] == "invalidated"}
    assert closed
    # Re-running the final scan (no new candle): the same failed structure is seen again.
    tracker = Tracker(engine, (5,))
    ctx = ctx_of(df)
    for n in range(2):
        out = tracker.process(f"again{n}", ctx, [d for d in detect_series(ctx, calendar) if d.pattern == "falling_channel"])
        for d in out:
            if d.status == "invalidated":
                assert d.extra["track_id"] in closed  # linked to the closed track, never a new one
    after = tracks(engine)
    assert len(after) == len(before)  # no duplicate track per rescan (live QA: 18 duplicates on one 1D rescan)
    assert {(t["track_id"], t["state"], t["closed_reason"]) for t in after} == {(t["track_id"], t["state"], t["closed_reason"]) for t in before}


# Reversals are the patterns that can already be terminal on first sight: their
# formation ends at the latest pivot, so the lifecycle can run far past it (line
# windows always end three bars before the newest candle).
DT_ARCHIVED = PathSpec(points=((0, 900), (25, 1000), (37, 940), (49, 1003), (53, 935), (56, 920), (78, 915)))


@pytest.mark.parametrize("slug", ["double_top", "head_shoulders"])
def test_invalidated_on_first_sight_is_closed_once(engine, calendar, slug):
    """Live QA (1D rescan, no new candle): 18 already-invalidated reversals got a new
    closed track on every scan, with invalidated_time = the latest candle."""
    ctx = ctx_of(series_for(P2_INVALIDATION[slug][1], calendar))
    det = [d for d in detect_series(ctx, calendar) if d.pattern == slug][0]
    assert det.status == "invalidated"
    inv_time = [ev["time"] for ev in det.events if ev["event"] == "invalidated"][-1]
    assert inv_time != det.last_candle_time
    tracker = Tracker(engine, (5,))
    for n in range(3):
        out = tracker.process(f"s{n}", ctx, [d for d in detect_series(ctx, calendar) if d.pattern == slug])
        assert len(out) == 1
    trs = tracks(engine)
    assert len(trs) == 1
    t = trs[0]
    assert t["state"] == "closed" and t["closed_reason"] == "invalidated" and t["invalidated_time"] == inv_time
    assert all(d.extra["track_id"] == t["track_id"] for d in out)
    assert events(engine, t["track_id"]).count("first_detected") == 1


def test_archived_on_first_sight_is_closed(engine, calendar):
    """Live QA (IDFCFIRSTB 1D): a detection already archived on first sight was stored
    as a frozen track and only archived on the next scan."""
    ctx = ctx_of(series_for(DT_ARCHIVED, calendar))
    det = [d for d in detect_series(ctx, calendar) if d.pattern == "double_top"][0]
    assert det.archived and det.status == "broke_below"
    arch_time = [ev["time"] for ev in det.events if ev["event"] == "archived"][-1]
    tracker = Tracker(engine, (5,))
    tracker.process("s1", ctx, [det])
    t = tracks(engine)[0]
    assert t["state"] == "closed" and t["closed_reason"] == "archived" and t["archived_time"] == arch_time
    assert t["breakout_direction"] == "down" and t["breakout_time"]
    tracker.process("s2", ctx, [d for d in detect_series(ctx, calendar) if d.pattern == "double_top"])
    assert len(tracks(engine)) == 1  # re-observed archived formation is linked, not duplicated


def test_distinct_formation_next_to_frozen_track_keeps_its_track(engine, calendar):
    """Live QA (CHOLAHLDNG 1D): a validated wedge that dedup kept next to a broken one
    (IoU ~0.5) was suppressed as a refit on the next scan and its track 'disappeared'."""
    spec = replace(POSITIVE["falling_channel"], post=[0.5, 0.6, 0.7, 1.3, 1.35, 1.4])
    ctx = ctx_of(band_series(spec, calendar))
    broke = [d for d in detect_series(ctx, calendar) if d.pattern == "falling_channel" and d.status == "broke_above"][0]
    s1, e1 = broke.formation_start_index, broke.formation_end_index
    s2 = e1 - int((e1 - s1 + 1) / 0.6) + 1  # IoU with the broken window = 0.6 >= track_match_iou
    assert s2 >= 0
    other = replace(
        broke,
        detection_id="distinct",
        status="validated",
        status_label="Validated",
        formation_start_index=s2,
        formation_start=iso(ctx.times[s2]),
        events=[],
        metrics={k: v for k, v in broke.metrics.items() if k not in ("breakout_index", "breakout_direction")},
        extra={},
    )
    tracker = Tracker(engine, (5,))
    for n in range(3):  # identical data every scan
        out = tracker.process(f"s{n}", ctx, [replace(broke, extra={}), replace(other, extra={})])
        assert {d.status for d in out} == {"broke_above", "validated"}
    trs = tracks(engine)
    assert len(trs) == 2
    assert sorted(t["state"] for t in trs) == ["active", "frozen"]
    assert all("disappeared" not in events(engine, t["track_id"]) for t in trs)


def test_archived_after_tracking_window(engine, calendar):
    cfg_bars = DEFAULT_CONFIG.post_breakout_track_bars
    spec = replace(POSITIVE["falling_channel"], post=[0.5, 0.6, 0.7] + [1.5] * (cfg_bars + 3))
    df = band_series(spec, calendar)
    _, e = pattern_window(spec)
    simulate(engine, df, e + 4, calendar)
    first = [t for t in tracks(engine) if t["pattern"] == "falling_channel"][0]
    assert first["closed_reason"] == "archived" and first["archived_time"]
    assert "archived" in events(engine, first["track_id"])


def test_disappeared_track_is_closed(engine, calendar):
    df = band_series(POSITIVE["falling_channel"], calendar)
    tracker = Tracker(engine, (5,))
    ctx = ctx_of(df)
    dets = [d for d in detect_series(ctx, calendar) if d.pattern == "falling_channel"]
    tracker.process("s1", ctx, dets)
    tracker.process("s2", ctx, [])
    t = tracks(engine)[0]
    assert t["state"] == "closed" and t["closed_reason"] == "disappeared"


def test_continuing_scans_keep_one_track_and_record_quality(engine, calendar):
    spec = replace(POSITIVE["falling_channel"], post=[0.5, 0.6, 0.5, 0.4, 0.5, 0.6])
    df = band_series(spec, calendar)
    _, e = pattern_window(spec)
    simulate(engine, df, e + 4, calendar)
    trs = [t for t in tracks(engine) if t["pattern"] == "falling_channel"]
    assert len(trs) == 1 and trs[0]["state"] == "active"
    ev = events(engine, trs[0]["track_id"])
    assert ev.count("first_detected") == 1 and "boundary_update" in ev


def test_remap_by_timestamp_survives_index_shift(engine, calendar):
    df = band_series(POSITIVE["falling_channel"], calendar)
    d = [x for x in detect_series(ctx_of(df), calendar) if x.pattern == "falling_channel"][0]
    shorter = df.iloc[10:]  # provider window slid: every index moves by -10
    ctx = ctx_of(shorter)
    g = remap_geometry(d.geometry, epoch_index(ctx), ctx)
    assert g["formation"]["start"]["i"] == d.formation_start_index - 10
    assert g["formation"]["end"]["t"] == d.geometry["formation"]["end"]["t"]
    gone = df.drop(df.index[d.formation_start_index + 5])  # a formation candle vanished
    cg = ctx_of(gone)
    assert remap_geometry(d.geometry, epoch_index(cg), cg) is None


def test_outcome_measurement(calendar):
    df = band_series(BandSpec(upper=(1000, 1000), lower=(900, 900), length=40, post=[0.5, 1.2, 1.3, 1.5, 1.1, 1.6]), calendar)
    ctx = ctx_of(df)
    b = len(df) - 5
    o = compute_outcome(ctx, b, "up", (2, 4, 10))
    c0 = float(ctx.close[b])
    h2 = o["horizons"]["2"]
    assert h2["status"] == "complete" and h2["return_pct"] == pytest.approx((ctx.close[b + 2] / c0 - 1) * 100, abs=1e-3)
    assert h2["mfe_pct"] == pytest.approx((max(ctx.high[b + 1 : b + 3]) / c0 - 1) * 100, abs=1e-3)
    assert o["horizons"]["10"]["status"] == "pending"
    down = compute_outcome(ctx, b, "down", (2,))
    assert down["horizons"]["2"]["return_pct"] == pytest.approx(-h2["return_pct"], abs=1e-6)
    agg = aggregate([o, o], (2, 10), min_sample=30)
    assert agg["2"]["n"] == 2 and agg["2"]["insufficient_sample"] and agg["10"]["n"] == 0


def test_tracks_of_another_detection_config_are_closed_not_continued(engine, calendar):
    """A config change (new hash) closes open tracks as config_changed; the formation
    is re-detected under the new rules on a fresh track (old rules never linger)."""
    df = band_series(POSITIVE["falling_channel"], calendar)
    tracker = Tracker(engine, (5,))
    old_cfg = replace(DEFAULT_CONFIG, touch_tol_atr_frac=DEFAULT_CONFIG.touch_tol_atr_frac + 0.01)
    old_ctx = SeriesContext("SYN", "1d", df, old_cfg)
    old = [d for d in detect_series(old_ctx, calendar) if d.pattern == "falling_channel"]
    assert old
    tracker.process("s1", old_ctx, old)
    ctx = ctx_of(df)
    new = [d for d in detect_series(ctx, calendar) if d.pattern == "falling_channel"]
    out = tracker.process("s2", ctx, new)
    trs = [t for t in tracks(engine) if t["pattern"] == "falling_channel"]
    closed = [t for t in trs if t["closed_reason"] == "config_changed"]
    assert len(closed) == 1 and closed[0]["state"] == "closed"
    assert "closed" in events(engine, closed[0]["track_id"])
    fresh = [t for t in trs if t["state"] == "active"]
    assert len(fresh) == 1 and fresh[0]["config_hash"] != closed[0]["config_hash"]
    assert all(d.extra["track_id"] == fresh[0]["track_id"] for d in out)
