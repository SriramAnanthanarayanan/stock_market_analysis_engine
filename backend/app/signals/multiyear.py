"""Multi-year breakouts on completed daily candles (docs/multi-year-breakouts.md).

A multi-year breakout is a completed daily close above a resistance the stock
has not exceeded for years - "breaking out after many years". The number of
years is not fixed: every breakout whose base lasted at least
``multiyear_min_years`` is stored with its age, and the scanner filters by any
number (5, 10, 15, 20, 7.5 ...).

Definitions for the candle ``t`` (no look-ahead: only candles before ``t``):

* ``j_last`` = the most recent earlier candle whose high reached today's close
  (none: the close is an all-time high within the available data);
* ``level`` = the highest high since then (candles ``j_last+1 .. t-1``) - the
  resistance the close is breaking; ``level_time`` = when it was set;
* breakout when ``close > level + multiyear_buffer_atr x ATR(14)``;
* the **base** starts at the first candle after ``j_last`` whose high came within
  ``multiyear_zone_pct`` % of the level (ASSUMPTION: a marginal poke above an
  old high inside that zone does not restart the clock); ``base_years`` = time
  from the base start to the breakout candle = how long the stock stayed capped
  below this resistance. ``level_years`` is the strict age of the exact high;
* one event per base: further closes above the same base are continuation,
  unless the breakout failed first (``multiyear_fail_closes`` consecutive closes
  back below the level) - then the next close above the base (including the
  failed attempt's highs, which stay part of the original base) is a re-break.

The monotonic stack of suffix maxima answers ``j_last`` / ``level`` in
O(log n) per candle, so the full history (daily since 1996) is cheap.

Forward returns of past events (descriptive event study, never used for
detection) are price returns on the split / bonus-adjusted close, compared with
the NIFTY 50 price index over the same calendar window when it is available.
"""

from __future__ import annotations

import bisect
import hashlib
from typing import Any

import numpy as np
import pandas as pd

from ..config import SignalConfig
from ..timeutil import epoch_s, iso

YEAR_SECONDS = 365.2425 * 86400.0
STATUS_LABELS = {"holding": "Holding above", "below_level": "Back below level", "failed": "Failed"}


def _years(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return (b - a).total_seconds() / YEAR_SECONDS


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _same_tz(when: pd.Timestamp, times: pd.DatetimeIndex) -> pd.Timestamp:
    if times.tz is None and when.tzinfo is not None:
        return when.tz_convert(None)
    if times.tz is not None and when.tzinfo is None:
        return when.tz_localize(times.tz)
    return when


def _close_at(times: pd.DatetimeIndex, values: np.ndarray, when: pd.Timestamp) -> tuple[float | None, int | None]:
    """Close of the last candle at or before ``when`` (value, index)."""
    k = int(times.searchsorted(_same_tz(when, times), side="right")) - 1
    if k < 0 or not np.isfinite(values[k]):
        return None, None
    return float(values[k]), k


def _forward(times, c, h, l, i: int, months: tuple[int, ...], bench: pd.Series | None) -> dict[str, Any]:
    """Forward price returns of an event at candle i (only candles up to the cutoff)."""
    t0 = times[i]
    last = times[-1]
    c0 = float(c[i])
    b_times = bench.index if bench is not None and len(bench) else None
    b_vals = bench.to_numpy(dtype=float) if b_times is not None else None
    b0 = _close_at(b_times, b_vals, t0)[0] if b_times is not None else None
    out = []
    for m in months:
        target = t0 + pd.DateOffset(months=m)
        if target > last:
            out.append({"months": m, "return_pct": None, "benchmark_pct": None, "excess_pct": None, "status": "pending"})
            continue
        cv, k = _close_at(times, c, target)
        ret = (cv / c0 - 1.0) * 100.0 if cv is not None and c0 > 0 else None
        bret = None
        if b0 is not None and b0 > 0:
            bv = _close_at(b_times, b_vals, target)[0]
            bret = (bv / b0 - 1.0) * 100.0 if bv is not None else None
        out.append({"months": m, "return_pct": _r(ret, 2), "benchmark_pct": _r(bret, 2), "excess_pct": _r(ret - bret, 2) if (ret is not None and bret is not None) else None, "status": "ok" if ret is not None else "no_data"})
    # best / worst move within 12 months (raw high / low vs the breakout close)
    end12 = t0 + pd.DateOffset(months=12)
    k12 = int(times.searchsorted(min(end12, last), side="right"))
    seg_h, seg_l = h[i + 1 : k12], l[i + 1 : k12]
    mfe = (float(np.nanmax(seg_h)) / c0 - 1.0) * 100.0 if len(seg_h) and c0 > 0 else None
    mae = (float(np.nanmin(seg_l)) / c0 - 1.0) * 100.0 if len(seg_l) and c0 > 0 else None
    return {"horizons": out, "mfe_12m_pct": _r(mfe, 2), "mae_12m_pct": _r(mae, 2), "window_complete_12m": end12 <= last}


def multiyear_breakouts(frame: pd.DataFrame, atr: np.ndarray, cfg: SignalConfig, symbol: str = "", config_hash: str = "", benchmark: pd.Series | None = None) -> list[dict[str, Any]]:
    """Every multi-year breakout (base >= ``multiyear_min_years``) of the series, oldest
    first, with its state at the latest completed candle and its forward returns."""
    n = len(frame)
    if n < 3:
        return []
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    v = frame["volume"].to_numpy(dtype=float)
    times = frame.index
    secs = np.asarray(times.as_unit("s").asi8, dtype=np.int64)  # unit-safe (the index may be ns or us)
    atr = np.asarray(atr, dtype=float)
    zone = cfg.multiyear_zone_pct / 100.0
    min_sec = cfg.multiyear_min_years * YEAR_SECONDS
    stack: list[int] = []
    neg: list[float] = []  # -high of the stack (increasing), for bisect
    raw: list[dict[str, Any]] = []
    last_ev: dict[str, Any] | None = None
    below = 0
    for t in range(n):
        if t > 0 and np.isfinite(c[t]) and stack:
            C = float(c[t])
            pos = bisect.bisect_right(neg, -C)  # stack elements with high >= C
            if pos < len(stack):
                a = stack[pos]
                j_last = stack[pos - 1] if pos > 0 else -1
                L = float(h[a])
                buf = cfg.multiyear_buffer_atr * (float(atr[t]) if np.isfinite(atr[t]) else 0.0)
                lo = j_last + 1
                # the base cannot start before lo: skip cheaply when even that is too recent
                if C > L + buf and secs[t] - secs[lo] >= min_sec:
                    floor = L * (1.0 - zone)
                    bs = lo + int(np.argmax(h[lo : a + 1] >= floor))
                    if last_ev is not None and last_ev["failed"] and a >= last_ev["i"] and last_ev["bs"] >= lo:
                        # the resistance was set by a failed breakout of an earlier base: its highs
                        # belong to that base, so the base keeps its original start (re-break)
                        bs = min(bs, last_ev["bs"])
                    if secs[t] - secs[bs] >= min_sec:
                        same_base = last_ev is not None and last_ev["i"] >= bs
                        if same_base and not last_ev["failed"]:
                            last_ev["continuation_closes"] += 1
                        else:
                            ev = {"i": t, "a": a, "bs": bs, "j_last": j_last, "level": L, "buffer": buf, "failed": False, "failed_i": None, "continuation_closes": 0, "rebreak_of": last_ev["i"] if same_base else None}
                            raw.append(ev)
                            last_ev = ev
                            below = 0
        if last_ev is not None and t > last_ev["i"] and np.isfinite(c[t]):
            below = below + 1 if c[t] < last_ev["level"] else 0
            if below >= cfg.multiyear_fail_closes and not last_ev["failed"]:
                last_ev["failed"], last_ev["failed_i"] = True, t
        if not np.isfinite(h[t]):
            continue  # a missing high is never guessed: the candle cannot set a level
        while stack and h[stack[-1]] <= h[t]:
            stack.pop()
            neg.pop()
        stack.append(t)
        neg.append(-float(h[t]))
    return [_record(e, frame, h, l, c, v, atr, cfg, symbol, config_hash, benchmark) for e in raw]


def _record(e: dict[str, Any], frame, h, l, c, v, atr, cfg: SignalConfig, symbol: str, chash: str, bench) -> dict[str, Any]:
    times = frame.index
    n = len(frame)
    i, a, bs, j = e["i"], e["a"], e["bs"], e["j_last"]
    L = e["level"]
    last = n - 1
    floor = L * (1.0 - cfg.multiyear_zone_pct / 100.0)
    tests: list[int] = []  # separate visits to the resistance zone (the first one is the old high itself)
    prev = None
    for k in np.flatnonzero(h[bs:i] >= floor):
        k = int(k) + bs
        if prev is None or k - prev >= cfg.multiyear_test_separation_sessions:
            tests.append(k)
        prev = k
    lb = cfg.multiyear_volume_lookback
    ref = v[max(0, i - lb) : i]
    ref = ref[np.isfinite(ref)]
    vr = (float(v[i]) / float(ref.mean())) if (len(ref) >= max(1, lb // 2) and ref.mean() > 0 and np.isfinite(v[i])) else None
    a_i = float(atr[i]) if np.isfinite(atr[i]) and atr[i] > 0 else None
    failed = e["failed"]
    status = "failed" if failed else ("holding" if c[last] >= L else "below_level")
    after_h, after_l = h[i + 1 :], l[i + 1 :]
    base_years = _years(times[bs], times[i])
    ev_id = hashlib.sha1(f"{symbol}|multiyear|{epoch_s(times[i])}|{chash}".encode()).hexdigest()[:16]
    rec = {
        "event_id": ev_id,
        "kind": "multiyear_breakout",
        "direction": "bullish",
        "index": i,
        "t": epoch_s(times[i]),
        "time": iso(times[i]),
        "age_bars": last - i,
        "close": _r(c[i]),
        "high": _r(h[i]),
        "level": _r(L),
        "level_t": epoch_s(times[a]),
        "level_time": iso(times[a]),
        "level_years": round(_years(times[a], times[i]), 2),
        "base_start_t": epoch_s(times[bs]),
        "base_start_time": iso(times[bs]),
        "base_years": round(base_years, 2),
        "base_starts_at_data_start": bs == 0,
        "base_low": _r(float(np.min(l[bs:i]))),
        "base_depth_pct": _r((L - float(np.min(l[bs:i]))) / L * 100.0, 2),
        "zone_floor": _r(floor),
        "zone_tests": len(tests),
        "zone_test_times": [iso(times[k]) for k in tests[:12]],
        "all_time_high_in_data": j < 0,
        "highest_since_time": iso(times[j]) if j >= 0 else None,
        "years_since_higher": round(_years(times[j], times[i]), 2) if j >= 0 else None,
        "data_start": iso(times[0]),
        "years_of_data": round(_years(times[0], times[i]), 2),
        "breakout_pct": _r((c[i] / L - 1.0) * 100.0, 3),
        "strength_atr": _r((c[i] - L) / a_i, 3) if a_i else None,
        "buffer": _r(e["buffer"]),
        "volume": _r(v[i], 1) if np.isfinite(v[i]) else None,
        "volume_ratio": _r(vr, 3),
        "rebreak": e["rebreak_of"] is not None,
        "rebreak_of_time": iso(times[e["rebreak_of"]]) if e["rebreak_of"] is not None else None,
        "continuation_closes": e["continuation_closes"],
        "status": status,
        "status_label": STATUS_LABELS[status],
        "failed_time": iso(times[e["failed_i"]]) if e["failed_i"] is not None else None,
        "latest_close": _r(c[last]),
        "latest_time": iso(times[last]),
        "above_level_pct": _r((c[last] / L - 1.0) * 100.0, 3),
        "return_since_pct": _r((c[last] / c[i] - 1.0) * 100.0, 2) if c[i] > 0 else None,
        "max_gain_pct": _r((float(np.max(after_h)) / c[i] - 1.0) * 100.0, 2) if len(after_h) and c[i] > 0 else None,
        "max_drawdown_pct": _r((float(np.min(after_l)) / c[i] - 1.0) * 100.0, 2) if len(after_l) and c[i] > 0 else None,
        "forward": _forward(times, c, h, l, i, tuple(cfg.multiyear_horizons_months), bench),
        "zone_pct": cfg.multiyear_zone_pct,
        "fail_closes": cfg.multiyear_fail_closes,
    }
    since = f"highest close since {rec['highest_since_time'][:10]} ({rec['years_since_higher']} years)" if j >= 0 else f"all-time high within the data (since {rec['data_start'][:10]})"
    rec["explanation"] = [
        f"Close {rec['close']} above {rec['level']}, the highest high since the stock last traded at this price: {since}.",
        f"Base: {rec['base_years']} years below this resistance (from {rec['base_start_time'][:10]}, first high within {cfg.multiyear_zone_pct} % of the level{'; the data starts here, so the base may be longer' if bs == 0 else ''}); the exact high was set {rec['level_time'][:10]} ({rec['level_years']} years).",
        f"Resistance tests in the base: {rec['zone_tests']}; base depth {rec['base_depth_pct']} %.",
        f"Breakout candle: {rec['breakout_pct']} % above the level ({rec['strength_atr']} ATR), volume {rec['volume_ratio']}x the {lb}-session average." + (" Re-break after a failed breakout of the same base." if rec["rebreak"] else ""),
        f"Since then: {STATUS_LABELS[status]}" + (f" (failed {rec['failed_time'][:10]}: {cfg.multiyear_fail_closes} closes back below the level)" if failed else "") + f"; latest close {rec['latest_close']} ({rec['above_level_pct']} % vs the level).",
    ]
    return rec


def recent(events: list[dict[str, Any]], n_bars: int, sessions: int) -> list[dict[str, Any]]:
    """Events whose breakout candle lies within the last ``sessions`` completed sessions."""
    return [e for e in events if e["index"] >= n_bars - sessions]


def compact(e: dict[str, Any]) -> dict[str, Any]:
    """History entry (stored once per stock and scan) for the chart and the event study."""
    keys = ("event_id", "t", "time", "close", "level", "level_t", "level_time", "level_years", "base_start_t", "base_start_time", "base_years", "base_starts_at_data_start",
            "base_depth_pct", "zone_tests", "all_time_high_in_data", "highest_since_time", "years_since_higher", "breakout_pct", "strength_atr", "volume_ratio",
            "rebreak", "status", "failed_time", "return_since_pct", "max_gain_pct", "max_drawdown_pct", "forward")
    return {k: e.get(k) for k in keys}
