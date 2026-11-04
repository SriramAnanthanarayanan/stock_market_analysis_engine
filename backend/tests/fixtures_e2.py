"""E2 fixtures: a deterministic stand-in for the official NSE index history and a
catalogue payload shaped like ``/api/allIndices``. Results are fixtures, not market data."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.data.calendar import NSECalendar
from app.indices.data import NseIndexHistory, parse_history
from app.synthetic import DEFAULT_END

ORIGIN = date(2019, 1, 1)

# name -> (drift per day, daily vol, start level, launch date or None)
SERIES = {
    "NIFTY 50": (0.0003, 0.009, 11000.0, None),
    "NIFTY 500": (0.0003, 0.010, 9000.0, None),
    "NIFTY BANK": (0.0002, 0.012, 27000.0, None),
    "NIFTY IT": (0.0008, 0.013, 15000.0, None),  # outperformer
    "NIFTY METAL": (-0.0003, 0.016, 3000.0, None),  # underperformer
    "NIFTY200 MOMENTUM 30": (0.0006, 0.012, 15000.0, None),
    "NIFTY INDIA DEFENCE": (0.0010, 0.018, 3000.0, date(2026, 1, 5)),  # launched < 1 year before the fixture end
    "NIFTY GAPPY": (0.0002, 0.010, 1000.0, None),  # weeks missing in the history
}
GAP_WEEKS = [(date(2026, 3, 2), date(2026, 3, 20))]  # 3 consecutive weeks missing for NIFTY GAPPY


def _seed(name: str) -> int:
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)


def full_series(name: str, calendar: NSECalendar, end: date = DEFAULT_END) -> list[dict]:
    drift, vol, level, launch = SERIES[name]
    rng = np.random.default_rng(_seed(name))
    rows = []
    d = ORIGIN
    c = level
    while d <= end:
        if calendar.is_trading_day(d):
            r = rng.normal(drift, vol)
            o = c
            c = c * float(np.exp(r))
            hi = max(o, c) * (1 + abs(rng.normal(0, vol / 3)))
            lo = min(o, c) * (1 - abs(rng.normal(0, vol / 3)))
            skip = (launch is not None and d < launch) or (name == "NIFTY GAPPY" and any(a <= d <= b for a, b in GAP_WEEKS))
            if not skip:
                rows.append({
                    "EOD_INDEX_NAME": name,
                    "EOD_OPEN_INDEX_VAL": round(o, 2),
                    "EOD_HIGH_INDEX_VAL": round(hi, 2),
                    "EOD_LOW_INDEX_VAL": round(lo, 2),
                    "EOD_CLOSE_INDEX_VAL": round(c, 2),
                    "HIT_TRADED_QTY": 1_000_000,
                    "HIT_TURN_OVER": 1000.0,
                    "EOD_TIMESTAMP": d.strftime("%d-%b-%Y").upper(),
                })
        d += timedelta(days=1)
    return rows


class FakeHistory:
    """Same interface as ``NseIndexHistory``; records every requested range."""

    def __init__(self, calendar: NSECalendar, end: date = DEFAULT_END):
        self.calendar = calendar
        self.end = end
        self.calls: list[tuple[str, date, date]] = []
        self._cache: dict[str, list[dict]] = {}

    def fetch(self, name: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((name, start, end))
        if name not in SERIES:
            return parse_history([])
        rows = self._cache.setdefault(name, full_series(name, self.calendar, self.end))
        sel = [r for r in rows if start <= pd.Timestamp(r["EOD_TIMESTAMP"].title()).date() <= min(end, self.end)]
        return parse_history(sel)

    fetch_back = NseIndexHistory.fetch_back  # yearly backward requests through ``fetch``


def catalogue(calendar: NSECalendar, end: date = DEFAULT_END, mismatch: str | None = None) -> dict:
    """allIndices-like payload; reference closes taken from the fixture series."""
    groups = {
        "NIFTY 50": ("INDICES ELIGIBLE IN DERIVATIVES", "NIFTY 50"),
        "NIFTY BANK": ("INDICES ELIGIBLE IN DERIVATIVES", "NIFTY BANK"),
        "NIFTY 500": ("BROAD MARKET INDICES", "NIFTY 500"),
        "NIFTY IT": ("SECTORAL INDICES", "NIFTY IT"),
        "NIFTY METAL": ("SECTORAL INDICES", "NIFTY METAL"),
        "NIFTY200 MOMENTUM 30": ("STRATEGY INDICES", "NIFTY200MOMENTM30"),
        "NIFTY INDIA DEFENCE": ("THEMATIC INDICES", "NIFTY IND DEFENCE"),
        "NIFTY GAPPY": ("THEMATIC INDICES", "NIFTY GAPPY"),
    }
    data = []
    for name, (group, symbol) in groups.items():
        rows = full_series(name, calendar, end)
        by_date = {pd.Timestamp(r["EOD_TIMESTAMP"].title()).date(): r["EOD_CLOSE_INDEX_VAL"] for r in rows}
        dates = sorted(by_date)

        def on_or_before(d: date) -> date:
            return max(x for x in dates if x <= d)

        last = dates[-1]
        ref = {
            "previousDay": last, "oneWeekAgo": on_or_before(last - timedelta(days=7)),
            "date30dAgo": on_or_before(last - timedelta(days=30)), "date365dAgo": on_or_before(last - timedelta(days=365)) if dates[0] <= last - timedelta(days=365) else None,
        }
        val = {k: (by_date[v] if v else None) for k, v in ref.items()}
        if name == mismatch:
            val["previousDay"] = round(val["previousDay"] * 1.05, 2)
        data.append({
            "key": group, "index": name, "indexSymbol": symbol, "last": val["previousDay"],
            "previousDay": ref["previousDay"].strftime("%d-%b-%Y"), "previousDayVal": val["previousDay"],
            "oneWeekAgo": ref["oneWeekAgo"].strftime("%d-%b-%Y"), "oneWeekAgoVal": val["oneWeekAgo"],
            "date30dAgo": ref["date30dAgo"].strftime("%d-%b-%Y"), "oneMonthAgoVal": val["date30dAgo"],
            "date365dAgo": ref["date365dAgo"].strftime("%d-%b-%Y") if ref["date365dAgo"] else "-", "oneYearAgoVal": val["date365dAgo"] if ref["date365dAgo"] else "-",
        })
    data += [
        {"key": "BROAD MARKET INDICES", "index": "INDIA VIX", "indexSymbol": "INDIA VIX"},
        {"key": "STRATEGY INDICES", "index": "NIFTY50 TR 2X LEVERAGE", "indexSymbol": "NIFTY50 TR 2X LEV"},
        {"key": "FIXED INCOME INDICES", "index": "NIFTY 10 YR BENCHMARK G-SEC", "indexSymbol": "NIFTY GS 10YR"},
    ]
    return {"data": data}
