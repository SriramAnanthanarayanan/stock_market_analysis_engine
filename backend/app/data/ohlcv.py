"""OHLCV hygiene, NSE 4H resampling and completed-candle filtering.

Nothing here manufactures candles. Rows are dropped (and counted) when they are
invalid, duplicated, outside the regular session, or provider phantom bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..providers.base import ADJ_CLOSE, OHLCV_COLUMNS
from ..timeutil import IST
from .calendar import NSECalendar, where_index

_OPEN = pd.Timedelta(hours=9, minutes=15)
_SPLIT = pd.Timedelta(hours=13, minutes=15)
_CLOSE = pd.Timedelta(hours=15, minutes=30)
_REPAIR_REL_TOL = 1e-4  # float/adjustment rounding tolerance for H/L vs O/C
# Daily bar with no volume and no range (O=H=L=C) whose price jumps more than this
# from the previous kept close: a stale/mis-scaled provider print (Yahoo's pre-2015
# NSE history has ~135 of them, e.g. RELIANCE 28-Jul-2005 at 218.31 amid ~110).
# A day without trades keeps the previous price, so a real one never jumps.
_STALE_FLAT_JUMP = 0.20


@dataclass
class CleanReport:
    rows_in: int = 0
    rows_out: int = 0
    dropped_nan: int = 0
    dropped_invalid: int = 0
    repaired: int = 0
    dropped_duplicates: int = 0
    dropped_outside_session: int = 0
    dropped_phantom: int = 0
    dropped_stale_flat: int = 0
    volume_missing: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def clean_ohlcv(df: pd.DataFrame, interval: str, calendar: NSECalendar) -> tuple[pd.DataFrame, CleanReport]:
    """Validate and tidy a canonical OHLCV frame for one provider interval."""
    rep = CleanReport(rows_in=len(df))
    if df.empty:
        return df.copy(), rep
    # adj_close (split + dividend adjusted) rides along untouched; it is never used
    # for chart prices, patterns or levels (docs/data-sources.md, price basis).
    cols = OHLCV_COLUMNS + ([ADJ_CLOSE] if ADJ_CLOSE in df.columns else [])
    out = df[cols].astype("float64").copy()
    if out.index.tz is None:
        raise ValueError("OHLCV index must be timezone-aware")
    out.index = out.index.tz_convert(IST)

    dup = out.index.duplicated(keep="last")
    rep.dropped_duplicates = int(dup.sum())
    out = out[~dup].sort_index()

    nan = out[["open", "high", "low", "close"]].isna().any(axis=1)
    rep.dropped_nan = int(nan.sum())
    out = out[~nan]

    o, h, l, c = (out[k].to_numpy() for k in ("open", "high", "low", "close"))
    top = np.maximum(o, c)
    bot = np.minimum(o, c)
    fix_h = (h < top) & (top - h <= _REPAIR_REL_TOL * top)
    fix_l = (l > bot) & (l - bot <= _REPAIR_REL_TOL * bot)
    if fix_h.any() or fix_l.any():
        h = np.where(fix_h, top, h)
        l = np.where(fix_l, bot, l)
        out = out.assign(high=h, low=l)
        rep.repaired = int((fix_h | fix_l).sum())
    invalid = (out["low"] <= 0) | (out["high"] < out[["open", "close"]].max(axis=1)) | (
        out["low"] > out[["open", "close"]].min(axis=1)
    ) | (out["high"] < out["low"])
    rep.dropped_invalid = int(invalid.sum())
    out = out[~invalid]

    days = out.index.normalize()
    if interval == "1h":
        tod = out.index - days
        outside = (tod < _OPEN) | (tod >= _CLOSE)
        rep.dropped_outside_session = int(outside.sum())
        out = out[~outside]
        days = out.index.normalize()
        # Yahoo reports the 09:15 hourly bar (and occasionally others) with 0
        # volume. A real zero-volume hour is implausible for these stocks, so
        # treat it as *missing*, not as a true zero.
        zero = out["volume"] == 0
        if zero.any():
            out.loc[zero, "volume"] = np.nan
    rep.volume_missing = int(out["volume"].isna().sum())

    if interval in ("1h", "1d"):
        dates = [ts.date() for ts in days]
        no_volume = (out["volume"].isna() | (out["volume"] == 0)).to_numpy()
        flat = (out["high"] == out["low"]).to_numpy()
        on_holiday = np.array([calendar.is_holiday(d) for d in dates], dtype=bool)
        on_weekend = np.array([d.weekday() >= 5 for d in dates], dtype=bool)
        # Provider phantom bars: holiday/weekend rows with no volume. Special
        # sessions (Muhurat, budget Saturdays) carry real volume and are kept.
        phantom = (on_holiday | on_weekend) & no_volume
        if interval == "1d":
            phantom |= on_holiday & flat & no_volume
        rep.dropped_phantom = int(phantom.sum())
        out = out[~phantom]
        if interval == "1d":
            stale = _stale_flat_prints(out)
            rep.dropped_stale_flat = int(stale.sum())
            out = out[~stale]

    rep.rows_out = len(out)
    out.index.name = "ts"
    return out, rep


def _stale_flat_prints(df: pd.DataFrame) -> np.ndarray:
    """Mask of no-volume, no-range daily bars that jump > _STALE_FLAT_JUMP from
    the previous kept close. Trailing comparison only (no look-ahead)."""
    c = df["close"].to_numpy()
    cand = ((df["volume"].isna() | (df["volume"] == 0)) & (df["high"] == df["low"])).to_numpy()
    mask = np.zeros(len(df), dtype=bool)
    if not cand.any():
        return mask
    prev = np.nan
    for i in range(len(c)):
        if cand[i] and np.isfinite(prev) and abs(c[i] / prev - 1.0) > _STALE_FLAT_JUMP:
            mask[i] = True
            continue
        prev = c[i]
    return mask


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Aggregate cleaned 1H bars into NSE session-aligned 4H bars.

    Bins: 09:15-13:15 and 13:15-15:30 IST (the second bar is intentionally
    shorter). Never uses midnight-anchored or generic clock boundaries.
    """
    if df_1h.empty:
        out = df_1h.copy()
        out["source_bars"] = pd.Series(dtype="int64")
        return out
    idx = df_1h.index.tz_convert(IST)
    day = idx.normalize()
    tod = idx - day
    key = where_index(np.asarray(tod < _SPLIT), day + _OPEN, day + _SPLIT)
    grouped = df_1h.groupby(key, sort=True)
    out = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(min_count=1),
            "source_bars": grouped["close"].count(),
        }
    )
    if ADJ_CLOSE in df_1h.columns:
        out[ADJ_CLOSE] = grouped[ADJ_CLOSE].last()
    out.index = pd.DatetimeIndex(out.index).tz_convert(IST)
    out.index.name = "ts"
    return out


@dataclass
class PreparedSeries:
    """Completed candles for one symbol/timeframe, ready for detection."""

    symbol: str
    timeframe: str
    frame: pd.DataFrame  # open, high, low, close, volume (+ source_bars for 4H)
    ends: pd.DatetimeIndex
    cutoff: datetime
    dropped_incomplete: int
    clean_report: dict


def prepare_series(
    raw: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cutoff: datetime,
    calendar: NSECalendar,
) -> PreparedSeries:
    """Clean, resample (4H) and keep only bars completed at or before ``cutoff``."""
    interval = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}[timeframe]
    cleaned, rep = clean_ohlcv(raw, interval, calendar)
    frame = resample_4h(cleaned) if timeframe == "4h" else cleaned
    if frame.empty:
        return PreparedSeries(symbol, timeframe, frame, pd.DatetimeIndex([], tz=IST), cutoff, 0, rep.as_dict())
    ends = calendar.bar_ends(frame.index, timeframe)
    cut = pd.Timestamp(cutoff).tz_convert(IST)
    complete = np.asarray(ends <= cut)
    dropped = int((~complete).sum())
    return PreparedSeries(
        symbol=symbol,
        timeframe=timeframe,
        frame=frame[complete],
        ends=ends[complete],
        cutoff=cutoff,
        dropped_incomplete=dropped,
        clean_report=rep.as_dict(),
    )
