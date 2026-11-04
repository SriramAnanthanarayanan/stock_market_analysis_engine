"""Deterministic synthetic VCP fixtures (tests and self-test). Not market data.

A flat history, an advance into the base, then contractions: each declines from
its high by ``depths[i]`` % and recovers to ``recover[i]`` x that high, with its
own volume level. Optional tail candles (breakout, failure, undercut) follow.
Synthetic series prove the rules are implemented as specified; they say nothing
about real-chart accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .data.calendar import NSECalendar
from .synthetic import DEFAULT_END, trading_day_index


@dataclass
class VCPSpec:
    depths: tuple[float, ...] = (20.0, 10.0, 5.0)  # % decline of each contraction
    recover: tuple[float, ...] = (0.99, 0.99, 0.985)  # next high / this contraction's high
    down_bars: tuple[int, ...] = (8, 6, 4)
    up_bars: tuple[int, ...] = (8, 6, 5)
    volumes: tuple[float, ...] = (1.0e5, 0.7e5, 0.45e5)  # volume during each contraction
    flat_bars: int = 160
    advance_bars: int = 60
    advance_pct: float = 45.0
    lead_volume: float = 1.2e5
    high: float = 100.0
    wick: float = 0.3
    tail: list[tuple[float, float]] = field(default_factory=list)  # (close as x pivot-high, volume) candles after the base


def _path(spec: VCPSpec) -> tuple[list[float], list[float]]:
    start = spec.high / (1.0 + spec.advance_pct / 100.0)
    closes = [start] * spec.flat_bars
    vols = [spec.lead_volume] * spec.flat_bars
    adv = np.linspace(start, spec.high, spec.advance_bars + 1)[1:]
    closes += list(adv)
    vols += [spec.lead_volume] * spec.advance_bars
    hi = spec.high
    for i, d in enumerate(spec.depths):
        lo = hi * (1.0 - d / 100.0)
        nxt = hi * spec.recover[i]
        down = np.linspace(hi, lo, spec.down_bars[i] + 1)[1:]
        up = np.linspace(lo, nxt, spec.up_bars[i] + 1)[1:]
        closes += list(down) + list(up)
        vols += [spec.volumes[i]] * (len(down) + len(up))
        hi = nxt
    return closes, vols


def vcp_frame(spec: VCPSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    closes, vols = _path(spec)
    piv = pivot_high(spec)
    for mult, vol in spec.tail:
        closes.append(piv * mult)
        vols.append(vol)
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + spec.wick
    l = np.minimum(o, c) - spec.wick
    idx = trading_day_index(len(c), calendar, end)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.asarray(vols, dtype=float)}, index=idx)


def pivot_high(spec: VCPSpec) -> float:
    """Close at the start of the final contraction (the pivot candle's high is this + wick)."""
    hi = spec.high
    for i in range(len(spec.depths) - 1):
        hi = hi * spec.recover[i]
    return hi


def base_start_index(spec: VCPSpec) -> int:
    return spec.flat_bars + spec.advance_bars - 1
