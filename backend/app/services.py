"""Application service container (built once per process / test)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from typing import Any

from . import database as db
from .config import (
    DEFAULT_CONFIG,
    DEFAULT_CONFLUENCE_CONFIG,
    DEFAULT_INDEX_CONFIG,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STAGE_CONFIG,
    DEFAULT_TECH_CONFIG,
    DEFAULT_VCP_CONFIG,
    ConfluenceConfig,
    DetectionConfig,
    IndexConfig,
    Settings,
    SignalConfig,
    StageConfig,
    TechnicalConfig,
    VCPConfig,
    config_hash,
)
from .data.calendar import NSECalendar
from .data.store import MarketDataService, OhlcvStore
from .data.universe import UniverseService
from .providers.base import MarketDataProvider
from .scanner import ScanManager, register_config
from .timeutil import now_ist, parse_iso

log = logging.getLogger(__name__)


class Services:
    def __init__(
        self,
        settings: Settings,
        provider: MarketDataProvider | None = None,
        cfg: DetectionConfig = DEFAULT_CONFIG,
        tech_cfg: TechnicalConfig = DEFAULT_TECH_CONFIG,
        index_cfg: IndexConfig = DEFAULT_INDEX_CONFIG,
        index_history=None,
        stage_cfg: StageConfig = DEFAULT_STAGE_CONFIG,
        signal_cfg: SignalConfig = DEFAULT_SIGNAL_CONFIG,
        vcp_cfg: VCPConfig = DEFAULT_VCP_CONFIG,
        confluence_cfg: ConfluenceConfig = DEFAULT_CONFLUENCE_CONFIG,
    ):
        self.settings = settings
        self.cfg = cfg
        self.tech_cfg = tech_cfg
        self.index_cfg = index_cfg
        self.stage_cfg = stage_cfg
        self.signal_cfg = signal_cfg
        self.vcp_cfg = vcp_cfg
        self.confluence_cfg = confluence_cfg
        self.started_at = time.time()
        for d in (settings.data_dir, settings.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.engine = db.make_engine(settings.db_path)
        self._calendar = NSECalendar.load(settings.data_dir / "nse_holidays.json")
        self._calendar_lock = threading.Lock()
        if provider is None:
            from .providers.yahoo import YahooProvider

            provider = YahooProvider(
                batch_size=settings.yahoo_batch_size,
                max_attempts=settings.download_max_attempts,
                backoff_base_s=settings.download_backoff_base_s,
                cache_dir=settings.cache_dir / "yfinance",
            )
        self.provider = provider
        self.universe = UniverseService(self.engine, settings)
        self.store = OhlcvStore(settings.cache_dir, self.engine, provider.name, provider.price_basis)
        self.market_data = MarketDataService(provider, self.store, lambda: self.calendar, settings)
        self.scans = ScanManager(self)
        # E2: NSE index registry, official index history, index analysis / RRG.
        from .indices.analysis import IndexAnalysis
        from .indices.data import IndexDataService

        self.indices = IndexDataService(self.engine, settings, lambda: self.calendar, yahoo_market=self.market_data, history=index_history)
        self.index_analysis = IndexAnalysis(self.indices, index_cfg, tech_cfg)
        self.index_job: dict[str, Any] = {"state": "idle"}
        # E3: Stage Analysis / RS Line
        from .stage.service import StageService

        self.stage = StageService(self)
        self.index_analysis.stage_lookup = self.stage.index_stage_lookup
        self._index_lock = threading.Lock()
        self.scheduler = None  # set by startup when enabled
        self.bootstrap_state: dict[str, Any] = {"state": "pending"}

    @property
    def calendar(self) -> NSECalendar:
        return self._calendar

    def reload_calendar(self) -> None:
        with self._calendar_lock:
            self._calendar = NSECalendar.load(self.settings.data_dir / "nse_holidays.json")

    @property
    def config_hash(self) -> str:
        return config_hash(self.cfg)

    @property
    def tech_config_hash(self) -> str:
        return config_hash(self.tech_cfg)

    @property
    def stage_config_hash(self) -> str:
        return config_hash(self.stage_cfg)

    @property
    def signal_config_hash(self) -> str:
        return config_hash(self.signal_cfg)

    @property
    def vcp_config_hash(self) -> str:
        return config_hash(self.vcp_cfg)

    @property
    def confluence_config_hash(self) -> str:
        return config_hash(self.confluence_cfg)

    # ------------------------------------------------------------ lifecycle
    def startup(self, background: bool = True) -> None:
        version = db.migrate(self.engine)
        register_config(self.engine, self.cfg)
        register_config(self.engine, self.tech_cfg)
        register_config(self.engine, self.index_cfg)
        register_config(self.engine, self.stage_cfg)
        register_config(self.engine, self.signal_cfg)
        register_config(self.engine, self.vcp_cfg)
        register_config(self.engine, self.confluence_cfg)
        interrupted = self.scans.recover()
        log.info("database schema v%s ready (%s); %s interrupted scans marked", version, self.settings.db_path, interrupted)
        if background:
            threading.Thread(target=self._bootstrap, name="bootstrap", daemon=True).start()

    def _bootstrap(self) -> None:
        self.bootstrap_state = {"state": "running", "started_at": db.now_ist_iso()}
        try:
            uni = self.universe.ensure_loaded()
            hol = self._maybe_refresh_holidays()
            self.bootstrap_state.update({"universe": uni, "holidays": hol})
            if self.settings.network_enabled and self.settings.startup_scans:
                self.refresh_indices(background=True)
            if self.settings.scheduler_enabled:
                from .scheduler import ScanScheduler

                self.scheduler = ScanScheduler(self)
                self.scheduler.start(run_startup_scans=self.settings.startup_scans)
            self.bootstrap_state["state"] = "done"
        except Exception as exc:
            log.exception("bootstrap failed")
            self.bootstrap_state.update({"state": "error", "error": str(exc)})

    # ------------------------------------------------------------ E2 indices
    def refresh_indices(self, background: bool = True, force: bool = False, intraday: bool = True) -> dict[str, Any]:
        """Catalogue refresh -> official index history -> intraday mapping checks."""
        with self._index_lock:
            if self.index_job.get("state") == "running":
                return self.index_job
            self.index_job = {"state": "running", "started_at": db.now_ist_iso(), "phase": "catalogue"}

        def run() -> None:
            job = self.index_job
            try:
                job["catalogue"] = self.indices.refresh_catalogue()
                job["phase"] = "data"
                st = self.indices.refresh_all(force=force, progress=lambda m: job.update(progress=m))
                job["data"] = {"total": st.total, "failed": len(st.failed), "failures": dict(list(st.failed.items())[:20])}
                if intraday:
                    job["phase"] = "intraday mapping"
                    res = self.indices.verify_intraday(self.provider)
                    job["intraday"] = {"checked": len(res), "verified": sum(1 for r in res.values() if r["status"] == "verified")}
                job.update(state="done", phase="done", finished_at=db.now_ist_iso())
            except Exception as exc:
                log.exception("index refresh failed")
                job.update(state="error", error=f"{type(exc).__name__}: {exc}", finished_at=db.now_ist_iso())

        if background:
            threading.Thread(target=run, name="index-refresh", daemon=True).start()
        else:
            run()
        return self.index_job

    def _maybe_refresh_holidays(self) -> Any:
        year = now_ist().year
        status = self.universe.status().get("holidays", {})
        last = parse_iso(status.get("last_success"))
        if self.calendar.year_covered(year) and last is not None and now_ist() - last < timedelta(days=7):
            return "fresh"
        res = self.universe.refresh_holidays()
        if res.get("status") == "ok":
            self.reload_calendar()
        return res

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown()
        self.scans.shutdown()
        self.engine.dispose()
