"""N-day high breakout / N-day low breakdown on completed **daily** candles (E1, spec 5).

Reference window for candle i: the previous N completed daily candles
``i-N .. i-1`` (the candle itself is excluded; N trading candles, not calendar
days). With ``buffer = breakout_buffer_atr x ATR(14)[i]``:

    bullish breakout : close[i] > max(high[i-N:i]) + buffer
    bearish breakdown: close[i] < min(low[i-N:i])  - buffer

Confirmation modes:

* 1 close  - confirmed on the first completed close beyond the level;
* 2 closes - the level and buffer are fixed at the first close beyond (bar f);
  confirmed when close[f+1] is also beyond it. If close[f+1] is not beyond, the
  event is a **failed** breakout (false break). If f is the latest completed
  candle, the event is **pending**.

Intraday charts never compute an "N-bar" breakout: ``daily_reference`` gives
the levels from the last N completed daily candles for display.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..candlesticks.marubozu import prior_volume_ratio
from ..config import TechnicalConfig
from ..timeutil import epoch_s, iso


def reference_levels(high: Any, low: Any, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    """ref_high[i] = max(high[i-N:i]), ref_low[i] = min(low[i-N:i]); NaN until N candles exist."""
    h = pd.Series(np.asarray(high, dtype=float))
    l = pd.Series(np.asarray(low, dtype=float))
    rh = h.rolling(n_days, min_periods=n_days).max().shift(1).to_numpy()
    rl = l.rolling(n_days, min_periods=n_days).min().shift(1).to_numpy()
    return rh, rl


def _event(kind: str, direction: str, frame: pd.DataFrame, atr: np.ndarray, rh: np.ndarray, rl: np.ndarray, f: int, conf: int | None, status: str, mode: int, n_days: int, cfg: TechnicalConfig) -> dict[str, Any]:
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    times = frame.index
    i = conf if conf is not None else f
    level = rh[f] if direction == "bullish" else rl[f]
    buf = cfg.breakout_buffer_atr * float(atr[f])
    a = float(atr[i]) if np.isfinite(atr[i]) else None
    dist = c[i] - level
    return {
        "kind": kind,
        "direction": direction,
        "status": status,  # confirmed | failed | pending
        "confirm_closes": mode,
        "lookback_days": n_days,
        "first_close_index": f,
        "first_close_time": iso(times[f]),
        "index": i,
        "time": iso(times[i]),
        "t": epoch_s(times[i]),
        "age_bars": len(c) - 1 - i,
        "reference_start": iso(times[f - n_days]),
        "reference_end": iso(times[f - 1]),
        "prev_high": float(rh[f]),
        "prev_low": float(rl[f]),
        "level": float(level),
        "buffer": round(buf, 4),
        "close": float(c[i]),
        "distance": round(float(dist), 4),
        "breakout_pct": round(float(dist / level * 100.0), 3),
        "distance_atr": round(float(dist) / a, 3) if a else None,
        "range_atr": round(float(h[i] - l[i]) / a, 3) if a else None,
        "atr": a,
        "volume": None if not np.isfinite(v[i]) else float(v[i]),
        "volume_ratio": None if (r := prior_volume_ratio(v, i, cfg.breakout_volume_lookback)) is None else round(r, 3),
        "volume_lookback": cfg.breakout_volume_lookback,
    }


def breakout_events(frame: pd.DataFrame, atr: np.ndarray, n_days: int, mode: int, cfg: TechnicalConfig, since: int = 0) -> list[dict[str, Any]]:
    """Breakout/breakdown events whose confirmation (or failure) bar is >= ``since``."""
    if mode not in (1, 2):
        raise ValueError("confirmation mode must be 1 or 2 closes")
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    n = len(c)
    rh, rl = reference_levels(h, l, n_days)
    out: list[dict[str, Any]] = []
    for direction, ref, sign in (("bullish", rh, 1.0), ("bearish", rl, -1.0)):
        kind = f"breakout_high_{n_days}d" if direction == "bullish" else f"breakdown_low_{n_days}d"
        with np.errstate(invalid="ignore"):
            buf = cfg.breakout_buffer_atr * atr
            beyond = (sign * (c - ref) > buf) & np.isfinite(ref) & np.isfinite(atr)
        for f in np.nonzero(beyond)[0]:
            f = int(f)
            if mode == 1:
                if f >= since:
                    ev = _event(kind, direction, frame, atr, rh, rl, f, f, "confirmed", 1, n_days, cfg)
                    ev["first_of_run"] = not (f >= 1 and bool(beyond[f - 1]))  # previous close was not beyond its own level
                    out.append(ev)
                continue
            # Two closes: level + buffer fixed at the first close beyond.
            if f >= 1 and beyond[f - 1] and np.isfinite(rh[f - 1] if direction == "bullish" else rl[f - 1]):
                continue  # f is itself the second close of an earlier attempt (handled there)
            if f == n - 1:
                if f >= since:
                    out.append(_event(kind, direction, frame, atr, rh, rl, f, None, "pending", 2, n_days, cfg))
                continue
            level = ref[f] + sign * buf[f]
            second_ok = sign * (c[f + 1] - level) > 0
            if f + 1 >= since:
                out.append(_event(kind, direction, frame, atr, rh, rl, f, f + 1, "confirmed" if second_ok else "failed", 2, n_days, cfg))
    out.sort(key=lambda e: (e["index"], e["direction"]))
    return out


def daily_reference(frame: pd.DataFrame, n_days: int) -> dict[str, Any] | None:
    """Levels a breakout on the next session is judged against: high/low of the
    last N completed daily candles (for overlays on 1H/4H/1D charts)."""
    if len(frame) < n_days:
        return None
    win = frame.iloc[-n_days:]
    return {
        "lookback_days": n_days,
        "high": float(win["high"].max()),
        "low": float(win["low"].min()),
        "reference_start": iso(win.index[0]),
        "reference_end": iso(win.index[-1]),
        "t_start": epoch_s(win.index[0]),
        "t_end": epoch_s(win.index[-1]),
        "basis": "daily",
    }
