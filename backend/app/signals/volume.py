"""Volume Shocker (spec 4): unusually high volume relative to the stock's own history.

Completed candles only; the current candle is always excluded from its own
reference. Daily / weekly candles compare with the previous N completed candles
of the same timeframe. Intraday candles (1H / 4H) compare with the **same
time-of-day candle** of the previous N sessions (e.g. today's 10:15-11:15 hour
with the 10:15-11:15 hours of the previous 20 sessions), never with full-day
volume, so a partial session is never judged against complete days.

    RVOL       = volume / mean(reference volumes)
    RVOL(50)   = volume / mean(previous 50 references)
    percentile = share of the previous 60 references below the current volume
                 (ties count half), and ``volume >= P90(previous 60)``
    z-score    = (volume - mean) / sample std of the reference
    traded value = close x volume (rupees)

Missing volume is never guessed: a candle without volume is skipped, and a
reference needs ``vs_min_valid_frac`` known volumes. Yahoo's 09:15 hourly bar
has no volume (stored as missing), so that hour never gets an intraday RVOL.
Zero is a real (no-trade) value in a reference but a zero-volume candle is never
a shock.

Direction (ASSUMPTION thresholds, configurable): Bullish = close > open, body >=
``vs_min_body_frac`` of the range and close location >= ``vs_close_location_split``;
Bearish mirrors it; anything else is Neutral (high volume without a meaningful
directional close). High volume alone never implies accumulation or distribution.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import SignalConfig
from ..timeutil import IST, epoch_s, iso


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


class References:
    """Indices of the comparable earlier candles of each candle (oldest first).

    Daily / weekly: the previous candles. Intraday: the previous candles with the
    same IST time of day (one per session)."""

    def __init__(self, times: pd.DatetimeIndex, timeframe: str):
        self.intraday = timeframe in ("1h", "4h")
        n = len(times)
        if self.intraday and n:
            idx = times.tz_convert(IST)
            self.tod = (idx - idx.normalize()).asi8
            self.groups: dict[int, np.ndarray] = {}
            self.pos = np.zeros(n, dtype=int)
            for key in np.unique(self.tod):
                members = np.flatnonzero(self.tod == key)
                self.groups[int(key)] = members
                self.pos[members] = np.arange(len(members))

    def last(self, i: int, size: int) -> np.ndarray:
        if not self.intraday:
            return np.arange(max(0, i - size), i)
        g = self.groups[int(self.tod[i])]
        p = int(self.pos[i])
        return g[max(0, p - size) : p]


def _window(v: np.ndarray, idx: np.ndarray, size: int, min_frac: float) -> np.ndarray | None:
    if len(idx) < size:
        return None
    win = v[idx]
    known = win[np.isfinite(win)]
    if len(known) < max(1, int(np.ceil(min_frac * size))):
        return None
    return known


def classify_direction(o: float, h: float, l: float, c: float, cfg: SignalConfig) -> tuple[str, float | None, float | None]:
    rng = h - l
    if not np.isfinite(rng) or rng <= 0:
        return "neutral", None, None
    body = abs(c - o) / rng
    loc = (c - l) / rng
    if c > o and body >= cfg.vs_min_body_frac and loc >= cfg.vs_close_location_split:
        return "bullish", body, loc
    if c < o and body >= cfg.vs_min_body_frac and loc <= 1.0 - cfg.vs_close_location_split:
        return "bearish", body, loc
    return "neutral", body, loc


def volume_shocks(
    frame: pd.DataFrame,
    timeframe: str,
    atr: np.ndarray,
    ema20: np.ndarray | None,
    ema50: np.ndarray | None,
    cfg: SignalConfig,
    since: int,
) -> list[dict[str, Any]]:
    """Candles >= ``since`` with RVOL >= the smallest selectable threshold."""
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    times = frame.index
    n = len(c)
    refs = References(times, timeframe)
    floor = min(cfg.vs_rvol_options) if cfg.vs_rvol_options else cfg.vs_rvol_threshold
    tv = c * v
    out: list[dict[str, Any]] = []
    for i in range(max(0, since), n):
        vi = v[i]
        if not np.isfinite(vi) or vi <= 0:
            continue
        ref = _window(v, refs.last(i, cfg.vs_lookback), cfg.vs_lookback, cfg.vs_min_valid_frac)
        if ref is None or ref.mean() <= 0:
            continue
        avg = float(ref.mean())
        rvol = vi / avg
        if rvol < floor:
            continue
        ref50 = _window(v, refs.last(i, cfg.vs_long_lookback), cfg.vs_long_lookback, cfg.vs_min_valid_frac)
        ref60 = _window(v, refs.last(i, cfg.vs_percentile_lookback), cfg.vs_percentile_lookback, cfg.vs_min_valid_frac)
        std = float(ref.std(ddof=1)) if len(ref) > 1 else 0.0
        pct = p90 = None
        if ref60 is not None:
            pct = 100.0 * (float((ref60 < vi).sum()) + 0.5 * float((ref60 == vi).sum())) / len(ref60)
            p90 = float(np.percentile(ref60, cfg.vs_percentile))
        tv_ref = tv[refs.last(i, cfg.vs_lookback)]
        tv_ref = tv_ref[np.isfinite(tv_ref)]
        direction, body, loc = classify_direction(o[i], h[i], l[i], c[i], cfg)
        a = float(atr[i]) if np.isfinite(atr[i]) else None
        prev_c = c[i - 1] if i >= 1 else np.nan
        lo_r = max(0, i - cfg.vs_recent_range_bars)
        rh = float(h[lo_r:i].max()) if i > lo_r else np.nan
        rl = float(l[lo_r:i].min()) if i > lo_r else np.nan
        above_p = bool(p90 is not None and vi >= p90)
        is_shock = rvol >= cfg.vs_rvol_threshold and (above_p or not cfg.vs_require_percentile)

        def ema_dist(e: np.ndarray | None) -> float | None:
            if e is None or not np.isfinite(e[i]) or e[i] == 0:
                return None
            return _r((c[i] / e[i] - 1.0) * 100.0, 3)

        out.append(
            {
                "kind": f"volume_shock_{direction}",
                "direction": direction,
                "status": "shock" if is_shock else "elevated",
                "index": i,
                "time": iso(times[i]),
                "t": epoch_s(times[i]),
                "age_bars": n - 1 - i,
                "timeframe": timeframe,
                "reference": "same time-of-day candle of the previous sessions" if timeframe in ("1h", "4h") else "previous completed candles",
                "volume": float(vi),
                "avg_volume": _r(avg, 1),
                "median_volume": _r(float(np.median(ref)), 1),
                "max_volume": _r(float(ref.max()), 1),
                "reference_bars": int(len(ref)),
                "rvol": _r(rvol, 3),
                "rvol_50": _r(vi / ref50.mean(), 3) if ref50 is not None and ref50.mean() > 0 else None,
                "percentile_60": _r(pct, 1),
                "p90_60": _r(p90, 1),
                "above_percentile": above_p,
                "zscore": _r((vi - avg) / std, 3) if std > 0 else None,
                "traded_value": _r(c[i] * vi, 0),
                "avg_traded_value": _r(float(tv_ref.mean()), 0) if len(tv_ref) else None,
                "open": _r(o[i]),
                "high": _r(h[i]),
                "low": _r(l[i]),
                "close": _r(c[i]),
                "change_pct": _r((c[i] / prev_c - 1.0) * 100.0, 3) if np.isfinite(prev_c) and prev_c > 0 else None,
                "body_pct": _r(body * 100.0, 2) if body is not None else None,
                "close_location": _r(loc, 3),
                "range_atr": _r((h[i] - l[i]) / a, 3) if a else None,
                "atr": _r(a),
                "close_vs_ema20_pct": ema_dist(ema20),
                "close_vs_ema50_pct": ema_dist(ema50),
                "recent_high": _r(rh),
                "recent_low": _r(rl),
                "distance_from_recent_high_pct": _r((c[i] / rh - 1.0) * 100.0, 3) if np.isfinite(rh) else None,
                "distance_from_recent_low_pct": _r((c[i] / rl - 1.0) * 100.0, 3) if np.isfinite(rl) else None,
                "recent_bars": cfg.vs_recent_range_bars,
            }
        )
    return out
