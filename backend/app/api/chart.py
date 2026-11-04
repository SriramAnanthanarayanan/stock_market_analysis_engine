"""Builds the Pattern Explorer payload: candles + backend geometry.

The frontend never re-detects anything. Candles are cut at the same
information cutoff the scan used, so geometry indices/timestamps line up with
the bars the detector saw. When the provider data changed afterwards (new bars
or a re-adjustment) the payload says so instead of silently mixing data.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select

from .. import database as db
from ..config import TIMEFRAME_SOURCE_INTERVAL
from ..data.ohlcv import prepare_series
from ..indicators import swing_points
from ..patterns.base import SPEC_BY_SLUG, SeriesContext
from ..patterns.engine import detect_series
from ..analytics.sr import compute_zones, nearest, zone_record
from ..scanner import detections_for_symbol, find_detection, geometry_for, get_scan, lifecycle_for, zones_for
from ..services import Services
from ..timeutil import asof_cutoff, epoch_s, parse_iso

BASIS_NOTE = (
    "Candles, patterns, S/R, candlesticks and breakout levels use raw OHLC (adjusted for splits/bonus issues only, "
    "not for dividends - the TradingView default). Returns and relative performance use the provider's dividend-adjusted close where available."
)


def data_source(s: Services, frame, scan: dict[str, Any] | None) -> dict[str, Any]:
    from ..providers.base import ADJ_CLOSE, PRICE_BASIS_LABELS

    basis = s.store.price_basis
    return {
        "provider": s.provider.name,
        "price_basis": basis,
        "price_basis_label": PRICE_BASIS_LABELS.get(basis, basis),
        "scan_price_basis": scan.get("price_basis") if scan else None,
        "adjusted_close_available": bool(ADJ_CLOSE in frame.columns and frame[ADJ_CLOSE].notna().any()),
        "note": BASIS_NOTE,
    }


SR_NOTE = ("Nearest support/resistance zones (up to 4 above and 4 below the latest close) from k=3 swing clusters over the last 250 completed bars; role flips after two closes beyond the zone by 0.1 ATR.")


def _scan_for_symbol(s: Services, symbol: str, timeframe: str, asof: date | None) -> dict[str, Any] | None:
    t, ss = db.scans, db.scan_symbols
    q = (
        select(t.c.scan_id)
        .join(ss, ss.c.scan_id == t.c.scan_id)
        .where(t.c.timeframe == timeframe)
        .where(t.c.state.in_(("completed", "interrupted")))
        .where(ss.c.symbol == symbol)
        .where(t.c.asof == asof.isoformat() if asof else t.c.asof.is_(None))
        .order_by(t.c.finished_at.desc(), t.c.created_at.desc())
        .limit(1)
    )
    with s.engine.connect() as conn:
        row = conn.execute(q).first()
    return get_scan(s.engine, row[0]) if row else None


def match_fields(d: dict[str, Any]) -> dict[str, Any]:
    spec = SPEC_BY_SLUG[d["pattern"]]
    from ..patterns.base import STATUS_LABELS, Status

    return {
        "pattern_id": d["detection_id"],
        "scan_id": d.get("scan_id"),
        "symbol": d["symbol"],
        "company": d.get("company"),
        "fno_status": d.get("fno_status") or "UNKNOWN",
        "in_nifty500": bool(d.get("in_nifty500")),
        "pattern": d["pattern"],
        "pattern_name": spec.name,
        "category": d["category"],
        "family": d.get("family"),
        "timeframe": d["timeframe"],
        "status": d["status"],
        "status_label": STATUS_LABELS[Status(d["status"])],
        "status_reason": d.get("status_reason"),
        "quality": d["quality"],
        "quality_label": d["quality_label"],
        "formation_start": d["formation_start"],
        "formation_end": d["formation_end"],
        "formation_length": d["formation_length"],
        "upper_touches": d.get("upper_touches"),
        "lower_touches": d.get("lower_touches"),
        "width_pct": _clean(d.get("width_pct")),
        "move_pct": _clean(d.get("move_pct")),
        "volume_ratio": _clean(d.get("volume_ratio")),
        "last_close": d.get("last_close"),
        "last_candle_time": d.get("last_candle_time"),
        "metrics": d.get("metrics", {}),
        "config_version": d["config_version"],
        "config_hash": d["config_hash"],
    }


def _geometry_points(geom: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for el in geom.get("elements", []):
        if el["type"] == "line":
            out.extend(el["points"])
        elif el["type"] == "band":
            out.extend(el["upper"] + el["lower"])
        elif el["type"] == "marker":
            out.append(el)
    return out


def _realign(item: dict[str, Any], pos: dict[int, int]) -> bool:
    """Move a stored detection onto the current candles when only the bar
    numbering changed (older history was prepended to the cache, e.g. the
    daily backfill to the provider's full history). Every candle point is
    matched by timestamp; projected points and stored indices move by the same
    offset. Returns False when the formation's candles no longer line up."""
    geo = item["geometry"]
    start, end = geo["formation"]["start"], geo["formation"]["end"]
    s_new, e_new = pos.get(int(start["t"])), pos.get(int(end["t"]))
    if s_new is None or e_new is None:
        return False
    offset = s_new - int(start["i"])
    if e_new - int(end["i"]) != offset:
        return False
    if offset == 0:
        return True

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if "t" in o and "i" in o and isinstance(o["i"], int):
                o["i"] = pos.get(int(o["t"]), o["i"] + offset) if not o.get("projected") else o["i"] + offset
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(geo)
    model = geo.get("lifecycle_model")
    if model and model.get("apex_index") is not None:
        model["apex_index"] = round(float(model["apex_index"]) + offset, 3)
    for ev in item.get("lifecycle") or []:
        if isinstance(ev.get("index"), int):
            ev["index"] += offset
    metrics = item.get("metrics") or {}
    for k, v in list(metrics.items()):
        if k.endswith("_index") and isinstance(v, (int, float)) and not isinstance(v, bool):
            metrics[k] = v + offset
    return True


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def build_chart(s: Services, symbol: str, timeframe: str, pattern_id: str | None, asof: date | None) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    stock = s.universe.stock(symbol)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r} (not in the NIFTY 500 / F&O universe)")
    notes: list[str] = []
    scan = None
    if pattern_id:
        det = find_detection(s.engine, pattern_id)
        if det is None:
            raise HTTPException(status_code=404, detail=f"pattern_id {pattern_id!r} not found")
        if det["symbol"] != symbol or det["timeframe"] != timeframe:
            raise HTTPException(
                status_code=404,
                detail=f"pattern_id {pattern_id!r} belongs to {det['symbol']} {det['timeframe']}, not {symbol} {timeframe}",
            )
        scan = get_scan(s.engine, det["scan_id"])
        asof = date.fromisoformat(scan["asof"]) if scan and scan.get("asof") else None
    else:
        scan = _scan_for_symbol(s, symbol, timeframe, asof)

    interval = TIMEFRAME_SOURCE_INTERVAL[timeframe]
    cutoff = parse_iso(scan["data_cutoff"]) if scan and scan.get("data_cutoff") else asof_cutoff(asof)
    raw = s.store.load_current(symbol, interval)
    if raw is None or s.market_data.needs_backfill(s.store.meta(symbol, interval), interval):
        # Missing cache, or a cache shallower than the provider's full history
        # (one-time backfill of this symbol; the old cache stays if it fails).
        s.market_data.ensure([symbol], interval, cutoff=cutoff)
        raw = s.store.load_current(symbol, interval)
    if raw is None or raw.empty:
        raise HTTPException(status_code=404, detail=f"no cached {interval} market data for {symbol}; refresh failed or not yet downloaded")

    ps = prepare_series(raw, symbol, timeframe, cutoff, s.calendar)
    frame = ps.frame
    n = len(frame)
    if n == 0:
        raise HTTPException(status_code=404, detail=f"no completed {timeframe} candles for {symbol} at the cutoff")
    times = frame.index
    newer = len(prepare_series(raw, symbol, timeframe, asof_cutoff(asof), s.calendar).frame) > n

    fno = s.universe.fno_status(symbol, asof)
    detections: list[dict[str, Any]] = []
    if scan is not None:
        source = "scan"
        for d in detections_for_symbol(s.engine, scan["scan_id"], symbol):
            if d["timeframe"] != timeframe:
                continue
            geom = geometry_for(s.engine, scan["scan_id"], d["detection_id"])
            if geom is None:
                continue
            item = match_fields(d)
            item.update(
                quality_components=d["quality_components"],
                gates=d["gates"],
                explanation=d["explanation"],
                lifecycle=lifecycle_for(s.engine, scan["scan_id"], d["detection_id"]),
                geometry=geom,
            )
            detections.append(item)
        if newer:
            notes.append("Newer completed candles exist than this scan used; the chart stops at the scan's data cutoff. Re-scan to update.")
        if scan.get("price_basis") and scan.get("price_basis") != s.store.price_basis:
            notes.append(
                f"This scan was computed on {scan.get('price_basis')} prices; the candles are now {s.store.price_basis}. "
                "Its geometry is not comparable with these candles - re-run the scan."
            )
    else:
        source = "adhoc"
        notes.append("No stored scan covers this symbol/timeframe/date; detections were computed on demand and are not persisted.")
        ctx = SeriesContext(symbol, timeframe, frame, s.cfg)
        for d in detect_series(ctx, s.calendar):
            rec = d.as_dict()
            rec.update(company=stock.get("company"), fno_status=fno, in_nifty500=stock.get("in_nifty500"), scan_id=None)
            item = match_fields(rec)
            item.update(
                quality_components=d.quality_components,
                gates=d.gates,
                explanation=d.explanation,
                lifecycle=[{"time": e["time"], "t": e["t"], "index": e["index"], "event": e["event"], "status": e["status"], "detail": e["detail"]} for e in d.events],
                geometry=d.geometry,
            )
            detections.append(item)

    # Alignment check: stored geometry indices/timestamps must match these candles.
    pos = {epoch_s(t): j for j, t in enumerate(times)}
    for d in detections:
        start = d["geometry"]["formation"]["start"]
        i = start["i"]
        if (i >= n or epoch_s(times[i]) != start["t"]) and not _realign(d, pos):
            notes.append(f"Detection {d['pattern_id']}: stored geometry does not align with current cached candles (provider history changed). Re-run the scan.")
            continue
        last = d["geometry"]["last_candle"]
        if last["i"] < n and abs(float(frame["close"].iloc[last["i"]]) - last["p"]) > 0.005 * max(abs(last["p"]), 1e-9):
            notes.append(f"Detection {d['pattern_id']}: provider prices were re-adjusted since the scan; geometry may be offset. Re-run the scan.")

    selected = pattern_id or (next((d["pattern_id"] for d in detections if d["status"] != "developing"), None) or (detections[0]["pattern_id"] if detections else None))
    max_len = max((d["formation_length"] for d in detections), default=0)
    earliest = min((d["geometry"]["formation"]["start"]["i"] for d in detections), default=n)
    count = max(s.settings.chart_min_candles, max_len + s.settings.chart_context_bars, min(n, s.settings.chart_max_candles))
    first = max(0, min(n - count, earliest - s.settings.chart_context_bars))

    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    candles = [
        {"t": epoch_s(times[i]), "i": i, "o": o[i], "h": h[i], "l": l[i], "c": c[i], "v": None if not np.isfinite(v[i]) else v[i]}
        for i in range(first, n)
    ]
    # Chart time scales are index based: every bar between the last candle and
    # the furthest projected point must exist (as whitespace) or projections
    # would be squeezed next to the last candle. Prefer the geometry's own
    # projected timestamps; fill gaps from the NSE trading calendar.
    proj = {p["i"]: p["t"] for d in detections for p in _geometry_points(d["geometry"]) if p.get("projected")}
    future: list[dict[str, int]] = []
    if proj:
        last_i = n - 1
        extra = max(proj) - last_i
        cal = s.calendar.project_bar_starts(times[-1], timeframe, extra)
        future = [{"i": last_i + k, "t": proj.get(last_i + k, epoch_s(cal[k - 1]))} for k in range(1, extra + 1)]

    k = s.cfg.swing_k_structure
    sp = swing_points(h, l, k)
    swings = [{"t": epoch_s(times[i]), "i": int(i), "p": float(h[i]), "kind": "high"} for i in sp.highs if i >= first]
    swings += [{"t": epoch_s(times[i]), "i": int(i), "p": float(l[i]), "kind": "low"} for i in sp.lows if i >= first]
    swings.sort(key=lambda x: (x["i"], x["kind"]))

    if scan is not None:
        zones = zones_for(s.engine, scan["scan_id"], symbol)
    else:
        zctx = SeriesContext(symbol, timeframe, frame, s.cfg)
        zones = [zone_record(z, zctx) for z in nearest(compute_zones(zctx), s.cfg.sr_display_per_side)]

    return {
        "symbol": symbol,
        "company": stock.get("company"),
        "fno_status": fno,
        "in_nifty500": bool(stock.get("in_nifty500")),
        "timeframe": timeframe,
        "asof": asof.isoformat() if asof else None,
        "source": source,
        "scan_id": scan["scan_id"] if scan else None,
        "data_cutoff": cutoff.isoformat() if cutoff else None,
        "candles": candles,
        "future_bars": future,
        "swing_points": swings,
        "swing_k": k,
        "detections": detections,
        "selected_detection_id": selected,
        "sr_zones": zones,
        "sr_zones_note": SR_NOTE,
        "newer_data_available": bool(newer),
        "notes": notes,
        "data_source": data_source(s, frame, scan),
    }
