"""Technical-analysis orchestration (E1).

One entry point per use, all on the same completed raw-OHLC candles:

* ``indicator_series`` - EMA / RSI / ATR arrays for charts (the chart and the
  scanners read the same functions, so values are identical);
* ``analyze`` - the latest indicator snapshot plus candlestick, EMA-crossover,
  RSI-divergence and (daily) N-day breakout signals that scans persist.

Price-action pattern detection is untouched: this module only shares candles.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd

from .breakouts import breakout_events, daily_reference
from .candlesticks import marubozu_events
from .config import DEFAULT_CONFIG, DetectionConfig, TechnicalConfig, config_hash
from .indicators.core import atr_wilder
from .indicators.divergence import Divergence, current_divergences, find_divergences
from .indicators.technical import crossover, crossunder, ema, ema_alignment, rsi
from .timeutil import epoch_s, iso


def _f(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


# ------------------------------------------------------------------ indicator cache
class _LRU:
    """Small in-process cache. The key holds everything a value depends on
    (symbol, timeframe, basis, parameters, first/last candle time, bar count and
    the last close), so new or revised candles and changed parameters can never
    return a stale result."""

    def __init__(self, size: int = 256):
        self.size = size
        self._d: OrderedDict[tuple, Any] = OrderedDict()
        self._lock = Lock()

    def get(self, key: tuple) -> Any:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return None

    def put(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


_CACHE = _LRU()


def frame_key(frame: pd.DataFrame) -> tuple:
    if frame.empty:
        return (0,)
    c = frame["close"].to_numpy(dtype=float)
    return (len(frame), epoch_s(frame.index[0]), epoch_s(frame.index[-1]), float(np.nansum(c)), float(c[-1]))


def indicator_series(
    frame: pd.DataFrame,
    tcfg: TechnicalConfig,
    ema_periods: tuple[int, ...] | None = None,
    rsi_period: int | None = None,
    atr_period: int | None = None,
    cache_key: tuple | None = None,
) -> dict[str, Any]:
    """EMA/RSI/ATR arrays on the full frame (warm-up included)."""
    periods = tuple(ema_periods or tcfg.ema_periods)
    rp = rsi_period or tcfg.rsi_period
    ap = atr_period or tcfg.atr_period
    key = None
    if cache_key is not None:
        key = ("series", *cache_key, frame_key(frame), periods, rp, ap, tcfg.ema_seed)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    o, h, l, c = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    out = {
        "ema": {p: ema(c, p, tcfg.ema_seed) for p in periods},
        "rsi": rsi(c, rp),
        "atr": atr_wilder(h, l, c, ap),
        "ema_periods": periods,
        "rsi_period": rp,
        "atr_period": ap,
    }
    if key is not None:
        _CACHE.put(key, out)
    return out


# ------------------------------------------------------------------ analysis
@dataclass
class TechResult:
    status: str  # ok | insufficient_history
    snapshot: dict[str, Any]
    signals: list[dict[str, Any]] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)


def divergence_record(d: Divergence, times: pd.DatetimeIndex, n: int) -> dict[str, Any]:
    def pv(p) -> dict[str, Any]:
        return {"i": p.i, "t": epoch_s(times[p.i]), "time": iso(times[p.i]), "price": _f(p.price), "rsi": _f(p.rsi, 2)}

    ev = d.event_index
    return {
        "kind": "rsi_divergence",
        "direction": d.direction,
        "status": d.status,
        "p1": pv(d.p1),
        "p2": pv(d.p2),
        "confirmed_index": d.confirmed_index,
        "confirmed_time": iso(times[d.confirmed_index]) if d.confirmed_index is not None else None,
        "confirmed_t": epoch_s(times[d.confirmed_index]) if d.confirmed_index is not None else None,
        "invalidated_time": iso(times[d.invalidated_index]) if d.invalidated_index is not None else None,
        "invalidated_t": epoch_s(times[d.invalidated_index]) if d.invalidated_index is not None else None,
        "superseded_time": iso(times[d.superseded_index]) if d.superseded_index is not None and d.superseded_index < n else None,
        "price_diff_atr": _f(d.price_diff_atr, 3),
        "rsi_diff": _f(d.rsi_diff, 2),
        "separation": d.separation,
        "gates": d.gates,
        "index": ev,
        "time": iso(times[ev]),
        "t": epoch_s(times[ev]),
        "age_bars": n - 1 - ev,
    }


def _crosses(ema_map: dict[int, np.ndarray], pairs: tuple[tuple[int, int], ...], times: pd.DatetimeIndex, since: int) -> list[dict[str, Any]]:
    out = []
    n = len(times)
    for fast, slow in pairs:
        if fast not in ema_map or slow not in ema_map:
            continue
        a, b = ema_map[fast], ema_map[slow]
        for direction, mask in (("bullish", crossover(a, b)), ("bearish", crossunder(a, b))):
            for i in np.nonzero(mask)[0]:
                i = int(i)
                if i < since:
                    continue
                out.append(
                    {
                        "kind": f"ema_cross_{fast}_{slow}",
                        "direction": direction,
                        "status": "confirmed",
                        "fast": fast,
                        "slow": slow,
                        "index": i,
                        "time": iso(times[i]),
                        "t": epoch_s(times[i]),
                        "age_bars": n - 1 - i,
                        "fast_value": _f(a[i]),
                        "slow_value": _f(b[i]),
                    }
                )
    return out


def nearest_sr(frame: pd.DataFrame, f: int, direction: str, timeframe: str, dcfg: DetectionConfig) -> dict[str, Any] | None:
    """The support/resistance zone (as of the candle before the breakout) the
    move attacked: nearest resistance above the prior close for a breakout,
    nearest support below it for a breakdown."""
    from .analytics.sr import compute_zones
    from .patterns.base import SeriesContext

    if f < 2:
        return None
    sub = frame.iloc[:f]
    ctx = SeriesContext("_", timeframe, sub, dcfg)
    zones = compute_zones(ctx)
    prev_close = float(sub["close"].iloc[-1])
    if direction == "bullish":
        cand = [z for z in zones if z.high >= prev_close]
        z = min(cand, key=lambda z: z.low) if cand else None
    else:
        cand = [z for z in zones if z.low <= prev_close]
        z = max(cand, key=lambda z: z.high) if cand else None
    if z is None:
        return None
    return {"low": _f(z.low), "high": _f(z.high), "role": z.role, "touches": len(z.touches), "status_before": z.status}


def analyze(frame: pd.DataFrame, timeframe: str, tcfg: TechnicalConfig, dcfg: DetectionConfig = DEFAULT_CONFIG, with_sr: bool = True) -> TechResult:
    n = len(frame)
    times = frame.index
    if n == 0:
        return TechResult("insufficient_history", {"bars": 0})
    ser = indicator_series(frame, tcfg)
    c = frame["close"].to_numpy(dtype=float)
    last = n - 1
    emas = {p: _f(v[last]) for p, v in ser["ema"].items()}
    align = ema_alignment(emas, tcfg.ema_periods, tcfg.ema_partial_alignment)
    r = _f(ser["rsi"][last], 2)
    a = _f(ser["atr"][last])
    close = float(c[last])
    rsi_state = None
    if r is not None:
        rsi_state = "overbought" if r >= tcfg.rsi_overbought else "oversold" if r <= tcfg.rsi_oversold else "neutral"

    def rel(p: int) -> str | None:
        v = emas.get(p)
        return None if v is None else ("above" if close > v else "below" if close < v else "at")

    snapshot: dict[str, Any] = {
        "bars": n,
        "last_candle_time": iso(times[last]),
        "close": _f(close),
        "ema": {str(p): v for p, v in emas.items()},
        "ema_distance_pct": {str(p): (_f((close / v - 1) * 100, 3) if v else None) for p, v in emas.items()},
        "alignment": align.state,
        "ema_missing": list(align.missing),
        "price_vs_ema": {str(p): rel(p) for p in (20, 50, 200) if p in emas},
        "rsi": r,
        "rsi_period": tcfg.rsi_period,
        "rsi_state": rsi_state,
        "atr": a,
        "atr_period": tcfg.atr_period,
        "atr_pct": _f(a / close * 100, 3) if a else None,
        "range_atr": _f((float(frame["high"].iloc[last]) - float(frame["low"].iloc[last])) / a, 3) if a else None,
    }
    signals: list[dict[str, Any]] = []
    for ev in marubozu_events(frame, ser["atr"], tcfg, timeframe, since=n - tcfg.candle_scan_recent_bars):
        signals.append({**ev, "category": "candlestick", "status": "confirmed"})
    crosses = _crosses(ser["ema"], tcfg.ema_cross_pairs, times, n - tcfg.ema_cross_recent_bars)
    signals += [{**x, "category": "ema_cross"} for x in crosses]
    divs = find_divergences(frame["high"], frame["low"], c, ser["rsi"], ser["atr"], tcfg)
    for d in current_divergences(divs, last, tcfg.div_scan_max_age_bars):
        signals.append({**divergence_record(d, times, n), "category": "rsi_divergence"})
    if timeframe == "1d":
        sr_memo: dict[tuple[int, str], Any] = {}
        for nd in tcfg.breakout_lookback_days:
            for mode in (1, 2):
                for ev in breakout_events(frame, ser["atr"], nd, mode, tcfg, since=n - tcfg.breakout_scan_recent_bars):
                    vr = ev.get("volume_ratio")
                    ev["volume_ok"] = tcfg.breakout_min_volume_ratio <= 0 or (vr is not None and vr >= tcfg.breakout_min_volume_ratio)
                    if with_sr:
                        key = (ev["first_close_index"], ev["direction"])
                        if key not in sr_memo:
                            sr_memo[key] = nearest_sr(frame, key[0], key[1], timeframe, dcfg)
                        ev["prior_level"] = sr_memo[key]
                    signals.append({**ev, "category": "breakout"})
        refs = {str(nd): daily_reference(frame, nd) for nd in tcfg.breakout_lookback_days}
        snapshot["daily_reference"] = refs
    snapshot["crosses_recent"] = [{"kind": x["kind"], "direction": x["direction"], "age_bars": x["age_bars"]} for x in crosses]
    status = "ok" if not align.missing else "insufficient_history"
    return TechResult(status, snapshot, signals, divs)


def tech_hash(tcfg: TechnicalConfig) -> str:
    return config_hash(tcfg)
