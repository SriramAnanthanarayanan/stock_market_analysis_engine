"""Multi-month candles (1 / 3 / 6 / 12 months) and rolling performance horizons.

Candles are aggregated from **completed, cleaned daily candles** - nothing is
filled in for missing sessions:

* 1 month  = calendar month          (``1mo``)
* 3 months = calendar quarter        (``3mo``: Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec)
* 6 months = half year               (``6mo``: Jan-Jun, Jul-Dec)
* 12 months = calendar year          (``12mo``)

``open`` = first daily open, ``high`` = max, ``low`` = min, ``close`` = last daily
close, ``volume`` = sum (missing if no daily volume is known), ``adj_close`` = last.
A candle is labelled with the first calendar day of its period at 09:15 IST and
is **complete** once the last NSE trading day of the period has closed (15:30
IST) at the cutoff. The running period is returned separately as a flagged
*partial* candle built from the completed daily candles so far; indicators and
any calculation use completed candles only.

Performance horizons are different: rolling returns over 1 / 3 / 6 / 12 months
from the last trading day on or before (latest date - months), on the
dividend-adjusted close when available (returns only, never chart prices).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from ..config import MULTI_MONTH_MONTHS
from ..providers.base import ADJ_CLOSE
from ..timeutil import IST, iso
from .calendar import NSECalendar

_OPEN = pd.Timedelta(hours=9, minutes=15)
PERFORMANCE_HORIZONS: tuple[tuple[str, int], ...] = (("1M", 1), ("3M", 3), ("6M", 6), ("12M", 12))


def period_start(d: date, months: int) -> date:
    m0 = ((d.month - 1) // months) * months + 1
    return date(d.year, m0, 1)


def period_last_day(start: date, months: int) -> date:
    y, m = start.year, start.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1) - timedelta(days=1)


def period_end(start: date, months: int, calendar: NSECalendar) -> pd.Timestamp:
    """Session close of the last trading day of the period (weekday fallback for uncovered years)."""
    d = period_last_day(start, months)
    for _ in range(40):
        if calendar.is_trading_day(d):
            break
        d -= timedelta(days=1)
    return pd.Timestamp(calendar.session_close(d))


def resample_months(daily: pd.DataFrame, months: int) -> pd.DataFrame:
    """Aggregate daily candles into calendar periods of ``months`` months (labels: period start 09:15 IST)."""
    if daily.empty:
        out = daily.copy()
        out["source_bars"] = pd.Series(dtype="int64")
        return out
    idx = daily.index.tz_convert(IST)
    starts = [period_start(ts.date(), months) for ts in idx]
    key = pd.DatetimeIndex([pd.Timestamp(s, tz=IST) + _OPEN for s in starts])
    g = daily.groupby(key, sort=True)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(min_count=1),
            "source_bars": g["close"].count(),
        }
    )
    if ADJ_CLOSE in daily.columns:
        out[ADJ_CLOSE] = g[ADJ_CLOSE].last()
    sessions = pd.Series(daily.index, index=key).groupby(level=0)
    out["first_session"] = sessions.first()
    out["last_session"] = sessions.last()
    out.index = pd.DatetimeIndex(out.index).tz_convert(IST)
    out.index.name = "ts"
    return out


def period_bar_ends(starts: pd.DatetimeIndex, months: int, calendar: NSECalendar) -> pd.DatetimeIndex:
    cache: dict[date, pd.Timestamp] = {}
    ends = []
    for ts in starts.tz_convert(IST):
        d = period_start(ts.date(), months)
        if d not in cache:
            cache[d] = period_end(d, months, calendar)
        ends.append(cache[d])
    return pd.DatetimeIndex(ends).tz_convert(IST) if ends else pd.DatetimeIndex([], tz=IST)


def split_complete(daily_completed: pd.DataFrame, timeframe: str, cutoff, calendar: NSECalendar) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(complete candles, the partial running-period candle or an empty frame)."""
    months = MULTI_MONTH_MONTHS[timeframe]
    agg = resample_months(daily_completed, months)
    if agg.empty:
        return agg, agg
    ends = period_bar_ends(agg.index, months, calendar)
    cut = pd.Timestamp(cutoff).tz_convert(IST)
    done = np.asarray(ends <= cut)
    return agg[done], agg[~done]


def partial_week(daily_completed: pd.DataFrame, cutoff, calendar: NSECalendar) -> pd.DataFrame:
    """The running NSE week (Monday 09:15 IST label, like every weekly candle) built
    from the completed daily candles of that week so far, or an empty frame when the
    latest completed day's week has already closed at the cutoff (then it is a
    regular, complete weekly candle)."""
    cols = ["open", "high", "low", "close", "volume", "source_bars", "first_session", "last_session"]
    if daily_completed is None or daily_completed.empty:
        return pd.DataFrame(columns=cols)
    idx = daily_completed.index.tz_convert(IST)
    last = idx[-1]
    monday = pd.Timestamp(last.date() - timedelta(days=last.weekday()), tz=IST) + _OPEN
    end = calendar.bar_ends(pd.DatetimeIndex([monday]), "1w")[0]
    if end <= pd.Timestamp(cutoff).tz_convert(IST):
        return pd.DataFrame(columns=cols)
    week = daily_completed[idx >= monday]
    vol = week["volume"].to_numpy(dtype=float)
    known = vol[np.isfinite(vol)]
    out = pd.DataFrame(
        {
            "open": [float(week["open"].iloc[0])],
            "high": [float(week["high"].max())],
            "low": [float(week["low"].min())],
            "close": [float(week["close"].iloc[-1])],
            "volume": [float(known.sum()) if len(known) else np.nan],
            "source_bars": [int(len(week))],
            "first_session": [week.index[0]],
            "last_session": [week.index[-1]],
        },
        index=pd.DatetimeIndex([monday], name="ts"),
    )
    if ADJ_CLOSE in week.columns:
        out[ADJ_CLOSE] = [float(week[ADJ_CLOSE].iloc[-1])]
    return out


def running_period(daily_completed: pd.DataFrame, timeframe: str, cutoff, calendar: NSECalendar) -> pd.DataFrame:
    """The current incomplete 1W / 1-12 month candle from completed daily candles (may be empty).
    Display only: detectors and default indicators never see it."""
    if timeframe == "1w":
        return partial_week(daily_completed, cutoff, calendar)
    if timeframe in MULTI_MONTH_MONTHS:
        return split_complete(daily_completed, timeframe, cutoff, calendar)[1]
    return pd.DataFrame()


def partial_record(part: pd.DataFrame, timeframe: str, calendar: NSECalendar) -> dict[str, Any] | None:
    """API shape of the running candle: OHLCV + ``is_complete: false`` and its sessions."""
    if part is None or part.empty:
        return None
    r = part.iloc[-1]
    ts = part.index[-1]
    if timeframe == "1w":
        end = calendar.bar_ends(pd.DatetimeIndex([ts]), "1w")[0]
    else:
        end = period_bar_ends(part.index[-1:], MULTI_MONTH_MONTHS[timeframe], calendar)[0]
    vol = float(r["volume"]) if r["volume"] is not None and np.isfinite(float(r["volume"])) else None
    last = r["last_session"]
    first = r["first_session"]
    return {
        "t": int(ts.timestamp()),
        "o": float(r["open"]),
        "h": float(r["high"]),
        "l": float(r["low"]),
        "c": float(r["close"]),
        "v": vol,
        "is_complete": False,
        "label": "INCOMPLETE",
        "timeframe": timeframe,
        "sessions": int(r["source_bars"]),
        "first_session": pd.Timestamp(first).isoformat() if first is not None else None,
        "last_session": pd.Timestamp(last).isoformat() if last is not None else None,
        "period_end": pd.Timestamp(end).isoformat(),
        "note": "Running period aggregated from completed daily candles so far (open = first day's open, high/low so far, close = latest close, volume accumulated). Display only: pattern detection and default indicators use completed candles.",
    }


def candle_meta(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for ts, r in frame.iterrows():
        out.append({"t": int(ts.timestamp()), "first_session": iso(r["first_session"]), "last_session": iso(r["last_session"]), "sessions": int(r["source_bars"])})
    return out


def performance_horizons(daily: pd.DataFrame) -> dict[str, Any]:
    """Rolling returns (not candles): latest completed daily close vs the last trading day
    on or before (latest date - N months)."""
    if daily.empty:
        return {"basis": None, "asof": None, "horizons": []}
    use_adj = ADJ_CLOSE in daily.columns and daily[ADJ_CLOSE].notna().any()
    col = ADJ_CLOSE if use_adj else "close"
    s = daily[col].dropna()
    if s.empty:
        return {"basis": col, "asof": None, "horizons": []}
    last_t = s.index[-1]
    out = []
    for label, m in PERFORMANCE_HORIZONS:
        target = last_t - pd.DateOffset(months=m)
        prior = s[s.index <= target]
        if prior.empty or prior.iloc[-1] == 0:
            out.append({"label": label, "months": m, "return_pct": None, "base_date": None, "status": "insufficient_history"})
            continue
        out.append({"label": label, "months": m, "return_pct": round((float(s.iloc[-1]) / float(prior.iloc[-1]) - 1.0) * 100.0, 3), "base_date": iso(prior.index[-1]), "status": "ok"})
    return {
        "basis": "dividend-adjusted close" if use_adj else "raw close (adjusted close unavailable)",
        "asof": iso(last_t),
        "horizons": out,
        "note": "Rolling returns from the last trading day on or before the same date N months earlier; different from the 1/3/6/12-month candles.",
    }
