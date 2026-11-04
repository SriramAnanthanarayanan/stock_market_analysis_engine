"""Support and resistance zones (spec section 9) - shared analytical features.

* Input: k=3 swing points over the last 250 completed bars; each pivot carries
  its own ATR(14).
* Pivots sorted by (price, bar, type); a pivot joins the current cluster when
  it is within ``0.5 x its ATR`` of the cluster mean (mean recomputed after
  each addition). Sorting makes the result independent of input order.
* Zone = cluster min..max, padded by ``0.1 x median cluster ATR``; needs >= 2
  touches at least 5 bars apart. Zones that overlap or are closer than
  ``0.25 x median ATR`` are merged (bounds and touches recomputed), unless the
  merged zone would exceed ``sr_max_zone_width_atr x median ATR`` (P2 decision:
  stops sequential merges chaining a congested area into one wide band).
* Lifecycle, walked bar by bar after the zone is established (2nd touch):
  support breaks on two consecutive closes below ``low - 0.1 ATR``; resistance
  on two above ``high + 0.1 ATR``; a break flips the role. A retest needs a
  later candle whose range intersects the zone *and* that closes on the
  reclaimed side of the midpoint without closing beyond the opposite boundary
  by the buffer - an intrabar touch alone is not a retest.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from statistics import median
from typing import Any

import numpy as np

from ..patterns.base import SeriesContext
from ..timeutil import epoch_s, iso


@dataclass
class Pivot:
    price: float
    index: int
    kind: str  # "H" | "L"
    atr: float


@dataclass
class SRZone:
    zone_id: str
    low: float
    high: float
    members: list[Pivot]
    touches: list[int]
    established_index: int
    role: str = "support"
    status: str = "intact"  # intact | broken | retested
    broken_index: int | None = None
    retest_index: int | None = None
    flips: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    median_atr: float = 0.0
    side: str = "below"
    distance_pct: float = 0.0
    distance_atr: float = 0.0

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)

    @property
    def width(self) -> float:
        return self.high - self.low


def _spaced(indices: list[int], gap: int) -> list[int]:
    out: list[int] = []
    for i in sorted(set(indices)):
        if not out or i - out[-1] >= gap:
            out.append(i)
    return out


def cluster_pivots(pivots: list[Pivot], frac: float) -> list[list[Pivot]]:
    """Deterministic 1-D clustering (sorted by price, then bar, then type)."""
    ordered = sorted(pivots, key=lambda p: (p.price, p.index, p.kind))
    clusters: list[list[Pivot]] = []
    cur: list[Pivot] = []
    total = 0.0
    for p in ordered:
        if cur and abs(p.price - total / len(cur)) <= frac * p.atr:
            cur.append(p)
            total += p.price
            continue
        if cur:
            clusters.append(cur)
        cur, total = [p], p.price
    if cur:
        clusters.append(cur)
    return clusters


def _zone_from(members: list[Pivot], cfg) -> SRZone | None:
    med = median(p.atr for p in members)
    touches = _spaced([p.index for p in members], cfg.sr_touch_min_gap_bars)
    if len(touches) < cfg.sr_min_touches:
        return None
    pad = cfg.sr_pad_atr_frac * med
    lo = min(p.price for p in members) - pad
    hi = max(p.price for p in members) + pad
    return SRZone(zone_id="", low=lo, high=hi, members=members, touches=touches, established_index=touches[cfg.sr_min_touches - 1], median_atr=med)


def build_zones(pivots: list[Pivot], cfg) -> list[SRZone]:
    zones = [z for z in (_zone_from(c, cfg) for c in cluster_pivots(pivots, cfg.sr_cluster_atr_frac)) if z is not None]
    zones.sort(key=lambda z: (z.low, z.high))
    merged: list[SRZone] = []
    for z in zones:
        if merged:
            prev = merged[-1]
            gap = z.low - prev.high
            if gap < cfg.sr_merge_atr_frac * max(prev.median_atr, z.median_atr):
                combined = _zone_from(prev.members + z.members, cfg)
                if combined is not None and combined.width <= cfg.sr_max_zone_width_atr * combined.median_atr:
                    merged[-1] = combined
                    continue
        merged.append(z)
    return merged


def _walk(z: SRZone, ctx: SeriesContext) -> None:
    cfg = ctx.cfg
    close, high, low, atr = ctx.close, ctx.high, ctx.low, ctx.atr
    est = z.established_index
    z.role = "support" if close[est] >= z.mid else "resistance"
    run = 0
    for j in range(est + 1, ctx.n):
        buf = cfg.break_buffer_atr_frac * float(atr[j])
        c = float(close[j])
        beyond = c < z.low - buf if z.role == "support" else c > z.high + buf
        run = run + 1 if beyond else 0
        if run >= cfg.breakout_confirm_closes:
            old = z.role
            z.role = "resistance" if old == "support" else "support"
            z.status, z.broken_index, z.retest_index = "broken", j, None
            z.flips += 1
            z.events.append({"index": j, "event": f"{old}_broken", "role": z.role})
            run = 0
            continue
        if z.status == "broken" and z.retest_index is None and j > z.broken_index:
            touches_zone = low[j] <= z.high and high[j] >= z.low
            if z.role == "support":  # former resistance, reclaimed from above
                ok = c > z.mid and c >= z.low - buf
            else:  # former support, reclaimed from below
                ok = c < z.mid and c <= z.high + buf
            if touches_zone and ok:
                z.status, z.retest_index = "retested", j
                z.events.append({"index": j, "event": "retest", "role": z.role})


def compute_zones(ctx: SeriesContext) -> list[SRZone]:
    """All zones with lifecycle and distance from the latest close."""
    cfg = ctx.cfg
    if ctx.n == 0:
        return []
    start = max(0, ctx.n - cfg.sr_lookback_bars)
    sw = ctx.swings(cfg.sr_swing_k)
    pivots = [Pivot(float(ctx.high[i]), int(i), "H", float(ctx.atr[i])) for i in sw.highs if i >= start and np.isfinite(ctx.atr[i])]
    pivots += [Pivot(float(ctx.low[i]), int(i), "L", float(ctx.atr[i])) for i in sw.lows if i >= start and np.isfinite(ctx.atr[i])]
    zones = build_zones(pivots, cfg)
    last_close = float(ctx.close[ctx.last])
    last_atr = float(ctx.atr[ctx.last]) if np.isfinite(ctx.atr[ctx.last]) else float("nan")
    for z in zones:
        _walk(z, ctx)
        if z.low <= last_close <= z.high:
            dist = 0.0
            z.side = "above" if z.mid >= last_close else "below"
        elif z.low > last_close:
            dist, z.side = z.low - last_close, "above"
        else:
            dist, z.side = last_close - z.high, "below"
        z.distance_pct = 100 * dist / last_close
        z.distance_atr = dist / last_atr if last_atr and np.isfinite(last_atr) else float("nan")
        raw = f"{ctx.symbol}|{ctx.timeframe}|{round(z.low, 4)}|{round(z.high, 4)}|{z.touches[0]}"
        z.zone_id = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return zones


def nearest(zones: list[SRZone], per_side: int) -> list[SRZone]:
    above = sorted((z for z in zones if z.side == "above"), key=lambda z: z.distance_pct)[:per_side]
    below = sorted((z for z in zones if z.side == "below"), key=lambda z: z.distance_pct)[:per_side]
    return sorted(above + below, key=lambda z: -z.mid)


def zone_record(z: SRZone, ctx: SeriesContext) -> dict[str, Any]:
    t = lambda i: None if i is None else iso(ctx.times[i])  # noqa: E731
    return {
        "zone_id": z.zone_id,
        "p_low": round(z.low, 4),
        "p_high": round(z.high, 4),
        "width": round(z.width, 4),
        "role": z.role,
        "status": z.status,
        "side": z.side,
        "touches": len(z.touches),
        "touch_times": [iso(ctx.times[i]) for i in z.touches],
        "first_touch_time": t(z.touches[0]),
        "last_touch_time": t(z.touches[-1]),
        "broken_time": t(z.broken_index),
        "retest_time": t(z.retest_index),
        "flips": z.flips,
        "distance_pct": round(z.distance_pct, 4),
        "distance_atr": None if not np.isfinite(z.distance_atr) else round(z.distance_atr, 4),
        "median_atr": round(z.median_atr, 4),
        "t_start": epoch_s(ctx.times[z.touches[0]]),
        "t_end": epoch_s(ctx.times[ctx.last]),
        "events": [{**e, "time": iso(ctx.times[e["index"]])} for e in z.events],
    }
