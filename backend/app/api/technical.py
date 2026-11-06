"""E1 API: indicator series, chart technical overlays, and the candlestick,
breakout and indicator scanners.

Every series is computed by the backend from the same completed raw-OHLC
candles as /api/chart (same data cutoff, same window), so chart values and
scan values are identical. Missing values are ``null``, never zero.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_, select

from .. import database as db
from ..breakouts import breakout_events, daily_reference
from ..candlesticks import marubozu_events
from ..config import CHART_SOURCE_INTERVAL, MULTI_MONTH_TIMEFRAMES, TECH_PARAM_META, config_hash, describe_config
from ..data.ohlcv import prepare_series
from ..indicators.divergence import find_divergences
from ..models import ChartTimeframe, Timeframe, Universe
from ..scanner import find_latest_scan, get_scan
from ..services import Services
from ..technical import _crosses, analyze, divergence_record, indicator_series
from ..timeutil import asof_cutoff, epoch_s, iso, now_ist, parse_iso

router = APIRouter(prefix="/api")

SYMBOL = Query(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9&\-_.^ :/]+$")
INSTRUMENT = Query("stock", pattern=r"^(stock|index)$", description="stock (NSE equity) or index (official NSE index symbol)")


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _num(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def _series(arr: np.ndarray, start: int, nd: int = 4) -> list[float | None]:
    return [_num(v, nd) for v in arr[start:]]


def resolve_cutoff(s: Services, timeframe: str, asof: date | None, scan_id: str | None):
    scan = None
    if scan_id:
        scan = get_scan(s.engine, scan_id)
        if scan is None:
            raise HTTPException(status_code=404, detail=f"scan {scan_id!r} not found")
        if scan["timeframe"] != timeframe:
            raise HTTPException(status_code=422, detail=f"scan {scan_id!r} is a {scan['timeframe']} scan, not {timeframe}")
        cutoff = parse_iso(scan["data_cutoff"]) if scan.get("data_cutoff") else asof_cutoff(asof)
    else:
        cutoff = asof_cutoff(asof)
    return cutoff, scan


def load_frame(s: Services, symbol: str, timeframe: str, cutoff, instrument: str = "stock"):
    """Completed candles <= cutoff on the current price basis (same as /api/chart and /api/index/chart)."""
    if instrument == "index":
        from ..indices.data import IndexDataError

        it = s.indices.registry.get(symbol.strip()) or s.indices.registry.find(symbol)
        if it is None:
            raise HTTPException(status_code=404, detail=f"unknown index {symbol!r}")
        try:
            frame = s.indices.frame(it["code"], timeframe, cutoff)
        except IndexDataError as exc:
            raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": str(exc)})
        if frame.empty:
            raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": "no completed candles at the cutoff"})
        return it["code"], frame
    symbol = symbol.strip().upper()
    if s.universe.stock(symbol) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r} (not in the NIFTY 500 / F&O universe)")
    interval = CHART_SOURCE_INTERVAL[timeframe]
    raw = s.store.load_current(symbol, interval)
    if raw is None:
        s.market_data.ensure([symbol], interval, cutoff=cutoff)
        raw = s.store.load_current(symbol, interval)
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no cached {interval} market data for {symbol} on the current price basis"})
    frame = prepare_series(raw, symbol, timeframe, cutoff, s.calendar).frame
    if frame.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": f"no completed {timeframe} candles for {symbol} at the cutoff"})
    return symbol, frame


def running_frame_for(s: Services, symbol: str, timeframe: str, cutoff, frame, instrument: str = "stock"):
    """The running 1W / multi-month period (1-row frame) or None - same rows the charts draw."""
    if instrument == "index":
        from .indices import index_running_frame

        return index_running_frame(s, symbol, timeframe, cutoff, frame)
    from ..config import MULTI_MONTH_TIMEFRAMES
    from ..data.periods import running_period

    if timeframe != "1w" and timeframe not in MULTI_MONTH_TIMEFRAMES:
        return None
    raw = s.store.load_current(symbol, "1d")
    if raw is None or raw.empty:
        return None
    part = running_period(prepare_series(raw, symbol, "1d", cutoff, s.calendar).frame, timeframe, cutoff, s.calendar)
    if part is None or part.empty or (len(frame) and part.index[-1] <= frame.index[-1]):
        return None
    return part


def _window_start(frame, start_t: int | None, s: Services) -> int:
    times = [epoch_s(t) for t in frame.index]
    if start_t is None:
        return max(0, len(times) - s.settings.chart_max_candles)
    for i, t in enumerate(times):
        if t >= start_t:
            return i
    return len(times)


def _int_list(value: str | None, lo: int, hi: int, name: str, max_items: int = 10) -> tuple[int, ...] | None:
    if not value:
        return None
    try:
        items = tuple(dict.fromkeys(int(v) for v in value.split(",") if v.strip()))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be a comma-separated list of integers")
    if not items or len(items) > max_items or any(not lo <= v <= hi for v in items):
        raise HTTPException(status_code=422, detail=f"{name}: 1-{max_items} integers between {lo} and {hi}")
    return items


def _meta(s: Services, symbol: str, timeframe: str, frame, cutoff, scan, instrument: str = "stock") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data_cutoff": iso(cutoff),
        "scan_id": scan["scan_id"] if scan else None,
        "bars": len(frame),
        "last_candle_time": iso(frame.index[-1]),
        "instrument": instrument,
        "price_basis": s.indices.store.price_basis if instrument == "index" else s.store.price_basis,
        "config_version": s.tech_cfg.version,
        "config_hash": config_hash(s.tech_cfg),
    }


# ------------------------------------------------------------------ indicators
@router.get("/indicators")
def indicators(
    request: Request,
    symbol: str = SYMBOL,
    timeframe: ChartTimeframe = "1d",
    asof: date | None = None,
    scan_id: str | None = Query(None, max_length=120),
    start_t: int | None = Query(None, ge=0),
    ema: str | None = Query(None, max_length=80, description="comma-separated EMA periods, default 10,20,30,50,100,200"),
    rsi_period: int | None = Query(None, ge=2, le=100),
    atr_period: int | None = Query(None, ge=2, le=100),
    instrument: str = INSTRUMENT,
    include_partial: bool = Query(False, description="1W / multi-month: also compute the running (incomplete) period; its values are provisional"),
) -> dict[str, Any]:
    """EMA / RSI / ATR series aligned with the chart candles from ``start_t``."""
    _check_asof(asof)
    s = svc(request)
    tcfg = s.tech_cfg
    cutoff, scan = resolve_cutoff(s, timeframe, asof, scan_id)
    symbol, frame = load_frame(s, symbol, timeframe, cutoff, instrument)
    partial_t = None
    if include_partial:
        part = running_frame_for(s, symbol, timeframe, cutoff, frame, instrument)
        if part is not None:
            cols = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns]
            frame = pd.concat([frame[cols], part[cols]])
            partial_t = epoch_s(part.index[-1])
    periods = _int_list(ema, 1, 400, "ema") or tcfg.ema_periods
    ser = indicator_series(frame, tcfg, periods, rsi_period, atr_period, cache_key=(instrument, symbol, timeframe, s.store.price_basis))
    start = _window_start(frame, start_t, s)
    n = len(frame)
    close = frame["close"].to_numpy(dtype=float)

    def status(arr: np.ndarray, need: int) -> str:
        return "ok" if np.isfinite(arr[-1]) else ("insufficient_history" if n < need else "not_ready")

    atr = ser["atr"]
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = atr / close * 100.0
    return {
        **_meta(s, symbol, timeframe, frame, cutoff, scan, instrument),
        "times": [epoch_s(t) for t in frame.index[start:]],
        "ema": {str(p): _series(v, start) for p, v in ser["ema"].items()},
        "rsi": _series(ser["rsi"], start, 2),
        "atr": _series(atr, start),
        "atr_pct": _series(atr_pct, start, 3),
        "latest": {
            "close": _num(close[-1]),
            "ema": {str(p): _num(v[-1]) for p, v in ser["ema"].items()},
            "rsi": _num(ser["rsi"][-1], 2),
            "atr": _num(atr[-1]),
            "atr_pct": _num(atr_pct[-1], 3),
        },
        "status": {
            "ema": {str(p): status(v, p) for p, v in ser["ema"].items()},
            "rsi": status(ser["rsi"], ser["rsi_period"] + 1),
            "atr": status(atr, ser["atr_period"]),
        },
        "validated_timeframe": timeframe not in MULTI_MONTH_TIMEFRAMES,
        "partial_included": partial_t is not None,
        "partial_t": partial_t,
        "params": {
            "ema_periods": list(periods),
            "ema_seed": tcfg.ema_seed,
            "rsi_period": ser["rsi_period"],
            "atr_period": ser["atr_period"],
            "rsi_overbought": tcfg.rsi_overbought,
            "rsi_oversold": tcfg.rsi_oversold,
            "rsi_reference_levels": list(tcfg.rsi_reference_levels),
        },
    }


@router.get("/technical")
def technical_overlays(
    request: Request,
    symbol: str = SYMBOL,
    timeframe: ChartTimeframe = "1d",
    asof: date | None = None,
    scan_id: str | None = Query(None, max_length=120),
    start_t: int | None = Query(None, ge=0),
    confirm: int = Query(1, ge=1, le=2, description="1 = one completed close beyond the level, 2 = two consecutive closes"),
    instrument: str = INSTRUMENT,
) -> dict[str, Any]:
    """Chart overlays: Marubozu candles, RSI divergences, EMA crossovers, the
    indicator snapshot and daily N-day levels / breakouts (daily candles only)."""
    _check_asof(asof)
    s = svc(request)
    tcfg = s.tech_cfg
    cutoff, scan = resolve_cutoff(s, timeframe, asof, scan_id)
    symbol, frame = load_frame(s, symbol, timeframe, cutoff, instrument)
    start = _window_start(frame, start_t, s)
    ser = indicator_series(frame, tcfg, cache_key=(instrument, symbol, timeframe, s.store.price_basis))
    n = len(frame)
    res = analyze(frame, timeframe, tcfg, s.cfg, with_sr=False)
    multi = timeframe in MULTI_MONTH_TIMEFRAMES
    if multi:
        # Candlestick / divergence / crossover scanners are not run on multi-month candles.
        maru, divs, crosses = [], [], []
    else:
        maru = marubozu_events(frame, ser["atr"], tcfg, timeframe, since=start)
        divs = [divergence_record(d, frame.index, n) for d in find_divergences(frame["high"], frame["low"], frame["close"], ser["rsi"], ser["atr"], tcfg) if d.p1.i >= start]
        crosses = _crosses(ser["ema"], tcfg.ema_cross_pairs, frame.index, start)

    # N-day levels always come from completed DAILY candles as of the same cutoff.
    daily: dict[str, Any] = {"status": "ok", "levels": {}, "breakouts": [], "confirm_closes": confirm}
    if timeframe == "1d":
        dframe = frame
    else:
        try:
            _, dframe = load_frame(s, symbol, "1d", cutoff, instrument)
        except HTTPException:
            dframe = None
            daily["status"] = "data_unavailable"
    if dframe is not None:
        datr = ser["atr"] if timeframe == "1d" else indicator_series(dframe, tcfg, cache_key=(instrument, symbol, "1d", s.store.price_basis))["atr"]
        for nd in tcfg.breakout_lookback_days:
            ref = daily_reference(dframe, nd)
            daily["levels"][str(nd)] = ref
            if ref is None:
                daily["status"] = "insufficient_history"
        if timeframe == "1d":
            for nd in tcfg.breakout_lookback_days:
                daily["breakouts"] += breakout_events(dframe, datr, nd, confirm, tcfg, since=start)
        daily["latest_daily_candle"] = iso(dframe.index[-1])
    return {
        **_meta(s, symbol, timeframe, frame, cutoff, scan, instrument),
        "snapshot": res.snapshot,
        "snapshot_status": res.status,
        "marubozu": maru,
        "divergences": divs,
        "ema_crosses": crosses,
        "daily": daily,
        "notes": ([f"{timeframe}: Marubozu, divergence and crossover scanners are not run on multi-month candles (not validated); levels are daily-derived."] if multi else []) + [
            "Marubozu: body >= {:.0%} of range, each wick <= {:.0%} (raw OHLC, completed candles).".format(tcfg.marubozu_min_body_ratio, tcfg.marubozu_max_upper_wick_ratio),
            "N-day levels use the previous N completed daily candles; on 1H/4H charts they are daily-derived reference levels, not N-bar levels.",
            "Divergence pivots are confirmed swing points (k={}); a divergence is confirmed only at P2 + k.".format(tcfg.div_swing_k),
        ],
    }


@router.get("/config/technical")
def technical_config(request: Request) -> dict[str, Any]:
    s = svc(request)
    params = [p for p in describe_config(s.tech_cfg, TECH_PARAM_META) if p["name"] != "version"]
    return {"version": s.tech_cfg.version, "config_hash": config_hash(s.tech_cfg), "parameters": params}


# ------------------------------------------------------------------ scanners
def _universe_clause(t, universe: str):
    if universe == "nifty500":
        return t.c.in_nifty500 == 1
    if universe == "fno":
        return t.c.fno_status == "YES"
    if universe == "non_fno":
        return and_(t.c.in_nifty500 == 1, t.c.fno_status == "NO")
    return None


def _scan_or_404(s: Services, universe: str, timeframe: str, asof: date | None) -> dict[str, Any] | None:
    return find_latest_scan(s.engine, universe, timeframe, asof)


def _scan_summary(scan: dict[str, Any] | None) -> dict[str, Any] | None:
    if scan is None:
        return None
    keys = ("scan_id", "universe", "timeframe", "asof", "state", "finished_at", "data_cutoff", "successful_symbols", "failed_symbols", "price_basis", "tech_config_version", "tech_config_hash")
    return {k: scan.get(k) for k in keys}


def _signal_rows(s: Services, scan_id: str, universe: str, category: str, extra=None) -> list[dict[str, Any]]:
    t = db.tech_signals
    q = select(t).where((t.c.scan_id == scan_id) & (t.c.category == category))
    uc = _universe_clause(t, universe)
    if uc is not None:
        q = q.where(uc)
    if extra is not None:
        q = q.where(extra)
    with s.engine.connect() as conn:
        rows = conn.execute(q.order_by(t.c.age_bars, t.c.symbol)).mappings().all()
    out = []
    for r in rows:
        m = db.loads(r["metrics_json"]) or {}
        out.append({**m, "symbol": r["symbol"], "company": r["company"], "fno_status": r["fno_status"], "in_nifty500": bool(r["in_nifty500"]), "timeframe": r["timeframe"], "age_bars": r["age_bars"]})
    return out


def _empty(scan, note: str) -> dict[str, Any]:
    return {"scan": _scan_summary(scan), "count": 0, "items": [], "note": note}


NO_SCAN = "No completed scan for this universe/timeframe/date yet. Run a scan (Pattern Scanner -> Refresh scan); candlestick, breakout and indicator results are computed by the same scan."
NO_TECH = "This scan was run before technical analysis existed (or failed for it); run a new scan."


def _has_tech(scan: dict[str, Any]) -> bool:
    return bool(scan.get("tech_config_hash"))


@router.get("/scan/candlesticks")
def scan_candlesticks(
    request: Request,
    universe: Universe = "all",
    timeframe: Timeframe = "1d",
    asof: date | None = None,
    kind: Literal["bullish_marubozu", "bearish_marubozu"] | None = None,
    min_range_atr: float | None = Query(None, ge=0, le=50),
    min_volume_ratio: float | None = Query(None, ge=0, le=100),
    max_age: int = Query(0, ge=0, le=50),
) -> dict[str, Any]:
    _check_asof(asof)
    s = svc(request)
    scan = _scan_or_404(s, universe, timeframe, asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not _has_tech(scan):
        return _empty(scan, NO_TECH)
    t = db.tech_signals
    extra = t.c.age_bars <= max_age
    if kind:
        extra = and_(extra, t.c.kind == kind)
    items = _signal_rows(s, scan["scan_id"], universe, "candlestick", extra)
    if min_range_atr is not None:
        items = [x for x in items if x.get("range_atr") is not None and x["range_atr"] >= min_range_atr]
    if min_volume_ratio is not None:
        items = [x for x in items if x.get("volume_ratio") is not None and x["volume_ratio"] >= min_volume_ratio]
    return {"scan": _scan_summary(scan), "count": len(items), "items": items, "note": "Marubozu candles on completed candles of this scan; volume ratio is supporting information only."}


@router.get("/scan/breakouts")
def scan_breakouts(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    kind: str | None = Query(None, pattern=r"^(breakout_high|breakdown_low)_\d+d$"),
    confirm: int = Query(1, ge=1, le=2, description="1 = one completed close beyond the level, 2 = two consecutive closes"),
    status: Literal["confirmed", "failed", "pending", "all"] = "confirmed",
    min_volume_ratio: float | None = Query(None, ge=0, le=100),
    min_atr: float | None = Query(None, ge=0, le=50, description="minimum close distance beyond the level, x ATR"),
    max_age: int = Query(0, ge=0, le=50),
    first_of_run: bool = False,
) -> dict[str, Any]:
    """N-day breakouts from the latest daily scan (always daily candles)."""
    _check_asof(asof)
    s = svc(request)
    scan = _scan_or_404(s, universe, "1d", asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not _has_tech(scan):
        return _empty(scan, NO_TECH)
    t = db.tech_signals
    extra = and_(t.c.age_bars <= max_age, t.c.confirm_closes == confirm)
    if kind:
        extra = and_(extra, t.c.kind == kind)
    if status != "all":
        extra = and_(extra, t.c.status == status)
    items = _signal_rows(s, scan["scan_id"], universe, "breakout", extra)
    if min_volume_ratio is not None:
        items = [x for x in items if x.get("volume_ratio") is not None and x["volume_ratio"] >= min_volume_ratio]
    if min_atr is not None:
        items = [x for x in items if x.get("distance_atr") is not None and abs(x["distance_atr"]) >= min_atr]
    if first_of_run and confirm == 1:
        items = [x for x in items if x.get("first_of_run")]
    return {
        "scan": _scan_summary(scan),
        "count": len(items),
        "items": items,
        "confirm_closes": confirm,
        "note": f"Previous N completed daily candles (current candle excluded), buffer {s.tech_cfg.breakout_buffer_atr} x ATR({s.tech_cfg.atr_period}); {confirm}-close confirmation. Rule-based levels, not trade recommendations.",
    }


ALIGNMENTS = Literal["bullish_full", "bearish_full", "bullish_partial", "bearish_partial", "mixed", "not_available"]


@router.get("/scan/indicators")
def scan_indicators(
    request: Request,
    universe: Universe = "all",
    timeframe: Timeframe = "1d",
    asof: date | None = None,
    alignment: ALIGNMENTS | None = None,
    price_vs_ema200: Literal["above", "below"] | None = None,
    cross: str | None = Query(None, pattern=r"^ema_cross_\d+_\d+_(bullish|bearish)$"),
    rsi_state: Literal["overbought", "oversold", "neutral"] | None = None,
    divergence: Literal["bullish", "bearish"] | None = None,
    divergence_status: Literal["confirmed", "developing", "invalidated"] | None = None,
    max_age: int = Query(5, ge=0, le=50),
    limit: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """EMA alignment / crossover, RSI and divergence screening (separate from the pattern scanner)."""
    _check_asof(asof)
    s = svc(request)
    scan = _scan_or_404(s, universe, timeframe, asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not _has_tech(scan):
        return _empty(scan, NO_TECH)
    t = db.tech_snapshots
    q = select(t).where(t.c.scan_id == scan["scan_id"])
    uc = _universe_clause(t, universe)
    if uc is not None:
        q = q.where(uc)
    with s.engine.connect() as conn:
        snaps = conn.execute(q.order_by(t.c.symbol)).mappings().all()
    crosses: dict[str, list[dict[str, Any]]] = {}
    for x in _signal_rows(s, scan["scan_id"], universe, "ema_cross", db.tech_signals.c.age_bars <= max_age):
        crosses.setdefault(x["symbol"], []).append(x)
    divs: dict[str, list[dict[str, Any]]] = {}
    for x in _signal_rows(s, scan["scan_id"], universe, "rsi_divergence", db.tech_signals.c.age_bars <= max_age):
        divs.setdefault(x["symbol"], []).append(x)
    items = []
    for r in snaps:
        m = db.loads(r["metrics_json"]) or {}
        sym = r["symbol"]
        item = {
            "symbol": sym,
            "company": r["company"],
            "fno_status": r["fno_status"],
            "in_nifty500": bool(r["in_nifty500"]),
            "timeframe": r["timeframe"],
            "status": r["status"],
            "last_candle_time": r["last_candle_time"],
            "close": r["close"],
            "alignment": m.get("alignment"),
            "ema": m.get("ema"),
            "price_vs_ema": m.get("price_vs_ema"),
            "rsi": m.get("rsi"),
            "rsi_state": m.get("rsi_state"),
            "atr": m.get("atr"),
            "atr_pct": m.get("atr_pct"),
            "crosses": [{"kind": c["kind"], "direction": c["direction"], "age_bars": c["age_bars"], "time": c.get("time")} for c in crosses.get(sym, [])],
            "divergences": [{"direction": d["direction"], "status": d["status"], "age_bars": d["age_bars"], "time": d.get("time"), "p1": d.get("p1"), "p2": d.get("p2")} for d in divs.get(sym, [])],
        }
        if alignment and item["alignment"] != alignment:
            continue
        if price_vs_ema200 and (item["price_vs_ema"] or {}).get("200") != price_vs_ema200:
            continue
        if cross and not any(f"{c['kind']}_{c['direction']}" == cross for c in item["crosses"]):
            continue
        if rsi_state and item["rsi_state"] != rsi_state:
            continue
        if divergence and not any(d["direction"] == divergence and (divergence_status is None or d["status"] == divergence_status) for d in item["divergences"]):
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return {
        "scan": _scan_summary(scan),
        "count": len(items),
        "items": items,
        "note": "EMA alignment and crossovers describe the current order of moving averages; they are not signals or guarantees.",
    }


# ------------------------------------------------------------------ symbol search
@router.get("/symbols")
def symbols(
    request: Request,
    q: str = Query("", max_length=40),
    limit: int = Query(20, ge=1, le=600),
) -> dict[str, Any]:
    """Common symbol search (NSE stocks of the NIFTY 500 / F&O universe; indices are added by E2)."""
    s = svc(request)
    needle = q.strip().upper()
    out = []
    for st in s.universe.members("all", None).stocks:
        name = (st.company or "").upper()
        if not needle or st.symbol.startswith(needle) or needle in name:
            rank = 0 if st.symbol == needle else 1 if st.symbol.startswith(needle) else 2
            out.append((rank, st.symbol, {"symbol": st.symbol, "name": st.company, "kind": "stock", "fno_status": st.fno_status}))
    from ..indices.registry import CATEGORY_LABELS

    for r in s.indices.registry.all(active_only=True):
        if not r["analysis_enabled"]:
            continue
        name = r["name"].upper()
        code = r["code"].upper()
        if not needle or code.startswith(needle) or needle in name or needle in code:
            rank = 0 if code == needle or name == needle else 1 if code.startswith(needle) else 2
            out.append((rank, r["code"], {"symbol": r["code"], "name": r["name"], "kind": "index", "category": CATEGORY_LABELS.get(r["category"], r["category"]), "data_status": r["data_status"]}))
    out.sort(key=lambda x: (x[0], x[2]["kind"] != "stock", x[1]))
    return {"count": len(out), "items": [x[2] for x in out[:limit]]}
