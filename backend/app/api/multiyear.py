"""Multi-year breakout API (docs/multi-year-breakouts.md): recent breakouts of the
latest daily scan for any number of years, one stock's full breakout history
(chart overlay) and a descriptive event study of every stored past breakout.
Analytical screening, not trade recommendations; the study is not a success rate."""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_

from .. import database as db
from ..config import config_hash
from ..data.ohlcv import prepare_series
from ..indicators.core import atr_wilder
from ..models import Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..signals.multiyear import STATUS_LABELS, compact, multiyear_breakouts
from ..timeutil import asof_cutoff, now_ist
from .signals import NO_SCAN, NO_SIGNALS, _empty, _row, _version, evidence_rows, has_signals, scan_summary

router = APIRouter(prefix="/api")
MULTIYEAR_SINCE = (1, 2, 0)  # SignalConfig version that added the multi-year breakout scanner
DEFAULT_STATUSES = ("holding", "below_level")
OLD_SCAN = "The latest daily scan was run before the multi-year breakout scanner existed (signal config {v}). A new 1D scan computes it (after an upgrade the scheduler starts one automatically); refresh this page when it has finished."
CAVEATS = [
    "Descriptive only: what happened after past breakouts of today's universe, not a success rate and not a probability.",
    "Survivorship bias: the universe is today's NIFTY 500 / F&O list, so stocks that later collapsed or were delisted are missing - results look better than a real-time screen would have been.",
    "Breakouts cluster in bull markets, so events are not independent; a few market phases dominate the averages.",
    "Price returns on the split / bonus-adjusted close (no dividends) against the benchmark price index over the same calendar window.",
    "History starts with the provider data (daily from about 1996 or the listing): an 'all-time high in data' may not be one before that.",
]


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _scan(s: Services, universe: str, asof: date | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(latest daily scan, empty response when it cannot answer)."""
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return None, _empty(None, NO_SCAN)
    if not has_signals(scan):
        return scan, _empty(scan, NO_SIGNALS)
    if _version(scan.get("signal_config_version")) < MULTIYEAR_SINCE:
        return scan, _empty(scan, OLD_SCAN.format(v=scan.get("signal_config_version")))
    return scan, None


def _statuses(status: str | None) -> set[str]:
    wanted = set(status.split(",")) if status else set(DEFAULT_STATUSES)
    unknown = wanted - set(STATUS_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown multi-year status {sorted(unknown)}; allowed {sorted(STATUS_LABELS)}")
    return wanted


@router.get("/scan/multiyear-breakouts")
def scan_multiyear(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    min_years: float = Query(5.0, ge=0, le=100, description="minimum base length in years (any number, e.g. 5, 10, 15, 20, 7.5)"),
    max_age: int = Query(20, ge=0, le=1000, description="breakout candle within the last N completed sessions"),
    status: str | None = Query(None, max_length=100, description="comma-separated: holding, below_level, failed (default holding, below_level)"),
    all_time_high: bool | None = Query(None, description="true: all-time highs within the data only; false: only levels below an older higher price"),
    min_volume_ratio: float | None = Query(None, ge=0, le=100),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Stocks that closed above a resistance they had not exceeded for at least ``min_years``."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan, empty = _scan(s, universe, asof)
    if empty is not None:
        return empty
    eff = max(min_years, cfg.multiyear_min_years)
    wanted = _statuses(status)
    t = db.signal_evidence
    rows = evidence_rows(s, scan["scan_id"], universe, ("multiyear",), and_(t.c.kind == "event", t.c.strength >= eff, t.c.age_bars <= max_age, t.c.status.in_(sorted(wanted))))
    syms = sorted({e["symbol"] for e in rows})
    hist = {e["symbol"]: e for e in evidence_rows(s, scan["scan_id"], universe, ("multiyear",), and_(t.c.signal_type == "multiyear_history", t.c.symbol.in_(syms)))} if syms else {}
    items = []
    for e in rows:
        if fno and e["fno_status"] != fno:
            continue
        r = _row(e)
        if all_time_high is not None and bool(r.get("all_time_high_in_data")) != all_time_high:
            continue
        if min_volume_ratio is not None and (r.get("volume_ratio") or 0) < min_volume_ratio:
            continue
        h = hist.get(e["symbol"])
        r["past_events"] = [x for x in (h["metrics"].get("events", []) if h else []) if x["t"] < r["t"]]
        items.append(r)
    items.sort(key=lambda r: (r.get("age_bars") or 0, -(r.get("base_years") or 0)))
    note = (
        f"Close above a resistance not exceeded for at least {eff:g} years: the highest high since the stock last traded at the breakout close; the base starts at the first high within "
        f"{cfg.multiyear_zone_pct:g} % of it. Completed daily candles; breakouts of the last {max_age} sessions. Failed = {cfg.multiyear_fail_closes} closes back below the level. Not trade recommendations."
    )
    if min_years < cfg.multiyear_min_years:
        note += f" Bases shorter than {cfg.multiyear_min_years:g} years are not stored."
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "min_years": eff,
        "stored_min_years": cfg.multiyear_min_years,
        "status_labels": STATUS_LABELS,
        "params": {k: getattr(cfg, k) for k in ("multiyear_min_years", "multiyear_zone_pct", "multiyear_buffer_atr", "multiyear_fail_closes", "multiyear_volume_lookback", "multiyear_store_sessions", "multiyear_benchmark")},
        "note": note,
    }


@router.get("/multiyear/symbol")
def multiyear_symbol(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    asof: date | None = None,
) -> dict[str, Any]:
    """Every multi-year breakout in one stock's history: stored by the latest daily scan, else computed on demand (not stored)."""
    _check_asof(asof)
    s = svc(request)
    sym = symbol.strip().upper()
    if s.universe.stock(sym) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {sym!r}")
    scan = find_latest_scan(s.engine, "all", "1d", asof)
    if scan is not None and has_signals(scan) and _version(scan.get("signal_config_version")) >= MULTIYEAR_SINCE:
        t = db.signal_evidence
        rows = evidence_rows(s, scan["scan_id"], "all", ("multiyear",), t.c.signal_type == "multiyear_history", symbol=sym)
        m = rows[0]["metrics"] if rows else {}
        return {"symbol": sym, "source": "scan", "scan": scan_summary(scan), "events": m.get("events", []), "data_start": m.get("data_start"), "zone_pct": s.signal_cfg.multiyear_zone_pct}
    raw = s.store.load_current(sym, "1d")
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no cached daily data for {sym}"})
    cutoff = asof_cutoff(asof)
    frame = prepare_series(raw, sym, "1d", cutoff, s.calendar).frame
    cfg = s.signal_cfg
    try:
        bench = s.indices.frame(cfg.multiyear_benchmark, "1d", cutoff)["close"]
    except Exception:
        bench = None
    h, l, c = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    evs = multiyear_breakouts(frame, atr_wilder(h, l, c, cfg.atr_period), cfg, symbol=sym, config_hash=config_hash(cfg), benchmark=bench)
    return {
        "symbol": sym, "source": "adhoc", "scan": None, "events": [compact(e) for e in evs], "data_start": frame.index[0].isoformat() if len(frame) else None,
        "zone_pct": cfg.multiyear_zone_pct, "note": "No daily scan with the multi-year scanner covers this date; computed on demand and not stored.",
    }


def _stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0, "median": None, "mean": None, "pct_positive": None}
    return {"n": len(vals), "median": round(statistics.median(vals), 2), "mean": round(statistics.fmean(vals), 2), "pct_positive": round(100.0 * sum(v > 0 for v in vals) / len(vals), 1)}


@router.get("/multiyear/study")
def multiyear_study(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    min_years: float = Query(5.0, ge=0, le=100),
    max_years: float | None = Query(None, ge=0, le=100),
    all_time_high: bool | None = None,
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    """What happened after every stored past multi-year breakout (forward returns vs the benchmark)."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan, empty = _scan(s, universe, asof)
    if empty is not None:
        return {**empty, "summary": [], "caveats": CAVEATS}
    eff = max(min_years, cfg.multiyear_min_years)
    t = db.signal_evidence
    events: list[dict[str, Any]] = []
    for e in evidence_rows(s, scan["scan_id"], universe, ("multiyear",), t.c.signal_type == "multiyear_history"):
        if fno and e["fno_status"] != fno:
            continue
        for x in e["metrics"].get("events", []):
            if x["base_years"] < eff or (max_years is not None and x["base_years"] > max_years):
                continue
            if all_time_high is not None and bool(x.get("all_time_high_in_data")) != all_time_high:
                continue
            events.append({"symbol": e["symbol"], "company": e["company"], "fno_status": e["fno_status"], **x})
    summary = []
    for m in cfg.multiyear_horizons_months:
        hs = [hz for ev in events for hz in (ev.get("forward") or {}).get("horizons", []) if hz["months"] == m]
        rets = [hz["return_pct"] for hz in hs if hz.get("return_pct") is not None]
        exc = [hz["excess_pct"] for hz in hs if hz.get("excess_pct") is not None]
        ex = _stats(exc)
        summary.append({
            "months": m, **_stats(rets), "pending": sum(1 for hz in hs if hz.get("status") == "pending"),
            "excess_n": ex["n"], "excess_median": ex["median"], "excess_mean": ex["mean"], "pct_beat_benchmark": ex["pct_positive"],
        })
    full12 = [ev["forward"] for ev in events if (ev.get("forward") or {}).get("window_complete_12m")]
    events.sort(key=lambda r: r["t"], reverse=True)
    return {
        "scan": scan_summary(scan),
        "min_years": eff,
        "max_years": max_years,
        "events_total": len(events),
        "stocks": len({e["symbol"] for e in events}),
        "summary": summary,
        "mfe_12m": _stats([f["mfe_12m_pct"] for f in full12 if f.get("mfe_12m_pct") is not None]),
        "mae_12m": _stats([f["mae_12m_pct"] for f in full12 if f.get("mae_12m_pct") is not None]),
        "benchmark": cfg.multiyear_benchmark,
        "count": min(len(events), limit),
        "items": events[:limit],
        "caveats": CAVEATS,
    }
