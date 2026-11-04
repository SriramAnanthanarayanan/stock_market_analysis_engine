"""Relative Rotation Graph - an internally implemented RRG-style calculation (E2).

This is **not** the proprietary JdK RS-Ratio / RS-Momentum; it is a transparent,
configurable approximation (docs/rrg.md):

    RS          = index_close / benchmark_close                (same completed weeks)
    RS_Ratio    = 100 x RS / EMA(RS, rs_period)
    RS_Momentum = 100 x RS_Ratio / EMA(RS_Ratio, momentum_period)

EMA = TradingView-style EMA seeded with an SMA (indicators.technical.ema).
Weekly closes only; both series are aligned on identical completed weeks
(inner join on the Monday-labelled week). Weeks missing for the index are never
forward-filled: more than ``rrg_max_gap_weeks`` consecutive missing weeks inside
the window -> status ``data_gap``. Quadrant boundaries at ``rrg_center`` (100):

    Leading   ratio > 100, momentum > 100      Weakening ratio > 100, momentum <= 100
    Lagging   ratio <= 100, momentum <= 100    Improving ratio <= 100, momentum > 100
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import IndexConfig
from .indicators.technical import ema
from .timeutil import epoch_s, iso

QUADRANTS = ("leading", "weakening", "lagging", "improving")


def quadrant(ratio: float, momentum: float, center: float = 100.0) -> str:
    if ratio > center:
        return "leading" if momentum > center else "weakening"
    return "improving" if momentum > center else "lagging"


def heading(dx: float, dy: float) -> tuple[float | None, str]:
    """Direction of the latest weekly move in the (ratio, momentum) plane."""
    if dx == 0 and dy == 0:
        return None, "unchanged"
    ang = math.degrees(math.atan2(dy, dx)) % 360
    names = ["right", "up-right", "up", "up-left", "left", "down-left", "down", "down-right"]
    return round(ang, 1), names[int(((ang + 22.5) % 360) // 45)]


@dataclass
class RRGPoint:
    week: str
    t: int
    ratio: float
    momentum: float


def max_consecutive_missing(bench_weeks: pd.DatetimeIndex, idx_weeks: pd.DatetimeIndex) -> int:
    have = set(idx_weeks)
    run = best = 0
    for w in bench_weeks:
        if w in have:
            run = 0
        else:
            run += 1
            best = max(best, run)
    return best


def rrg_series(index_close: pd.Series, bench_close: pd.Series, cfg: IndexConfig) -> tuple[str, pd.DataFrame, dict[str, Any]]:
    """RS, RS_Ratio, RS_Momentum on aligned completed weeks. Returns (status, frame, info)."""
    bench = bench_close.dropna().iloc[-cfg.rrg_lookback_weeks :]
    idx = index_close.dropna()
    idx = idx[idx.index >= bench.index[0]] if len(bench) else idx
    info: dict[str, Any] = {"weeks_benchmark": int(len(bench)), "weeks_index": int(len(idx))}
    if bench.empty or idx.empty:
        return "data_unavailable", pd.DataFrame(), info
    # Missing weeks are counted from the index's first week (a later launch is not a gap).
    window = bench[bench.index >= idx.index[0]]
    gap = max_consecutive_missing(window.index, idx.index)
    info["max_gap_weeks"] = int(gap)
    joined = pd.concat({"index": idx, "bench": bench}, axis=1, join="inner").dropna()
    info["weeks_aligned"] = int(len(joined))
    if gap > cfg.rrg_max_gap_weeks:
        return "data_gap", pd.DataFrame(), info
    rs = (joined["index"] / joined["bench"]).to_numpy(dtype=float)
    ratio = 100.0 * rs / ema(rs, cfg.rrg_rs_period)
    mom = 100.0 * ratio / ema(ratio, cfg.rrg_momentum_period)
    out = pd.DataFrame({"rs": rs, "ratio": ratio, "momentum": mom}, index=joined.index).dropna()
    info["weeks_valid"] = int(len(out))
    if len(out) < 2:
        return "insufficient_history", out, info
    return "ok", out, info


def rrg_entry(code: str, index_close: pd.Series, bench_close: pd.Series, cfg: IndexConfig, trail: int) -> dict[str, Any]:
    status, df, info = rrg_series(index_close, bench_close, cfg)
    entry: dict[str, Any] = {"code": code, "status": status, **info, "trail": [], "latest": None}
    if status != "ok":
        return entry
    tail = df.iloc[-(trail + 1) :]  # chronological, oldest first (+1 for the latest direction)
    pts = [RRGPoint(iso(ts), epoch_s(ts), round(float(r.ratio), 4), round(float(r.momentum), 4)) for ts, r in tail.iterrows()]
    entry["trail"] = [p.__dict__ for p in pts[-trail:]] if len(pts) > trail else [p.__dict__ for p in pts]
    last = pts[-1]
    prev = pts[-2] if len(pts) >= 2 else None
    ang, name = heading(last.ratio - prev.ratio, last.momentum - prev.momentum) if prev else (None, "unchanged")
    entry["latest"] = {
        "week": last.week,
        "t": last.t,
        "ratio": last.ratio,
        "momentum": last.momentum,
        "quadrant": quadrant(last.ratio, last.momentum, cfg.rrg_center),
        "heading_deg": ang,
        "direction": name,
        "previous_quadrant": quadrant(prev.ratio, prev.momentum, cfg.rrg_center) if prev else None,
    }
    if len(entry["trail"]) < trail:
        entry["status"] = "ok"
        entry["note"] = f"only {len(entry['trail'])} weeks of RRG history (trail {trail})"
    return entry
