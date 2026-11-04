"""Official NSE 52-week high / low report (optional cross-check for spec 5.2).

NSE publishes a daily capital-market report with each stock's 52-week high and
low **adjusted for corporate actions** (bonus, splits, rights) and their dates
(nseindia.com -> All Reports -> "52 Week High Low"; archive file
``CM_52_wk_High_low_DDMMYYYY.csv``). This module downloads and stores it so the
52-week scanner can show the official adjusted levels next to the levels it
computes from the provider's candles.

Rules:

* The app's own levels are computed from provider OHLC adjusted for splits /
  bonus issues (``raw_split_adjusted`` basis) and compared only with closes on
  that same basis. The official values are a separate reference: they are never
  substituted into the calculation and never compared with a differently
  adjusted close.
* A comparison reports ``match`` (within ``tolerance``), ``mismatch`` (shown with
  both values, e.g. a rights issue the provider did not adjust) or
  ``unavailable`` (no report for that date).
* Actual (unadjusted) traded 52-week prices are not available from either
  source in this app; that limitation is reported, never filled in.

The parser is tolerant about column order and quoting but strict about content:
a file without a SYMBOL column and at least one 52-week high/low column is
rejected.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, delete, select

from .. import database as db

URL_TEMPLATE = "https://nsearchives.nseindia.com/content/CM_52_wk_High_low_{ddmmyyyy}.csv"
TOLERANCE = 0.005  # relative difference treated as the same level (rounding, tick size)


class ReportError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if not s or s in {"-", "NA", "N.A."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.title() if "b" in fmt.lower() else s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_report(text: str) -> list[dict[str, Any]]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    head_i = next((i for i, r in enumerate(rows) if any(_norm(c) == "symbol" for c in r)), None)
    if head_i is None:
        raise ReportError("no SYMBOL header row in the 52-week report")
    header = [_norm(c) for c in rows[head_i]]

    def col(*need: str, exclude: tuple[str, ...] = ()) -> int | None:
        for j, h in enumerate(header):
            if all(n in h for n in need) and not any(x in h for x in exclude):
                return j
        return None

    j_sym = header.index("symbol")
    j_ser = col("series")
    j_hi = col("high", exclude=("date", "dt"))
    j_hi_d = col("high", "dt") if col("high", "dt") is not None else col("high", "date")
    j_lo = col("low", exclude=("date", "dt"))
    j_lo_d = col("low", "dt") if col("low", "dt") is not None else col("low", "date")
    if j_hi is None and j_lo is None:
        raise ReportError("no 52-week high/low columns in the report")
    out = []
    for r in rows[head_i + 1 :]:
        if len(r) <= j_sym or not r[j_sym].strip():
            continue
        get = lambda j: r[j] if j is not None and j < len(r) else None  # noqa: E731
        out.append(
            {
                "symbol": r[j_sym].strip().upper(),
                "series": (get(j_ser) or "").strip() or None,
                "adj_high": _num(get(j_hi)),
                "adj_high_date": _date(get(j_hi_d)),
                "adj_low": _num(get(j_lo)),
                "adj_low_date": _date(get(j_lo_d)),
            }
        )
    if not out:
        raise ReportError("the 52-week report has no rows")
    return out


def store_report(engine: Engine, report_date: date, rows: list[dict[str, Any]], source: str) -> int:
    t = db.nse_52w_reference
    now = db.now_ist_iso()
    payload = [{**r, "report_date": report_date.isoformat(), "fetched_at": now, "source": source} for r in rows if r.get("series") in (None, "EQ", "BE", "BZ", "SM", "ST")]
    with engine.begin() as conn:
        conn.execute(delete(t).where(t.c.report_date == report_date.isoformat()))
        if payload:
            conn.execute(t.insert(), payload)
    return len(payload)


def fetch_report(report_date: date) -> str:
    from ..data.universe import nse_get

    url = URL_TEMPLATE.format(ddmmyyyy=report_date.strftime("%d%m%Y"))
    return nse_get(url).text


def refresh(engine: Engine, report_date: date, text: str | None = None, source: str | None = None) -> dict[str, Any]:
    try:
        body = text if text is not None else fetch_report(report_date)
        rows = parse_report(body)
        n = store_report(engine, report_date, rows, source or URL_TEMPLATE.format(ddmmyyyy=report_date.strftime("%d%m%Y")))
        return {"status": "ok", "report_date": report_date.isoformat(), "rows": n}
    except Exception as exc:  # recorded and reported; never fatal
        return {"status": "error", "report_date": report_date.isoformat(), "error": f"{type(exc).__name__}: {exc}"[:500]}


def lookup(engine: Engine, report_date: str) -> dict[str, dict[str, Any]]:
    t = db.nse_52w_reference
    with engine.connect() as conn:
        rows = conn.execute(select(t).where(t.c.report_date == report_date)).mappings().all()
    return {r["symbol"]: dict(r) for r in rows}


def latest_report_date(engine: Engine) -> str | None:
    t = db.nse_52w_reference
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.report_date).distinct().order_by(t.c.report_date.desc()).limit(1)).first()
    return rows[0] if rows else None


def compare(own_high: float | None, own_low: float | None, ref: dict[str, Any] | None, tolerance: float = TOLERANCE) -> dict[str, Any]:
    """Official adjusted levels next to the app's levels (never substituted)."""
    if not ref:
        return {"status": "unavailable", "note": "no official NSE 52-week report stored for this date"}

    def rel(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b == 0:
            return None
        return abs(a - b) / abs(b)

    dh, dl = rel(own_high, ref.get("adj_high")), rel(own_low, ref.get("adj_low"))
    decided = [d for d in (dh, dl) if d is not None]
    status = "unavailable" if not decided else ("match" if all(d <= tolerance for d in decided) else "mismatch")
    return {
        "status": status,
        "report_date": ref.get("report_date"),
        "nse_adjusted_high": ref.get("adj_high"),
        "nse_adjusted_high_date": ref.get("adj_high_date"),
        "nse_adjusted_low": ref.get("adj_low"),
        "nse_adjusted_low_date": ref.get("adj_low_date"),
        "high_rel_diff": None if dh is None else round(dh, 5),
        "low_rel_diff": None if dl is None else round(dl, 5),
        "tolerance": tolerance,
        "basis": "NSE adjusted for corporate actions (bonus, splits, rights)",
    }
