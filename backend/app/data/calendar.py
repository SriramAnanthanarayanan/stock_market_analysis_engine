"""NSE trading calendar: sessions, holidays, bar boundaries and completion.

* Regular equity session: 09:15-15:30 Asia/Kolkata.
* 1H bars start at :15 (Yahoo alignment); the last bar 15:15-15:30 is 15 minutes.
* 4H bars are session aligned: 09:15-13:15 and 13:15-15:30 (shorter partial
  bar, intentional).
* 1D bar ends at 15:30 on its date.
* 1W bar ends at 15:30 on the last trading session of its Mon-Fri week, which
  is earlier than Friday when Friday (or more) is a holiday.

Holidays come from ``backend/data/nse_holidays.json``: the current year is
refreshed from the NSE holiday API (it only publishes the current year); earlier
years are a bundled list (see docs/data-sources.md). For a year with no holiday
list, weekdays are assumed to be trading days - this errs towards treating a
week as *not yet complete*, never towards using an incomplete bar.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..timeutil import IST, ist_datetime

log = logging.getLogger(__name__)

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
FOUR_HOUR_SPLIT = time(13, 15)
_OPEN_OFFSET = pd.Timedelta(hours=9, minutes=15)
_SPLIT_OFFSET = pd.Timedelta(hours=13, minutes=15)
_CLOSE_OFFSET = pd.Timedelta(hours=15, minutes=30)
HOURLY_STARTS = [time(9, 15), time(10, 15), time(11, 15), time(12, 15), time(13, 15), time(14, 15), time(15, 15)]
HOURLY_ENDS = [time(10, 15), time(11, 15), time(12, 15), time(13, 15), time(14, 15), time(15, 15), time(15, 30)]
FOUR_HOUR_STARTS = [SESSION_OPEN, FOUR_HOUR_SPLIT]
FOUR_HOUR_ENDS = [FOUR_HOUR_SPLIT, SESSION_CLOSE]


@dataclass
class NSECalendar:
    holidays: dict[date, str] = field(default_factory=dict)
    covered_years: set[int] = field(default_factory=set)
    sources: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path) -> "NSECalendar":
        if not path.exists():
            log.warning("NSE holiday file %s missing; assuming all weekdays trade", path)
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: dict) -> "NSECalendar":
        holidays: dict[date, str] = {}
        years: set[int] = set()
        sources: dict[str, str] = {}
        for year, block in payload.get("years", {}).items():
            years.add(int(year))
            sources[str(year)] = block.get("source", "unknown")
            for item in block.get("holidays", []):
                holidays[date.fromisoformat(item["date"])] = item.get("description", "")
        return cls(holidays=holidays, covered_years=years, sources=sources)

    # --------------------------------------------------------------- queries
    def year_covered(self, year: int) -> bool:
        return year in self.covered_years

    def is_holiday(self, d: date) -> bool:
        return d in self.holidays

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session_open(self, d: date) -> datetime:
        return ist_datetime(d, SESSION_OPEN)

    def session_close(self, d: date) -> datetime:
        return ist_datetime(d, SESSION_CLOSE)

    def previous_trading_day(self, d: date) -> date:
        cur = d - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cur):
                return cur
            cur -= timedelta(days=1)
        return cur

    def next_trading_day(self, d: date) -> date:
        cur = d + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cur):
                return cur
            cur += timedelta(days=1)
        return cur

    def week_last_trading_day(self, d: date) -> date | None:
        monday = d - timedelta(days=d.weekday())
        for offset in range(4, -1, -1):
            day = monday + timedelta(days=offset)
            if self.is_trading_day(day):
                return day
        return None

    def in_session_window(self, now: datetime, after_close_minutes: int = 15) -> bool:
        """True during the regular session and up to ``after_close_minutes`` after it."""
        now = now.astimezone(IST)
        d = now.date()
        if not self.is_trading_day(d):
            return False
        return self.session_open(d) <= now <= self.session_close(d) + timedelta(minutes=after_close_minutes)

    # ---------------------------------------------------------- bar timings
    def bar_ends(self, starts: pd.DatetimeIndex, timeframe: str) -> pd.DatetimeIndex:
        """Nominal completion time for each bar start (vectorised)."""
        if starts.tz is None:
            raise ValueError("bar starts must be timezone-aware")
        starts = starts.tz_convert(IST)
        day = starts.normalize()
        if timeframe == "1h":
            ends = starts + pd.Timedelta(hours=1)
            return min_index(ends, day + _CLOSE_OFFSET)
        if timeframe == "4h":
            before_split = (starts - day) < _SPLIT_OFFSET
            return where_index(before_split, day + _SPLIT_OFFSET, day + _CLOSE_OFFSET)
        if timeframe == "1d":
            return day + _CLOSE_OFFSET
        if timeframe in ("1w", "1wk"):
            out = []
            cache: dict[date, pd.Timestamp] = {}
            for ts in day:
                d = ts.date()
                monday = d - timedelta(days=d.weekday())
                if monday not in cache:
                    last = self.week_last_trading_day(monday) or (monday + timedelta(days=4))
                    cache[monday] = pd.Timestamp(self.session_close(last))
                out.append(cache[monday])
            return pd.DatetimeIndex(out, tz=IST) if out else pd.DatetimeIndex([], tz=IST)
        raise ValueError(f"unknown timeframe {timeframe!r}")

    def latest_completed_bar_end(self, timeframe: str, now: datetime) -> datetime:
        """Most recent nominal bar end at or before ``now`` (used for freshness)."""
        now = now.astimezone(IST)
        d = now.date()
        if timeframe in ("1w", "1wk"):
            last = self.week_last_trading_day(d)
            if last is not None and now >= self.session_close(last):
                return self.session_close(last)
            monday = d - timedelta(days=d.weekday())
            prev = monday - timedelta(days=7)
            for _ in range(10):
                last = self.week_last_trading_day(prev)
                if last is not None:
                    return self.session_close(last)
                prev -= timedelta(days=7)
            return self.session_close(prev)
        if timeframe == "1d":
            if self.is_trading_day(d) and now >= self.session_close(d):
                return self.session_close(d)
            return self.session_close(self.previous_trading_day(d))
        ends = HOURLY_ENDS if timeframe in ("1h",) else FOUR_HOUR_ENDS if timeframe == "4h" else None
        if ends is None:
            raise ValueError(f"unknown timeframe {timeframe!r}")
        if self.is_trading_day(d):
            passed = [ist_datetime(d, t) for t in ends if ist_datetime(d, t) <= now]
            if passed:
                return passed[-1]
        return self.session_close(self.previous_trading_day(d))

    def project_bar_starts(self, last_start: pd.Timestamp, timeframe: str, n: int) -> list[pd.Timestamp]:
        """Projected start times of the next ``n`` bars (for drawing projections only)."""
        if n <= 0:
            return []
        last = pd.Timestamp(last_start).tz_convert(IST)
        out: list[pd.Timestamp] = []
        if timeframe in ("1h", "4h"):
            starts = HOURLY_STARTS if timeframe == "1h" else FOUR_HOUR_STARTS
            d = last.date()
            later = [t for t in starts if ist_datetime(d, t) > last.to_pydatetime()]
            while len(out) < n:
                for t in later:
                    out.append(pd.Timestamp(ist_datetime(d, t)))
                    if len(out) >= n:
                        break
                d = self.next_trading_day(d)
                later = list(starts)
            return out
        if timeframe == "1d":
            d = last.date()
            while len(out) < n:
                d = self.next_trading_day(d)
                out.append(pd.Timestamp(ist_datetime(d, SESSION_OPEN)))
            return out
        if timeframe in ("1w", "1wk"):
            monday = last.date() - timedelta(days=last.date().weekday())
            while len(out) < n:
                monday += timedelta(days=7)
                if self.week_last_trading_day(monday) is not None:
                    out.append(pd.Timestamp(ist_datetime(monday, SESSION_OPEN)))
            return out
        raise ValueError(f"unknown timeframe {timeframe!r}")


def min_index(a: pd.DatetimeIndex, b: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return where_index(a <= b, a, b)


def where_index(mask: np.ndarray, a: pd.DatetimeIndex, b: pd.DatetimeIndex) -> pd.DatetimeIndex:
    ai = a.tz_convert("UTC").as_unit("s").asi8
    bi = b.tz_convert("UTC").as_unit("s").asi8
    vals = np.where(np.asarray(mask), ai, bi)
    return pd.to_datetime(vals, unit="s", utc=True).tz_convert(IST)


# ----------------------------------------------------------------- refresh


def parse_nse_holiday_payload(payload: dict, segment: str = "CM") -> list[dict]:
    """Parse the NSE ``holiday-master`` JSON (capital-market segment)."""
    rows = payload.get(segment)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"holiday payload has no {segment!r} segment")
    out = []
    for item in rows:
        raw = item.get("tradingDate")
        if not raw:
            raise ValueError("holiday row without tradingDate")
        d = datetime.strptime(raw, "%d-%b-%Y").date()
        out.append({"date": d.isoformat(), "description": (item.get("description") or "").strip()})
    out.sort(key=lambda r: r["date"])
    return out


def merge_holiday_file(path: Path, rows: list[dict], source: str, fetched_at: str) -> dict:
    """Merge freshly fetched rows (grouped by year) into the holiday file."""
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"years": {}}
    by_year: dict[str, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["date"][:4], []).append(r)
    for year, items in by_year.items():
        payload.setdefault("years", {})[year] = {"source": source, "fetched_at": fetched_at, "holidays": items}
    payload["years"] = dict(sorted(payload["years"].items()))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload
