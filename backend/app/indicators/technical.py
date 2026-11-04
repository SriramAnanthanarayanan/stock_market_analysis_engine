"""Technical indicators (E1): SMA, EMA, RMA, RSI, Pine-style rising/falling and
crossovers, EMA alignment.

All functions are pure, operate on completed candles only and never look
forward: value ``i`` depends on inputs ``0..i``. Missing values propagate as NaN
(never zero). Warm-up bars are NaN.

Formulas follow TradingView's built-ins so chart values can be compared with
TradingView (docs/indicators.md):

* ``sma``  - ``ta.sma``: mean of the last n values; NaN if any is missing.
* ``ema``  - ``ta.ema``: alpha = 2/(n+1); the first value is the SMA of the first
  n values (``seed="sma"``). ``ema_pandas`` is the literal pandas form
  ``ewm(span=n, adjust=False)`` (first value = first input) used where a source
  specification prescribes it (Stage Analysis, E3).
* ``rma``  - ``ta.rma`` (Wilder): alpha = 1/n, seeded with the SMA of the first n.
* ``rsi``  - ``ta.rsi``: rma of gains / rma of losses (Wilder).
* ATR      - ``indicators.core.atr_wilder`` (``ta.atr``), shared with pattern detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _arr(x: Any) -> np.ndarray:
    return np.asarray(x, dtype=float)


def sma(x: Any, n: int) -> np.ndarray:
    a = _arr(x)
    if n < 1:
        raise ValueError("period must be >= 1")
    return pd.Series(a).rolling(n, min_periods=n).mean().to_numpy()


def _seeded_recursive(a: np.ndarray, n: int, alpha: float) -> np.ndarray:
    """Pine's ``na(sum[1]) ? ta.sma(src, n) : alpha*src + (1-alpha)*sum[1]``.

    A missing input makes the output missing; the recursion then restarts from
    the next SMA of n valid values (as in Pine, where ``sum[1]`` is na)."""
    out = np.full(len(a), np.nan)
    seed = sma(a, n)
    first = np.flatnonzero(~np.isnan(seed))
    if len(first) and not np.isnan(a[first[0] :]).any():
        # Fast path (no gaps after the seed): ewm(adjust=False) started at the
        # SMA seed is exactly y[t] = alpha*x[t] + (1-alpha)*y[t-1].
        s0 = int(first[0])
        x = a[s0:].copy()
        x[0] = seed[s0]
        out[s0:] = pd.Series(x).ewm(alpha=alpha, adjust=False).mean().to_numpy()
        return out
    prev = np.nan
    for i, v in enumerate(a):
        if np.isnan(prev):
            prev = seed[i]
        elif np.isnan(v):
            prev = np.nan
        else:
            prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def ema(x: Any, n: int, seed: str = "sma") -> np.ndarray:
    a = _arr(x)
    if n < 1:
        raise ValueError("period must be >= 1")
    if seed == "sma":
        return _seeded_recursive(a, n, 2.0 / (n + 1))
    if seed == "first":
        return ema_pandas(a, n)
    raise ValueError(f"unknown EMA seed {seed!r}")


def ema_pandas(x: Any, n: int) -> np.ndarray:
    """``pd.Series(x).ewm(span=n, adjust=False).mean()`` exactly (first value = first input)."""
    return pd.Series(_arr(x)).ewm(span=n, adjust=False).mean().to_numpy()


def rma(x: Any, n: int) -> np.ndarray:
    return _seeded_recursive(_arr(x), n, 1.0 / n)


def rsi(close: Any, n: int) -> np.ndarray:
    """Wilder RSI (``ta.rsi``). The first value is at index n.

    Edge cases: no losses over the smoothing window -> 100; no gains -> 0;
    neither (flat prices) -> 50. The formula is undefined for flat prices;
    50 (neutral) is reported instead of a missing value."""
    c = _arr(close)
    ch = np.full(len(c), np.nan)
    ch[1:] = c[1:] - c[:-1]
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0.0)), n)
    down = rma(np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0.0)), n)
    out = np.full(len(c), np.nan)
    ok = ~(np.isnan(up) | np.isnan(down))
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / down
        val = 100.0 - 100.0 / (1.0 + rs)
    val = np.where(down == 0, np.where(up == 0, 50.0, 100.0), val)
    out[ok] = val[ok]
    return out


def _compare_window(a: np.ndarray, length: int, greater: bool) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view

    out = np.zeros(len(a), dtype=bool)
    if len(a) <= length:
        return out
    win = sliding_window_view(a, length + 1)  # win[k] = a[k .. k+length]; current value last
    cur = win[:, -1:]
    prev = win[:, :-1]
    with np.errstate(invalid="ignore"):
        cmp = (cur > prev) if greater else (cur < prev)
    ok = ~np.isnan(win).any(axis=1)  # a missing value anywhere -> False (Pine na comparisons)
    out[length:] = cmp.all(axis=1) & ok
    return out


def rising(x: Any, length: int) -> np.ndarray:
    """``ta.rising``: x[i] > x[i-j] for every j in 1..length (False if any is missing)."""
    return _compare_window(_arr(x), length, True)


def falling(x: Any, length: int) -> np.ndarray:
    """``ta.falling``: x[i] < x[i-j] for every j in 1..length (False if any is missing)."""
    return _compare_window(_arr(x), length, False)


def crossover(a: Any, b: Any) -> np.ndarray:
    """``ta.crossover``: a[i] > b[i] and a[i-1] <= b[i-1] (completed bars only)."""
    x, y = _arr(a), _arr(b)
    out = np.zeros(len(x), dtype=bool)
    if len(x) < 2:
        return out
    with np.errstate(invalid="ignore"):
        out[1:] = (x[1:] > y[1:]) & (x[:-1] <= y[:-1])
    return out


def crossunder(a: Any, b: Any) -> np.ndarray:
    x, y = _arr(a), _arr(b)
    out = np.zeros(len(x), dtype=bool)
    if len(x) < 2:
        return out
    with np.errstate(invalid="ignore"):
        out[1:] = (x[1:] < y[1:]) & (x[:-1] >= y[:-1])
    return out


# ---------------------------------------------------------------- EMA alignment
@dataclass(frozen=True)
class Alignment:
    state: str  # bullish_full | bearish_full | bullish_partial | bearish_partial | mixed | not_available
    missing: tuple[int, ...]  # EMA periods without a value (insufficient history)

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "missing": list(self.missing)}


def _ordered(values: list[float], descending: bool) -> bool:
    pairs = zip(values, values[1:])
    return all(a > b for a, b in pairs) if descending else all(a < b for a, b in pairs)


def ema_alignment(latest: dict[int, float | None], periods: tuple[int, ...], partial: tuple[int, ...]) -> Alignment:
    """Full alignment: all periods strictly ordered (EMA10 > EMA20 > ... > EMA200 is
    bullish). Partial: the ``partial`` subset strictly ordered without full
    alignment. Any missing EMA makes the full state unavailable (never guessed)."""
    missing = tuple(p for p in periods if latest.get(p) is None or not np.isfinite(latest[p]))
    if not missing:
        vals = [float(latest[p]) for p in sorted(periods)]
        if _ordered(vals, True):
            return Alignment("bullish_full", missing)
        if _ordered(vals, False):
            return Alignment("bearish_full", missing)
    sub_missing = [p for p in partial if latest.get(p) is None or not np.isfinite(latest[p])]
    if sub_missing:
        return Alignment("not_available", missing)
    vals = [float(latest[p]) for p in sorted(partial)]
    if _ordered(vals, True):
        return Alignment("bullish_partial", missing)
    if _ordered(vals, False):
        return Alignment("bearish_partial", missing)
    return Alignment("mixed", missing)
