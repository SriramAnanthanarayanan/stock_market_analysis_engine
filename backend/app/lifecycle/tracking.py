"""Cross-scan lifecycle tracking (P3, spec 10.1 and 17).

A *track* is one formation followed over successive live scans; its
``track_id`` is the lifecycle identity (terminal states are never reactivated).

Per symbol/timeframe and live scan:

1. Frozen tracks (a confirmed break happened) are re-evaluated on the new
   completed candles with their **frozen geometry** - remapped onto the current
   series by candle timestamp, never refitted. They stay visible for
   ``post_breakout_track_bars`` bars, then are archived; three closes back inside
   invalidate them (``failed_breakout``).
2. Fresh detections are assigned to tracks of the same pattern greedily by
   formation-window IoU (>= ``track_match_iou``) over all pairs. A detection
   whose best match is a frozen track is a refit distorted by the breakout bars
   and is suppressed.
3. A detection matched to an active track continues it; otherwise it starts a
   new track. A fresh confirmed break freezes the track.
4. Open tracks that no longer match any detection are closed (``disappeared``).
5. Tracks opened under another detection configuration (config hash) are
   closed as ``config_changed``: their geometry was produced by rules that no
   longer apply, so they are neither continued nor shown again.
6. Invalidated and archived tracks are closed and never reactivated. A fresh
   detection that is itself already terminal and overlaps such a closed track is
   the same failed structure seen again and is linked to it (no duplicate
   track); any other later formation gets a new track.

Outcomes after a breakout are measured on later candles (analytics.outcomes)
and stored with the track; they never influence detection.
"""

from __future__ import annotations

import copy
import hashlib
import threading
from typing import Any

from sqlalchemy import Engine, select, update

from .. import database as db
from ..analytics.outcomes import compute_outcome
from ..config import config_hash
from ..patterns.base import STATUS_LABELS, SeriesContext, Status
from ..patterns.engine import Detection, boundary_model_from_geometry
from ..patterns.shared import iou
from ..timeutil import epoch_s, iso
from .status import NECK_DOWN, NECK_UP, evaluate_lifecycle

BROKE = ("broke_above", "broke_below")
LIFECYCLE_MARKERS = ("breakout_up", "breakout_down", "invalidation")
OPEN_STATES = ("active", "frozen")
TERMINAL_REASONS = ("invalidated", "archived")


def epoch_index(ctx: SeriesContext) -> dict[int, int]:
    secs = ctx.times.tz_convert("UTC").as_unit("s").asi8
    return {int(t): i for i, t in enumerate(secs)}


def _points(el: dict[str, Any]) -> list[dict[str, Any]]:
    if el["type"] == "line":
        return el["points"]
    if el["type"] == "band":
        return el["upper"] + el["lower"]
    if el["type"] == "marker":
        return [el]
    return []


def remap_geometry(geom: dict[str, Any], pos: dict[int, int], ctx: SeriesContext) -> dict[str, Any] | None:
    """Move a stored geometry onto the current series by candle timestamp.

    Projections and lifecycle markers are dropped (rebuilt by ``refresh_geometry``).
    Returns None when a real candle of the formation no longer exists (provider
    history changed) - the frozen pattern can then not be evaluated honestly.
    """
    g = copy.deepcopy(geom)
    fe_old = int(g["formation"]["end"]["i"])
    span_old = fe_old - int(g["formation"]["start"]["i"])
    s_new, e_new = pos.get(int(g["formation"]["start"]["t"])), pos.get(int(g["formation"]["end"]["t"]))
    if s_new is None or e_new is None or e_new - s_new != span_old:
        return None  # candles inside the formation changed: bar spacing no longer matches
    kept = []
    for el in g["elements"]:
        if el["type"] == "line" and el.get("style") == "dashed":
            continue
        if el["type"] == "marker" and el["role"] in LIFECYCLE_MARKERS:
            continue
        keep = True
        for p in _points(el):
            i = pos.get(int(p["t"]))
            if i is None:
                if p.get("projected"):
                    keep = False  # a projected point (e.g. apex) not yet reached
                    break
                return None
            p["i"], p["projected"] = i, False
        if keep:
            kept.append(el)
    for key in ("start", "end"):
        i = pos.get(int(g["formation"][key]["t"]))
        if i is None:
            return None
        g["formation"][key]["i"] = i
    shift = int(g["formation"]["end"]["i"]) - fe_old
    model = g.get("lifecycle_model")
    if model and model.get("apex_index") is not None:
        model["apex_index"] = round(float(model["apex_index"]) + shift, 3)
    g["elements"] = kept
    g["last_candle"] = {"t": epoch_s(ctx.times[-1]), "i": ctx.last, "p": round(float(ctx.close[-1]), 4), "projected": False}
    return g


def refresh_geometry(g: dict[str, Any], ctx: SeriesContext, life) -> dict[str, Any]:
    """Re-add projections to the current last bar and the lifecycle markers."""
    model = boundary_model_from_geometry(g)
    fe = int(g["formation"]["end"]["i"])
    last = ctx.last

    def pt(i: int, p: float) -> dict[str, Any]:
        return {"t": epoch_s(ctx.times[i]), "i": i, "p": round(float(p), 4), "projected": False}

    if last > fe:
        lines = []
        if model.kind == NECK_DOWN:
            lines = [("neckline", "neckline", model.lower)]
        elif model.kind == NECK_UP:
            lines = [("neckline", "neckline", model.upper)]
        else:
            lines = [("upper", "upper_boundary", model.upper), ("lower", "lower_boundary", model.lower)]
        for eid, role, line in lines:
            g["elements"].append({"type": "line", "id": f"{eid}_projection", "role": role, "style": "dashed", "points": [pt(fe, line.at(fe)), pt(last, line.at(last))]})
    for ev in life.events:
        if ev.event in BROKE:
            up = ev.event == "broke_above"
            g["elements"].append({"type": "marker", "id": ev.event, "role": "breakout_up" if up else "breakout_down", **pt(ev.index, ctx.close[ev.index]), "label": "Broke Above" if up else "Broke Below"})
        elif ev.event == "invalidated":
            g["elements"].append({"type": "marker", "id": "invalidated", "role": "invalidation", **pt(ev.index, ctx.close[ev.index]), "label": f"Invalidated ({ev.detail.get('reason')})"})
    return g


def _snapshot(det: Detection) -> dict[str, Any]:
    d = det.as_dict()
    d.pop("geometry", None)
    d.pop("events", None)
    return d


def _track_id(det: Detection, scan_id: str) -> str:
    raw = f"{det.symbol}|{det.timeframe}|{det.pattern}|{det.formation_start}|{scan_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class Tracker:
    def __init__(self, engine: Engine, horizons: tuple[int, ...], price_basis: str | None = None):
        from ..providers.base import PRICE_BASIS_RAW

        self.engine = engine
        self.horizons = horizons
        self.price_basis = price_basis or PRICE_BASIS_RAW
        # Scans of overlapping universes may run concurrently; tracks are shared.
        self._lock = threading.Lock()

    # ------------------------------------------------------------ storage
    def open_tracks(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        t = db.pattern_tracks
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t).where((t.c.symbol == symbol) & (t.c.timeframe == timeframe) & (t.c.state.in_(OPEN_STATES))).order_by(t.c.first_detected_at)
            ).mappings().all()
        return [dict(r) for r in rows]

    def closed_terminal_tracks(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        t = db.pattern_tracks
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t.c.track_id, t.c.pattern, t.c.formation_start, t.c.formation_end).where(
                    (t.c.symbol == symbol) & (t.c.timeframe == timeframe) & (t.c.state == "closed") & (t.c.closed_reason.in_(TERMINAL_REASONS))
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    def _event(self, conn, track_id: str, scan_id: str, time: str, event: str, status: str | None, quality: float | None, det_id: str | None, detail: dict | None = None) -> None:
        conn.execute(
            db.track_events.insert().values(
                track_id=track_id, scan_id=scan_id, event_time=time, event=event, status=status, quality=quality, detection_id=det_id, detail_json=db.dumps(detail or {})
            )
        )

    def _known_events(self, conn, track_id: str) -> set[tuple[str, str]]:
        t = db.track_events
        rows = conn.execute(select(t.c.event, t.c.event_time).where(t.c.track_id == track_id)).all()
        return {(e, tm) for e, tm in rows}

    # ------------------------------------------------------------ process
    def process(self, scan_id: str, ctx: SeriesContext, dets: list[Detection]) -> list[Detection]:
        """Link this scan's detections to tracks; returns what the scan should store."""
        with self._lock:
            return self._process(scan_id, ctx, dets)

    def _process(self, scan_id: str, ctx: SeriesContext, dets: list[Detection]) -> list[Detection]:
        cfg = ctx.cfg
        pos = epoch_index(ctx)
        now = db.now_ist_iso()
        out: list[Detection] = []
        tracks = self.open_tracks(ctx.symbol, ctx.timeframe)
        frozen_windows: list[tuple[str, tuple[int, int]]] = []
        current = config_hash(cfg)
        with self.engine.begin() as conn:
            # 0. tracks of another detection configuration: closed, never continued
            for tr in [t for t in tracks if t["config_hash"] != current]:
                conn.execute(
                    update(db.pattern_tracks)
                    .where(db.pattern_tracks.c.track_id == tr["track_id"])
                    .values(state="closed", closed_reason="config_changed", closed_time=now, last_scan_id=scan_id, updated_at=now)
                )
                self._event(conn, tr["track_id"], scan_id, iso(ctx.times[ctx.last]), "closed", tr["status"], None, None, {"reason": f"detection config changed ({tr['config_hash']} -> {current}); the formation is re-detected under the new rules"})
            tracks = [t for t in tracks if t["config_hash"] == current]
            # 1. frozen tracks: re-evaluate frozen geometry, never refit
            for tr in [t for t in tracks if t["state"] == "frozen"]:
                tracked = self._evaluate_frozen(conn, scan_id, tr, ctx, pos, now)
                if tracked is not None:
                    out.append(tracked)
                    frozen_windows.append((tr["pattern"], (tracked.formation_start_index, tracked.formation_end_index)))
            active = [t for t in tracks if t["state"] == "active"]
            # 2/3. Assign each fresh detection to its best-overlapping track, greedily by
            # IoU over all pairs (order independent). Losing to a frozen track means the
            # detection is a refit of that broken formation and is suppressed; a distinct
            # formation that dedup kept next to it continues its own track.
            pairs: list[tuple[float, int, int, str]] = []  # (iou, kind: 0 frozen / 1 active, det index, track key)
            for di, det in enumerate(dets):
                window = (det.formation_start_index, det.formation_end_index)
                for fi, (p, w) in enumerate(frozen_windows):
                    ov = iou(window, w)
                    if p == det.pattern and ov >= cfg.track_match_iou:
                        pairs.append((ov, 0, di, str(fi)))
                for tr in active:
                    w = _window(tr, pos)
                    if tr["pattern"] != det.pattern or w is None:
                        continue
                    ov = iou(window, w)
                    if ov >= cfg.track_match_iou:
                        pairs.append((ov, 1, di, tr["track_id"]))
            pairs.sort(key=lambda x: (-x[0], x[1], x[2]))  # highest IoU first; frozen wins ties
            by_id = {t["track_id"]: t for t in active}
            assigned: dict[int, str | None] = {}  # det index -> active track id, or None = suppressed refit
            matched: set[str] = set()
            for _, kind, di, key in pairs:
                if di in assigned:
                    continue
                if kind == 0:
                    assigned[di] = None
                elif key not in matched:
                    assigned[di] = key
                    matched.add(key)
            closed_terminal = None
            for di, det in enumerate(dets):
                if di in assigned:
                    if assigned[di] is None:
                        continue  # refit of a frozen (broken) formation
                    tr = by_id[assigned[di]]
                    self._continue(conn, scan_id, tr, det, ctx, now)
                    det.extra["track_id"] = tr["track_id"]
                elif det.status == "invalidated" or det.archived:
                    # A formation that is already terminal on first sight may be the
                    # re-observation of a track closed earlier (same failed structure, seen
                    # again because it is still inside the lookback): link, never duplicate.
                    if closed_terminal is None:
                        closed_terminal = self.closed_terminal_tracks(ctx.symbol, ctx.timeframe)
                    prior = _best_overlap(det, closed_terminal, pos, cfg.track_match_iou)
                    det.extra["track_id"] = prior["track_id"] if prior else self._create(conn, scan_id, det, ctx, now)
                else:
                    det.extra["track_id"] = self._create(conn, scan_id, det, ctx, now)
                out.append(det)
            # 4. open (unfrozen) tracks without a matching detection
            for tr in active:
                if tr["track_id"] not in matched:
                    conn.execute(
                        update(db.pattern_tracks)
                        .where(db.pattern_tracks.c.track_id == tr["track_id"])
                        .values(state="closed", closed_reason="disappeared", closed_time=now, last_scan_id=scan_id, updated_at=now)
                    )
                    self._event(conn, tr["track_id"], scan_id, iso(ctx.times[ctx.last]), "disappeared", tr["status"], tr["quality_last"], None, {"detail": "no matching detection in this scan"})
        return out

    def _freeze_values(self, det: Detection, ctx: SeriesContext, now: str) -> dict[str, Any]:
        bi = det.metrics.get("breakout_index")
        return {
            "state": "frozen",
            "frozen_at": now,
            "breakout_time": iso(ctx.times[int(bi)]) if bi is not None else det.last_candle_time,
            "breakout_direction": det.metrics.get("breakout_direction"),
            "breakout_close": float(ctx.close[int(bi)]) if bi is not None else det.last_close,
        }

    def _create(self, conn, scan_id: str, det: Detection, ctx: SeriesContext, now: str) -> str:
        tid = _track_id(det, scan_id)
        values = {
            "track_id": tid,
            "symbol": det.symbol,
            "timeframe": det.timeframe,
            "pattern": det.pattern,
            "family": det.family,
            "state": "active",
            "status": det.status,
            "status_reason": det.status_reason,
            "first_scan_id": scan_id,
            "last_scan_id": scan_id,
            "first_detected_at": now,
            "last_detected_at": now,
            "first_detection_id": det.detection_id,
            "current_detection_id": det.detection_id,
            "formation_start": det.formation_start,
            "formation_end": det.formation_end,
            "quality_first": det.quality,
            "quality_last": det.quality,
            "quality_max": det.quality,
            "config_hash": det.config_hash,
            "config_version": det.config_version,
            "geometry_json": db.dumps(det.geometry),
            "detection_json": db.dumps(_snapshot(det)),
            "updated_at": now,
            "price_basis": self.price_basis,
        }
        if det.status in BROKE:
            values.update(self._freeze_values(det, ctx, now))
        values.update(_terminal_values(det, now))
        conn.execute(db.pattern_tracks.insert().values(**values))
        self._event(conn, tid, scan_id, det.formation_end, "first_detected", det.status, det.quality, det.detection_id, {"formation_start": det.formation_start})
        self._status_events(conn, tid, scan_id, det, set())
        return tid

    def _continue(self, conn, scan_id: str, tr: dict[str, Any], det: Detection, ctx: SeriesContext, now: str) -> None:
        values: dict[str, Any] = {
            "status": det.status,
            "status_reason": det.status_reason,
            "last_scan_id": scan_id,
            "last_detected_at": now,
            "current_detection_id": det.detection_id,
            "formation_start": det.formation_start,
            "formation_end": det.formation_end,
            "quality_last": det.quality,
            "quality_max": max(tr["quality_max"] or 0.0, det.quality),
            "geometry_json": db.dumps(det.geometry),
            "detection_json": db.dumps(_snapshot(det)),
            "config_hash": det.config_hash,
            "config_version": det.config_version,
            "updated_at": now,
        }
        if det.status in BROKE:
            values.update(self._freeze_values(det, ctx, now))
        values.update(_terminal_values(det, now))
        conn.execute(update(db.pattern_tracks).where(db.pattern_tracks.c.track_id == tr["track_id"]).values(**values))
        known = self._known_events(conn, tr["track_id"])
        if det.status != tr["status"]:
            self._event(conn, tr["track_id"], scan_id, det.last_candle_time, "status_change", det.status, det.quality, det.detection_id, {"from": tr["status"]})
        if abs((tr["quality_last"] or 0) - det.quality) >= 0.01:
            self._event(conn, tr["track_id"], scan_id, det.last_candle_time, "quality_change", det.status, det.quality, det.detection_id, {"from": tr["quality_last"]})
        if det.formation_end != tr["formation_end"]:
            self._event(conn, tr["track_id"], scan_id, det.last_candle_time, "boundary_update", det.status, det.quality, det.detection_id, {"formation_end": det.formation_end})
        self._status_events(conn, tr["track_id"], scan_id, det, known)

    def _status_events(self, conn, tid: str, scan_id: str, det: Detection, known: set[tuple[str, str]]) -> None:
        for ev in det.events:
            if ev["event"] in ("broke_above", "broke_below", "invalidated", "archived") and (ev["event"], ev["time"]) not in known:
                self._event(conn, tid, scan_id, ev["time"], ev["event"], ev["status"], det.quality, det.detection_id, ev.get("detail"))

    def _evaluate_frozen(self, conn, scan_id: str, tr: dict[str, Any], ctx: SeriesContext, pos: dict[int, int], now: str) -> Detection | None:
        geom = remap_geometry(db.loads(tr["geometry_json"]), pos, ctx)
        if geom is None:
            conn.execute(
                update(db.pattern_tracks)
                .where(db.pattern_tracks.c.track_id == tr["track_id"])
                .values(state="closed", closed_reason="data_changed", closed_time=now, last_scan_id=scan_id, updated_at=now)
            )
            self._event(conn, tr["track_id"], scan_id, iso(ctx.times[ctx.last]), "closed", tr["status"], None, None, {"reason": "provider history changed; frozen geometry no longer maps onto the candles"})
            return None
        fe = int(geom["formation"]["end"]["i"])
        life = evaluate_lifecycle(boundary_model_from_geometry(geom), ctx.close, ctx.atr, fe, ctx.last, ctx.cfg)
        geom = refresh_geometry(geom, ctx, life)
        known = self._known_events(conn, tr["track_id"])
        for ev in life.events:
            t = iso(ctx.times[ev.index])
            if ev.event in ("broke_above", "broke_below", "invalidated", "archived") and (ev.event, t) not in known:
                self._event(conn, tr["track_id"], scan_id, t, ev.event, ev.status, tr["quality_last"], tr["current_detection_id"], ev.detail)
        outcome = None
        if life.breakout_index is not None:
            outcome = compute_outcome(ctx, life.breakout_index, life.breakout_direction, self.horizons)
        values: dict[str, Any] = {"status": life.status, "status_reason": life.reason, "last_scan_id": scan_id, "updated_at": now, "outcome_json": db.dumps(outcome) if outcome else tr["outcome_json"]}
        if life.status == "invalidated":
            values.update(state="closed", closed_reason="invalidated", closed_time=now, invalidated_time=iso(ctx.times[life.events[-1].index]))
        elif life.archived:
            values.update(state="closed", closed_reason="archived", closed_time=now, archived_time=iso(ctx.times[life.events[-1].index]))
        elif life.breakout_index is None:
            # Re-evaluation found no confirmed break (e.g. revised provider closes): keep frozen, report honestly.
            values["status_reason"] = "frozen break no longer confirmed by current candles"
        conn.execute(update(db.pattern_tracks).where(db.pattern_tracks.c.track_id == tr["track_id"]).values(**values))

        snap = db.loads(tr["detection_json"])
        last_close = float(ctx.close[ctx.last])
        bars_since = ctx.last - life.breakout_index if life.breakout_index is not None else None
        metrics = dict(snap.get("metrics") or {})
        metrics.update({"tracked": True, "track_id": tr["track_id"], "bars_since_breakout": bars_since, "upper_now": life.upper_now, "lower_now": life.lower_now, "breakout_index": life.breakout_index, "breakout_direction": life.breakout_direction})
        events = [{"index": ev.index, "event": ev.event, "status": ev.status, "detail": ev.detail, "time": iso(ctx.times[ev.index]), "t": epoch_s(ctx.times[ev.index])} for ev in life.events]
        snap.update(
            status=life.status,
            status_label=STATUS_LABELS[Status(life.status)],
            status_reason=life.reason,
            formation_start_index=int(geom["formation"]["start"]["i"]),
            formation_end_index=fe,
            last_close=last_close,
            last_candle_time=iso(ctx.times[ctx.last]),
            metrics=metrics,
            archived=life.archived,
            extra={"track_id": tr["track_id"], "tracked": True},
        )
        geom["detection_id"] = snap["detection_id"]
        return Detection(**snap, geometry=geom, events=events)


def _t(text: str) -> int:
    import pandas as pd

    return int(pd.Timestamp(text).timestamp())


def _window(tr: dict[str, Any], pos: dict[int, int]) -> tuple[int, int] | None:
    s, e = pos.get(_t(tr["formation_start"])), pos.get(_t(tr["formation_end"]))
    return None if s is None or e is None else (s, e)


def _best_overlap(det: Detection, tracks: list[dict[str, Any]], pos: dict[int, int], threshold: float) -> dict[str, Any] | None:
    window = (det.formation_start_index, det.formation_end_index)
    best, best_iou = None, 0.0
    for tr in tracks:
        w = _window(tr, pos)
        if tr["pattern"] != det.pattern or w is None:
            continue
        ov = iou(window, w)
        if ov >= threshold and ov > best_iou:
            best, best_iou = tr, ov
    return best


def _event_time(det: Detection, event: str) -> str:
    times = [ev["time"] for ev in det.events if ev["event"] == event]
    return times[-1] if times else det.last_candle_time


def _terminal_values(det: Detection, now: str) -> dict[str, Any]:
    """Close a track whose detection is already terminal (invalidated or archived)."""
    if det.status == "invalidated":
        return {"state": "closed", "closed_reason": "invalidated", "closed_time": now, "invalidated_time": _event_time(det, "invalidated")}
    if det.archived:
        return {"state": "closed", "closed_reason": "archived", "closed_time": now, "archived_time": _event_time(det, "archived")}
    return {}
