"""Marubozu candlestick detection (E1, spec 4).

Per completed candle (raw OHLC):

    range = high - low                 body = |close - open|
    upper_wick = high - max(open, close)
    lower_wick = min(open, close) - low

Bullish Marubozu: close > open, body/range >= min_body_ratio,
upper_wick/range <= max_upper_wick_ratio, lower_wick/range <= max_lower_wick_ratio.
Bearish Marubozu mirrors close < open. Zero-range candles and invalid OHLC
(missing values, high < max(open, close), low > min(open, close)) are never
classified. Volume is supporting information only (ratio to the mean of the
previous N completed candles), never a validity condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import TechnicalConfig
from ..timeutil import epoch_s, iso


@dataclass(frozen=True)
class CandleShape:
    body_ratio: np.ndarray
    upper_ratio: np.ndarray
    lower_ratio: np.ndarray
    rng: np.ndarray
    valid: np.ndarray  # bool: OHLC present, consistent and range > min_range


def candle_shape(o: Any, h: Any, l: Any, c: Any, cfg: TechnicalConfig) -> CandleShape:
    o, h, l, c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
    rng = h - l
    top = np.maximum(o, c)
    bot = np.minimum(o, c)
    with np.errstate(invalid="ignore"):
        valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (h >= top) & (l <= bot) & (rng > cfg.marubozu_min_range)
    safe = np.where(valid, rng, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return CandleShape(np.abs(c - o) / safe, (h - top) / safe, (bot - l) / safe, rng, valid)


def classify_marubozu(o: Any, h: Any, l: Any, c: Any, cfg: TechnicalConfig) -> np.ndarray:
    """+1 bullish, -1 bearish, 0 neither (per candle)."""
    sh = candle_shape(o, h, l, c, cfg)
    o, c = np.asarray(o, dtype=float), np.asarray(c, dtype=float)
    with np.errstate(invalid="ignore"):
        shape_ok = (
            sh.valid
            & (sh.body_ratio >= cfg.marubozu_min_body_ratio)
            & (sh.upper_ratio <= cfg.marubozu_max_upper_wick_ratio)
            & (sh.lower_ratio <= cfg.marubozu_max_lower_wick_ratio)
        )
    out = np.zeros(len(c), dtype=int)
    out[shape_ok & (c > o)] = 1
    out[shape_ok & (c < o)] = -1
    return out


def prior_volume_ratio(volume: Any, i: int, lookback: int) -> float | None:
    """volume[i] / mean(volume[i-lookback:i]); None when the candle's volume or
    more than half of the previous candles' volumes are missing (never guessed)."""
    v = np.asarray(volume, dtype=float)
    if i < 1 or not np.isfinite(v[i]):
        return None
    prev = v[max(0, i - lookback) : i]
    prev = prev[np.isfinite(prev)]
    if len(prev) < max(1, lookback // 2) or prev.mean() <= 0:
        return None
    return float(v[i] / prev.mean())


def marubozu_events(frame: pd.DataFrame, atr: np.ndarray, cfg: TechnicalConfig, timeframe: str, since: int = 0) -> list[dict[str, Any]]:
    """Every Marubozu candle at index >= ``since`` with its metrics, oldest first."""
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    kind = classify_marubozu(o, h, l, c, cfg)
    sh = candle_shape(o, h, l, c, cfg)
    times = frame.index
    n = len(c)
    out = []
    for i in np.nonzero(kind)[0]:
        i = int(i)
        if i < since:
            continue
        a = float(atr[i]) if np.isfinite(atr[i]) else None
        out.append(
            {
                "kind": "bullish_marubozu" if kind[i] > 0 else "bearish_marubozu",
                "direction": "bullish" if kind[i] > 0 else "bearish",
                "index": i,
                "time": iso(times[i]),
                "t": epoch_s(times[i]),
                "timeframe": timeframe,
                "age_bars": n - 1 - i,
                "open": o[i],
                "high": h[i],
                "low": l[i],
                "close": c[i],
                "body_pct": round(float(sh.body_ratio[i]) * 100, 2),
                "upper_wick_pct": round(float(sh.upper_ratio[i]) * 100, 2),
                "lower_wick_pct": round(float(sh.lower_ratio[i]) * 100, 2),
                "range": float(sh.rng[i]),
                "atr": a,
                "range_atr": round(float(sh.rng[i]) / a, 3) if a else None,
                "volume": None if not np.isfinite(v[i]) else float(v[i]),
                "volume_ratio": _r(prior_volume_ratio(v, i, cfg.candle_volume_lookback)),
                "volume_lookback": cfg.candle_volume_lookback,
            }
        )
    return out


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)
