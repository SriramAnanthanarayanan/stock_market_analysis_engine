"""RS Line - Python translation of the user's TradingView indicator ``rs_line.txt``
("Relative Strength", Pine v6, (c) bharatTrader). Line references are to that file.

    L8   source = close
    L9   comparativeTickerId = 'NSE:NIFTY'                  -> StageConfig.benchmark (NIFTY 50)
    L10  length = 123                                        -> per timeframe (stage_analysis.txt: 52 W / 63 D)
    L25  baseSymbol = request.security(syminfo.tickerid, timeframe.period, source)
    L26  comparativeSymbol = request.security(comparativeTickerId, timeframe.period, source)
    L29  res = baseSymbol / baseSymbol[length] / (comparativeSymbol / comparativeSymbol[length]) - 1
    L30  resColor = res > 0 ? green : red
    L36  y0 = res - res[base]
    L37  angle0 = math.atan(y0 / base)                       (radians)
    L38  zeroLineColor = angle0 > 0.0 ? green : maroon       (when "RS Trend" is shown)
    L39  sma_res = ta.sma(res, lengthRSMA)
    L41  sma_symb = ta.sma(baseSymbol, lengthPriceSMA)
    L42  pos_div = ta.rising(sma_symb, 3) and baseSymbol >= sma_symb
    L43  neg_div = ta.falling(sma_symb, 3) and baseSymbol < sma_symb
    L44  div_started = pos_div or neg_div                   (confirmation bubbles, L54)
    L46  ma_rising = ta.rising(sma_res, 3)                   (alert "Rising Strength")
    L47  ma_falling = ta.falling(sma_res, 3)                 (alert "Declining Strength")
    L31-35 reference label: date of time[length] on the last bar

Bar alignment: ``request.security`` on the chart timeframe returns the comparative
symbol's value for the same bar; with the default ``gaps_off`` a bar the
comparative symbol lacks gets its previous value. The stock's bars define the
timeline and ``[length]`` counts stock bars. Here the benchmark is aligned the
same way (latest value at or before each stock bar), with carried values capped
at ``benchmark_max_ffill_bars`` consecutive bars (beyond that: missing, reported).

No look-ahead: value i uses bars 0..i only; only completed candles are passed in
(Pine recalculates the open realtime bar - completed candles avoid that repaint).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import StageConfig
from ..indicators.technical import crossover, crossunder, falling, rising, sma


@dataclass
class Alignment:
    values: np.ndarray  # benchmark close per stock bar (NaN where not available)
    exact: int
    carried: int
    missing: int
    max_run: int


def align_benchmark(stock_index: pd.DatetimeIndex, bench: pd.Series, max_ffill: int) -> Alignment:
    """Benchmark value per stock bar: same bar if present, else the latest earlier one (Pine gaps_off)."""
    bench = bench.dropna().sort_index()
    out = np.full(len(stock_index), np.nan)
    if bench.empty or len(stock_index) == 0:
        return Alignment(out, 0, 0, len(stock_index), 0)
    bvals = bench.to_numpy(dtype=float)
    pos = bench.index.searchsorted(stock_index, side="right") - 1  # latest benchmark bar <= stock bar
    has_prev = pos >= 0
    is_exact = np.zeros(len(stock_index), dtype=bool)
    is_exact[has_prev] = bench.index[pos[has_prev]] == stock_index[has_prev]
    # Run length of consecutive non-exact bars (carried forward since the last exact match).
    run = np.zeros(len(stock_index), dtype=int)
    r = 0
    for i, e in enumerate(is_exact):  # O(n) integer loop only
        r = 0 if e else r + 1
        run[i] = r
    carried_ok = has_prev & ~is_exact & (run <= max_ffill)
    take = is_exact | carried_ok
    out[take] = bvals[pos[take]]
    return Alignment(out, int(is_exact.sum()), int(carried_ok.sum()), int((~take).sum()), int(run[~is_exact].max()) if (~is_exact).any() else 0)


def _shift(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if n < len(a):
        out[n:] = a[: len(a) - n]
    return out


@dataclass
class RSLine:
    length: int
    res: np.ndarray
    angle: np.ndarray
    sma_res: np.ndarray
    ma_rising: np.ndarray
    ma_falling: np.ndarray
    sma_symb: np.ndarray
    pos_div: np.ndarray
    neg_div: np.ndarray
    cross_up: np.ndarray
    cross_down: np.ndarray
    alignment: Alignment
    info: dict[str, Any] = field(default_factory=dict)


def compute_rs_line(close: Any, bench_aligned: np.ndarray, length: int, cfg: StageConfig, alignment: Alignment | None = None) -> RSLine:
    c = np.asarray(close, dtype=float)
    b = np.asarray(bench_aligned, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        res = c / _shift(c, length) / (b / _shift(b, length)) - 1.0  # L29
    res = np.where(np.isfinite(res), res, np.nan)
    base = cfg.rs_trend_base
    y0 = res - _shift(res, base)  # L36
    angle = np.arctan(y0 / base)  # L37 (radians; NaN propagates)
    sma_res = sma(res, cfg.rs_ma_length)  # L39
    sma_symb = sma(c, cfg.price_sma_length)  # L41
    with np.errstate(invalid="ignore"):
        pos_div = rising(sma_symb, cfg.price_sma_rising_bars) & (c >= sma_symb)  # L42
        neg_div = falling(sma_symb, cfg.price_sma_rising_bars) & (c < sma_symb)  # L43
    zero = np.zeros(len(res))
    return RSLine(
        length=length,
        res=res,
        angle=angle,
        sma_res=sma_res,
        ma_rising=rising(sma_res, cfg.rs_ma_rising_bars),  # L46
        ma_falling=falling(sma_res, cfg.rs_ma_rising_bars),  # L47
        sma_symb=sma_symb,
        pos_div=pos_div,
        neg_div=neg_div,
        cross_up=crossover(res, zero),
        cross_down=crossunder(res, zero),
        alignment=alignment or Alignment(b, int(np.isfinite(b).sum()), 0, int((~np.isfinite(b)).sum()), 0),
    )


def rs_length_for(timeframe: str, cfg: StageConfig) -> int:
    """stage_analysis.txt: weekly 52, daily 63; rs_line.txt default 123 elsewhere (1H/4H)."""
    return {"1w": cfg.rs_length_weekly, "1d": cfg.rs_length_daily}.get(timeframe, cfg.rs_length_pine_default)


def latest_state(rs: RSLine, i: int | None = None) -> dict[str, Any]:
    i = len(rs.res) - 1 if i is None else i

    def f(x: float, nd: int = 6) -> float | None:
        return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)

    r = rs.res[i] if i >= 0 else np.nan
    ang = rs.angle[i] if i >= 0 else np.nan
    return {
        "rs": f(r),
        "rs_zero_line": None if not math.isfinite(r) else ("above" if r > 0 else "below"),  # L30 colour rule
        "rs_trend_angle": f(ang),
        "rs_trend": None if not math.isfinite(ang) else ("improving" if ang > 0 else "deteriorating" if ang < 0 else "flat"),
        "rs_sma": f(rs.sma_res[i]),
        "rs_sma_direction": "rising" if rs.ma_rising[i] else "falling" if rs.ma_falling[i] else ("neutral" if math.isfinite(rs.sma_res[i]) else None),
        "price_sma": f(rs.sma_symb[i], 4),
        "price_confirmation": "bullish" if rs.pos_div[i] else "bearish" if rs.neg_div[i] else "neutral",
        "rs_crossed_above_zero": bool(rs.cross_up[i]),
        "rs_crossed_below_zero": bool(rs.cross_down[i]),
    }
