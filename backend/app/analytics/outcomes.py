"""Post-breakout outcome measurement (P3).

Outcomes use candles *after* a tracked breakout and are kept entirely separate
from detection: nothing here feeds back into a detector, a fit, a status or a
quality score. Small samples are flagged; these are descriptive statistics,
not predictive success rates.
"""

from __future__ import annotations

from statistics import mean, median
from typing import Any

import numpy as np

from ..patterns.base import SeriesContext
from ..timeutil import iso


def compute_outcome(ctx: SeriesContext, breakout_index: int, direction: str, horizons: tuple[int, ...]) -> dict[str, Any]:
    """Return (signed in the breakout direction), MFE and MAE per horizon, in % of
    the breakout close. Horizons without enough later bars are ``pending``."""
    up = direction == "up"
    # Returns use the dividend-adjusted series when the provider supplies it
    # (factor adj_close / close per bar, also applied to highs/lows); breakout
    # levels themselves stay on raw prices.
    adj = ctx.adj_close
    factor = np.ones(len(ctx.close))
    basis = "raw_close (adjusted close unavailable)"
    if adj is not None and np.isfinite(adj[breakout_index:]).all():
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(np.isfinite(adj), adj / ctx.close, 1.0)
        basis = "dividend_adjusted_close"
    c0 = float(ctx.close[breakout_index])
    c0a = c0 * float(factor[breakout_index])
    out: dict[str, Any] = {
        "breakout_index": int(breakout_index),
        "breakout_time": iso(ctx.times[breakout_index]),
        "breakout_close": c0,
        "direction": direction,
        "measured_from": "close of the bar that confirmed the break",
        "return_basis": basis,
        "horizons": {},
    }
    for h in horizons:
        end = breakout_index + h
        if end > ctx.last:
            out["horizons"][str(h)] = {"status": "pending", "bars_available": int(ctx.last - breakout_index)}
            continue
        sl = slice(breakout_index + 1, end + 1)
        seg_hi = float(np.max(ctx.high[sl] * factor[sl]))
        seg_lo = float(np.min(ctx.low[sl] * factor[sl]))
        ret = (float(ctx.close[end] * factor[end]) / c0a - 1.0) * 100.0
        if up:
            mfe, mae = (seg_hi / c0a - 1.0) * 100.0, (seg_lo / c0a - 1.0) * 100.0
        else:
            ret, mfe, mae = -ret, (1.0 - seg_lo / c0a) * 100.0, (1.0 - seg_hi / c0a) * 100.0
        out["horizons"][str(h)] = {
            "status": "complete",
            "end_time": iso(ctx.times[end]),
            "return_pct": round(ret, 4),
            "mfe_pct": round(mfe, 4),
            "mae_pct": round(mae, 4),
        }
    return out


def aggregate(outcomes: list[dict[str, Any]], horizons: tuple[int, ...], min_sample: int) -> dict[str, Any]:
    """Descriptive summary of complete horizons (one entry per horizon)."""
    out: dict[str, Any] = {}
    for h in horizons:
        vals = [o["horizons"][str(h)] for o in outcomes if o and o.get("horizons", {}).get(str(h), {}).get("status") == "complete"]
        n = len(vals)
        if not n:
            out[str(h)] = {"n": 0, "insufficient_sample": True}
            continue
        rets = [v["return_pct"] for v in vals]
        out[str(h)] = {
            "n": n,
            "insufficient_sample": n < min_sample,
            "mean_return_pct": round(mean(rets), 4),
            "median_return_pct": round(median(rets), 4),
            "share_positive": round(sum(r > 0 for r in rets) / n, 4),
            "mean_mfe_pct": round(mean(v["mfe_pct"] for v in vals), 4),
            "mean_mae_pct": round(mean(v["mae_pct"] for v in vals), 4),
        }
    return out
