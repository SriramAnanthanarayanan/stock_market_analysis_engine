from dataclasses import replace

import numpy as np

from app.config import DEFAULT_CONFIG as CFG
from app.lifecycle import BoundaryModel, classify_position, evaluate_lifecycle
from app.patterns.base import Line, Status

BAND = BoundaryModel(Line(0, 110.0, 0.0), Line(0, 90.0, 0.0))  # width 20


def walk(closes, model=BAND, cfg=CFG, atr=1.0):
    c = np.array([100.0] + list(closes))
    return evaluate_lifecycle(model, c, np.full(len(c), atr), 0, len(c) - 1, cfg)


def test_inside_is_validated_and_near_boundaries():
    assert walk([100, 101]).status == "validated"
    assert walk([100, 107.5]).status == "near_upper"  # 2.5 <= 0.15 x 20
    assert walk([100, 92.0]).status == "near_lower"


def test_near_both_picks_smaller_normalised_distance():
    # Narrow band where the close is near both boundaries at once.
    assert classify_position(103.0, 105.0, 100.0, 0.7) == (Status.NEAR_UPPER, 0.4)
    assert classify_position(101.0, 105.0, 100.0, 0.9) == (Status.NEAR_LOWER, 0.2)


def test_single_close_beyond_is_not_a_break():
    r = walk([100, 111.0])
    assert r.status == "near_upper" and r.breakout_index is None


def test_two_consecutive_closes_beyond_buffer_break_above_and_freeze():
    r = walk([100, 111.0, 111.5])
    assert r.status == "broke_above" and r.breakout_index == 3 and r.breakout_direction == "up"
    ev = [e for e in r.events if e.event == "broke_above"][0]
    assert ev.detail["geometry"] == "frozen" and ev.detail["first_close_beyond_index"] == 2


def test_break_below():
    assert walk([95, 88.0, 87.0]).status == "broke_below"


def test_failed_breakout_is_terminal():
    r = walk([100, 111.0, 112.0, 105, 104, 103, 115, 116])
    assert (r.status, r.reason) == ("invalidated", "failed_breakout")
    assert [e.event for e in r.events][-1] == "invalidated"


def test_apex_passed_for_converging():
    model = BoundaryModel(Line(0, 110.0, -1.0), Line(0, 90.0, 1.0), apex_index=10.0)
    r = walk([100] * 9, model=model)
    assert (r.status, r.reason) == ("invalidated", "apex_passed")
    assert r.events[-1].index == 7  # apex_index - 3


def test_archived_after_tracking_window():
    cfg = replace(CFG, post_breakout_track_bars=5)
    r = walk([100, 111.0, 112.0] + [113.0] * 6, cfg=cfg)
    assert r.status == "broke_above" and r.archived


# ------------------------------------------------------------ reversal mode

TOP = BoundaryModel(None, Line(0, 90.0, 0.0), kind="neckline_down", extreme=110.0)  # neckline 90, peaks 110
BOTTOM = BoundaryModel(Line(0, 110.0, 0.0), None, kind="neckline_up", extreme=90.0)


def test_top_near_neckline_within_one_atr_and_validated_above():
    assert walk([100, 100.5], model=TOP).status == "validated"
    assert walk([100, 90.8], model=TOP).status == "near_lower"  # 0.8 ATR above the neckline
    assert walk([100, 89.9], model=TOP).status == "near_lower"  # below, not yet confirmed


def test_top_breaks_below_neckline_only():
    assert walk([100, 89.8, 89.5], model=TOP).status == "broke_below"
    r = walk([100, 115, 116], model=TOP)  # far above the peaks
    assert (r.status, r.reason) == ("invalidated", "extreme_exceeded")  # never "broke above"


def test_top_extreme_needs_half_atr_beyond_peak():
    assert walk([100, 110.4], model=TOP).status == "validated"
    assert (walk([100, 110.6], model=TOP).reason) == "extreme_exceeded"


def test_top_failed_breakout_back_above_neckline():
    r = walk([100, 89.5, 89.0, 91, 92, 93], model=TOP)
    assert (r.status, r.reason) == ("invalidated", "failed_breakout")


def test_bottom_mirror():
    assert walk([100, 109.5], model=BOTTOM).status == "near_upper"
    assert walk([100, 110.3, 110.6], model=BOTTOM).status == "broke_above"
    assert walk([100, 89.4], model=BOTTOM).reason == "extreme_exceeded"
    r = walk([100, 110.5, 111, 109, 108, 107], model=BOTTOM)
    assert (r.status, r.reason) == ("invalidated", "failed_breakout")


def test_lifecycle_events_are_ordered_transitions():
    r = walk([100, 107.5, 100, 111, 112])
    assert [e.event for e in r.events] == ["formation_identified", "status_change", "status_change", "status_change", "broke_above"]
