"""Deterministic synthetic post-listing price paths for the IPO base detector
(tests, self-test and the offline demo). Not market data.

A path is a list of segments (target close, sessions, daily range %, volume):
closes move linearly to each target, highs / lows sit half the daily range
above / below the close, the open is the previous close. The default is a
listing pop (price discovery), a fade into a ~24 % deep base, two rallies back
to the base high (resistance touches) and a tight, quiet right side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .data.calendar import NSECalendar
from .synthetic import DEFAULT_END, trading_day_index

DEFAULT_SEGMENTS: tuple[tuple[float, int, float, float], ...] = (
    (118.0, 5, 6.0, 4.0e6),  # listing pop: wide ranges, heavy volume (price discovery)
    (95.0, 12, 5.0, 2.5e6),  # fade: the base's first leg
    (116.5, 10, 3.0, 1.5e6),  # rally back to the base high (2nd resistance touch)
    (106.0, 8, 2.5, 1.0e6),  # pullback
    (117.0, 8, 2.0, 0.8e6),  # 3rd touch
    (114.0, 8, 1.2, 0.5e6),  # tight, quiet right side
)


@dataclass
class IPOSpec:
    segments: tuple[tuple[float, int, float, float], ...] = DEFAULT_SEGMENTS
    start: float = 100.0
    first_range_pct: float = 8.0
    first_volume: float = 8.0e6
    tail: list[tuple[float, float, float]] = field(default_factory=list)  # (close, range %, volume) after the segments


def ipo_frame(spec: IPOSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
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
    o = np.concatenate([[spec.start * 0.97], c[:-1]])
    half = c * r / 2.0
    h = np.maximum(np.maximum(o, c), c + half)
    l = np.minimum(np.minimum(o, c), c - half)
    idx = trading_day_index(len(c), calendar, end)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.asarray(vols, float)}, index=idx)


def base_high(spec: IPOSpec) -> float:
    """Highest high of the path when it is the end of the listing pop (default segments)."""
    c = spec.segments[0][0]
    return c + c * spec.segments[0][2] / 100.0 / 2.0
