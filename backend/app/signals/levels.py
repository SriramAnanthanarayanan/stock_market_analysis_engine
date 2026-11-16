"""Shared level helpers (SignalConfig 1.4.0, docs/level-engine.md).

One place for the three questions every horizontal-level scanner asks about a
resistance (support for the bearish side):

* how often was it **tested** - separate visits to its zone (``separate_tests``);
* how should that count rank a stock (``tests_score``: 0, 1, 2, 3, 4+ tests ->
  ``SignalConfig.level_tests_score``; the user's "3+ touches show buyer
  strength" - a ranking aid, not a probability);
* where is the latest close - **Approaching**, **Very Near**, **Touched**
  (``approach_state``; a close beyond the level is a breakout, handled by each
  scanner's own event logic).

Completed candles only; callers pass index ranges that end before the evaluated
candle, so nothing reads the future.
"""

from __future__ import annotations

from typing import Any

import numpy as np

APPROACH_LABELS = {"approaching": "Approaching", "very_near": "Very Near", "touched": "Touched"}


def separate_tests(extreme: np.ndarray, start: int, end: int, *, zone_lo: float | None = None, zone_hi: float | None = None, separation: int = 1) -> list[int]:
    """Candle indices that start a separate test of a level inside ``start .. end``
    (inclusive). A candle is in the zone when its high >= ``zone_lo`` (resistance) or its
    low <= ``zone_hi`` (support); a zone candle at least ``separation`` sessions after the
    previous zone candle starts a new test (consecutive zone candles are one test)."""
    if end < start or start < 0:
        return []
    seg = np.asarray(extreme[start : end + 1], dtype=float)
    with np.errstate(invalid="ignore"):
        mask = (seg >= zone_lo) if zone_lo is not None else (seg <= zone_hi)
    out: list[int] = []
    prev: int | None = None
    for k in np.flatnonzero(mask):
        k = int(k) + start
        if prev is None or k - prev >= separation:
            out.append(k)
        prev = k
    return out


def tests_score(n: int, scores: tuple[float, ...]) -> float:
    """0-1 score of ``n`` separate tests (the last entry covers every larger count)."""
    if n <= 0 or not scores:
        return float(scores[0]) if scores else 0.0
    return float(scores[min(n, len(scores) - 1)])


def approach_state(close: float, high: float, level: float, approach_pct: float, very_near_pct: float) -> tuple[str | None, float | None]:
    """(state, distance %) of a close below a resistance ``level``. Touched = the candle
    reached the level intraday but closed at or below it; a close above it is None (the
    breakout belongs to the scanner's event logic)."""
    if not (np.isfinite(close) and np.isfinite(level)) or level <= 0:
        return None, None
    dist = (level - close) / level * 100.0
    if close > level:
        return None, dist
    if np.isfinite(high) and high >= level:
        return "touched", dist
    if dist <= very_near_pct:
        return "very_near", dist
    if dist <= approach_pct:
        return "approaching", dist
    return None, dist


def lows_rising(low: np.ndarray, start: int, end: int) -> bool | None:
    """Buyers stepping in higher: the lowest low of the second half of ``start .. end`` is
    above that of the first half (None when fewer than 4 candles)."""
    if end - start + 1 < 4:
        return None
    mid = start + (end - start + 1) // 2
    a = np.asarray(low[start:mid], dtype=float)
    b = np.asarray(low[mid : end + 1], dtype=float)
    if not (np.isfinite(a).any() and np.isfinite(b).any()):
        return None
    return bool(np.nanmin(b) > np.nanmin(a))


def summarize_tests(times, idx: list[int], scores: tuple[float, ...], limit: int = 12) -> dict[str, Any]:
    from ..timeutil import iso

    return {"tests": len(idx), "tests_score": tests_score(len(idx), scores), "test_times": [iso(times[k]) for k in idx[:limit]]}
