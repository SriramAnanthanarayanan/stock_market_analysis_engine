"""ATR and swing points on completed candles only.

Both functions see only the bars they are given. Callers truncate series to the
as-of cutoff first, so no future candle can influence a value.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR with Wilder smoothing.

    TR[0] = high - low; TR[i] = max(h - l, |h - c[i-1]|, |l - c[i-1]|).
    ATR[period-1] = mean(TR[0:period]); ATR[i] = (ATR[i-1] * (period-1) + TR[i]) / period.
    Values before index ``period - 1`` are NaN.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if n < period:
        return out
    prev_close = np.empty(n)
    prev_close[0] = np.nan
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)],
    )
    tr[0] = high[0] - low[0]
    out[period - 1] = tr[:period].mean()
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


@dataclass(frozen=True)
class SwingPoints:
    """Indices of confirmed swing highs/lows for lookback ``k``."""

    k: int
    highs: np.ndarray  # bar indices
    lows: np.ndarray

    def highs_in(self, start: int, end: int) -> np.ndarray:
        return self.highs[(self.highs >= start) & (self.highs <= end)]

    def lows_in(self, start: int, end: int) -> np.ndarray:
        return self.lows[(self.lows >= start) & (self.lows <= end)]


def swing_points(high: np.ndarray, low: np.ndarray, k: int) -> SwingPoints:
    """Swing high at i: high[i] > max(high[i-k:i]) and high[i] >= max(high[i+1:i+k+1]).

    Swing low mirrors it. A swing is reported only when k later completed bars
    exist (confirmation), so the last k bars can never be swing points.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    n = len(high)
    if n < 2 * k + 1:
        empty = np.array([], dtype=int)
        return SwingPoints(k, empty, empty)
    win_h = sliding_window_view(high, k)  # win_h[j] = high[j:j+k]
    win_l = sliding_window_view(low, k)
    centre = np.arange(k, n - k)
    prev_max = win_h[centre - k].max(axis=1)
    next_max = win_h[centre + 1].max(axis=1)
    prev_min = win_l[centre - k].min(axis=1)
    next_min = win_l[centre + 1].min(axis=1)
    is_high = (high[centre] > prev_max) & (high[centre] >= next_max)
    is_low = (low[centre] < prev_min) & (low[centre] <= next_min)
    return SwingPoints(k, centre[is_high].astype(int), centre[is_low].astype(int))


def provisional_pivot(arr: np.ndarray, k: int, min_confirm: int) -> int | None:
    """Latest index with the left-side swing condition (> previous k) and >= all
    later bars, but fewer than k later bars (i.e. not yet confirmed)."""
    n = len(arr)
    for i in range(n - 1 - min_confirm, n - 1 - k, -1):
        if i - k < 0:
            break
        if arr[i] > arr[i - k : i].max() and arr[i] >= arr[i + 1 : n].max():
            return i
    return None
