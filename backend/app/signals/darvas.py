"""Darvas box on completed daily candles (SignalConfig 1.4.0, docs/darvas-box.md).

Nicolas Darvas, *How I Made $2,000,000 in the Stock Market* (1960): trade stocks
making new highs; a box forms when the advance pauses, and the stock is bought
when it breaks out of the top of its box (and sold when it falls through the
bottom). Rules translated literally where the book is explicit (one ASSUMPTION
added: a box deeper than ``darvas_max_depth_pct`` is a decline, not a pause):

* **Box top** - a new high: a candle whose high is the highest high of the last
  ``darvas_new_high_lookback`` sessions (52 weeks) and which the next
  ``darvas_confirm_sessions`` (3) sessions fail to exceed. The top is known only
  on the 3rd of those sessions (no look-ahead). A higher high before the box is
  complete restarts the top.
* **Box bottom** - after the top, the lowest low that the next 3 sessions do not
  undercut; confirmed on the 3rd of them. Box = top + bottom confirmed.
* **Breakout** - a completed close above the top of a complete box: a new box
  is built above it (boxes stack in an advance). **Breakdown** - a completed
  close below the bottom: the advance ends; a new 52-week high restarts the walk.
* Volume on the breakout candle is measured against the previous
  ``darvas_volume_lookback`` sessions and flagged at ``darvas_breakout_volume_ratio``
  (Darvas wanted rising volume; reported, not a gate).

States of the latest candle: ``forming_top`` (a new high, top not confirmed),
``forming_bottom`` (top confirmed, bottom not yet), ``in_box``, ``near_top``
(within ``darvas_near_pct`` below the top), ``breakout`` / ``breakdown`` (within
``darvas_track_sessions``). Separate tests of the top are scored by the shared
level engine. Engineering choices beyond the book are flagged ASSUMPTION in
``SIGNAL_PARAM_META``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import SignalConfig
from ..timeutil import epoch_s, iso
from .levels import lows_rising, separate_tests, tests_score

STATE_LABELS = {
    "forming_top": "Forming top",
    "forming_bottom": "Forming bottom",
    "in_box": "In box",
    "near_top": "Near top",
    "breakout": "Breakout",
    "breakdown": "Breakdown",
}
STATE_RANK = {"breakout": 0, "near_top": 1, "in_box": 2, "forming_bottom": 3, "forming_top": 4, "breakdown": 5}


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def darvas_walk(frame: pd.DataFrame, cfg: SignalConfig) -> dict[str, Any]:
    """Deterministic left-to-right walk; every decision at candle ``j`` uses candles <= j.

    Returns the boxes (complete ones with their outcome) and the open state at the end."""
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    n = len(c)
    K = cfg.darvas_confirm_sessions
    N = cfg.darvas_new_high_lookback
    # rolling max of the previous N highs (candle excluded): a new 52-week high is high > it
    prev_max = pd.Series(h).rolling(N, min_periods=1).max().shift(1).to_numpy()
    boxes: list[dict[str, Any]] = []
    top_i: int | None = None  # candidate / confirmed top candle
    top_ok = False
    bot_i: int | None = None  # candidate / confirmed bottom candle
    bot_ok = False
    active = False  # inside an advance (after a new high, before a breakdown)
    for j in range(n):
        if not (np.isfinite(h[j]) and np.isfinite(l[j]) and np.isfinite(c[j])):
            continue
        new_high = j > 0 and (not np.isfinite(prev_max[j]) or h[j] > prev_max[j])
        # 1. a complete box: breakout / breakdown on the close
        if top_ok and bot_ok:
            top, bot = float(h[top_i]), float(l[bot_i])
            if c[j] > top:
                boxes.append({"top_i": top_i, "bot_i": bot_i, "top": top, "bottom": bot, "end_i": j, "outcome": "breakout"})
                # the breakout candle starts the next box's top search (boxes stack)
                top_i, top_ok, bot_i, bot_ok, active = j, False, None, False, True
                continue
            if c[j] < bot:
                boxes.append({"top_i": top_i, "bot_i": bot_i, "top": top, "bottom": bot, "end_i": j, "outcome": "breakdown"})
                top_i, top_ok, bot_i, bot_ok, active = None, False, None, False, False
                continue
            continue
        # 2. no advance yet: wait for a new 52-week high
        if not active:
            if new_high:
                top_i, top_ok, bot_i, bot_ok, active = j, False, None, False, True
            continue
        # 3. building the top: a higher high restarts it; K quiet sessions confirm it
        if not top_ok:
            if h[j] > h[top_i]:
                top_i = j
                continue
            if j - top_i >= K:  # K sessions without exceeding it (a higher one would have restarted the top)
                top_ok = True
                bot_i = top_i + 1 + int(np.argmin(l[top_i + 1 : j + 1]))
            continue
        # 4. building the bottom (top confirmed); a higher high before it is confirmed restarts the top
        if h[j] > h[top_i]:
            top_i, top_ok, bot_i, bot_ok = j, False, None, False
            continue
        if bot_i is None or l[j] < l[bot_i]:
            bot_i = j
        if (h[top_i] - l[bot_i]) / h[top_i] * 100.0 > cfg.darvas_max_depth_pct:
            # ASSUMPTION: a fall deeper than a Darvas box is not a pause in the advance - stop
            # tracking until the next new 52-week high
            top_i, top_ok, bot_i, bot_ok, active = None, False, None, False, False
            continue
        if bot_i == j:
            continue
        if j - bot_i >= K:  # K sessions without undercutting it (a lower low would have restarted the bottom)
            bot_ok = True
    return {"boxes": boxes, "top_i": top_i, "top_ok": top_ok, "bot_i": bot_i, "bot_ok": bot_ok, "active": active}


def detect_darvas(frame: pd.DataFrame, cfg: SignalConfig, symbol: str = "") -> dict[str, Any] | None:
    """Darvas state of the latest completed candle (None when the stock is not in an advance
    and no box event is recent)."""
    n = len(frame)
    if n < cfg.darvas_confirm_sessions * 2 + 3:
        return None
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    v = frame["volume"].to_numpy(dtype=float)
    times = frame.index
    w = darvas_walk(frame, cfg)
    last = n - 1
    boxes = w["boxes"]
    ev = boxes[-1] if boxes else None
    recent_ev = ev is not None and last - ev["end_i"] < cfg.darvas_track_sessions
    state: str | None = None
    box: dict[str, Any] | None = None
    if w["top_ok"] and w["bot_ok"]:
        top, bot = float(h[w["top_i"]]), float(l[w["bot_i"]])
        box = {"top_i": w["top_i"], "bot_i": w["bot_i"], "top": top, "bottom": bot, "end_i": last, "outcome": "open"}
        state = "near_top" if (top - c[last]) / top * 100.0 <= cfg.darvas_near_pct else "in_box"
    elif w["active"] and w["top_i"] is not None:
        state = "forming_bottom" if w["top_ok"] else "forming_top"
        top = float(h[w["top_i"]])
        bot = float(l[w["bot_i"]]) if w["bot_i"] is not None else None
        box = {"top_i": w["top_i"], "bot_i": w["bot_i"], "top": top, "bottom": bot, "end_i": last, "outcome": "open"}
    # a recent breakout / breakdown outranks a box that is only starting to form above it
    if recent_ev and (state in (None, "forming_top", "forming_bottom")):
        state = ev["outcome"]
        box = ev
    if state is None or box is None:
        return None
    # boxes of the current advance (stacked after breakouts), oldest first
    stack: list[dict[str, Any]] = []  # boxes broken upward in the current advance (oldest first)
    for b in reversed(boxes):
        if b["outcome"] != "breakout":
            break
        stack.insert(0, b)
    shown = list(stack)
    if not any(b is box for b in shown):
        shown.append(box)
    shown = shown[-cfg.darvas_max_boxes :]
    top = float(box["top"])
    bottom = box.get("bottom")
    t_i = box["top_i"]
    end_tests = (box["end_i"] - 1) if box["outcome"] in ("breakout", "breakdown") else last
    tests = separate_tests(h, t_i, end_tests, zone_lo=top * (1.0 - cfg.w52_zone_pct / 100.0), separation=cfg.darvas_confirm_sessions)
    vol = None
    if box["outcome"] in ("breakout", "breakdown"):
        b_i = box["end_i"]
        ref = v[max(0, b_i - cfg.darvas_volume_lookback) : b_i]
        ref = ref[np.isfinite(ref)]
        vr = (float(v[b_i]) / float(ref.mean())) if (len(ref) >= max(1, cfg.darvas_volume_lookback // 2) and ref.mean() > 0 and np.isfinite(v[b_i])) else None
        vol = {"index": b_i, "time": iso(times[b_i]), "t": epoch_s(times[b_i]), "close": _r(c[b_i]), "volume_ratio": _r(vr, 3), "volume_ok": bool(vr is not None and vr >= cfg.darvas_breakout_volume_ratio), "sessions_ago": last - b_i}

    def geo(b: dict[str, Any]) -> dict[str, Any]:
        return {
            "t_start": epoch_s(times[b["top_i"]]), "i_start": b["top_i"], "t_end": epoch_s(times[b["end_i"]]), "i_end": b["end_i"],
            "top": _r(b["top"]), "bottom": _r(b.get("bottom")), "outcome": b["outcome"],
            "t_bottom": epoch_s(times[b["bot_i"]]) if b.get("bot_i") is not None else None,
        }

    stacked = len(stack)
    rec = {
        "kind": "darvas_box",
        "symbol": symbol,
        "state": state,
        "state_label": STATE_LABELS[state],
        "time": iso(times[last]),
        "t": epoch_s(times[last]),
        "close": _r(c[last]),
        "box_top": _r(top),
        "box_bottom": _r(bottom),
        "box_depth_pct": _r((top - bottom) / top * 100.0, 2) if bottom is not None else None,
        "box_start_time": iso(times[t_i]),
        "box_start_t": epoch_s(times[t_i]),
        "box_sessions": box["end_i"] - t_i,
        "distance_to_top_pct": _r((top - c[last]) / top * 100.0, 3),
        "top_tests": len(tests),
        "tests_score": tests_score(len(tests), cfg.level_tests_score),
        "lows_rising": lows_rising(l, t_i, end_tests) if bottom is not None else None,
        "boxes_stacked": stacked,
        "event": vol,
        "partial_history": n - 1 < cfg.darvas_new_high_lookback,
        "geometry": {"schema_version": "darvas-1.0", "boxes": [geo(b) for b in shown], "current": geo(box)},
    }
    # a breakout whose latest close is back at or below the broken top: still a breakout by the
    # rules (the stop is the old box bottom), but flagged - it has not held
    rec["back_below_top"] = bool(state == "breakout" and c[last] <= top)
    if rec["back_below_top"]:
        rec["state_label"] = "Breakout - back below top"
    expl = [f"{rec['state_label']}: box {rec['box_top']} / {rec['box_bottom'] if bottom is not None else '(bottom not confirmed)'} since {rec['box_start_time'][:10]} ({rec['box_sessions']} sessions, {rec['box_depth_pct']} % deep)."]
    if vol:
        expl.append(f"{'Breakout' if box['outcome'] == 'breakout' else 'Breakdown'} close {vol['close']} on {vol['time'][:10]} ({vol['sessions_ago']} sessions ago), volume {vol['volume_ratio']}x the {cfg.darvas_volume_lookback}-session average{' (volume-confirmed)' if vol['volume_ok'] else ''}.")
    expl.append(f"Top tests: {rec['top_tests']} (score {rec['tests_score']}); boxes stacked in this advance: {stacked}. The top was a new {cfg.darvas_new_high_lookback}-session high held for {cfg.darvas_confirm_sessions} sessions (Darvas).")
    rec["explanation"] = expl
    return rec
