"""Confluence snapshots: score every stock of a scan from its persisted evidence.

A snapshot is identified by its inputs (primary scan, weekly context scan,
confluence configuration). It is computed once - after a scan completes, on
first use, or on a manual refresh - and stored (``confluence_runs`` /
``confluence_snapshots``), so opening the dashboard never re-runs a scanner.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Callable

from sqlalchemy import literal_column, select

from .. import database as db
from ..config import config_hash
from ..scanner import find_latest_scan
from .engine import inputs_key, score_symbol, summarize

log = logging.getLogger(__name__)
CONTEXT_SOURCES = ("stage", "rs_line", "rrg", "sector")
# One computation at a time: the post-scan hook and a dashboard request that
# arrives meanwhile must not both build a snapshot for the same inputs.
_SNAPSHOT_LOCK = threading.Lock()


def _cat(row: dict[str, Any], key: str) -> float:
    for c in row["explanation"]:
        if c["key"] == key:
            return c["score"] if c["available"] else 0.0
    return 0.0


def _combo(row: dict[str, Any]) -> str | None:
    return (row.get("combo") or {}).get("type")


# Predefined analytical screens (spec 8.8). Filters, not trade recommendations.
SCREENS: list[dict[str, Any]] = [
    {"key": "breakout_volume", "label": "Fresh Breakout + Volume Shock", "direction": "bullish", "description": "A fresh 10-day or 52-week breakout and a volume shock on the same completed session (combination window).", "test": lambda r: _combo(r) in ("breakout_volume", "breakout_marubozu_volume")},
    {"key": "breakout_marubozu_volume", "label": "Fresh Breakout + Bullish Marubozu + Volume Shock", "direction": "bullish", "description": "All three on the same completed session - the combination that earns the full bonus.", "test": lambda r: _combo(r) == "breakout_marubozu_volume"},
    {"key": "vcp_near_pivot", "label": "VCP + Near Pivot", "direction": "bullish", "description": "A qualified VCP whose close is within the near-pivot distance below the pivot.", "test": lambda r: r["summary"].get("vcp_status") == "near_pivot"},
    {"key": "vcp_breakout", "label": "VCP Breakout + Volume Expansion", "direction": "bullish", "description": "A close above the VCP pivot on volume >= the breakout volume ratio.", "test": lambda r: r["summary"].get("vcp_status") == "breakout"},
    {"key": "week52_rs", "label": "52-week High + Strong Relative Strength", "direction": "bullish", "description": "A fresh 52-week high breakout and a relative-strength category score >= 0.6.", "test": lambda r: bool(r["summary"].get("week52")) and _cat(r, "relative_strength") >= 0.6},
    {"key": "stage2_ema", "label": "Stage 2 + EMA Bullish Alignment", "direction": "bullish", "description": "Your Stage Analysis says Early Stage 2 or Stage 2 Confirmed and the EMAs are in bullish order (full or partial).", "test": lambda r: r["summary"].get("stage") in ("stage2_confirmed", "early_stage2") and (r["summary"].get("ema_alignment") or "").startswith("bullish")},
    {"key": "pattern_volume", "label": "Price Action Pattern + Volume Expansion", "direction": "bullish", "description": "An active bullish price-action pattern and a fresh volume shock or high breakout volume.", "test": lambda r: bool(r["summary"].get("pattern")) and _cat(r, "setup") >= 0.5 and next((c.get("parts", {}).get("volume", 0) for c in r["explanation"] if c["key"] == "volume_candle"), 0) > 0},
    {"key": "multiple_bullish", "label": "Multiple Independent Bullish Evidence", "direction": "bullish", "description": "At least three of the five categories score >= 0.5 and the stock is ranked (enough coverage).", "test": lambda r: r["ranked"] and sum(1 for c in r["explanation"] if c["available"] and c["score"] >= 0.5) >= 3},
    {"key": "about_to_breakout", "label": "10D About to Break Out + Bullish Trend", "direction": "bullish", "description": "Close Approaching / Very Near / Touching the previous 10-day high (before the breakout) with a bullish EMA alignment.", "test": lambda r: r["summary"].get("setup_state") in ("approaching", "very_near", "touched") and (r["summary"].get("ema_alignment") or "").startswith("bullish")},
    {"key": "ipo_base", "label": "IPO Base Near Pivot / Breakout", "direction": "bullish", "description": "A post-listing base near its pivot or breaking out above it on volume (IPO base scanner).", "test": lambda r: r["summary"].get("ipo_status") in ("near_pivot", "breakout")},
    {"key": "bearish_breakdown", "label": "Bearish Breakdown Confluence", "direction": "bearish", "description": "A fresh breakdown (10-day, 52-week or pattern) with volume / candle confirmation (bearish scoring).", "test": lambda r: _cat(r, "trigger") >= 0.5 and _cat(r, "volume_candle") >= 0.3},
]
SCREEN_BY_KEY = {s["key"]: s for s in SCREENS}


def _evidence(s, scan_id: str, sources: tuple[str, ...] | None = None) -> dict[str, list[dict[str, Any]]]:
    t = db.signal_evidence
    q = select(t).where(t.c.scan_id == scan_id)
    if sources:
        q = q.where(t.c.source.in_(sources))
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with s.engine.connect() as conn:
        for r in conn.execute(q).mappings():
            d = dict(r)
            d["metrics"] = db.loads(d.pop("metrics_json", None)) or {}
            d["explanation"] = db.loads(d.pop("explanation_json", None)) or []
            out[d["symbol"]].append(d)
    return out


def find_inputs(s, universe: str, timeframe: str, asof: date | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    primary = find_latest_scan(s.engine, universe, timeframe, asof)
    if primary is None or not primary.get("signal_config_hash"):
        return primary, None
    context = primary if timeframe == "1w" else find_latest_scan(s.engine, universe, "1w", asof)
    if context is not None and not context.get("signal_config_hash"):
        context = None
    return primary, context


def snapshot(s, universe: str, timeframe: str, asof: date | None, force: bool = False) -> dict[str, Any] | None:
    """The stored snapshot for the current inputs (computed on first use / when forced)."""
    primary, context = find_inputs(s, universe, timeframe, asof)
    if primary is None or not primary.get("signal_config_hash"):
        return None
    cfg = s.confluence_cfg
    chash = config_hash(cfg)
    key = inputs_key(primary["scan_id"], context["scan_id"] if context else None, chash, timeframe)
    t = db.confluence_runs
    with _SNAPSHOT_LOCK:
        with s.engine.connect() as conn:
            # newest first; rowid breaks ties inside the same second (created_at has 1 s resolution)
            row = conn.execute(select(t).where(t.c.inputs_key == key).order_by(t.c.created_at.desc(), literal_column("rowid").desc())).mappings().first()
        if row is not None and not force:
            return {**dict(row), "counts": db.loads(row["counts_json"]), "primary": primary, "context": context}
        run = compute(s, primary, context, timeframe, asof, key)
    return {**run, "primary": primary, "context": context}


def compute(s, primary: dict[str, Any], context: dict[str, Any] | None, timeframe: str, asof: date | None, key: str) -> dict[str, Any]:
    cfg = s.confluence_cfg
    chash = config_hash(cfg)
    prim = _evidence(s, primary["scan_id"])
    ctx = _evidence(s, context["scan_id"], CONTEXT_SOURCES) if context else {}
    snapshot_id = f"cf-{timeframe}-{uuid.uuid4().hex[:10]}"
    rows = []
    counts: dict[str, int] = defaultdict(int)
    for sym, evs in prim.items():
        cevs = ctx.get(sym, [])
        meta = evs[0]
        for direction in ("bullish", "bearish"):
            res = score_symbol(evs, cevs, direction, cfg, timeframe)
            summ = summarize(evs, cevs, direction, cfg)
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "symbol": sym,
                    "direction": direction,
                    "company": meta.get("company"),
                    "fno_status": meta.get("fno_status"),
                    "in_nifty500": int(bool(meta.get("in_nifty500"))),
                    "timeframe": timeframe,
                    "close": summ.get("close"),
                    "score": res["score"],
                    "base_score": res["base_score"],
                    "bonus": res["bonus"],
                    "band": res["band"],
                    "coverage": res["coverage"],
                    "ranked": int(res["ranked"]),
                    "active_signals": res["active_signals"],
                    "latest_signal_time": summ.get("latest_signal_time"),
                    "summary_json": db.dumps({**summ, "combo": res["combo"]}),
                    "explanation_json": db.dumps(res["categories"]),
                }
            )
            if res["ranked"]:
                counts[f"{direction}:{res['band']}"] += 1
    run = {
        "snapshot_id": snapshot_id,
        "created_at": db.now_ist_iso(),
        "timeframe": timeframe,
        "asof": asof.isoformat() if asof else None,
        "primary_scan_id": primary["scan_id"],
        "context_scan_id": context["scan_id"] if context else None,
        "inputs_key": key,
        "config_version": cfg.version,
        "config_hash": chash,
        "counts_json": db.dumps(dict(counts)),
    }
    with s.engine.begin() as conn:
        conn.execute(db.confluence_runs.insert().values(**run))
        if rows:
            conn.execute(db.confluence_snapshots.insert(), rows)
    return {**run, "counts": dict(counts)}


def snapshot_rows(s, snapshot_id: str) -> list[dict[str, Any]]:
    t = db.confluence_snapshots
    with s.engine.connect() as conn:
        rows = conn.execute(select(t).where(t.c.snapshot_id == snapshot_id)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = db.loads(d.pop("summary_json")) or {}
        d["explanation"] = db.loads(d.pop("explanation_json")) or []
        d["combo"] = d["summary"].pop("combo", None)
        d["ranked"] = bool(d["ranked"])
        d["in_nifty500"] = bool(d["in_nifty500"])
        out.append(d)
    return out


def refresh_after_scan(s, universe: str, timeframe: str, asof: date | None) -> None:
    """Called when a scan completes: a 1D scan changes the daily snapshot, a weekly
    scan changes the context of the daily snapshot and the weekly snapshot."""
    for tf in (("1d", "1w") if timeframe == "1w" else ("1d",) if timeframe == "1d" else ()):
        try:
            snapshot(s, universe, tf, asof)
        except Exception:  # never fails a scan
            log.exception("confluence snapshot %s %s failed", universe, tf)


def screen_filter(key: str | None) -> tuple[str | None, Callable[[dict[str, Any]], bool] | None]:
    if not key:
        return None, None
    sc = SCREEN_BY_KEY.get(key)
    if sc is None:
        raise KeyError(key)
    return sc["direction"], sc["test"]
