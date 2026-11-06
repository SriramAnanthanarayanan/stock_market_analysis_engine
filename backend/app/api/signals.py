"""Advanced scanner API: fresh 10-day breakouts, Volume Shocker, 52-week breakouts,
normalized evidence and the advanced configurations.

All results are read from the latest completed scan (same scan, candles and
cutoff as the pattern scanner); nothing is recalculated per request. Scans made
before these scanners existed carry no evidence and say so.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from sqlalchemy import and_, select

from .. import database as db
from ..config import (
    CONFLUENCE_PARAM_META,
    DETECTOR_TIMEFRAMES,
    SIGNAL_PARAM_META,
    VCP_PARAM_META,
    VCP_SOURCE_PARAMS,
    config_hash,
    describe_config,
)
from ..models import Timeframe, Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..signals import nse52w
from ..signals.crossing import first_in_lookback
from ..timeutil import now_ist

router = APIRouter(prefix="/api")

NO_SCAN = "No completed scan for this universe/timeframe/date yet. Run a scan (any scanner page -> Run scan)."
NO_SIGNALS = "This scan was run before the advanced scanners existed (no evidence stored). Run a new scan."


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def universe_clause(t, universe: str):
    if universe == "nifty500":
        return t.c.in_nifty500 == 1
    if universe == "fno":
        return t.c.fno_status == "YES"
    if universe == "non_fno":
        return and_(t.c.in_nifty500 == 1, t.c.fno_status == "NO")
    return None


def scan_summary(scan: dict[str, Any] | None) -> dict[str, Any] | None:
    if scan is None:
        return None
    keys = (
        "scan_id", "universe", "timeframe", "asof", "state", "finished_at", "data_cutoff", "successful_symbols", "failed_symbols",
        "price_basis", "signal_config_version", "signal_config_hash", "vcp_config_version", "vcp_config_hash", "tech_config_version",
        "ipo_config_version", "ipo_config_hash",
    )
    return {k: scan.get(k) for k in keys}


def has_signals(scan: dict[str, Any] | None) -> bool:
    return bool(scan and scan.get("signal_config_hash"))


SETUPS_SINCE = (1, 1, 0)  # SignalConfig version that added the about-to-break-out states


def _version(v: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def evidence_rows(s: Services, scan_id: str, universe: str = "all", sources: tuple[str, ...] | None = None, extra=None, symbol: str | None = None) -> list[dict[str, Any]]:
    t = db.signal_evidence
    q = select(t).where(t.c.scan_id == scan_id)
    if sources:
        q = q.where(t.c.source.in_(sources))
    uc = universe_clause(t, universe)
    if uc is not None:
        q = q.where(uc)
    if symbol:
        q = q.where(t.c.symbol == symbol)
    if extra is not None:
        q = q.where(extra)
    with s.engine.connect() as conn:
        rows = conn.execute(q.order_by(t.c.symbol, t.c.signal_t)).mappings().all()
    return [evidence_dict(r) for r in rows]


def evidence_dict(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["metrics"] = db.loads(d.pop("metrics_json", None)) or {}
    d["explanation"] = db.loads(d.pop("explanation_json", None)) or []
    d["in_nifty500"] = bool(d.get("in_nifty500"))
    return d


def _row(e: dict[str, Any]) -> dict[str, Any]:
    return {**e["metrics"], "symbol": e["symbol"], "company": e["company"], "fno_status": e["fno_status"], "in_nifty500": e["in_nifty500"], "evidence_id": e["evidence_id"], "lifecycle": e["lifecycle"], "valid_until": e["valid_until"], "age_bars": e["age_bars"]}


def _empty(scan, note: str, **extra: Any) -> dict[str, Any]:
    return {"scan": scan_summary(scan), "count": 0, "items": [], "note": note, **extra}


# ------------------------------------------------------------------ fresh 10-day breakouts
@router.get("/scan/fresh-breakouts")
def scan_fresh_breakouts(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    direction: Literal["bullish", "bearish", "both"] = "both",
    mode: Literal["close", "touch"] = "close",
    confirm: int = Query(1, ge=1, le=2, description="1 = the breakout close; 2 = two consecutive closes"),
    status: Literal["confirmed", "failed", "pending", "all"] = "confirmed",
    max_age: int = Query(0, ge=0, le=60, description="sessions since the breakout candle (0 = today's fresh crossing, Filter A)"),
    first_only: bool = Query(False, description="Filter B: First Breakout in Lookback"),
    lookback_n: int | None = Query(None, ge=1, le=250, description="N for Filter B (default from SignalConfig)"),
    active_only: bool = Query(False, description="exclude events whose episode was reset (closed back inside)"),
    min_volume_ratio: float | None = Query(None, ge=0, le=100),
    min_atr: float | None = Query(None, ge=0, le=50),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Fresh crossings of the previous 10 completed daily candles' high / low (spec 3)."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    n = lookback_n or cfg.fresh_first_lookback_sessions
    t = db.signal_evidence
    if mode == "touch":
        rows = evidence_rows(s, scan["scan_id"], universe, ("fresh_breakout",), and_(t.c.status.in_(("closed_inside", "closed_beyond")), t.c.age_bars <= max_age))
    else:
        rows = evidence_rows(s, scan["scan_id"], universe, ("fresh_breakout",), and_(t.c.status == "confirmed", t.c.age_bars <= max_age))
    items = []
    for e in rows:
        r = _row(e)
        if direction != "both" and r["direction"] != direction:
            continue
        if fno and r["fno_status"] != fno:
            continue
        if mode == "touch":
            r["confirmation_status"] = "provisional touch"
            items.append(r)
            continue
        conf = "confirmed" if confirm == 1 else r.get("confirm_2")
        if status != "all" and conf != status:
            continue
        r["confirmation_status"] = conf
        r["first_in_lookback_n"] = first_in_lookback(r.get("sessions_since_last_beyond"), r.get("history_sessions_before", 0), n)
        if first_only and r["first_in_lookback_n"] is not True:
            continue
        if active_only and r.get("episode_state") == "reset":
            continue
        if min_volume_ratio is not None and (r.get("volume_ratio") is None or r["volume_ratio"] < min_volume_ratio):
            continue
        if min_atr is not None and (r.get("distance_atr") is None or abs(r["distance_atr"]) < min_atr):
            continue
        items.append(r)
    items.sort(key=lambda r: (r["age_bars"], -abs(r.get("distance_atr") or 0)))
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "lookback_n": n,
        "mode": mode,
        "confirm_closes": confirm,
        "note": (
            f"Fresh crossing of the previous {cfg.fresh_lookback_days} completed daily candles' high/low (current candle excluded) by "
            f"{cfg.fresh_buffer_atr} x ATR({cfg.atr_period}); the previous candle is judged on its own window; one event per episode "
            f"(re-armed after {cfg.fresh_rearm_closes} closes back inside the prior range). 'First Breakout in Lookback' means no close "
            f"beyond its own level in the preceding {n} sessions - not the first breakout in the stock's history. Not trade recommendations."
        ),
    }


# ------------------------------------------------------------------ about to break out (10-day level)
SETUP_RANK = {"breakout": 0, "confirmed_breakout": 1, "touched": 2, "very_near": 3, "approaching": 4, "rejected": 5, "expired": 6}


@router.get("/scan/breakout-setups")
def scan_breakout_setups(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    direction: Literal["bullish", "bearish", "both"] = "bullish",
    state: str | None = Query(None, max_length=200, description="comma-separated states (default: approaching, very_near, touched, breakout, confirmed_breakout)"),
    include_rejected: bool = False,
    include_expired: bool = False,
    min_quality: float = Query(0.0, ge=0, le=100),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """Stocks about to break out of (or just breaking) their previous 10-day high / low,
    with distance %, distance ATR, EMA 20/50, volume ratio, RS, Stage and a setup quality."""
    from ..signals.breakouts import SETUP_LABELS, setup_quality
    from .vcp import stage_context

    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    if _version(scan.get("signal_config_version")) < SETUPS_SINCE:
        return _empty(scan, f"The latest daily scan was run with signal config {scan.get('signal_config_version')}, before the About to Break Out states existed. A new 1D scan computes them (after an upgrade the scheduler starts one automatically); refresh this page when it has finished.")
    wanted = set(state.split(",")) if state else {"approaching", "very_near", "touched", "breakout", "confirmed_breakout"} | ({"rejected"} if include_rejected else set()) | ({"expired"} if include_expired else set())
    unknown = wanted - set(SETUP_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown setup state {sorted(unknown)}; allowed {sorted(SETUP_LABELS)}")
    t = db.signal_evidence
    rows = evidence_rows(s, scan["scan_id"], universe, ("fresh_breakout",), and_(t.c.kind == "state", t.c.status.in_(sorted(wanted))))
    ctx, wscan = stage_context(s, universe, asof)
    items = []
    for e in rows:
        r = _row(e)
        if direction != "both" and r["direction"] != direction:
            continue
        if fno and r["fno_status"] != fno:
            continue
        st = ctx.get(r["symbol"])
        r["stage"] = st
        r["rs"] = st.get("rs") if st else None
        q, comps = setup_quality(r, r["rs"], st["stage"] if st else None, cfg)
        r["setup_quality"], r["setup_quality_components"] = q, comps
        if q < min_quality:
            continue
        items.append(r)
    items.sort(key=lambda r: (SETUP_RANK.get(r["state"], 9), -r["setup_quality"], r.get("distance_pct") or 0))
    return {
        "scan": scan_summary(scan),
        "stage_scan": wscan,
        "count": len(items),
        "items": items,
        "state_labels": SETUP_LABELS,
        "thresholds": {"approach_pct": cfg.about_to_breakout_pct, "approach_atr": cfg.about_to_breakout_atr, "very_near_pct": cfg.very_near_pct, "very_near_atr": cfg.very_near_atr, "rule": cfg.about_to_breakout_rule, "expiry_sessions": cfg.setup_expiry_sessions, "rejection_sessions": cfg.setup_rejection_sessions},
        "note": (
            f"10D high = highest high of the previous {cfg.fresh_lookback_days} completed daily candles (current excluded). "
            f"Approaching: within {cfg.about_to_breakout_pct} % {'and' if cfg.about_to_breakout_rule == 'both' else 'or'} {cfg.about_to_breakout_atr} ATR below it; "
            f"Very Near: within {cfg.very_near_pct} % / {cfg.very_near_atr} ATR; Touched: intraday at or through the level, close still near it; "
            "Breakout / Confirmed Breakout: the existing fresh-crossing rule (one / two closes); Rejected: a push through that closed back away, "
            f"or a breakout that closed back inside within {cfg.setup_rejection_sessions} sessions; Expired: stopped approaching within {cfg.setup_expiry_sessions} sessions without a breakout. "
            "Setup quality is an explainable screening score, not a probability. Not trade recommendations."
        ),
    }


# ------------------------------------------------------------------ Volume Shocker
@router.get("/scan/volume-shocks")
def scan_volume_shocks(
    request: Request,
    universe: Universe = "all",
    timeframe: Timeframe = "1d",
    asof: date | None = None,
    direction: Literal["bullish", "bearish", "neutral", "all"] = "all",
    min_rvol: float | None = Query(None, ge=1, le=100, description="default SignalConfig.vs_rvol_threshold"),
    percentile: bool | None = Query(None, description="require volume >= the 90th percentile of the previous 60 candles"),
    liquidity: bool = Query(True, description="apply the minimum average traded value filter"),
    min_traded_value: float | None = Query(None, ge=0, description="override the per-timeframe minimum (rupees per candle)"),
    max_age: int = Query(0, ge=0, le=10),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan = find_latest_scan(s.engine, universe, timeframe, asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    t = db.signal_evidence
    # events only: the per-candle ``volume_state`` row (kind "state") is context for the confluence engine
    rows = evidence_rows(s, scan["scan_id"], universe, ("volume_shock",), and_(t.c.age_bars <= max_age, t.c.kind == "event"))
    rv = cfg.vs_rvol_threshold if min_rvol is None else min_rvol
    need_pct = cfg.vs_require_percentile if percentile is None else percentile
    floor = cfg.vs_min_avg_traded_value.get(timeframe, 0.0) if min_traded_value is None else min_traded_value
    items = []
    for e in rows:
        r = _row(e)
        if r.get("rvol") is None or r["rvol"] < rv:
            continue
        if direction != "all" and r["direction"] != direction:
            continue
        if need_pct and not r.get("above_percentile"):
            continue
        if liquidity and (r.get("avg_traded_value") is None or r["avg_traded_value"] < floor):
            continue
        if fno and r["fno_status"] != fno:
            continue
        items.append(r)
    items.sort(key=lambda r: (r["age_bars"], -(r.get("rvol") or 0)))
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "min_rvol": rv,
        "percentile_required": need_pct,
        "liquidity_floor": floor if liquidity else None,
        "rvol_options": list(cfg.vs_rvol_options),
        "note": (
            f"RVOL = volume / mean volume of the previous {cfg.vs_lookback} completed candles (current excluded"
            + ("; intraday: the same time-of-day candle of the previous sessions" if timeframe in ("1h", "4h") else "")
            + "). Direction describes the candle; volume alone never implies accumulation or distribution. Screening thresholds, not validated predictions."
        ),
    }


# ------------------------------------------------------------------ 52-week
@router.get("/scan/52week")
def scan_52week(
    request: Request,
    universe: Universe = "all",
    asof: date | None = None,
    view: Literal["events", "touch", "state"] = "events",
    direction: Literal["bullish", "bearish", "both"] = "both",
    confirm: int = Query(1, ge=1, le=2),
    max_age: int = Query(0, ge=0, le=20),
    near_pct: float | None = Query(None, ge=0, le=50, description="state view: within this % of the 52-week high (bullish) / low (bearish)"),
    include_insufficient: bool = False,
    min_volume_ratio: float | None = Query(None, ge=0, le=100),
    fno: Literal["YES", "NO", "UNKNOWN"] | None = None,
) -> dict[str, Any]:
    """52-week (252 completed sessions, current excluded) breakouts / breakdowns (spec 5)."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.signal_cfg
    scan = find_latest_scan(s.engine, universe, "1d", asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    t = db.signal_evidence
    state_rows = {e["symbol"]: e for e in evidence_rows(s, scan["scan_id"], universe, ("week52",), t.c.signal_type == "week52_state")}
    states = {sym: e["metrics"] for sym, e in state_rows.items()}
    ref_date = next((m.get("time", "")[:10] for m in states.values() if m.get("time")), None)
    official = nse52w.lookup(s.engine, ref_date) if ref_date else {}
    items = []
    if view == "state":
        out = []
        for sym, m in states.items():
            it = {"symbol": sym, **m}
            if m.get("status") != "ok" and not include_insufficient:
                continue
            ev = state_rows[sym]
            if fno and ev["fno_status"] != fno:
                continue
            if near_pct is not None and it.get("status") == "ok":
                d_hi, d_lo = it.get("distance_from_high_pct"), it.get("distance_from_low_pct")
                ok = (direction in ("bullish", "both") and d_hi is not None and d_hi >= -near_pct) or (direction in ("bearish", "both") and d_lo is not None and d_lo <= near_pct)
                if not ok:
                    continue
            out.append({**it, "company": ev["company"], "fno_status": ev["fno_status"], "in_nifty500": ev["in_nifty500"], "official": nse52w.compare(it.get("high_52w"), it.get("low_52w"), official.get(it["symbol"]))})
        items = sorted(out, key=lambda r: (r.get("status") != "ok", -(r.get("distance_from_high_pct") or -1e9)))
    else:
        cond = and_(t.c.status.in_(("closed_inside", "closed_beyond")), t.c.age_bars <= max_age) if view == "touch" else and_(t.c.status == "confirmed", t.c.age_bars <= max_age)
        for e in evidence_rows(s, scan["scan_id"], universe, ("week52",), and_(cond, t.c.signal_type != "week52_state")):
            r = _row(e)
            if direction != "both" and r["direction"] != direction:
                continue
            if fno and r["fno_status"] != fno:
                continue
            if view == "events":
                conf = "confirmed" if confirm == 1 else r.get("confirm_2")
                if conf != "confirmed":
                    continue  # 2-close mode lists confirmed events only (pending / failed are visible in the fresh-breakout view)
                r["confirmation_status"] = conf
                if min_volume_ratio is not None and (r.get("volume_ratio") is None or r["volume_ratio"] < min_volume_ratio):
                    continue
            st = states.get(r["symbol"], {})
            r["state"] = {k: st.get(k) for k in ("status", "high_52w", "low_52w", "distance_from_high_pct", "distance_from_low_pct")}
            r["official"] = nse52w.compare(st.get("high_52w"), st.get("low_52w"), official.get(r["symbol"]))
            r["adjustment_basis"] = st.get("adjustment_basis")
            items.append(r)
        items.sort(key=lambda r: (r["age_bars"], -abs(r.get("distance_atr") or 0)))
    insufficient = sum(1 for m in states.values() if m.get("status") != "ok")
    return {
        "scan": scan_summary(scan),
        "count": len(items),
        "items": items,
        "view": view,
        "insufficient_history": insufficient,
        "official_report_date": ref_date if official else None,
        "note": (
            f"Previous {cfg.w52_lookback_sessions} completed daily sessions (current excluded), close beyond the level by {cfg.w52_buffer_atr} x ATR. "
            "Levels and closes are on the same basis: provider OHLC adjusted for splits/bonus issues (comparable with NSE's corporate-action-adjusted 52-week high/low). "
            "Actual unadjusted traded prices are not available from this provider. Fewer than 252 sessions -> INSUFFICIENT_HISTORY."
        ),
    }


@router.get("/52week/reference")
def week52_reference_status(request: Request) -> dict[str, Any]:
    s = svc(request)
    d = nse52w.latest_report_date(s.engine)
    return {"latest_report_date": d, "rows": len(nse52w.lookup(s.engine, d)) if d else 0, "source": nse52w.URL_TEMPLATE, "network_enabled": s.settings.network_enabled}


@router.post("/52week/reference/refresh")
def week52_reference_refresh(request: Request, report_date: date | None = Body(None, embed=True)) -> dict[str, Any]:
    s = svc(request)
    if not s.settings.network_enabled:
        return {"status": "skipped", "reason": "network disabled (PAS_NETWORK=0)"}
    d = report_date or s.calendar.latest_completed_bar_end("1d", now_ist()).date()
    return nse52w.refresh(s.engine, d)


# ------------------------------------------------------------------ evidence
@router.get("/evidence")
def evidence(
    request: Request,
    symbol: str | None = Query(None, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$"),
    universe: Universe = "all",
    timeframe: Timeframe = "1d",
    asof: date | None = None,
    source: str | None = Query(None, max_length=40),
    limit: int = Query(2000, ge=1, le=20000),
) -> dict[str, Any]:
    """Normalized evidence of the latest completed scan (all scanners, one shape)."""
    _check_asof(asof)
    s = svc(request)
    scan = find_latest_scan(s.engine, universe, timeframe, asof)
    if scan is None:
        return _empty(None, NO_SCAN)
    if not has_signals(scan):
        return _empty(scan, NO_SIGNALS)
    rows = evidence_rows(s, scan["scan_id"], universe, (source,) if source else None, symbol=symbol.upper() if symbol else None)
    return {"scan": scan_summary(scan), "count": len(rows), "items": rows[:limit]}


# ------------------------------------------------------------------ configuration
def _config(cfg, meta) -> dict[str, Any]:
    return {"version": cfg.version, "config_hash": config_hash(cfg), "parameters": [p for p in describe_config(cfg, meta) if p["name"] != "version"]}


@router.get("/config/signals")
def signals_config(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {**_config(s.signal_cfg, SIGNAL_PARAM_META), "detector_timeframes": {k: list(v) for k, v in DETECTOR_TIMEFRAMES.items()}}


@router.get("/config/vcp")
def vcp_config(request: Request) -> dict[str, Any]:
    s = svc(request)
    out = _config(s.vcp_cfg, VCP_PARAM_META)
    for p in out["parameters"]:
        p["basis"] = "source" if p["name"] in VCP_SOURCE_PARAMS else ("engineering" if p["description"].startswith(("ENGINEERING", "ASSUMPTION")) else "engineering")
    return out


@router.get("/config/ipo")
def ipo_config(request: Request) -> dict[str, Any]:
    from ..config import IPO_PARAM_META

    s = svc(request)
    return _config(s.ipo_cfg, IPO_PARAM_META)


@router.get("/config/confluence")
def confluence_config(request: Request) -> dict[str, Any]:
    s = svc(request)
    return _config(s.confluence_cfg, CONFLUENCE_PARAM_META)

