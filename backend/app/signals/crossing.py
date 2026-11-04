"""Reference-window crossings with event de-duplication (fresh N-day and 52-week breakouts).

One engine serves the fresh 10-day breakout (spec 3) and the 52-week breakout /
breakdown (spec 5). Completed daily candles only; candle ``i``'s reference
window is the previous ``N`` completed candles ``i-N .. i-1`` (the candle itself
excluded; trading candles, not calendar days):

    ref_high[i] = max(high[i-N .. i-1])        ref_low[i] = min(low[i-N .. i-1])
    buffer[i]   = buffer_atr x ATR(14)[i]

Bullish (bearish mirrors it with ref_low, lows and ``<``):

* **breakout state** (``beyond``): ``close[i] > ref_high[i] + buffer[i]``.
* **intraday touch** (``touch``): ``high[i] > ref_high[i] + touch_buffer[i]``,
  whatever the close (a provisional touch is never a confirmed breakout).
* **fresh crossing** (``fresh``): breakout state on ``i`` and

  1. the previous candle was *not* in breakout state judged on **its own**
     window: ``close[i-1] <= ref_high[i-1] + buffer[i-1]``;
  2. (``require_prior_level_clear``) the close also clears the previous
     candle's level: ``close[i] > ref_high[i-1] + buffer[i]``. When the highest
     high of the window has just rolled out, ``ref_high`` drops; a close above
     the lowered level that did not clear yesterday's level is not a genuine
     crossing of the resistance that was in force.

  The previous close can never be above ``ref_high[i]`` itself (its own high is
  inside that window), so "the previous close was not already above the same
  level" is implied.

Events (de-duplication, spec 3.5) - a deterministic walk over the candles:

* ``armed`` -> a fresh crossing creates an **event** (stable event ID) and opens
  an **episode** at that level.
* While the episode is open, further crossings are suppressed (price staying
  above the same breakout level never creates daily repeats).
* The episode **resets** (re-arms) after ``rearm_closes`` consecutive completed
  closes back inside the prior reference range (at or below the broken high /
  at or above the broken low).
* ``rearm_on_new_range``: a fresh crossing of a reference window that starts
  after the event candle (a new N-session range formed above the old level)
  creates a new event without the close-back rule.
* If the first computable candle is already in breakout state no crossing was
  seen: the walk starts ``blocked`` and arms after ``rearm_closes`` closes
  inside the candles' own windows.

Everything is recomputed from the completed candles of each scan (no stored
incremental state), so an as-of scan reproduces exactly what was known then.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..breakouts.nday import reference_levels


@dataclass(frozen=True)
class CrossingParams:
    lookback: int
    buffer_atr: float
    touch_buffer_atr: float
    require_prior_level_clear: bool
    rearm_closes: int
    rearm_on_new_range: bool


@dataclass
class Episode:
    event: int  # candle index of the event (first fresh crossing)
    level: float  # broken reference level (ref_high / ref_low of the event candle)
    trigger: str  # armed | new_range | blocked_rearmed
    reset: int | None = None  # candle that re-armed the walk (closes back inside)
    superseded_by: int | None = None  # a later new-range event closed this episode
    suppressed: list[int] = field(default_factory=list)  # fresh crossings inside the episode


@dataclass
class CrossingSeries:
    direction: str
    sign: float
    level: np.ndarray
    other: np.ndarray
    buffer: np.ndarray
    computable: np.ndarray
    beyond: np.ndarray
    touch: np.ndarray
    prior_clear: np.ndarray
    fresh: np.ndarray
    first: int | None
    episodes: list[Episode] = field(default_factory=list)
    blocked_until: int | None = None  # first candle armed after a blocked start

    def event_indices(self) -> list[int]:
        return [e.event for e in self.episodes]

    def last_beyond_before(self, i: int) -> int | None:
        idx = np.flatnonzero(self.beyond[:i])
        return int(idx[-1]) if len(idx) else None


def _first_run_end(mask: np.ndarray, k: int) -> int | None:
    """Index (within ``mask``) where the first run of ``k`` consecutive True ends."""
    if k <= 0:
        return 0 if len(mask) else None
    if len(mask) < k:
        return None
    m = mask.astype(np.int16)
    sums = np.convolve(m, np.ones(k, dtype=np.int16), mode="valid")
    hits = np.flatnonzero(sums == k)
    return int(hits[0] + k - 1) if len(hits) else None


def _next_index(sorted_idx: np.ndarray, at_least: int) -> int | None:
    j = int(np.searchsorted(sorted_idx, at_least, side="left"))
    return int(sorted_idx[j]) if j < len(sorted_idx) else None


def crossing_series(high, low, close, atr, p: CrossingParams, direction: str) -> CrossingSeries:
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    a = np.asarray(atr, dtype=float)
    n = len(c)
    rh, rl = reference_levels(h, l, p.lookback)
    bull = direction == "bullish"
    sign = 1.0 if bull else -1.0
    level = rh if bull else rl
    other = rl if bull else rh
    extreme = h if bull else l
    with np.errstate(invalid="ignore"):
        buf = p.buffer_atr * a
        tbuf = p.touch_buffer_atr * a
        computable = np.isfinite(level) & np.isfinite(a)
        beyond = computable & (sign * (c - level) > buf)
        touch = computable & (sign * (extreme - level) > tbuf)
        prev_level = np.concatenate(([np.nan], level[:-1])) if n else level
        prev_comp = np.concatenate(([False], computable[:-1])) if n else computable
        prev_beyond = np.concatenate(([False], beyond[:-1])) if n else beyond
        if p.require_prior_level_clear:
            prior_clear = np.isfinite(prev_level) & (sign * (c - prev_level) > buf)
        else:
            prior_clear = np.ones(n, dtype=bool)
        fresh = beyond & prev_comp & ~prev_beyond & prior_clear
    comp_idx = np.flatnonzero(computable)
    out = CrossingSeries(direction, sign, level, other, buf, computable, beyond, touch, prior_clear, fresh, int(comp_idx[0]) if len(comp_idx) else None)
    if out.first is None:
        return out
    fresh_idx = np.flatnonzero(fresh)
    pos = out.first
    trigger = "armed"
    if beyond[out.first]:
        # Already beyond at the first computable candle: no crossing was seen.
        with np.errstate(invalid="ignore"):
            inside_own = sign * (c[out.first :] - level[out.first :]) <= 0
        r = _first_run_end(inside_own, p.rearm_closes)
        if r is None:
            out.blocked_until = n
            return out
        pos = out.first + r + 1
        out.blocked_until = pos
        trigger = "blocked_rearmed"
    while pos < n:
        e = _next_index(fresh_idx, pos)
        if e is None:
            break
        ep = Episode(e, float(level[e]), trigger)
        out.episodes.append(ep)
        with np.errstate(invalid="ignore"):
            inside = sign * (c[e + 1 :] - ep.level) <= 0
        r = _first_run_end(inside, p.rearm_closes)
        reset = e + 1 + r if r is not None else None
        nxt = _next_index(fresh_idx, e + p.lookback + 1) if p.rearm_on_new_range else None
        stop = min(x for x in (reset, nxt, n) if x is not None)
        ep.suppressed = [int(i) for i in fresh_idx[(fresh_idx > e) & (fresh_idx < stop)]]
        if nxt is not None and (reset is None or nxt < reset):
            ep.superseded_by = nxt
            pos = nxt
            trigger = "new_range"
            continue
        if reset is None:
            break
        ep.reset = reset
        pos = reset + 1
        trigger = "armed"
    return out


def confirm_two_closes(cs: CrossingSeries, close, e: int) -> tuple[str, int | None]:
    """Two-close confirmation of the event at ``e``: level and buffer fixed at ``e``.
    Returns (confirmed | failed | pending, confirming candle index)."""
    c = np.asarray(close, dtype=float)
    if e >= len(c) - 1:
        return "pending", None
    threshold = cs.level[e] + cs.sign * cs.buffer[e]
    ok = cs.sign * (c[e + 1] - threshold) > 0
    return ("confirmed" if ok else "failed"), e + 1


def first_in_lookback(sessions_since_last_beyond: int | None, history_sessions_before: int, n_sessions: int) -> bool | None:
    """First Breakout in Lookback: no close beyond its own reference level (same
    direction) in the preceding ``n_sessions`` completed sessions. None = the
    history before the event is too short to establish it."""
    if sessions_since_last_beyond is not None:
        return sessions_since_last_beyond > n_sessions
    return True if history_sessions_before >= n_sessions else None
