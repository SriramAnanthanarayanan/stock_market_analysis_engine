"""Scan execution and persisted scan results.

* Identical scans (universe, timeframe, as-of, pattern filter) never overlap:
  submitting one that is already queued/running returns the existing scan ID.
* Each symbol's detections are committed as soon as they are computed, so
  partial results survive a crash or restart (the scan is then marked
  ``interrupted``). Completed results of earlier scans are never touched.
* Every scan and detection records the configuration version and hash.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, and_, func, select, update

from . import database as db
from .config import TIMEFRAME_SOURCE_INTERVAL, config_hash, config_to_dict
from .data.ohlcv import prepare_series
from .data.universe import StockInfo
from .analytics.sr import compute_zones, nearest, zone_record
from .lifecycle.tracking import Tracker
from .patterns.base import SeriesContext
from .patterns.engine import Detection, detect_series, min_bars_required
from .timeutil import asof_cutoff, iso, now_ist

if TYPE_CHECKING:  # pragma: no cover
    from .services import Services

log = logging.getLogger(__name__)

UNIVERSE_SUPERSETS: dict[str, list[str]] = {
    "nifty500": ["nifty500", "all"],
    "fno": ["fno", "all"],
    "non_fno": ["non_fno", "nifty500", "all"],
    "all": ["all"],
}
ACTIVE_STATES = ("queued", "running")


@dataclass(frozen=True)
class ScanRequest:
    universe: str
    timeframe: str
    asof: date | None = None
    patterns: tuple[str, ...] | None = None
    full_refresh: bool = False
    trigger: str = "manual"

    def key(self) -> tuple:
        return (self.universe, self.timeframe, self.asof, self.patterns)


def register_config(engine: Engine, cfg) -> str:
    h = config_hash(cfg)
    with engine.begin() as conn:
        exists = conn.execute(select(db.config_versions.c.config_hash).where(db.config_versions.c.config_hash == h)).first()
        if not exists:
            conn.execute(
                db.config_versions.insert().values(
                    config_hash=h, version=cfg.version, params_json=db.dumps(config_to_dict(cfg)), created_at=db.now_ist_iso()
                )
            )
    return h


class ScanManager:
    def __init__(self, services: "Services"):
        self.s = services
        self._lock = threading.Lock()
        self._active: dict[tuple, str] = {}
        self._futures: dict[str, Future] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(1, services.settings.scan_workers), thread_name_prefix="scan")
        self.tracker = Tracker(services.engine, services.settings.outcome_horizons, services.store.price_basis)

    # ------------------------------------------------------------ control
    def submit(self, req: ScanRequest) -> tuple[str, bool]:
        """Queue a scan. Returns (scan_id, created). Never overlaps an identical scan."""
        with self._lock:
            key = req.key()
            if key in self._active:
                return self._active[key], False
            scan_id = f"{req.timeframe}-{req.universe}-{req.asof.isoformat() if req.asof else 'live'}-{now_ist():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
            cfg = self.s.cfg
            with self.s.engine.begin() as conn:
                conn.execute(
                    db.scans.insert().values(
                        scan_id=scan_id,
                        universe=req.universe,
                        timeframe=req.timeframe,
                        asof=req.asof.isoformat() if req.asof else None,
                        patterns_json=db.dumps(sorted(req.patterns)) if req.patterns else None,
                        config_hash=config_hash(cfg),
                        config_version=cfg.version,
                        price_basis=self.s.store.price_basis,
                        tech_config_version=self.s.tech_cfg.version,
                        tech_config_hash=config_hash(self.s.tech_cfg),
                        stage_config_version=self.s.stage_cfg.version if req.timeframe == "1w" else None,
                        stage_config_hash=config_hash(self.s.stage_cfg) if req.timeframe == "1w" else None,
                        signal_config_version=self.s.signal_cfg.version,
                        signal_config_hash=config_hash(self.s.signal_cfg),
                        vcp_config_version=self.s.vcp_cfg.version if req.timeframe == "1d" else None,
                        vcp_config_hash=config_hash(self.s.vcp_cfg) if req.timeframe == "1d" else None,
                        trigger=req.trigger,
                        state="queued",
                        phase="queued",
                        created_at=db.now_ist_iso(),
                        total_symbols=0,
                        processed_symbols=0,
                        successful_symbols=0,
                        failed_symbols=0,
                        detection_count=0,
                    )
                )
            self._active[key] = scan_id
            self._futures[scan_id] = self._pool.submit(self._run, scan_id, req)
            return scan_id, True

    def wait(self, scan_id: str, timeout: float | None = None) -> None:
        fut = self._futures.get(scan_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def run_now(self, req: ScanRequest, timeout: float | None = None) -> str:
        scan_id, _ = self.submit(req)
        self.wait(scan_id, timeout)
        return scan_id

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._active.values())
        return [s for s in (get_scan(self.s.engine, i) for i in ids) if s]

    def active_for(self, universe: str, timeframe: str, asof: date | None) -> dict[str, Any] | None:
        with self._lock:
            ids = [sid for (u, tf, a, _p), sid in self._active.items() if u == universe and tf == timeframe and a == asof]
        return get_scan(self.s.engine, ids[0]) if ids else None

    def recover(self) -> int:
        """Mark scans left queued/running by a previous process as interrupted."""
        with self.s.engine.begin() as conn:
            res = conn.execute(
                update(db.scans)
                .where(db.scans.c.state.in_(ACTIVE_STATES))
                .values(state="interrupted", phase="interrupted", message="application stopped during the scan; partial results kept")
            )
        return res.rowcount or 0

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ worker
    def _update(self, scan_id: str, **values: Any) -> None:
        with self.s.engine.begin() as conn:
            conn.execute(update(db.scans).where(db.scans.c.scan_id == scan_id).values(**values))

    def _run(self, scan_id: str, req: ScanRequest) -> None:
        try:
            self._execute(scan_id, req)
        except Exception as exc:  # recorded; never propagated to API callers
            log.exception("scan %s failed", scan_id)
            self._update(scan_id, state="failed", phase="failed", finished_at=db.now_ist_iso(), message=f"{type(exc).__name__}: {exc}"[:2000])
        finally:
            with self._lock:
                self._active.pop(req.key(), None)

    def _execute(self, scan_id: str, req: ScanRequest) -> None:
        s = self.s
        now = now_ist()
        cutoff = asof_cutoff(req.asof, now)
        self._update(scan_id, state="running", phase="universe", started_at=db.now_ist_iso(), data_cutoff=iso(cutoff))
        members = s.universe.members(req.universe, req.asof)
        stocks = members.stocks
        self._update(scan_id, total_symbols=len(stocks), warnings_json=db.dumps(members.warnings))
        if not stocks:
            self._update(
                scan_id,
                state="completed",
                phase="done",
                finished_at=db.now_ist_iso(),
                message="universe is empty: " + "; ".join(members.warnings),
            )
            return

        interval = TIMEFRAME_SOURCE_INTERVAL[req.timeframe]
        self._update(scan_id, phase="data", message=f"refreshing {interval} data for {len(stocks)} symbols")
        report = s.market_data.ensure(
            [st.symbol for st in stocks],
            interval,
            now=now,
            cutoff=cutoff,
            full_refresh=req.full_refresh,
            scan_id=scan_id,
            progress=lambda m: self._update(scan_id, message=m),
        )
        data_msg = (
            f"data {interval}: {report.fresh} fresh, {report.incremental} incremental, {report.full} full, "
            f"{report.readjusted} re-adjusted, {len(report.failed)} failed"
            + (f" ({report.skipped_recent_failures} skipped after a recent provider failure)" if report.skipped_recent_failures else "")
            + (f"; {report.backfilled} extended to full history" if report.backfilled else "")
        )
        self._update(scan_id, phase="detect", message=data_msg)

        patterns = set(req.patterns) if req.patterns else None
        # Cross-scan lifecycle tracking follows live, full-catalogue scans only
        # (historical as-of scans and pattern-filtered scans are snapshots).
        track = req.asof is None and patterns is None
        min_bars = min_bars_required(s.cfg)
        # E3: weekly scans also classify Stage Analysis (benchmark: official NIFTY 50 weekly).
        stage_bench = None
        stage_note = None
        if req.timeframe == "1w":
            try:
                stage_bench = s.indices.frame(s.stage_cfg.benchmark, "1w", cutoff)["close"]
            except Exception as exc:
                stage_note = f"Stage Analysis benchmark {s.stage_cfg.benchmark} unavailable: {exc}"
                log.warning("scan %s: %s", scan_id, stage_note)
        # Advanced: weekly scans also place each stock on the RRG-style rotation vs the
        # RRG benchmark (existing rrg.rrg_series, same weekly candles) for confluence evidence.
        rrg_bench = None
        if req.timeframe == "1w":
            try:
                rrg_bench = s.indices.frame(s.index_cfg.rrg_default_benchmark, "1w", cutoff)["close"]
            except Exception as exc:
                log.warning("scan %s: RRG benchmark %s unavailable: %s", scan_id, s.index_cfg.rrg_default_benchmark, exc)
        processed = ok = failed = n_det = 0
        for st in stocks:
            status, err, bars, last_ts, note = "ok", None, 0, None, None
            try:
                raw = s.store.load_current(st.symbol, interval)
                if raw is None or raw.empty:
                    status, err = "no_data", report.failed.get(st.symbol, "no cached data")
                else:
                    if st.symbol in report.failed:
                        note = f"refresh failed, analysed cached data: {report.failed[st.symbol][:200]}"
                    ps = prepare_series(raw, st.symbol, req.timeframe, cutoff, s.calendar)
                    bars = len(ps.frame)
                    last_ts = iso(ps.frame.index[-1]) if bars else None
                    if bars < min_bars:
                        note = ((note + "; ") if note else "") + f"insufficient history: {bars} completed bars < {min_bars}"
                    ctx = SeriesContext(st.symbol, req.timeframe, ps.frame, s.cfg)
                    dets = detect_series(ctx, s.calendar, patterns)
                    if track and bars:
                        dets = self.tracker.process(scan_id, ctx, dets)
                    self._store_detections(scan_id, st, dets)
                    if bars:
                        self._store_zones(scan_id, st.symbol, req.timeframe, ctx)
                        stage_res = None
                        if req.timeframe == "1w":
                            stage_res = self._store_stage(scan_id, st, ps.frame, stage_bench, stage_note)
                        tech = None
                        try:
                            tech = self._store_technical(scan_id, st, req.timeframe, ps.frame)
                        except Exception as texc:  # technical analytics never fail the pattern scan
                            log.exception("scan %s: technical analysis for %s failed", scan_id, st.symbol)
                            note = ((note + "; ") if note else "") + f"technical analysis failed: {type(texc).__name__}: {texc}"[:300]
                        try:
                            self._store_signals(scan_id, st, req.timeframe, ps.frame, dets, tech, stage_res, rrg_bench)
                        except Exception as sexc:  # advanced scanners never fail the pattern scan
                            log.exception("scan %s: signal evidence for %s failed", scan_id, st.symbol)
                            note = ((note + "; ") if note else "") + f"signal scanners failed: {type(sexc).__name__}: {sexc}"[:300]
                    n_det += len(dets)
            except Exception as exc:
                log.exception("scan %s: symbol %s failed", scan_id, st.symbol)
                status, err = "error", f"{type(exc).__name__}: {exc}"
            processed += 1
            if status == "ok":
                ok += 1
            else:
                failed += 1
            self._store_symbol(scan_id, st, status, err, bars, last_ts, note)
            if processed % 10 == 0 or processed == len(stocks):
                self._update(scan_id, processed_symbols=processed, successful_symbols=ok, failed_symbols=failed, detection_count=n_det)
        self._update(
            scan_id,
            state="completed",
            phase="done",
            finished_at=db.now_ist_iso(),
            processed_symbols=processed,
            successful_symbols=ok,
            failed_symbols=failed,
            detection_count=n_det,
            message=data_msg,
        )
        # Confluence snapshot of the new evidence (persisted; the dashboard never re-runs scanners).
        if req.timeframe in ("1d", "1w"):
            from .confluence.service import refresh_after_scan

            refresh_after_scan(s, req.universe, req.timeframe, req.asof)

    def _store_symbol(self, scan_id: str, st: StockInfo, status: str, err: str | None, bars: int, last_ts: str | None, note: str | None) -> None:
        with self.s.engine.begin() as conn:
            conn.execute(
                db.scan_symbols.insert().values(
                    scan_id=scan_id,
                    symbol=st.symbol,
                    status=status,
                    error=err,
                    bars=bars,
                    last_candle_time=last_ts,
                    fno_status=st.fno_status,
                    in_nifty500=int(st.in_nifty500),
                    data_note=note,
                )
            )

    def _store_stage(self, scan_id: str, st: StockInfo, frame, bench, note: str | None) -> None:
        """E3: Stage Analysis of the latest completed week (never fails the scan)."""
        from .stage.analysis import STAGE_LABELS, evaluate

        cfg = self.s.stage_cfg
        try:
            if bench is None:
                res = {"stage": "data_unavailable", "metrics": {}, "gates": [], "reasons": [note or "benchmark unavailable"], "rank_key": None, "stage_since": None, "previous_stage": None}
            else:
                res = evaluate(frame, bench, cfg)
        except Exception as exc:  # recorded per symbol
            log.exception("scan %s: stage analysis for %s failed", scan_id, st.symbol)
            res = {"stage": "data_unavailable", "metrics": {}, "gates": [], "reasons": [f"stage analysis failed: {type(exc).__name__}: {exc}"], "rank_key": None, "stage_since": None, "previous_stage": None}
        with self.s.engine.begin() as conn:
            conn.execute(
                db.stage_results.insert().values(
                    scan_id=scan_id,
                    symbol=st.symbol,
                    company=st.company,
                    fno_status=st.fno_status,
                    in_nifty500=int(st.in_nifty500),
                    week=res["metrics"].get("week"),
                    stage=res["stage"],
                    stage_since=res.get("stage_since"),
                    previous_stage=res.get("previous_stage"),
                    rank_json=db.dumps(res.get("rank_key")),
                    metrics_json=db.dumps(res["metrics"]),
                    gates_json=db.dumps(res["gates"]),
                    reasons_json=db.dumps(res["reasons"]),
                    config_version=cfg.version,
                    config_hash=config_hash(cfg),
                )
            )
        return res

    def _store_technical(self, scan_id: str, st: StockInfo, timeframe: str, frame) -> None:
        """E1: indicator snapshot + candlestick / EMA-cross / RSI-divergence / breakout signals."""
        from .technical import analyze

        tcfg = self.s.tech_cfg
        res = analyze(frame, timeframe, tcfg, self.s.cfg, cache_key=("stock", st.symbol, timeframe, self.s.store.price_basis))
        th = config_hash(tcfg)
        snap = res.snapshot
        with self.s.engine.begin() as conn:
            conn.execute(
                db.tech_snapshots.insert().values(
                    scan_id=scan_id,
                    symbol=st.symbol,
                    timeframe=timeframe,
                    company=st.company,
                    fno_status=st.fno_status,
                    in_nifty500=int(st.in_nifty500),
                    last_candle_time=snap.get("last_candle_time"),
                    close=snap.get("close"),
                    status=res.status,
                    metrics_json=db.dumps(snap),
                    config_version=tcfg.version,
                    config_hash=th,
                )
            )
            rows = [
                {
                    "scan_id": scan_id,
                    "symbol": st.symbol,
                    "company": st.company,
                    "fno_status": st.fno_status,
                    "in_nifty500": int(st.in_nifty500),
                    "timeframe": timeframe,
                    "category": sig["category"],
                    "kind": sig["kind"],
                    "direction": sig["direction"],
                    "signal_time": sig["time"],
                    "age_bars": int(sig["age_bars"]),
                    "status": sig.get("status", "confirmed"),
                    "confirm_closes": sig.get("confirm_closes"),
                    "metrics_json": db.dumps(sig),
                    "config_version": tcfg.version,
                    "config_hash": th,
                }
                for sig in res.signals
            ]
            if rows:
                conn.execute(db.tech_signals.insert(), rows)
        return res

    def _store_signals(self, scan_id: str, st: StockInfo, timeframe: str, frame, dets: list[Detection], tech, stage_res, rrg_bench) -> int:
        """Advanced scanners (fresh breakouts, 52-week, Volume Shocker, VCP) and the
        normalized evidence of every scanner of this scan (docs/confluence-engine.md).
        Existing results (patterns, Marubozu, EMA alignment, Stage, RS) are re-labelled,
        never re-detected."""
        from .signals.evidence import Publisher
        from .signals.scan import run_signals
        from .technical import indicator_series

        s = self.s
        n = len(frame)
        if n == 0:
            return 0
        times = frame.index
        cal = s.calendar

        def valid_until(i: int, k: int) -> str | None:
            j = i + k
            if j < n:
                return iso(times[j])
            proj = cal.project_bar_starts(times[-1], timeframe, j - (n - 1))
            return iso(proj[-1]) if proj else None

        pub = Publisher(st.symbol, timeframe, n, valid_until, s.confluence_cfg.freshness_sessions)
        ser = tech.series if tech is not None and tech.series else indicator_series(frame, s.tech_cfg, cache_key=("stock", st.symbol, timeframe, s.store.price_basis))
        scfg = s.signal_cfg
        sv, sh = scfg.version, config_hash(scfg)
        outs = run_signals(frame, timeframe, ser["atr"], ser["ema"].get(20), ser["ema"].get(50), scfg, st.symbol)
        if timeframe == "1d":
            pub.fresh_breakouts(outs.fresh_events, outs.fresh_touches, sv, sh)
            pub.week52(outs.w52_events, outs.w52_touches, outs.w52_state, sv, sh)
            self._store_vcp(scan_id, st, frame, ser, pub)
        pub.volume_shocks(outs.shocks, sv, sh)
        pub.volume_state(outs.volume_state, sv, sh)
        if tech is not None:
            tv, th = s.tech_cfg.version, config_hash(s.tech_cfg)
            pub.marubozu(tech.signals, tv, th)
            pub.divergences(tech.signals, tv, th)
            pub.ema_alignment(tech.snapshot, tech.status, tv, th)
        pub.patterns(dets, times, s.cfg.version, config_hash(s.cfg))
        if stage_res is not None:
            pub.stage(stage_res, s.stage_cfg.version, config_hash(s.stage_cfg))
        if rrg_bench is not None:
            from .rrg import rrg_entry

            entry = rrg_entry(st.symbol, frame["close"], rrg_bench, s.index_cfg, s.index_cfg.rrg_trail_weeks)
            pub.rrg(entry, s.index_cfg.rrg_default_benchmark, s.index_cfg.version, config_hash(s.index_cfg))
        created = db.now_ist_iso()
        seen: set[str] = set()
        rows = []
        for ev in pub.rows:
            if ev["evidence_id"] in seen:
                continue
            seen.add(ev["evidence_id"])
            rows.append(
                {
                    "scan_id": scan_id,
                    "evidence_id": ev["evidence_id"],
                    "symbol": st.symbol,
                    "company": st.company,
                    "fno_status": st.fno_status,
                    "in_nifty500": int(st.in_nifty500),
                    "timeframe": timeframe,
                    "source": ev["source"],
                    "signal_type": ev["signal_type"],
                    "category": ev["category"],
                    "kind": ev["kind"],
                    "direction": ev["direction"],
                    "signal_time": ev["signal_time"],
                    "signal_t": ev["signal_t"],
                    "age_bars": ev["age_bars"],
                    "status": ev["status"],
                    "lifecycle": ev["lifecycle"],
                    "valid_until": ev["valid_until"],
                    "expiry_rule": ev["expiry_rule"],
                    "quality": ev["quality"],
                    "strength": ev["strength"],
                    "underlying_id": ev["underlying_id"],
                    "metrics_json": db.dumps(ev["metrics"]),
                    "explanation_json": db.dumps(ev["explanation"]),
                    "config_version": ev["config_version"],
                    "config_hash": ev["config_hash"],
                    "created_at": created,
                }
            )
        if rows:
            with s.engine.begin() as conn:
                conn.execute(db.signal_evidence.insert(), rows)
        return len(rows)

    def _store_vcp(self, scan_id: str, st: StockInfo, frame, ser, pub) -> None:
        """VCP detection (daily scans); implemented in app.vcp (P2)."""
        try:
            from .vcp.detector import detect_vcp
        except ImportError:  # pragma: no cover - P2 module
            return
        res = detect_vcp(frame, self.s.vcp_cfg, symbol=st.symbol, atr=ser["atr"], config_hash=config_hash(self.s.vcp_cfg))
        if res is None:
            return
        self._persist_vcp(scan_id, st, res)
        from .config import config_hash as _h

        pub.vcp(res, self.s.vcp_cfg.version, _h(self.s.vcp_cfg))

    def _persist_vcp(self, scan_id: str, st: StockInfo, v: dict) -> None:
        created = db.now_ist_iso()
        with self.s.engine.begin() as conn:
            conn.execute(
                db.vcp_formations.insert().values(
                    scan_id=scan_id,
                    vcp_id=v["vcp_id"],
                    symbol=st.symbol,
                    company=st.company,
                    fno_status=st.fno_status,
                    in_nifty500=int(st.in_nifty500),
                    timeframe="1d",
                    status=v["status"],
                    status_reason=v.get("status_reason"),
                    qualified=int(bool(v.get("qualified"))),
                    quality=float(v.get("quality") or 0.0),
                    contractions=int(v.get("contractions") or 0),
                    base_start=v["base_start"],
                    base_end=v["base_end"],
                    base_sessions=int(v["base_sessions"]),
                    pivot=v.get("pivot"),
                    pivot_time=v.get("pivot_time"),
                    final_depth_pct=v.get("final_depth_pct"),
                    depths_json=db.dumps(v.get("depths_pct")),
                    last_close=v.get("last_close"),
                    pivot_distance_pct=v.get("pivot_distance_pct"),
                    breakout_time=v.get("breakout_time"),
                    last_candle_time=v.get("last_candle_time"),
                    metrics_json=db.dumps(v.get("metrics", {})),
                    quality_json=db.dumps(v.get("quality_components", [])),
                    gates_json=db.dumps(v.get("gates", [])),
                    explanation_json=db.dumps(v.get("explanation", [])),
                    geometry_json=db.dumps(v.get("geometry", {})),
                    config_version=self.s.vcp_cfg.version,
                    config_hash=config_hash(self.s.vcp_cfg),
                    created_at=created,
                )
            )
            rows = [
                {
                    "scan_id": scan_id,
                    "vcp_id": v["vcp_id"],
                    "seq": k + 1,
                    "start_time": c["start_time"],
                    "end_time": c["end_time"],
                    "start_price": c["start_price"],
                    "end_price": c["end_price"],
                    "depth": c["depth"],
                    "depth_pct": c["depth_pct"],
                    "bars": c["bars"],
                    "avg_volume": c.get("avg_volume"),
                    "min_volume": c.get("min_volume"),
                    "volume_vs_avg50": c.get("volume_vs_avg50"),
                    "provisional": int(bool(c.get("provisional"))),
                }
                for k, c in enumerate(v.get("contraction_list", []))
            ]
            if rows:
                conn.execute(db.vcp_contractions.insert(), rows)

    def _store_zones(self, scan_id: str, symbol: str, timeframe: str, ctx: SeriesContext) -> None:
        """Persist the nearest S/R zones above and below the latest close."""
        zones = nearest(compute_zones(ctx), self.s.cfg.sr_display_per_side)
        if not zones:
            return
        rows = []
        for z in zones:
            r = zone_record(z, ctx)
            rows.append(
                {
                    "scan_id": scan_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "zone_id": r["zone_id"],
                    "p_low": r["p_low"],
                    "p_high": r["p_high"],
                    "role": r["role"],
                    "status": r["status"],
                    "side": r["side"],
                    "touches": r["touches"],
                    "distance_pct": r["distance_pct"],
                    "distance_atr": r["distance_atr"],
                    "first_touch_time": r["first_touch_time"],
                    "last_touch_time": r["last_touch_time"],
                    "broken_time": r["broken_time"],
                    "retest_time": r["retest_time"],
                    "detail_json": db.dumps(r),
                }
            )
        with self.s.engine.begin() as conn:
            conn.execute(db.sr_zones.insert(), rows)

    def _store_detections(self, scan_id: str, st: StockInfo, dets: list[Detection]) -> None:
        if not dets:
            return
        created = db.now_ist_iso()
        with self.s.engine.begin() as conn:
            for d in dets:
                conn.execute(
                    db.detections.insert().values(
                        scan_id=scan_id,
                        detection_id=d.detection_id,
                        symbol=d.symbol,
                        company=st.company,
                        fno_status=st.fno_status,
                        in_nifty500=int(st.in_nifty500),
                        timeframe=d.timeframe,
                        pattern=d.pattern,
                        category=d.category,
                        status=d.status,
                        status_reason=d.status_reason,
                        quality=d.quality,
                        quality_label=d.quality_label,
                        formation_start=d.formation_start,
                        formation_end=d.formation_end,
                        formation_length=d.formation_length,
                        upper_touches=d.upper_touches,
                        lower_touches=d.lower_touches,
                        width_pct=d.width_pct,
                        move_pct=d.move_pct,
                        volume_ratio=d.volume_ratio,
                        last_close=d.last_close,
                        last_candle_time=d.last_candle_time,
                        metrics_json=db.dumps({**d.metrics, "family": d.family, "formation_start_index": d.formation_start_index, "formation_end_index": d.formation_end_index, "archived": d.archived}),
                        quality_json=db.dumps(d.quality_components),
                        explanation_json=db.dumps({"lines": d.explanation, "gates": d.gates}),
                        config_hash=d.config_hash,
                        config_version=d.config_version,
                        created_at=created,
                        track_id=d.extra.get("track_id"),
                    )
                )
                conn.execute(
                    db.pattern_geometry.insert().values(
                        scan_id=scan_id, detection_id=d.detection_id, schema_version=d.geometry["schema_version"], geometry_json=db.dumps(d.geometry)
                    )
                )
                if d.events:
                    conn.execute(
                        db.lifecycle_events.insert(),
                        [
                            {
                                "scan_id": scan_id,
                                "detection_id": d.detection_id,
                                "event_time": ev["time"],
                                "event": ev["event"],
                                "status": ev["status"],
                                "detail_json": db.dumps({**ev.get("detail", {}), "index": ev["index"], "t": ev["t"]}),
                            }
                            for ev in d.events
                        ],
                    )


# --------------------------------------------------------------- queries


def _scan_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["warnings"] = db.loads(d.pop("warnings_json", None)) or []
    d["patterns"] = db.loads(d.pop("patterns_json", None))
    return d


def get_scan(engine: Engine, scan_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(select(db.scans).where(db.scans.c.scan_id == scan_id)).mappings().first()
    return _scan_row(row) if row else None


def find_latest_scan(
    engine: Engine,
    universe: str,
    timeframe: str,
    asof: date | None,
    pattern: str | None = None,
    states: tuple[str, ...] = ("completed",),
) -> dict[str, Any] | None:
    """Latest scan whose universe covers ``universe`` (superset allowed)."""
    q = (
        select(db.scans)
        .where(db.scans.c.timeframe == timeframe)
        .where(db.scans.c.universe.in_(UNIVERSE_SUPERSETS[universe]))
        .where(db.scans.c.state.in_(states))
        .where(db.scans.c.asof == asof.isoformat() if asof else db.scans.c.asof.is_(None))
        .order_by(db.scans.c.finished_at.desc(), db.scans.c.created_at.desc())
    )
    with engine.connect() as conn:
        for row in conn.execute(q).mappings():
            pats = db.loads(row["patterns_json"])
            if pats is None or pattern is None or pattern in pats:
                return _scan_row(row)
    return None


def _universe_clause(universe: str):
    t = db.detections
    if universe == "nifty500":
        return t.c.in_nifty500 == 1
    if universe == "fno":
        return t.c.fno_status == "YES"
    if universe == "non_fno":
        return and_(t.c.in_nifty500 == 1, t.c.fno_status == "NO")
    return None


def detection_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    metrics = db.loads(d.pop("metrics_json", None)) or {}
    d["metrics"] = metrics
    d["family"] = metrics.get("family")
    d["formation_start_index"] = metrics.get("formation_start_index")
    d["formation_end_index"] = metrics.get("formation_end_index")
    d["quality_components"] = db.loads(d.pop("quality_json", None)) or []
    exp = db.loads(d.pop("explanation_json", None)) or {}
    d["explanation"] = exp.get("lines", [])
    d["gates"] = exp.get("gates", [])
    return d


def scan_matches(
    engine: Engine,
    scan_id: str,
    universe: str = "all",
    patterns: list[str] | None = None,
    categories: list[str] | None = None,
    statuses: list[str] | None = None,
    min_quality: float = 0.0,
    include_developing: bool = False,
) -> list[dict[str, Any]]:
    t = db.detections
    q = select(t).where(t.c.scan_id == scan_id).where(t.c.quality >= min_quality)
    clause = _universe_clause(universe)
    if clause is not None:
        q = q.where(clause)
    if patterns:
        q = q.where(t.c.pattern.in_(patterns))
    if categories:
        q = q.where(t.c.category.in_(categories))
    if statuses:
        q = q.where(t.c.status.in_(statuses))
    elif not include_developing:
        q = q.where(t.c.status != "developing")
    q = q.order_by(t.c.quality.desc(), t.c.symbol, t.c.pattern)
    with engine.connect() as conn:
        return [detection_row(r) for r in conn.execute(q).mappings()]


def scan_counts(engine: Engine, scan_id: str, universe: str = "all") -> dict[str, Any]:
    t = db.detections
    clause = _universe_clause(universe)
    base = [t.c.scan_id == scan_id] + ([clause] if clause is not None else [])
    out: dict[str, Any] = {}
    with engine.connect() as conn:
        for col in ("pattern", "status", "fno_status", "category"):
            rows = conn.execute(select(getattr(t.c, col), func.count()).where(*base).group_by(getattr(t.c, col))).all()
            out[f"by_{col}"] = {k: v for k, v in rows}
    return out


def scan_symbol_counts(engine: Engine, scan_id: str, universe: str = "all") -> dict[str, int]:
    t = db.scan_symbols
    q = select(t.c.status, func.count()).where(t.c.scan_id == scan_id)
    if universe == "nifty500":
        q = q.where(t.c.in_nifty500 == 1)
    elif universe == "fno":
        q = q.where(t.c.fno_status == "YES")
    elif universe == "non_fno":
        q = q.where(and_(t.c.in_nifty500 == 1, t.c.fno_status == "NO"))
    with engine.connect() as conn:
        return {k: v for k, v in conn.execute(q.group_by(t.c.status)).all()}


def find_detection(engine: Engine, detection_id: str, scan_id: str | None = None) -> dict[str, Any] | None:
    t = db.detections
    q = select(t).where(t.c.detection_id == detection_id)
    if scan_id:
        q = q.where(t.c.scan_id == scan_id)
    q = q.order_by(t.c.created_at.desc())
    with engine.connect() as conn:
        row = conn.execute(q).mappings().first()
    return detection_row(row) if row else None


def detections_for_symbol(engine: Engine, scan_id: str, symbol: str) -> list[dict[str, Any]]:
    t = db.detections
    with engine.connect() as conn:
        rows = conn.execute(
            select(t).where((t.c.scan_id == scan_id) & (t.c.symbol == symbol)).order_by(t.c.quality.desc())
        ).mappings().all()
    return [detection_row(r) for r in rows]


def geometry_for(engine: Engine, scan_id: str, detection_id: str) -> dict[str, Any] | None:
    t = db.pattern_geometry
    with engine.connect() as conn:
        row = conn.execute(select(t.c.geometry_json).where((t.c.scan_id == scan_id) & (t.c.detection_id == detection_id))).first()
    return db.loads(row[0]) if row else None


def lifecycle_for(engine: Engine, scan_id: str, detection_id: str) -> list[dict[str, Any]]:
    t = db.lifecycle_events
    with engine.connect() as conn:
        rows = conn.execute(
            select(t).where((t.c.scan_id == scan_id) & (t.c.detection_id == detection_id)).order_by(t.c.id)
        ).mappings().all()
    out = []
    for r in rows:
        detail = db.loads(r["detail_json"]) or {}
        out.append({"time": r["event_time"], "event": r["event"], "status": r["status"], "t": detail.pop("t", None), "index": detail.pop("index", None), "detail": detail})
    return out


def scan_symbol(engine: Engine, scan_id: str, symbol: str) -> dict[str, Any] | None:
    t = db.scan_symbols
    with engine.connect() as conn:
        row = conn.execute(select(t).where((t.c.scan_id == scan_id) & (t.c.symbol == symbol))).mappings().first()
    return dict(row) if row else None


def history(
    engine: Engine,
    symbol: str | None = None,
    pattern: str | None = None,
    timeframe: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    fno_status: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Stored detection records across scans (newest first)."""
    t = db.detections
    q = select(t)
    if symbol:
        q = q.where(t.c.symbol == symbol)
    if pattern:
        q = q.where(t.c.pattern == pattern)
    if timeframe:
        q = q.where(t.c.timeframe == timeframe)
    if fno_status:
        q = q.where(t.c.fno_status == fno_status)
    if status:
        q = q.where(t.c.status == status)
    if date_from:
        q = q.where(t.c.formation_end >= date_from.isoformat())
    if date_to:
        q = q.where(t.c.formation_end <= f"{date_to.isoformat()}T23:59:59+05:30")
    q = q.order_by(t.c.created_at.desc(), t.c.quality.desc()).limit(limit)
    with engine.connect() as conn:
        return [detection_row(r) for r in conn.execute(q).mappings()]


def recent_scans(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(select(db.scans).order_by(db.scans.c.created_at.desc()).limit(limit)).mappings().all()
    return [_scan_row(r) for r in rows]


def last_scans_by_timeframe(engine: Engine, universe: str = "all", asof: date | None = None) -> dict[str, dict[str, Any] | None]:
    from .config import TIMEFRAMES

    return {tf: find_latest_scan(engine, universe, tf, asof) for tf in TIMEFRAMES}




def zones_for(engine: Engine, scan_id: str, symbol: str) -> list[dict[str, Any]]:
    t = db.sr_zones
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.detail_json).where((t.c.scan_id == scan_id) & (t.c.symbol == symbol))).all()
    zones = [db.loads(r[0]) for r in rows]
    return sorted(zones, key=lambda z: -z["p_high"])
