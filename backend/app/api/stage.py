"""E3 API: Stage Analysis watchlist / detail and the RS Line series for charts."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from .. import database as db
from ..config import STAGE_ASSUMPTION_PARAMS, STAGE_PARAM_META, config_hash, describe_config
from ..models import Universe
from ..scanner import find_latest_scan
from ..services import Services
from ..stage.analysis import STAGE_LABELS
from ..stage.rs_line import align_benchmark, compute_rs_line, latest_state, rs_length_for
from ..stage.service import StageError
from ..timeutil import asof_cutoff, epoch_s, iso, now_ist
from .technical import resolve_cutoff

router = APIRouter(prefix="/api")

SYMBOL = Query(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9&\-_.^ :/]+$")
STAGES = Literal["stage2_confirmed", "early_stage2", "stage1", "other", "insufficient_history", "data_unavailable"]
RS_FILTERS = Literal["above_zero", "below_zero", "improving", "deteriorating", "ma_rising", "ma_falling", "crossed_up"]


def svc(request: Request) -> Services:
    return request.app.state.services


def _check_asof(asof: date | None) -> None:
    if asof is not None and asof > now_ist().date():
        raise HTTPException(status_code=422, detail="asof cannot be in the future")


def _num(x: Any, nd: int = 6) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def config_block(s: Services) -> dict[str, Any]:
    params = [p for p in describe_config(s.stage_cfg, STAGE_PARAM_META) if p["name"] != "version"]
    from ..config import STAGE_USER_PARAMS

    for p in params:
        p["basis"] = "assumption" if p["name"] in STAGE_ASSUMPTION_PARAMS else "user" if p["name"] in STAGE_USER_PARAMS else "source"
    return {"version": s.stage_cfg.version, "config_hash": config_hash(s.stage_cfg), "parameters": params, "sources": ["stage_analysis.txt", "rs_line.txt"]}


def _rs_match(m: dict[str, Any], rs: str | None) -> bool:
    if not rs:
        return True
    return {
        "above_zero": m.get("rs_zero_line") == "above",
        "below_zero": m.get("rs_zero_line") == "below",
        "improving": m.get("rs_trend") == "improving",
        "deteriorating": m.get("rs_trend") == "deteriorating",
        "ma_rising": m.get("rs_sma_direction") == "rising",
        "ma_falling": m.get("rs_sma_direction") == "falling",
        "crossed_up": bool(m.get("rs_crossed_above_zero")),
    }[rs]


@router.get("/stage")
def stage_list(
    request: Request,
    instrument: Literal["stock", "index"] = "stock",
    universe: Universe = "nifty500",
    asof: date | None = None,
    stage: STAGES | None = None,
    rs: RS_FILTERS | None = None,
    setup: bool = Query(False, description="only instruments meeting every check of the entry setup"),
    limit: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """Stage Analysis watchlist (stage_analysis.txt s.9): ranked rows + summary counts."""
    _check_asof(asof)
    s = svc(request)
    scan = None
    if instrument == "index":
        rows = [r for r in s.stage.index_table(asof_cutoff(asof)) if r.get("symbol")]
    else:
        scan = find_latest_scan(s.engine, universe, "1w", asof)
        if scan is None or not scan.get("stage_config_hash"):
            return {
                "instrument": instrument,
                "scan": None,
                "summary": {},
                "count": 0,
                "items": [],
                "config": config_block(s),
                "note": "No weekly (1W) scan with Stage Analysis yet for this universe/date. Run a 1W scan; Stage Analysis uses completed weekly candles only.",
            }
        t = db.stage_results
        q = select(t).where(t.c.scan_id == scan["scan_id"])
        if universe == "nifty500":
            q = q.where(t.c.in_nifty500 == 1)
        elif universe == "fno":
            q = q.where(t.c.fno_status == "YES")
        elif universe == "non_fno":
            q = q.where((t.c.in_nifty500 == 1) & (t.c.fno_status == "NO"))
        with s.engine.connect() as conn:
            raw = conn.execute(q).mappings().all()
        rows = [
            {
                "symbol": r["symbol"], "company": r["company"], "instrument": "stock", "fno_status": r["fno_status"], "in_nifty500": bool(r["in_nifty500"]),
                "stage": r["stage"], "stage_label": STAGE_LABELS.get(r["stage"], r["stage"]), "stage_since": r["stage_since"], "previous_stage": r["previous_stage"],
                "rank_key": db.loads(r["rank_json"]), "metrics": db.loads(r["metrics_json"]) or {}, "gates": db.loads(r["gates_json"]) or [], "reasons": db.loads(r["reasons_json"]) or [],
                "config_version": r["config_version"], "config_hash": r["config_hash"],
            }
            for r in raw
        ]
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["stage"]] = summary.get(r["stage"], 0) + 1
    ranked = sorted(rows, key=lambda r: (r.get("rank_key") or [99.0], r["symbol"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    items = [r for r in ranked if (not stage or r["stage"] == stage) and _rs_match(r.get("metrics") or {}, rs) and (not setup or (r.get("metrics") or {}).get("setup_met"))]
    return {
        "instrument": instrument,
        "scan": {k: scan.get(k) for k in ("scan_id", "universe", "asof", "finished_at", "data_cutoff", "price_basis", "stage_config_version", "stage_config_hash")} if scan else None,
        "asof": asof.isoformat() if asof else None,
        "benchmark": s.stage_cfg.benchmark,
        "summary": {
            "stage1": summary.get("stage1", 0),
            "early_stage2": summary.get("early_stage2", 0),
            "stage2_confirmed": summary.get("stage2_confirmed", 0),
            "other": summary.get("other", 0),
            "insufficient_history": summary.get("insufficient_history", 0),
            "data_unavailable": summary.get("data_unavailable", 0),
            "entry_setup": sum(1 for r in rows if (r.get("metrics") or {}).get("setup_met")),
            "total": len(rows),
        },
        "count": len(items),
        "items": items[:limit],
        "config": config_block(s),
        "note": "Implements stage_analysis.txt with the RS Line of rs_line.txt (weekly: RS 52, benchmark NIFTY 50). Parameters marked ASSUMPTION are not specified in the source files. Screening aid, not a trade recommendation.",
    }


@router.get("/stage/symbol")
def stage_symbol(
    request: Request,
    symbol: str = SYMBOL,
    instrument: Literal["stock", "index"] = "stock",
    asof: date | None = None,
    start_t: int | None = Query(None, ge=0),
) -> dict[str, Any]:
    """Full Stage Analysis of one stock/index: metrics, gates (SOURCE / ASSUMPTION), reasons,
    stage history and the weekly overlay series (30W EMA, base resistance/support)."""
    _check_asof(asof)
    s = svc(request)
    if instrument == "stock" and s.universe.stock(symbol.strip().upper()) is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
    sym = symbol.strip().upper() if instrument == "stock" else symbol.strip()
    cutoff = asof_cutoff(asof)
    try:
        r = s.stage.evaluate(sym, instrument, cutoff)
    except StageError as exc:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": str(exc)})
    ss = r.pop("series", None)
    out: dict[str, Any] = {"symbol": sym, "instrument": instrument, "asof": asof.isoformat() if asof else None, "data_cutoff": iso(cutoff), "benchmark": s.stage_cfg.benchmark, **r, "config": config_block(s)}
    if ss is not None:
        n = len(ss.close)
        first = 0
        if start_t is not None:
            first = next((i for i, t in enumerate(ss.times) if epoch_s(t) >= start_t), n)
        times = [epoch_s(t) for t in ss.times[first:]]
        hist = []
        for i in range(1, n):
            if ss.stage[i] != ss.stage[i - 1]:
                hist.append({"week": iso(ss.times[i]), "t": epoch_s(ss.times[i]), "from": str(ss.stage[i - 1]), "to": str(ss.stage[i])})
        last = n - 1
        lo = max(0, last - s.stage_cfg.base_lookback_weeks)
        out["overlay"] = {
            "times": times,
            "ema30": [_num(x, 4) for x in ss.ema30[first:]],
            "base": {"high": _num(ss.base_high[last], 4), "low": _num(ss.base_low[last], 4), "t_start": epoch_s(ss.times[lo]), "t_end": epoch_s(ss.times[last])} if n else None,
        }
        out["history"] = hist[-30:]
    return out


@router.get("/rsline")
def rsline(
    request: Request,
    symbol: str = SYMBOL,
    instrument: Literal["stock", "index"] = "stock",
    timeframe: Literal["1h", "4h", "1d", "1w"] = "1w",
    asof: date | None = None,
    scan_id: str | None = Query(None, max_length=120),
    start_t: int | None = Query(None, ge=0),
    length: int | None = Query(None, ge=1, le=500, description="override the period (default per timeframe from the source setup)"),
    benchmark: Literal["NIFTY 50", "NIFTY 500"] | None = None,
) -> dict[str, Any]:
    """RS Line of rs_line.txt for a chart pane, on the chart's own timeframe and candles."""
    _check_asof(asof)
    s = svc(request)
    cfg = s.stage_cfg
    cutoff, _ = resolve_cutoff(s, timeframe, asof, scan_id) if instrument == "stock" else (asof_cutoff(asof), None)
    sym = symbol.strip().upper() if instrument == "stock" else symbol.strip()
    bench_code = benchmark or cfg.benchmark
    try:
        frame = s.stage.frame(sym, instrument, timeframe, cutoff)
        bench = s.stage.benchmark(timeframe, cutoff, bench_code)
    except StageError as exc:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": str(exc)})
    if frame.empty:
        raise HTTPException(status_code=404, detail={"status": "data_unavailable", "message": "no completed candles"})
    n_len = length or rs_length_for(timeframe, cfg)
    al = align_benchmark(frame.index, bench, cfg.benchmark_max_ffill_bars)
    rs = compute_rs_line(frame["close"].to_numpy(dtype=float), al.values, n_len, cfg, al)
    n = len(frame)
    first = 0
    if start_t is not None:
        first = next((i for i, t in enumerate(frame.index) if epoch_s(t) >= start_t), n)
    times = [epoch_s(t) for t in frame.index[first:]]
    st = latest_state(rs)
    ref_i = n - 1 - n_len
    status = "ok" if st["rs"] is not None else ("insufficient_history" if n <= n_len else "benchmark_gap")
    notes = []
    if length and length != rs_length_for(timeframe, cfg):
        notes.append(f"Period {length} overrides the source setup ({rs_length_for(timeframe, cfg)} on {timeframe}).")
    if bench_code != cfg.benchmark:
        notes.append(f"Benchmark {bench_code} instead of the source benchmark {cfg.benchmark}: RS then measures performance against a different index and is not comparable with the original setup.")
    if timeframe in ("1h", "4h"):
        notes.append(f"stage_analysis.txt configures weekly (52) and daily (63) only; {timeframe} uses the rs_line.txt default period {cfg.rs_length_pine_default}.")
    return {
        "symbol": sym,
        "instrument": instrument,
        "timeframe": timeframe,
        "benchmark": bench_code,
        "length": n_len,
        "status": status,
        "data_cutoff": iso(cutoff),
        "times": times,
        "rs": [_num(x) for x in rs.res[first:]],
        "rs_sma": [_num(x) for x in rs.sma_res[first:]],
        "positive": [None if not np.isfinite(x) else bool(x > 0) for x in rs.res[first:]],
        "bubbles": [{"t": epoch_s(frame.index[i]), "kind": "bullish" if rs.pos_div[i] else "bearish"} for i in range(first, n) if rs.pos_div[i] or rs.neg_div[i]],
        "latest": st,
        "reference": {"date": iso(frame.index[ref_i]), "label": f"RS-{n_len} reference, {frame.index[ref_i].strftime('%d-%m-%Y')}"} if ref_i >= 0 else None,
        "alignment": {"exact": al.exact, "carried": al.carried, "missing": al.missing},
        "params": {"trend_base": cfg.rs_trend_base, "ma_length": cfg.rs_ma_length, "price_sma_length": cfg.price_sma_length, "rising_bars": cfg.rs_ma_rising_bars},
        "config_version": cfg.version,
        "config_hash": config_hash(cfg),
        "notes": notes,
        "source": "rs_line.txt: res = close / close[length] / (benchmark / benchmark[length]) - 1",
    }
