"""Deterministic fixtures for E1 technical-analysis checks (indicators, divergence,
Marubozu, breakouts). Prices are synthetic; nothing here is market data."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .synthetic import DEFAULT_END, trading_day_index, zigzag_path


def frame_from(o, h, l, c, calendar, v=None, end: date = DEFAULT_END) -> pd.DataFrame:
    n = len(c)
    idx = trading_day_index(n, calendar, end)
    vol = np.full(n, 1e5) if v is None else np.asarray(v, dtype=float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": vol}, index=idx)


def closes_frame(closes, calendar, wick: float = 2.0, v=None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return frame_from(o, np.maximum(o, c) + wick, np.minimum(o, c) - wick, c, calendar, v)


def chop(start: float, end: float, bars: int, step_down: float, step_up: float) -> list[tuple[int, float]]:
    """Waypoints of a choppy trend: 2 bars against the move then 1 bar with it."""
    pts = []
    x, y = 0, start
    pts.append((x, y))
    going = end < start
    while x + 3 <= bars:
        x += 2
        y = y - step_down if going else y + step_down
        pts.append((x, y))
        x += 1
        y = y + step_up if going else y - step_up
        pts.append((x, y))
    pts.append((bars, end))
    return pts


def _join(segments: list[list[tuple[int, float]]]) -> np.ndarray:
    out: list[tuple[int, float]] = []
    off = 0
    for seg in segments:
        for x, y in seg:
            if out and x == 0:
                continue
            out.append((off + x, y))
        off = out[-1][0]
    return zigzag_path(out)


LEAD = 40


def bullish_divergence_closes() -> np.ndarray:
    lead = 1000 + 3 * np.sin(np.arange(LEAD))
    path = _join(
        [
            [(0, 1000.0), (8, 900.0)],  # sharp drop: RSI collapses (P1)
            [(0, 900.0), (10, 950.0)],  # rebound
            chop(950.0, 893.0, 30, 9.0, 4.5),  # choppy slower decline to a lower low (P2), RSI higher
            [(0, 893.0), (12, 960.0)],  # rally confirms P2
        ]
    )
    return np.concatenate([lead, path])


def bearish_divergence_closes() -> np.ndarray:
    return 2000.0 - bullish_divergence_closes()  # mirror image (prices ~1000)
