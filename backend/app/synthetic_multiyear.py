"""Deterministic synthetic long price paths for the multi-year breakout detector
(tests, self-test and the offline demo). Not market data.

A path is a list of segments (target close, sessions, daily range %, volume):
closes move linearly to each target, highs / lows sit half the daily range
above / below the close, the open is the previous close. The default rises for
a year to a peak (high ~101), then spends about ten years below it - two deep
declines and three rallies back towards the old high (resistance tests) - and
the last candle closes ~3 % above the old high on about four times the usual
volume: a ~10-year breakout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .data.calendar import NSECalendar
from .synthetic import DEFAULT_END, trading_day_index

DEFAULT_SEGMENTS: tuple[tuple[float, int, float, float], ...] = (
    (100.0, 250, 2.0, 1.0e6),  # one-year advance to the old high (high ~101)
    (60.0, 380, 2.0, 0.9e6),  # decline
    (98.0, 500, 2.0, 0.8e6),  # first rally back to the resistance zone
    (70.0, 500, 2.0, 0.7e6),  # second decline
    (98.5, 600, 1.8, 0.6e6),  # second test
    (85.0, 300, 1.5, 0.5e6),  # pullback
    (99.0, 230, 1.2, 0.5e6),  # tight third test
)
BREAKOUT: list[tuple[float, float, float]] = [(104.0, 3.0, 2.0e6)]  # close ~3 % above the old high on ~4x volume


@dataclass
class MultiYearSpec:
    segments: tuple[tuple[float, int, float, float], ...] = DEFAULT_SEGMENTS
    start: float = 50.0
    first_range_pct: float = 2.0
    first_volume: float = 1.0e6
    tail: list[tuple[float, float, float]] = field(default_factory=lambda: list(BREAKOUT))  # (close, range %, volume) after the segments


def multiyear_frame(spec: MultiYearSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    closes = [spec.start]
    ranges = [spec.first_range_pct]
    vols = [spec.first_volume]
    cur = spec.start
    for target, bars, rng, vol in spec.segments:
        path = np.linspace(cur, target, bars + 1)[1:]
        closes += list(path)
        ranges += [rng] * bars
        vols += [vol] * bars
        cur = target
    for c, rng, vol in spec.tail:
        closes.append(c)
        ranges.append(rng)
        vols.append(vol)
    c = np.asarray(closes, float)
    r = np.asarray(ranges, float) / 100.0
    o = np.concatenate([[spec.start * 0.99], c[:-1]])
    half = c * r / 2.0
    h = np.maximum(np.maximum(o, c), c + half)
    l = np.minimum(np.minimum(o, c), c - half)
    idx = trading_day_index(len(c), calendar, end)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.asarray(vols, float)}, index=idx)


def old_high(spec: MultiYearSpec) -> float:
    """Highest high of the default path before the breakout (end of the first advance)."""
    c = spec.segments[0][0]
    return c + c * spec.segments[0][2] / 100.0 / 2.0
