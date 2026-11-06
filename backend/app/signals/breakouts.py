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


# ------------------------------------------------------------------ about to break out (10-day level)
SETUP_STATES = ("approaching", "very_near", "touched", "breakout", "confirmed_breakout", "rejected", "expired")
SETUP_LABELS = {
    "approaching": "Approaching",
    "very_near": "Very Near",
    "touched": "Touched",
    "breakout": "Breakout",
    "confirmed_breakout": "Confirmed Breakout",
    "rejected": "Rejected",
    "expired": "Expired",
}


def _within(dist_pct: float, dist_atr: float | None, pct: float, atr_k: float, rule: str) -> bool:
    if not np.isfinite(dist_pct) or dist_pct < 0:
        return False
    a_ok = dist_atr is not None and np.isfinite(dist_atr) and dist_atr <= atr_k
    p_ok = dist_pct <= pct
    return (p_ok and a_ok) if rule == "both" else (p_ok or a_ok)


def _approach_state(cs: CrossingSeries, c: np.ndarray, atr: np.ndarray, i: int, cfg: SignalConfig) -> tuple[str | None, float | None, float | None]:
    """Approach state of candle ``i`` against its own 10-day level (no event check)."""
    lvl = float(cs.level[i])
    if not np.isfinite(lvl) or lvl == 0:
        return None, None, None
    a = float(atr[i]) if np.isfinite(atr[i]) and atr[i] > 0 else None
    dist = cs.sign * (lvl - c[i])  # > 0: still below the high (above the low for breakdowns)
    dpct = dist / lvl * 100.0
    datr = dist / a if a else None
    if bool(cs.beyond[i]):
        return None, dpct, datr  # a close beyond the level is a breakout (event logic)
    if bool(cs.touch[i]) or dist < 0:
        # intraday push through the level (or a close above it inside the buffer)
        near = dist < 0 or _within(dpct, datr, cfg.very_near_pct, cfg.very_near_atr, cfg.about_to_breakout_rule)
        return ("touched" if near else "rejected"), dpct, datr
    if _within(dpct, datr, cfg.very_near_pct, cfg.very_near_atr, cfg.about_to_breakout_rule):
        return "very_near", dpct, datr
    if _within(dpct, datr, cfg.about_to_breakout_pct, cfg.about_to_breakout_atr, cfg.about_to_breakout_rule):
        return "approaching", dpct, datr
    return None, dpct, datr


def breakout_setups(frame: pd.DataFrame, atr: np.ndarray, ema20: np.ndarray | None, ema50: np.ndarray | None, walks: dict[str, CrossingSeries], cfg: SignalConfig) -> list[dict[str, Any]]:
    """State of the latest completed candle against its 10-day level, per direction:
    Approaching / Very Near / Touched (before a breakout), Breakout / Confirmed
    Breakout / Rejected (a recent fresh crossing), Expired (a setup that stopped
    approaching without breaking out). Uses only candles up to the latest one."""
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    times = frame.index
    n = len(c)
    out: list[dict[str, Any]] = []
    if n < cfg.fresh_lookback_days + 2:
        return out
    i = n - 1
    for direction, cs in walks.items():
        bull = direction == "bullish"
        lvl = float(cs.level[i])
        if not np.isfinite(lvl):
            continue
        state, dpct, datr = _approach_state(cs, c, atr, i, cfg)
        ep = cs.episodes[-1] if cs.episodes else None
        age = (i - ep.event) if ep is not None else None
        event_info = None
        if ep is not None and age is not None and age <= cfg.setup_expiry_sessions:
            still = cs.sign * (c[i] - ep.level) > 0
            c2, _ = confirm_two_closes(cs, c, ep.event)
            if age == 0:
                state = "breakout"
            elif not still and age <= cfg.setup_rejection_sessions:
                state = "rejected"
            elif still:
                state = "confirmed_breakout" if c2 == "confirmed" else "breakout"
            event_info = {"event_time": iso(times[ep.event]), "event_t": epoch_s(times[ep.event]), "event_age": int(age), "event_level": _r(ep.level), "two_close": c2}
        if state is None:
            # Expired: in a setup during the last N sessions, not any more, and no breakout since.
            since = None
            for j in range(i - 1, max(cs.first or 0, i - cfg.setup_expiry_sessions) - 1, -1):
                sj, _, _ = _approach_state(cs, c, atr, j, cfg)
                if sj in ("approaching", "very_near", "touched"):
                    since = j
                    break
            if since is not None and not (ep is not None and ep.event > since):
                state = "expired"
                event_info = {"last_setup_time": iso(times[since]), "sessions_since_setup": int(i - since)}
        if state is None:
            continue
        # consecutive sessions in an approach state up to the latest one
        run = 0
        for j in range(i, max(0, i - 60), -1):
            sj, _, _ = _approach_state(cs, c, atr, j, cfg)
            if sj in ("approaching", "very_near", "touched"):
                run += 1
            else:
                break
        a = float(atr[i]) if np.isfinite(atr[i]) else None
        lo = max(0, i - cfg.fresh_lookback_days)
        r_hi, r_lo = float(np.max(h[lo:i])), float(np.min(l[lo:i]))
        e20 = float(ema20[i]) if ema20 is not None and np.isfinite(ema20[i]) else None
        e50 = float(ema50[i]) if ema50 is not None and np.isfinite(ema50[i]) else None
        lb = _level_bar(frame, i, cfg.fresh_lookback_days, direction)
        out.append(
            {
                "kind": f"breakout_setup_{state}",
                "state": state,
                "state_label": SETUP_LABELS[state],
                "direction": direction,
                "level": _r(lvl),
                "level_kind": f"{cfg.fresh_lookback_days}D high" if bull else f"{cfg.fresh_lookback_days}D low",
                "level_time": iso(times[lb]),
                "reference_start": iso(times[i - cfg.fresh_lookback_days]),
                "reference_end": iso(times[i - 1]),
                "time": iso(times[i]),
                "t": epoch_s(times[i]),
                "close": _r(c[i]),
                "high": _r(h[i]),
                "low": _r(l[i]),
                "distance_pct": _r(dpct, 3),
                "distance_atr": _r(datr, 3),
                "atr": _r(a),
                "ema20": _r(e20),
                "ema50": _r(e50),
                "close_vs_ema20_pct": _r((c[i] / e20 - 1.0) * 100.0, 3) if e20 else None,
                "close_vs_ema50_pct": _r((c[i] / e50 - 1.0) * 100.0, 3) if e50 else None,
                "ema20_vs_ema50": None if (e20 is None or e50 is None) else ("above" if e20 > e50 else "below"),
                "volume": None if not np.isfinite(v[i]) else float(v[i]),
                "volume_ratio": _r(prior_volume_ratio(v, i, cfg.fresh_volume_lookback), 3),
                "range_10d_pct": _r((r_hi - r_lo) / r_hi * 100.0, 3) if r_hi > 0 else None,
                "sessions_in_setup": run,
                "touch": bool(cs.touch[i]),
                "thresholds": {"approach_pct": cfg.about_to_breakout_pct, "approach_atr": cfg.about_to_breakout_atr, "very_near_pct": cfg.very_near_pct, "very_near_atr": cfg.very_near_atr, "rule": cfg.about_to_breakout_rule},
                **(event_info or {}),
            }
        )
    return out


def setup_quality(m: dict[str, Any], rs: float | None = None, stage: str | None = None, cfg: SignalConfig | None = None) -> tuple[float, list[dict[str, Any]]]:
    """Explainable 0-100 quality of an about-to-break-out setup (ASSUMPTION weights;
    not a probability): proximity 30, trend 25, tightness 15, volume 10, RS 10, stage 10.
    RS / stage come from the latest weekly scan (missing parts are left out)."""
    cfg = cfg or SignalConfig()
    bull = m.get("direction") == "bullish"
    comps: list[dict[str, Any]] = []

    def add(name: str, w: float, score: float | None, raw: Any, formula: str) -> None:
        if score is not None:
            comps.append({"name": name, "weight": w, "score": round(float(min(1.0, max(0.0, score))), 4), "raw": raw, "formula": formula})

    st = m.get("state")
    d = m.get("distance_pct")
    prox = 1.0 if st in ("touched", "breakout", "confirmed_breakout") else (None if d is None else 1.0 - d / cfg.about_to_breakout_pct)
    add("proximity", 30, prox, d, f"1 at the level, 0 at {cfg.about_to_breakout_pct} % below it")
    e20, e50, close = m.get("ema20"), m.get("ema50"), m.get("close")
    if e20 is not None and e50 is not None and close is not None:
        parts = [close > e20, close > e50, e20 > e50] if bull else [close < e20, close < e50, e20 < e50]
        add("trend", 25, sum(parts) / 3.0, m.get("ema20_vs_ema50"), "share of: close beyond EMA20, close beyond EMA50, EMA20 beyond EMA50 (direction of the setup)")
    rng = m.get("range_10d_pct")
    add("tightness", 15, None if rng is None else (15.0 - rng) / 10.0, rng, "10-day range: 1 at <= 5 %, 0 at >= 15 % (ASSUMPTION)")
    vr = m.get("volume_ratio")
    add("volume", 10, None if vr is None else (vr - 0.5) / 0.7, vr, "volume / 20-day average: 0 at 0.5, 1 at >= 1.2 (ASSUMPTION)")
    if rs is not None:
        add("relative_strength", 10, 1.0 if (rs > 0) == bull else 0.0, rs, "RS Line (weekly, rs_line.txt) on the setup's side of zero")
    if stage is not None:
        sc = {"stage2_confirmed": 1.0, "early_stage2": 1.0, "stage1": 0.5}.get(stage, 0.0) if bull else (0.0 if stage in ("stage2_confirmed", "early_stage2") else 0.5)
        add("stage", 10, sc, stage, "your Stage Analysis of the latest weekly scan: Stage 2 -> 1, Stage 1 -> 0.5 (bullish)")
    tw = sum(x["weight"] for x in comps)
    q = 100.0 * sum(x["weight"] * x["score"] for x in comps) / tw if tw else 0.0
    return round(q, 2), comps
