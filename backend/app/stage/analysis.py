"""Stage Analysis - translation of the user's ``stage_analysis.txt`` (weekly only).

Section references (s.N) are to that file; every rule below is evaluated for each
completed week i using weeks 0..i only, so the classification history (and the
stage transition date) never uses later candles.

s.2  Base (previous base, the current candle excluded - s.10 rule 6):
       BaseHigh = max(high[i-52 .. i-1]), BaseLow = min(low[i-52 .. i-1])
       BaseRange% = (BaseHigh - BaseLow) / BaseLow x 100 <= 30
       proximity  = (BaseHigh - close) / BaseHigh x 100, "within 5 %" of the base high
       prior decline (ASSUMPTION, "after a meaningful prior decline"):
       (max high of the weeks before the base window - BaseLow) / that high x 100 >= 20
s.3  ema30 = close.ewm(span=30, adjust=False).mean(); within +/-5 % = testing,
       below / above otherwise; direction = ema30 vs ema30 four weeks ago
       (flat tolerance: ASSUMPTION)
s.4  RS (rs_line.py, weekly length 52), 5-week RS trend angle, RS SMA 50 rising /
       falling over 3 bars, price confirmation (SMA 50 rising/falling + close side)
s.5  volume contraction: mean(vol last 26 w) < mean(vol the 26 w before);
       volume expansion: mean(vol last 4 w) >= 1.5 x mean(vol last 26 w)
s.6  Stage 2 - Confirmed: weekly close > previous base resistance, supported by
       volume (expansion) and positive RS (RS > 0), on a valid base
     Early Stage 2 - Candidate: close above a rising 30W EMA, RS positive and
       improving, approaching base resistance (within 5 % below it), valid base
     Stage 1 - Watchlist: consolidation base (valid base), price testing the 30W
       EMA; improving RS preferred, not required ("do not reject Stage 1
       candidates simply because RS is still negative")
     Other / Unqualified: none of the above.
     Evaluation order: Stage 2 Confirmed, Early Stage 2, Stage 1 (first match).
s.8  Ranking: stage, higher RS, stronger 5-week RS improvement, rising RS SMA,
       rising 30W EMA, lower base range, stronger volume expansion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import StageConfig
from ..indicators.core import swing_points
from ..indicators.technical import ema_pandas
from ..timeutil import iso
from .rs_line import RSLine, align_benchmark, compute_rs_line, latest_state

STAGE_LABELS = {
    "stage2_confirmed": "Stage 2 - Confirmed",
    "early_stage2": "Early Stage 2 - Candidate",
    "stage1": "Stage 1 - Watchlist",
    "other": "Other / Unqualified",
    "insufficient_history": "Insufficient history",
    "data_unavailable": "Data unavailable",
}


def _f(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _roll(a: np.ndarray, n: int, fn: str, shift: int = 0) -> np.ndarray:
    s = pd.Series(a)
    r = getattr(s.rolling(n, min_periods=n), fn)()
    return r.shift(shift).to_numpy()


@dataclass
class StageSeries:
    """Every rule input and output for each completed week (index aligned with the weekly frame)."""

    times: pd.DatetimeIndex
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    ema30: np.ndarray
    ema_dist_pct: np.ndarray
    ema_change_pct: np.ndarray
    base_high: np.ndarray
    base_low: np.ndarray
    base_range_pct: np.ndarray
    proximity_pct: np.ndarray
    prior_high: np.ndarray
    prior_decline_pct: np.ndarray
    vol_recent26: np.ndarray
    vol_prev26: np.ndarray
    vol_last4: np.ndarray
    vol_base: np.ndarray  # average volume of the 52 sideways weeks before the last 4 weeks
    vol_base_trend: np.ndarray  # avg of the later 26 / earlier 26 of those weeks (< 1 = volume drying up)
    rs: RSLine
    stage: np.ndarray  # object array of stage keys
    conditions: dict[str, np.ndarray] = field(default_factory=dict)


def compute_stage_series(weekly: pd.DataFrame, bench_weekly: pd.Series, cfg: StageConfig) -> StageSeries:
    c, h, l = (weekly[k].to_numpy(dtype=float) for k in ("close", "high", "low"))
    v = weekly["volume"].to_numpy(dtype=float)
    n = len(c)
    L = cfg.base_lookback_weeks
    # s.3: literal pandas form from the source
    ema30 = ema_pandas(c, cfg.ema_period)
    with np.errstate(divide="ignore", invalid="ignore"):
        ema_dist = (c / ema30 - 1.0) * 100.0
        ema_prev = pd.Series(ema30).shift(cfg.ema_direction_lookback).to_numpy()
        ema_change = (ema30 / ema_prev - 1.0) * 100.0
    # s.2: previous base (current candle excluded)
    base_high = _roll(h, L, "max", 1)
    base_low = _roll(l, L, "min", 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        base_range = (base_high - base_low) / base_low * 100.0
        proximity = (base_high - c) / base_high * 100.0
    # ASSUMPTION: prior high = max high of the weeks before the base window (available weeks, >= 4).
    P = cfg.prior_decline_lookback_weeks
    prior_high = pd.Series(h).rolling(P, min_periods=min(4, P)).max().shift(L + 1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        prior_decline = (prior_high - base_low) / prior_high * 100.0
    # s.5 volume (the current, completed week included in "recent")
    B, R = cfg.volume_base_weeks, cfg.volume_recent_weeks
    vr = _roll(v, B, "mean")
    vp = _roll(v, B, "mean", B)
    v4 = _roll(v, R, "mean")
    # User entry setup: the sideways base = the 52 weeks before the last 4 weeks; recent = last 4 weeks.
    vs = pd.Series(v)
    vb = vs.rolling(L, min_periods=int(L * 0.8)).mean().shift(R).to_numpy()
    half = L // 2
    with np.errstate(divide="ignore", invalid="ignore"):
        vbt = (vs.rolling(half, min_periods=int(half * 0.8)).mean().shift(R) / vs.rolling(half, min_periods=int(half * 0.8)).mean().shift(R + half)).to_numpy()
    # s.4 RS (weekly configuration, Nifty 50)
    al = align_benchmark(weekly.index, bench_weekly, cfg.benchmark_max_ffill_bars)
    rs = compute_rs_line(c, al.values, cfg.rs_length_weekly, cfg, al)

    with np.errstate(invalid="ignore"):
        tol = cfg.ema_flat_tolerance_pct
        ema_rising = ema_change > tol
        ema_falling = ema_change < -tol
        testing = np.abs(ema_dist) <= cfg.ema_testing_band_pct
        above = c > ema30
        range_ok = base_range <= cfg.max_base_range_pct
        decline_ok = prior_decline >= cfg.prior_decline_min_pct
        base_valid = range_ok & decline_ok
        breakout = c > base_high
        approaching = (proximity >= 0) & (proximity <= cfg.base_high_proximity_pct)
        rs_pos = rs.res > 0
        rs_improving = rs.angle > 0
        vol_expansion = v4 >= cfg.volume_expansion_ratio * vr
        vol_contraction = vr < vp
        volume_pickup = (v4 / vb) >= cfg.setup_volume_pickup_ratio
        near_or_breakout = proximity <= cfg.base_high_proximity_pct  # within 5 % below the base high, or above it
    min_hist = max(cfg.rs_length_weekly + cfg.rs_ma_length + cfg.rs_ma_rising_bars, L + 1)
    stage = np.full(n, "other", dtype=object)
    s2 = base_valid & breakout & vol_expansion & rs_pos
    e2 = base_valid & above & ema_rising & rs_pos & rs_improving & approaching & ~s2
    s1 = base_valid & testing & ~s2 & ~e2
    stage[s1] = "stage1"
    stage[e2] = "early_stage2"
    stage[s2] = "stage2_confirmed"
    insufficient = (np.arange(n) < min_hist - 1) | ~np.isfinite(base_high) | ~np.isfinite(rs.res)
    stage[insufficient] = "insufficient_history"
    setup = base_valid & above & rs_pos & near_or_breakout & volume_pickup & ~insufficient
    return StageSeries(
        times=weekly.index, close=c, high=h, low=l, volume=v, ema30=ema30, ema_dist_pct=ema_dist, ema_change_pct=ema_change,
        base_high=base_high, base_low=base_low, base_range_pct=base_range, proximity_pct=proximity, prior_high=prior_high,
        prior_decline_pct=prior_decline, vol_recent26=vr, vol_prev26=vp, vol_last4=v4, vol_base=vb, vol_base_trend=vbt, rs=rs, stage=stage,
        conditions={
            "ema_rising": ema_rising, "ema_falling": ema_falling, "testing": testing, "above": above, "range_ok": range_ok,
            "decline_ok": decline_ok, "base_valid": base_valid, "breakout": breakout, "approaching": approaching, "rs_pos": rs_pos,
            "rs_improving": rs_improving, "vol_expansion": vol_expansion, "vol_contraction": vol_contraction,
            "volume_pickup": volume_pickup, "near_or_breakout": near_or_breakout, "setup": setup,
        },
    )


def _higher_lows(ss: StageSeries, i: int, cfg: StageConfig) -> bool | None:
    """ASSUMPTION: the last two confirmed weekly swing lows inside the base window rise."""
    lo = max(0, i - cfg.base_lookback_weeks)
    sp = swing_points(ss.high[: i + 1], ss.low[: i + 1], cfg.higher_lows_swing_k)
    lows = [j for j in sp.lows if lo <= j <= i]
    if len(lows) < 2:
        return None
    return bool(ss.low[lows[-1]] > ss.low[lows[-2]])


def base_length(high: np.ndarray, low: np.ndarray, i: int, band_pct: float, cap: int = 520) -> int | None:
    """Weeks before week i (current week excluded) during which price stayed inside one band
    of at most ``band_pct`` (the base-range rule); the true base length, not capped at 52."""
    if i < 1:
        return None
    hi, lo, d = high[i - 1], low[i - 1], 0
    for j in range(i - 1, max(-1, i - 1 - cap), -1):
        hi, lo = max(hi, high[j]), min(lo, low[j])
        if lo <= 0 or (hi - lo) / lo * 100.0 > band_pct:
            break
        d += 1
    return d


def _gate(stage: str, name: str, value: Any, rule: str, passed: Any, source: str = "source") -> dict[str, Any]:
    p = None if passed is None or (isinstance(passed, float) and not math.isfinite(passed)) else bool(passed)
    return {"stage": stage, "name": name, "value": value, "rule": rule, "passed": p, "basis": source}


def evaluate(weekly: pd.DataFrame, bench_weekly: pd.Series, cfg: StageConfig) -> dict[str, Any]:
    """Stage classification of the latest completed week with every metric, gate and reason."""
    if weekly.empty:
        return {"stage": "data_unavailable", "stage_label": STAGE_LABELS["data_unavailable"], "reasons": ["no completed weekly candles"]}
    ss = compute_stage_series(weekly, bench_weekly, cfg)
    i = len(ss.close) - 1
    cond = {k: bool(v[i]) if isinstance(v[i], (bool, np.bool_)) else v[i] for k, v in ss.conditions.items()}
    st = latest_state(ss.rs, i)
    stage = str(ss.stage[i])
    vol_ok = bool(np.isfinite(ss.vol_last4[i]) and np.isfinite(ss.vol_recent26[i]))
    with np.errstate(invalid="ignore", divide="ignore"):
        contraction_ratio = ss.vol_recent26[i] / ss.vol_prev26[i]
        expansion_ratio = ss.vol_last4[i] / ss.vol_recent26[i]
    hl = _higher_lows(ss, i, cfg) if i > 0 else None
    duration = base_length(ss.high, ss.low, i, cfg.max_base_range_pct)
    ema_dir = "rising" if cond["ema_rising"] else "falling" if cond["ema_falling"] else ("flat" if np.isfinite(ss.ema_change_pct[i]) else None)
    ema_pos = None
    if np.isfinite(ss.ema30[i]):
        ema_pos = "testing" if cond["testing"] else ("above" if cond["above"] else "below")
    # stage transition: first week of the current uninterrupted classification
    since = i
    while since > 0 and ss.stage[since - 1] == stage:
        since -= 1
    prev_stage = str(ss.stage[since - 1]) if since > 0 else None
    rsv = st["rs"]
    T = cfg
    gates = [
        _gate("base", "base_range", _f(ss.base_range_pct[i], 2), f"(BaseHigh - BaseLow) / BaseLow x 100 <= {T.max_base_range_pct} (previous {T.base_lookback_weeks} weeks)", cond["range_ok"]),
        _gate("base", "prior_decline", _f(ss.prior_decline_pct[i], 2), f"decline from the prior high to the base low >= {T.prior_decline_min_pct} %", cond["decline_ok"] if np.isfinite(ss.prior_decline_pct[i]) else None, "assumption"),
        _gate("stage2_confirmed", "breakout", _f(ss.close[i], 2), f"weekly close > previous base resistance {_f(ss.base_high[i], 2)} (current candle excluded)", cond["breakout"]),
        _gate("stage2_confirmed", "volume_support", _f(expansion_ratio, 3), f"last {T.volume_recent_weeks}-week avg volume >= {T.volume_expansion_ratio} x {T.volume_base_weeks}-week avg", cond["vol_expansion"] if vol_ok else None),
        _gate("stage2_confirmed", "rs_positive", rsv, "RS > 0", cond["rs_pos"] if rsv is not None else None),
        _gate("early_stage2", "price_above_ema", _f(ss.ema_dist_pct[i], 2), f"close > 30W EMA {_f(ss.ema30[i], 2)}", cond["above"]),
        _gate("early_stage2", "ema_rising", _f(ss.ema_change_pct[i], 3), f"30W EMA vs {T.ema_direction_lookback} weeks ago > +{T.ema_flat_tolerance_pct} % (flat tolerance is an assumption)", cond["ema_rising"]),
        _gate("early_stage2", "rs_positive", rsv, "RS > 0", cond["rs_pos"] if rsv is not None else None),
        _gate("early_stage2", "rs_improving", st["rs_trend_angle"], f"atan((RS - RS[{T.rs_trend_base}]) / {T.rs_trend_base}) > 0", cond["rs_improving"] if st["rs_trend_angle"] is not None else None),
        _gate("early_stage2", "approaching_resistance", _f(ss.proximity_pct[i], 2), f"0 <= (BaseHigh - close) / BaseHigh x 100 <= {T.base_high_proximity_pct}", cond["approaching"]),
        _gate("stage1", "testing_ema", _f(ss.ema_dist_pct[i], 2), f"|close / 30W EMA - 1| x 100 <= {T.ema_testing_band_pct}", cond["testing"]),
        # Your entry setup (confluence): every check must pass.
        _gate("setup", "major_decline", _f(ss.prior_decline_pct[i], 2), f"decline into the base >= {T.prior_decline_min_pct} %", cond["decline_ok"] if np.isfinite(ss.prior_decline_pct[i]) else None, "user"),
        _gate("setup", "long_base", duration, f"base held inside a {T.max_base_range_pct} % band for >= {T.base_lookback_weeks} weeks (longer preferred)", cond["range_ok"], "user"),
        _gate("setup", "price_above_ema", _f(ss.ema_dist_pct[i], 2), f"close > 30W EMA {_f(ss.ema30[i], 2)}", cond["above"], "user"),
        _gate("setup", "rs_positive", rsv, "RS > 0 (green)", cond["rs_pos"] if rsv is not None else None, "user"),
        _gate("setup", "near_or_breaking_out", _f(ss.proximity_pct[i], 2), f"(BaseHigh - close) / BaseHigh x 100 <= {T.base_high_proximity_pct} (negative = above the base high)", cond["near_or_breakout"], "user"),
        _gate("setup", "volume_high_vs_base", _f(ss.vol_last4[i] / ss.vol_base[i] if np.isfinite(ss.vol_base[i]) and ss.vol_base[i] > 0 else np.nan, 3), f"last {T.volume_recent_weeks}-week avg volume >= {T.setup_volume_pickup_ratio} x avg of the {T.base_lookback_weeks} sideways weeks before them", cond["volume_pickup"] if np.isfinite(ss.vol_base[i]) and np.isfinite(ss.vol_last4[i]) else None, "user"),
    ]
    setup_met = stage != "insufficient_history" and all(g["passed"] is True for g in gates if g["stage"] == "setup")
    reasons = _explain(stage, gates, cond, st, cfg)
    setup_gates = [g for g in gates if g["stage"] == "setup"]
    if stage != "insufficient_history":
        if setup_met:
            reasons.insert(0, f"ENTRY SETUP: all {len(setup_gates)} checks pass (decline, {duration}-week base, above 30W EMA, RS > 0, at/near the base high, recent volume high vs the sideways base).")
        else:
            reasons.insert(0, "Entry setup not met: " + ", ".join(g["name"].replace("_", " ") for g in setup_gates if g["passed"] is not True) + ".")
    metrics = {
        "close": _f(ss.close[i], 2),
        "weekly_high": _f(ss.high[i], 2),
        "weekly_low": _f(ss.low[i], 2),
        "week": iso(ss.times[i]),
        "base_high": _f(ss.base_high[i], 2),
        "base_low": _f(ss.base_low[i], 2),
        "base_resistance": _f(ss.base_high[i], 2),
        "base_support": _f(ss.base_low[i], 2),
        "base_range_pct": _f(ss.base_range_pct[i], 2),
        "base_duration_weeks": duration,
        "setup_met": setup_met,
        "setup_failed": [g["name"] for g in gates if g["stage"] == "setup" and g["passed"] is not True],
        "setup_checks": sum(1 for g in gates if g["stage"] == "setup"),
        "volume_pickup_ratio": _f(ss.vol_last4[i] / ss.vol_base[i], 3) if np.isfinite(ss.vol_base[i]) and ss.vol_base[i] > 0 else None,
        "base_volume_trend": _f(ss.vol_base_trend[i], 3),
        "base_volume_drying_up": bool(ss.vol_base_trend[i] < 1.0) if np.isfinite(ss.vol_base_trend[i]) else None,
        "distance_to_breakout_pct": _f(ss.proximity_pct[i], 2),
        "higher_lows": hl,
        "prior_high": _f(ss.prior_high[i], 2),
        "prior_decline_pct": _f(ss.prior_decline_pct[i], 2),
        "ema30": _f(ss.ema30[i], 2),
        "ema30_distance_pct": _f(ss.ema_dist_pct[i], 2),
        "ema30_position": ema_pos,
        "ema30_change_4w_pct": _f(ss.ema_change_pct[i], 3),
        "ema30_direction": ema_dir,
        **st,
        "rs_length": cfg.rs_length_weekly,
        "volume_recent26": _f(ss.vol_recent26[i], 0),
        "volume_prev26": _f(ss.vol_prev26[i], 0),
        "volume_last4": _f(ss.vol_last4[i], 0),
        "volume_contraction_ratio": _f(contraction_ratio, 3),
        "volume_contraction": bool(cond["vol_contraction"]) if np.isfinite(contraction_ratio) else None,
        "volume_expansion_ratio": _f(expansion_ratio, 3),
        "volume_expansion": bool(cond["vol_expansion"]) if np.isfinite(expansion_ratio) else None,
        "benchmark_alignment": {"exact": ss.rs.alignment.exact, "carried": ss.rs.alignment.carried, "missing": ss.rs.alignment.missing},
        "weeks": len(ss.close),
    }
    return {
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "stage_since": iso(ss.times[since]),
        "previous_stage": prev_stage,
        "metrics": metrics,
        "gates": gates,
        "reasons": reasons,
        "rank_key": rank_key(stage, metrics, cfg),
        "series": ss,
    }


def _explain(stage: str, gates: list[dict[str, Any]], cond: dict[str, Any], st: dict[str, Any], cfg: StageConfig) -> list[str]:
    def failed(group: str) -> list[str]:
        return [f"{g['name']} ({'not measurable' if g['passed'] is None else 'failed'}: {g['value']}; rule {g['rule']}{'; ASSUMPTION' if g['basis'] == 'assumption' else ''})" for g in gates if g["stage"] in (group, "base") and g["passed"] is not True]

    if stage == "insufficient_history":
        return [f"needs >= {max(cfg.rs_length_weekly + cfg.rs_ma_length + cfg.rs_ma_rising_bars, cfg.base_lookback_weeks + 1)} completed weeks with benchmark data (RS {cfg.rs_length_weekly} + RS SMA {cfg.rs_ma_length})"]
    out = []
    if stage == "stage2_confirmed":
        out.append("Weekly close above the previous base resistance with volume expansion and positive RS, on a valid base.")
    elif stage == "early_stage2":
        out.append("Above a rising 30W EMA, RS positive and improving, approaching base resistance, valid base.")
    elif stage == "stage1":
        out.append("Valid consolidation base and price testing the 30W EMA." + (" RS improving (preferred)." if cond.get("rs_improving") else " RS not improving yet (preferred, not required)."))
    else:
        out.append("Does not meet Stage 1, Early Stage 2 or Stage 2 conditions.")
    for grp, label in (("stage2_confirmed", "Stage 2"), ("early_stage2", "Early Stage 2"), ("stage1", "Stage 1")):
        if grp == stage:
            continue
        f = failed(grp)
        if f:
            out.append(f"Not {label}: " + "; ".join(f))
    return out


def rank_key(stage: str, m: dict[str, Any], cfg: StageConfig) -> list[float]:
    """Entry setups first (longest base first - user setup), then the s.8 ranking."""
    order = {s: i for i, s in enumerate(cfg.stage_rank_order)}

    def neg(x: float | None) -> float:
        return -x if x is not None else math.inf

    setup = bool(m.get("setup_met"))
    base_len = float(m.get("base_duration_weeks") or 0)
    return [
        0.0 if setup else 1.0,
        -base_len if setup and cfg.setup_rank_by_base_length else 0.0,
        float(order.get(stage, len(order))),
        neg(m.get("rs")),
        neg(m.get("rs_trend_angle")),
        0.0 if m.get("rs_sma_direction") == "rising" else 1.0,
        0.0 if m.get("ema30_direction") == "rising" else 1.0,
        m.get("base_range_pct") if m.get("base_range_pct") is not None else math.inf,
        neg(m.get("volume_expansion_ratio")),
    ]
