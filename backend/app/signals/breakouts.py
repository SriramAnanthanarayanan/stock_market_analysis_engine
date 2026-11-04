"""Fresh N-day breakouts (spec 3) and 52-week breakouts / breakdowns (spec 5).

Both use ``crossing.crossing_series`` on completed **daily** candles; this module
turns the walk into event records (stable event IDs, confirmation modes,
First-Breakout-in-Lookback, episode state) and the 52-week state of the latest
candle. See docs/first-time-breakout.md and docs/52-week-breakouts.md.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from ..candlesticks.marubozu import prior_volume_ratio
from ..config import SignalConfig
from ..timeutil import IST, epoch_s, iso
from .crossing import CrossingParams, CrossingSeries, confirm_two_closes, crossing_series, first_in_lookback

_CLOSE = pd.Timedelta(hours=15, minutes=30)


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def session_close_iso(ts: pd.Timestamp) -> str:
    """Daily candle completion time (15:30 IST of its date)."""
    t = pd.Timestamp(ts).tz_convert(IST)
    return (t.normalize() + _CLOSE).isoformat()


def event_id(symbol: str, kind: str, t: int, config_hash: str) -> str:
    return hashlib.sha1(f"{symbol}|{kind}|{t}|{config_hash}".encode()).hexdigest()[:16]


def fresh_params(cfg: SignalConfig) -> CrossingParams:
    return CrossingParams(cfg.fresh_lookback_days, cfg.fresh_buffer_atr, cfg.fresh_touch_buffer_atr, cfg.fresh_require_prior_level_clear, cfg.fresh_rearm_closes, cfg.fresh_rearm_on_new_range)


def w52_params(cfg: SignalConfig) -> CrossingParams:
    return CrossingParams(cfg.w52_lookback_sessions, cfg.w52_buffer_atr, cfg.w52_touch_buffer_atr, cfg.w52_require_prior_level_clear, cfg.w52_rearm_closes, False)


def fresh_kind(direction: str, n: int) -> str:
    return f"fresh_breakout_high_{n}d" if direction == "bullish" else f"fresh_breakdown_low_{n}d"


def w52_kind(direction: str) -> str:
    return "week52_high_breakout" if direction == "bullish" else "week52_low_breakdown"


def _level_bar(frame: pd.DataFrame, i: int, n: int, direction: str) -> int:
    """Candle that set the reference level of candle i (latest one on ties)."""
    lo = max(0, i - n)
    col = frame["high"].to_numpy(dtype=float)[lo:i] if direction == "bullish" else frame["low"].to_numpy(dtype=float)[lo:i]
    target = col.max() if direction == "bullish" else col.min()
    return lo + int(np.flatnonzero(col == target)[-1])


def crossing_events(
    frame: pd.DataFrame,
    atr: np.ndarray,
    p: CrossingParams,
    *,
    symbol: str,
    kind_of,
    config_hash: str,
    since: int,
    volume_lookback: int,
    first_lookback: int,
) -> tuple[list[dict[str, Any]], dict[str, CrossingSeries]]:
    """Event records (both directions) whose event candle index is >= ``since``."""
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    times = frame.index
    n = len(c)
    out: list[dict[str, Any]] = []
    walks: dict[str, CrossingSeries] = {}
    for direction in ("bullish", "bearish"):
        cs = crossing_series(h, l, c, atr, p, direction)
        walks[direction] = cs
        kind = kind_of(direction)
        eps = cs.episodes
        for k, ep in enumerate(eps):
            i = ep.event
            if i < since:
                continue
            a = float(atr[i]) if np.isfinite(atr[i]) else None
            lvl = float(cs.level[i])
            dist = c[i] - lvl
            last_beyond = cs.last_beyond_before(i)
            since_beyond = i - last_beyond if last_beyond is not None else None
            hist = i - cs.first if cs.first is not None else 0
            c2, c2i = confirm_two_closes(cs, c, i)
            prev_ep = eps[k - 1] if k > 0 else None
            lb = _level_bar(frame, i, p.lookback, direction)
            if ep.superseded_by is not None:
                ep_state, ep_time = "superseded", iso(times[ep.superseded_by])
            elif ep.reset is not None:
                ep_state, ep_time = "reset", iso(times[ep.reset])
            else:
                ep_state, ep_time = "active", None
            still = bool(cs.sign * (c[-1] - lvl) > 0)
            t = epoch_s(times[i])
            rec = {
                "event_id": event_id(symbol, kind, t, config_hash),
                "kind": kind,
                "direction": direction,
                "lookback": p.lookback,
                "index": i,
                "time": iso(times[i]),
                "t": t,
                "confirmed_at": session_close_iso(times[i]),
                "age_bars": n - 1 - i,
                "level": _r(lvl),
                "level_time": iso(times[lb]),
                "previous_level": _r(cs.level[i - 1]) if i >= 1 else None,
                "buffer": _r(cs.buffer[i]),
                "reference_high": _r(cs.level[i] if direction == "bullish" else cs.other[i]),
                "reference_low": _r(cs.other[i] if direction == "bullish" else cs.level[i]),
                "reference_start": iso(times[i - p.lookback]),
                "reference_end": iso(times[i - 1]),
                "open": _r(o[i]),
                "high": _r(h[i]),
                "low": _r(l[i]),
                "close": _r(c[i]),
                "breakout_pct": _r(dist / lvl * 100.0, 3),
                "distance_atr": _r(dist / a, 3) if a else None,
                "range_atr": _r((h[i] - l[i]) / a, 3) if a else None,
                "atr": _r(a),
                "volume": None if not np.isfinite(v[i]) else float(v[i]),
                "volume_ratio": _r(prior_volume_ratio(v, i, volume_lookback), 3),
                "volume_lookback": volume_lookback,
                "trigger": ep.trigger,
                "confirm_1": "confirmed",
                "confirm_2": c2,
                "confirm_2_time": iso(times[c2i]) if c2i is not None else None,
                "episode_state": ep_state,
                "episode_change_time": ep_time,
                "suppressed_crossings": len(ep.suppressed),
                "latest_close": _r(c[-1]),
                "latest_vs_level_pct": _r((c[-1] / lvl - 1.0) * 100.0, 3),
                "still_beyond": still,
                "sessions_since_last_beyond": since_beyond,
                "history_sessions_before": int(hist),
                "first_lookback_sessions": first_lookback,
                "first_in_lookback": first_in_lookback(since_beyond, hist, first_lookback),
                "previous_event_time": iso(times[prev_ep.event]) if prev_ep else None,
                "sessions_since_previous_event": (i - prev_ep.event) if prev_ep else None,
                "price_basis_note": "raw OHLC (split/bonus adjusted by the provider, not dividend adjusted)",
            }
            out.append(rec)
    out.sort(key=lambda e: (e["index"], e["direction"]))
    return out, walks


def touch_records(frame: pd.DataFrame, walks: dict[str, CrossingSeries], kind_of, since: int) -> list[dict[str, Any]]:
    """Provisional intraday touches (high above / low below the level) on completed
    candles >= ``since``: the first touching candle of a run, with its close outcome."""
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    times = frame.index
    n = len(c)
    out = []
    for direction, cs in walks.items():
        for i in np.flatnonzero(cs.touch):
            i = int(i)
            if i < since or (i >= 1 and cs.touch[i - 1]):
                continue
            lvl = float(cs.level[i])
            out.append(
                {
                    "kind": kind_of(direction).replace("breakout", "touch").replace("breakdown", "touch"),
                    "direction": direction,
                    "index": i,
                    "time": iso(times[i]),
                    "t": epoch_s(times[i]),
                    "age_bars": n - 1 - i,
                    "level": _r(lvl),
                    "extreme": _r(h[i] if direction == "bullish" else l[i]),
                    "close": _r(c[i]),
                    "close_status": "closed_beyond" if bool(cs.beyond[i]) else "closed_inside",
                    "fresh_crossing": bool(cs.fresh[i]),
                    "status": "provisional_touch",
                }
            )
    out.sort(key=lambda e: (e["index"], e["direction"]))
    return out


def week52_state(frame: pd.DataFrame, atr: np.ndarray, cfg: SignalConfig, walks: dict[str, CrossingSeries] | None = None) -> dict[str, Any]:
    """52-week reference of the latest completed candle (current candle excluded).
    Fewer than ``w52_lookback_sessions`` previous sessions -> INSUFFICIENT_HISTORY
    (the available-period high / low are reported separately, never as 52-week levels)."""
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    n = len(c)
    N = cfg.w52_lookback_sessions
    times = frame.index
    i = n - 1
    base: dict[str, Any] = {
        "lookback_sessions": N,
        "time": iso(times[i]) if n else None,
        "t": epoch_s(times[i]) if n else None,
        "close": _r(c[i]) if n else None,
        "available_sessions": max(0, n - 1),
        "price_basis": "raw_split_adjusted",
        "adjustment_basis": "provider split/bonus-adjusted OHLC (corporate actions: splits and bonus issues); actual unadjusted traded prices are not available from this provider",
    }
    if n < 2:
        return {**base, "status": "insufficient_history"}
    if n - 1 < N:
        prev_h, prev_l = h[: n - 1], l[: n - 1]
        return {
            **base,
            "status": "insufficient_history",
            "available_high": _r(prev_h.max()),
            "available_low": _r(prev_l.min()),
            "available_from": iso(times[0]),
            "note": f"only {n - 1} completed sessions before the latest candle; a 52-week level needs {N}",
        }
    win_h, win_l = h[i - N : i], l[i - N : i]
    hi, lo = float(win_h.max()), float(win_l.min())
    hi_i = i - N + int(np.flatnonzero(win_h == hi)[-1])
    lo_i = i - N + int(np.flatnonzero(win_l == lo)[-1])
    a = float(atr[i]) if np.isfinite(atr[i]) else None
    buf = cfg.w52_buffer_atr * a if a else None
    tbuf = cfg.w52_touch_buffer_atr * a if a else None
    rec = {
        **base,
        "status": "ok",
        "high_52w": _r(hi),
        "high_52w_time": iso(times[hi_i]),
        "low_52w": _r(lo),
        "low_52w_time": iso(times[lo_i]),
        "reference_start": iso(times[i - N]),
        "reference_end": iso(times[i - 1]),
        "distance_from_high_pct": _r((c[i] / hi - 1.0) * 100.0, 3),
        "distance_from_low_pct": _r((c[i] / lo - 1.0) * 100.0, 3),
        "atr": _r(a),
        "close_above_high": bool(buf is not None and c[i] > hi + buf),
        "close_below_low": bool(buf is not None and c[i] < lo - buf),
        "touch_high": bool(tbuf is not None and h[i] > hi + tbuf),
        "touch_low": bool(tbuf is not None and l[i] < lo - tbuf),
        "high_atr_distance": _r((c[i] - hi) / a, 3) if a else None,
        "low_atr_distance": _r((c[i] - lo) / a, 3) if a else None,
    }
    if walks:
        rec["fresh_cross_high"] = bool(walks["bullish"].fresh[i])
        rec["fresh_cross_low"] = bool(walks["bearish"].fresh[i])
    return rec
