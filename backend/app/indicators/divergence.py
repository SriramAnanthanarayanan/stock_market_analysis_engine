"""Regular RSI divergence on confirmed swing points (E1, spec 6.3).

Bullish: price makes a lower low (low[b] < low[a] - min_price_diff_atr x ATR[b])
while RSI read at those two price-pivot bars makes a higher low
(RSI[b] > RSI[a] + min_rsi_diff). Bearish mirrors it with swing highs.

``a`` and ``b`` are the latest two swing pivots of that side:

* **confirmed** - both pivots are confirmed (k later completed bars); the
  divergence is confirmed at bar b + k, never earlier;
* **developing** - b is a provisional pivot (fewer than k later bars);
* **invalidated** - after b, a completed close goes beyond b's extreme by
  ``div_invalidation_buffer_atr`` x ATR (below low[b] for bullish, above high[b]
  for bearish) while the pair is still the latest one;
* **superseded** - a newer pivot of the same side confirmed; kept as history.

Nothing uses bars after the evaluation bar: a pair's confirmation bar is b + k,
and invalidation is only checked on bars after b.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import TechnicalConfig
from .core import provisional_pivot, swing_points


@dataclass
class Pivot:
    i: int
    price: float
    rsi: float


@dataclass
class Divergence:
    direction: str  # bullish | bearish
    status: str  # developing | confirmed | invalidated | superseded
    p1: Pivot
    p2: Pivot
    confirmed_index: int | None  # b + k (None while developing)
    invalidated_index: int | None = None
    superseded_index: int | None = None
    price_diff_atr: float = 0.0
    rsi_diff: float = 0.0
    separation: int = 0
    gates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def event_index(self) -> int:
        """Bar the current status was reached (age reference)."""
        if self.status == "invalidated" and self.invalidated_index is not None:
            return self.invalidated_index
        if self.confirmed_index is not None:
            return self.confirmed_index
        return self.p2.i


def _check(direction: str, a: int, b: int, price: np.ndarray, r: np.ndarray, atr: np.ndarray, cfg: TechnicalConfig) -> tuple[bool, list[dict[str, Any]], float, float]:
    sep = b - a
    gates: list[dict[str, Any]] = []
    gates.append({"name": "pivot_separation", "value": int(sep), "rule": f"{cfg.div_min_pivot_separation} <= bars <= {cfg.div_max_pivot_separation}", "passed": bool(cfg.div_min_pivot_separation <= sep <= cfg.div_max_pivot_separation)})
    finite = bool(np.isfinite([price[a], price[b], r[a], r[b], atr[b]]).all() and atr[b] > 0)
    gates.append({"name": "values_available", "value": finite, "rule": "price, RSI and ATR defined at both pivots", "passed": finite})
    if not finite:
        return False, gates, float("nan"), float("nan")
    if direction == "bullish":
        price_diff = (price[a] - price[b]) / atr[b]  # lower low > 0
        rsi_diff = r[b] - r[a]  # higher low > 0
        gates.append({"name": "price_lower_low", "value": round(float(price_diff), 4), "rule": f"(low[P1] - low[P2]) / ATR >= {cfg.div_min_price_diff_atr}", "passed": bool(price_diff >= cfg.div_min_price_diff_atr)})
        gates.append({"name": "rsi_higher_low", "value": round(float(rsi_diff), 4), "rule": f"RSI[P2] - RSI[P1] >= {cfg.div_min_rsi_diff}", "passed": bool(rsi_diff >= cfg.div_min_rsi_diff)})
    else:
        price_diff = (price[b] - price[a]) / atr[b]  # higher high > 0
        rsi_diff = r[a] - r[b]  # lower high > 0
        gates.append({"name": "price_higher_high", "value": round(float(price_diff), 4), "rule": f"(high[P2] - high[P1]) / ATR >= {cfg.div_min_price_diff_atr}", "passed": bool(price_diff >= cfg.div_min_price_diff_atr)})
        gates.append({"name": "rsi_lower_high", "value": round(float(rsi_diff), 4), "rule": f"RSI[P1] - RSI[P2] >= {cfg.div_min_rsi_diff}", "passed": bool(rsi_diff >= cfg.div_min_rsi_diff)})
    return all(g["passed"] for g in gates), gates, float(price_diff), float(rsi_diff)


def _first_close_beyond(direction: str, level: float, close: np.ndarray, atr: np.ndarray, start: int, end: int, cfg: TechnicalConfig) -> int | None:
    for j in range(start, end + 1):
        if not (np.isfinite(close[j]) and np.isfinite(atr[j])):
            continue
        buf = cfg.div_invalidation_buffer_atr * atr[j]
        if (direction == "bullish" and close[j] < level - buf) or (direction == "bearish" and close[j] > level + buf):
            return j
    return None


def find_divergences(high: Any, low: Any, close: Any, rsi_values: Any, atr: Any, cfg: TechnicalConfig) -> list[Divergence]:
    """All regular divergences in the series (history + the current one), oldest first."""
    h, lo, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    r = np.asarray(rsi_values, dtype=float)
    a_ = np.asarray(atr, dtype=float)
    n = len(c)
    k = cfg.div_swing_k
    out: list[Divergence] = []
    if n < 2 * k + 2:
        return out
    sp = swing_points(h, lo, k)
    for direction, pivots, price in (("bullish", sp.lows, lo), ("bearish", sp.highs, h)):
        piv = [int(i) for i in pivots]
        for idx in range(1, len(piv)):
            a, b = piv[idx - 1], piv[idx]
            ok, gates, pdiff, rdiff = _check(direction, a, b, price, r, a_, cfg)
            if not ok:
                continue
            confirmed = b + k
            nxt = piv[idx + 1] + k if idx + 1 < len(piv) else None  # when a newer pivot confirmed
            active_end = (nxt - 1) if nxt is not None else n - 1
            inv = _first_close_beyond(direction, price[b], c, a_, b + 1, active_end, cfg)
            status = "invalidated" if inv is not None else ("superseded" if nxt is not None else "confirmed")
            out.append(
                Divergence(
                    direction, status, Pivot(a, float(price[a]), float(r[a])), Pivot(b, float(price[b]), float(r[b])),
                    confirmed, inv, nxt, pdiff, rdiff, b - a, gates,
                )
            )
        # Developing: a provisional latest pivot paired with the latest confirmed one.
        src = -lo if direction == "bullish" else h
        prov = provisional_pivot(src, k, cfg.div_provisional_min_confirm_bars)
        if prov is not None and piv:
            confirmed_before = [i for i in piv if i + k <= n - 1 and i < prov]
            if confirmed_before:
                a = confirmed_before[-1]
                ok, gates, pdiff, rdiff = _check(direction, a, prov, price, r, a_, cfg)
                if ok:
                    inv = _first_close_beyond(direction, price[prov], c, a_, prov + 1, n - 1, cfg)
                    out.append(
                        Divergence(
                            direction, "invalidated" if inv is not None else "developing",
                            Pivot(a, float(price[a]), float(r[a])), Pivot(prov, float(price[prov]), float(r[prov])),
                            None, inv, None, pdiff, rdiff, prov - a, gates,
                        )
                    )
    out.sort(key=lambda d: (d.p2.i, d.direction))
    return out


def current_divergences(divs: list[Divergence], last_index: int, max_age: int) -> list[Divergence]:
    """Divergences that are live at the latest bar (developing / confirmed) or
    changed state within ``max_age`` bars (e.g. recently invalidated)."""
    out = []
    for d in divs:
        if d.status == "superseded":
            continue
        if last_index - d.event_index <= max_age:
            out.append(d)
    return out
