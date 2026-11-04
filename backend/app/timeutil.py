"""Time helpers. All NSE logic uses Asia/Kolkata explicitly, never machine-local time."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(ts: datetime | pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("naive timestamp: refusing to guess its timezone")
    return t.tz_convert(IST)


def iso(ts: datetime | pd.Timestamp | None) -> str | None:
    if ts is None:
        return None
    return to_ist(ts).isoformat()


def epoch_s(ts: datetime | pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).timestamp())


def parse_iso(text: str | None) -> pd.Timestamp | None:
    if not text:
        return None
    t = pd.Timestamp(text)
    if t.tzinfo is None:
        t = t.tz_localize(IST)
    return t.tz_convert(IST)


def ist_datetime(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def asof_cutoff(asof: date | None, now: datetime | None = None) -> datetime:
    """Information cutoff for a scan.

    Live scans use the current time. Historical ``asof`` scans use the end of
    that IST calendar day (capped at now), i.e. only candles completed by then.
    """
    current = now or now_ist()
    if asof is None:
        return current
    end = ist_datetime(asof, time(23, 59, 59))
    return min(end, current)
