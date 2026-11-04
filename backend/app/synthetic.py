"""Deterministic synthetic OHLCV fixtures for the self-test and unit tests.

Synthetic series prove that the code follows the intended formulas. They do
not prove that the rules identify real-world chart patterns accurately.

``band_series`` draws a zig-zag that touches two straight boundaries exactly:
highs reach the upper line at peaks, lows reach the lower line at troughs, and
every bar stays inside. Bars are placed on real NSE trading days (daily bars
start 09:15 IST) so calendar logic is exercised too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data.calendar import NSECalendar, SESSION_OPEN
from .timeutil import ist_datetime

DEFAULT_END = date(2026, 10, 30)


def trading_day_index(n: int, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DatetimeIndex:
    days: list[date] = []
    d = end
    while len(days) < n:
        if calendar.is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return pd.DatetimeIndex([pd.Timestamp(ist_datetime(x, SESSION_OPEN)) for x in days], name="ts")


def intraday_index(n: int, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DatetimeIndex:
    """Last ``n`` hourly bar starts (09:15 ... 15:15 IST) on NSE trading days up to ``end``."""
    from .data.calendar import HOURLY_STARTS

    stamps: list[pd.Timestamp] = []
    d = end
    while len(stamps) < n:
        if calendar.is_trading_day(d):
            stamps = [pd.Timestamp(ist_datetime(d, t)) for t in HOURLY_STARTS] + stamps
        d -= timedelta(days=1)
    return pd.DatetimeIndex(stamps[-n:], name="ts")


def weekly_index(n: int, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DatetimeIndex:
    """Last ``n`` weekly bar starts (Monday 09:15 IST) for weeks containing a trading day."""
    monday = end - timedelta(days=end.weekday())
    out: list[pd.Timestamp] = []
    while len(out) < n:
        if calendar.week_last_trading_day(monday) is not None:
            out.append(pd.Timestamp(ist_datetime(monday, SESSION_OPEN)))
        monday -= timedelta(days=7)
    out.reverse()
    return pd.DatetimeIndex(out, name="ts")


def with_index(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    if len(index) != len(df):
        raise ValueError("index length mismatch")
    out = df.copy()
    out.index = index
    return out


@dataclass
class BandSpec:
    upper: tuple[float, float]  # boundary price at pattern start / end
    lower: tuple[float, float]
    length: int
    half_period: int = 6  # bars between alternating touches (must be even)
    wick_frac: float = 0.05  # half candle range as a fraction of the initial width
    lead: int = 60  # flat, quiet bars before the pattern
    post: list[float] | None = None  # band positions of post-pattern closes (0=lower, 1=upper)
    n_post: int = 3
    noise_frac: float = 0.0
    seed: int = 7
    volume: float = 100_000.0


def _frame(index: pd.DatetimeIndex, o, h, l, c, v) -> pd.DataFrame:
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=index).astype("float64")


def band_series(spec: BandSpec, calendar: NSECalendar, end: date = DEFAULT_END) -> pd.DataFrame:
    if spec.half_period % 2:
        raise ValueError("half_period must be even")
    rng = np.random.default_rng(spec.seed)
    L = spec.length
    n_post = len(spec.post) if spec.post is not None else spec.n_post
    xs = np.arange(L + n_post, dtype=float)
    up = spec.upper[0] + (spec.upper[1] - spec.upper[0]) * xs / (L - 1)
    lo = spec.lower[0] + (spec.lower[1] - spec.lower[0]) * xs / (L - 1)
    w = up - lo
    h = spec.wick_frac * w[0]
    if (w[:L] <= 2.5 * h).any():
        raise ValueError("band too narrow for the wick size")

    period = 2 * spec.half_period
    g = ((xs + period // 4) % period) / period  # 0 -> upper touch, 0.5 -> lower touch
    tri = np.abs(2 * g - 1)  # 1 at the upper touch, 0 at the lower touch
    direction = np.where(g >= 0.5, 1.0, -1.0)  # moving up in the second half of the cycle
    mid = lo + h + tri * (w - 2 * h)
    if spec.noise_frac:
        noise = rng.normal(0.0, spec.noise_frac * w[0], len(xs))
        noise[(tri == 0) | (tri == 1)] = 0.0
        mid = np.clip(mid + noise, lo + h, up - h)
    high = mid + h
    low = mid - h
    close = np.clip(mid + 0.5 * h * direction, low, high)

    if spec.post is not None:
        for j, pos in enumerate(spec.post):
            k = L + j
            c = lo[k] + pos * w[k]
            close[k] = c
            high[k] = max(c, mid[k - 1]) + 0.3 * h
            low[k] = min(c, mid[k - 1]) - 0.3 * h

    # Lead-in: quiet bars around the pattern's starting mid price.
    level = mid[0]
    lead_close = level + 0.3 * h * np.sin(np.arange(spec.lead) / 2.0) + rng.normal(0, 0.1 * h, spec.lead)
    lead_high = lead_close + 0.6 * h
    lead_low = lead_close - 0.6 * h

    c_all = np.concatenate([lead_close, close])
    h_all = np.concatenate([lead_high, high])
    l_all = np.concatenate([lead_low, low])
    o_all = np.empty_like(c_all)
    o_all[0] = c_all[0]
    o_all[1:] = np.clip(c_all[:-1], l_all[1:], h_all[1:])
    h_all = np.maximum(h_all, np.maximum(o_all, c_all))
    l_all = np.minimum(l_all, np.minimum(o_all, c_all))
    n = len(c_all)
    vol = spec.volume * (1.0 + 0.2 * np.sin(np.arange(n) / 5.0))
    idx = trading_day_index(n, calendar, end)
    return _frame(idx, o_all, h_all, l_all, c_all, vol)


def pattern_window(spec: BandSpec) -> tuple[int, int]:
    """Absolute (start, end) bar indices of the drawn pattern inside the series."""
    return spec.lead, spec.lead + spec.length - 1


def random_walk(n: int, calendar: NSECalendar, seed: int = 11, start: float = 1000.0, vol: float = 0.012, end: date = DEFAULT_END) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, vol, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    spread = np.abs(rng.normal(0.0, vol * 0.6, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol_ = 1e5 * (1 + 0.3 * rng.random(n))
    return _frame(trading_day_index(n, calendar, end), open_, high, low, close, vol_)


def path_series(path: np.ndarray, calendar: NSECalendar, wick: float, end: date = DEFAULT_END, lead: int = 0) -> pd.DataFrame:
    """Candles around an arbitrary close path (e.g. an arch), optional quiet lead-in."""
    path = np.asarray(path, dtype=float)
    if lead:
        path = np.concatenate([np.full(lead, path[0]) + 0.2 * wick * np.sin(np.arange(lead)), path])
    open_ = np.concatenate([[path[0]], path[:-1]])
    high = np.maximum(open_, path) + wick
    low = np.minimum(open_, path) - wick
    n = len(path)
    return _frame(trading_day_index(n, calendar, end), open_, high, low, path, np.full(n, 1e5))


def zigzag_path(points: list[tuple[int, float]]) -> np.ndarray:
    """Piecewise-linear close path through (bar, price) waypoints."""
    xs, ys = zip(*points)
    return np.interp(np.arange(xs[-1] + 1), xs, ys)


def arch_path(n_up: int = 25, n_down: int = 25, base: float = 1000.0, height: float = 150.0, ripple: float = 15.0) -> np.ndarray:
    """Up-then-down arch with a small zig-zag ripple (creates swing points)."""
    trend = zigzag_path([(0, base), (n_up, base + height), (n_up + n_down, base)])
    x = np.arange(len(trend))
    ripple_wave = ripple * (np.abs(((x % 8) / 8.0) * 2 - 1) * 2 - 1)
    return trend + ripple_wave


# ------------------------------------------------------------------ catalogue
# Fixtures for the nine P1 detectors. Prices ~1000, daily timeframe (1D width
# band 3-30 %, minimum move 6 %).

POSITIVE: dict[str, BandSpec] = {
    "falling_channel": BandSpec(upper=(1100, 990), lower=(1020, 910), length=90),
    "rising_channel": BandSpec(upper=(990, 1100), lower=(910, 1020), length=90),
    "horizontal_channel": BandSpec(upper=(1080, 1112), lower=(1000, 1032), length=90),
    "rectangle": BandSpec(upper=(1080, 1080), lower=(1000, 1000), length=90),
    "ascending_triangle": BandSpec(upper=(1080, 1080), lower=(960, 1050), length=60),
    "descending_triangle": BandSpec(upper=(1080, 990), lower=(960, 960), length=60),
    "symmetrical_triangle": BandSpec(upper=(1080, 1035), lower=(960, 1005), length=60),
    "falling_wedge": BandSpec(upper=(1100, 960), lower=(980, 920), length=60),
    "rising_wedge": BandSpec(upper=(1020, 1080), lower=(900, 1040), length=60),
}

# Structurally different formations that must NOT produce the keyed pattern.
NEGATIVE: dict[str, tuple[str, BandSpec]] = {
    "falling_channel": ("rising channel", POSITIVE["rising_channel"]),
    "rising_channel": ("falling channel", POSITIVE["falling_channel"]),
    "horizontal_channel": ("steep falling channel", POSITIVE["falling_channel"]),
    "rectangle": ("rising channel", POSITIVE["rising_channel"]),
    "ascending_triangle": ("descending triangle", POSITIVE["descending_triangle"]),
    "descending_triangle": ("ascending triangle", POSITIVE["ascending_triangle"]),
    "symmetrical_triangle": ("rectangle (parallel)", POSITIVE["rectangle"]),
    "falling_wedge": ("rising wedge", POSITIVE["rising_wedge"]),
    "rising_wedge": ("falling wedge", POSITIVE["falling_wedge"]),
}

# Formations that miss by one rule. Expected outcome: the named gate fails, or
# "developing" (exactly one touch short; hidden by default, never Validated).
NEAR_MISS: dict[str, tuple[str, BandSpec, str]] = {
    "falling_channel": ("drop 4.5% < 6% minimum move", BandSpec(upper=(1040, 995), lower=(1000, 955), length=90), "total_drop_pct"),
    "rising_channel": ("rise 4.5% < 6% minimum move", BandSpec(upper=(995, 1040), lower=(955, 1000), length=90), "total_rise_pct"),
    "horizontal_channel": ("boundaries diverge: parallelism error 0.32 > 0.25", BandSpec(upper=(1080, 1125), lower=(1000, 1015), length=90), "parallelism_error"),
    "rectangle": ("only 5 touches (needs 6)", BandSpec(upper=(1080, 1080), lower=(1000, 1000), length=40, half_period=8), "developing"),
    "ascending_triangle": ("CR 0.68 > 0.60", BandSpec(upper=(1080, 1080), lower=(960, 998.4), length=60), "convergence_ratio_max"),
    "descending_triangle": ("CR 0.68 > 0.60", BandSpec(upper=(1080, 1041.6), lower=(960, 960), length=60), "convergence_ratio_max"),
    "symmetrical_triangle": ("CR 0.10 < 0.15 (collapsed)", BandSpec(upper=(1080, 1026), lower=(960, 1014), length=60, wick_frac=0.03), "convergence_ratio_min"),
    "falling_wedge": ("midline drop 4.5% < 6%", BandSpec(upper=(1060, 990), lower=(940, 920), length=60), "midline_drop_pct"),
    "rising_wedge": ("midline rise 4.5% < 6%", BandSpec(upper=(1060, 1080), lower=(940, 1010), length=60), "midline_rise_pct"),
}

_FAILED_BREAKOUT_UP = [0.5, 0.6, 0.7, 1.3, 1.35, 0.7, 0.6, 0.5]
_FAILED_BREAKOUT_DOWN = [0.5, 0.4, 0.3, -0.3, -0.35, 0.3, 0.4, 0.5]


def _with_post(spec: BandSpec, post: list[float]) -> BandSpec:
    return BandSpec(**{**spec.__dict__, "post": list(post)})


# Detected as of the pattern end (3 bars later), then tracked with frozen
# geometry over the following bars. Expected terminal reason.
INVALIDATION: dict[str, tuple[str, BandSpec, str]] = {
    "falling_channel": ("break above, 3 closes back inside", _with_post(POSITIVE["falling_channel"], _FAILED_BREAKOUT_UP), "failed_breakout"),
    "rising_channel": ("break below, 3 closes back inside", _with_post(POSITIVE["rising_channel"], _FAILED_BREAKOUT_DOWN), "failed_breakout"),
    "horizontal_channel": ("break above, 3 closes back inside", _with_post(POSITIVE["horizontal_channel"], _FAILED_BREAKOUT_UP), "failed_breakout"),
    "rectangle": ("break below, 3 closes back inside", _with_post(POSITIVE["rectangle"], _FAILED_BREAKOUT_DOWN), "failed_breakout"),
    "ascending_triangle": ("no break before apex - 3", _with_post(POSITIVE["ascending_triangle"], [0.5] * 24), "apex_passed"),
    "descending_triangle": ("no break before apex - 3", _with_post(POSITIVE["descending_triangle"], [0.5] * 24), "apex_passed"),
    "symmetrical_triangle": ("no break before apex - 3", _with_post(POSITIVE["symmetrical_triangle"], [0.5] * 24), "apex_passed"),
    "falling_wedge": ("break above, 3 closes back inside", _with_post(POSITIVE["falling_wedge"], _FAILED_BREAKOUT_UP), "failed_breakout"),
    "rising_wedge": ("no break before apex - 3", _with_post(POSITIVE["rising_wedge"], [0.5] * 36), "apex_passed"),
}

# Falling Channel regression fixtures (spec 20.3).
FALLING_CHANNEL_BREAK_ABOVE = _with_post(POSITIVE["falling_channel"], [0.6, 1.3, 1.4])
EXPANDING = BandSpec(upper=(1040, 1100), lower=(960, 900), length=60)
