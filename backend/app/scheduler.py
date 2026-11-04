"""APScheduler (v3) jobs for automatic scans and universe refresh.

Schedules (initial defaults): 1H every 30 min, 4H every 2 h, 1D every 6 h,
1W daily, universe/holiday refresh on weekdays at 08:30 IST.

* Intraday jobs run only on NSE trading days during the session and up to 15
  minutes after the close.
* A job only queues a scan when a new completed candle exists since the last
  successful scan with the current configuration (daily/weekly after a 30
  minute settle so the provider has the official close). Idle periods
  therefore cost nothing.
* Job IDs are fixed and ``replace_existing`` is set, so jobs are never
  duplicated; identical scans are also de-duplicated by the ScanManager.
* Startup scans run in the order 1D, 1W, 4H, 1H, each only when needed.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .scanner import ScanRequest, find_latest_scan
from .timeutil import IST, now_ist, parse_iso

if TYPE_CHECKING:  # pragma: no cover
    from .services import Services

log = logging.getLogger(__name__)

INTERVALS = {"1h": {"minutes": 30}, "4h": {"hours": 2}, "1d": {"hours": 6}, "1w": {"days": 1}}
STARTUP_ORDER = ("1d", "1w", "4h", "1h")
SCHEDULED_UNIVERSE = "all"


class ScanScheduler:
    def __init__(self, services: "Services"):
        self.s = services
        self.scheduler = BackgroundScheduler(
            timezone=IST, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
        )
        self.last_decisions: dict[str, str] = {}

    def start(self, run_startup_scans: bool = True) -> None:
        if self.scheduler.running:
            return
        for tf, kw in INTERVALS.items():
            self.scheduler.add_job(self.tick, IntervalTrigger(**kw, timezone=IST), args=[tf], id=f"scan_{tf}", replace_existing=True)
        # E2: official index catalogue + index history after the close has settled.
        self.scheduler.add_job(
            lambda: self.s.refresh_indices(background=False), CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=IST), id="index_refresh", replace_existing=True
        )
        self.scheduler.add_job(
            self.refresh_universe, CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=IST), id="universe_refresh", replace_existing=True
        )
        self.scheduler.start()
        if run_startup_scans:
            threading.Thread(target=self.startup_scans, name="startup-scans", daemon=True).start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def status(self) -> dict[str, Any]:
        return {
            "running": self.scheduler.running,
            "jobs": [
                {"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in self.scheduler.get_jobs()
            ],
            "last_decisions": dict(self.last_decisions),
        }

    # ------------------------------------------------------------ decisions
    def needs_scan(self, timeframe: str, now=None) -> tuple[bool, str]:
        now = now or now_ist()
        cal = self.s.calendar
        settle = timedelta(minutes=self.s.settings.daily_settle_minutes if timeframe in ("1d", "1w") else self.s.settings.intraday_settle_minutes)
        latest_end = cal.latest_completed_bar_end(timeframe, now)
        if now < latest_end + settle:
            # The newest bar has not settled yet; judge against the previous one.
            latest_end = cal.latest_completed_bar_end(timeframe, latest_end - timedelta(minutes=1))
        last = find_latest_scan(self.s.engine, SCHEDULED_UNIVERSE, timeframe, None)
        if last is None:
            return True, "no completed scan yet"
        if last.get("config_hash") != self.s.config_hash:
            return True, "detection configuration changed"
        if last.get("price_basis") != self.s.store.price_basis:
            return True, f"price basis changed ({last.get('price_basis')} -> {self.s.store.price_basis})"
        if last.get("tech_config_hash") != self.s.tech_config_hash:
            return True, "technical-analysis configuration changed"
        if timeframe == "1w" and last.get("stage_config_hash") != self.s.stage_config_hash:
            return True, "Stage Analysis configuration changed"
        if last.get("signal_config_hash") != self.s.signal_config_hash:
            return True, "signal scanner configuration changed (breakouts / 52-week / Volume Shocker)"
        if timeframe == "1d" and last.get("vcp_config_hash") != self.s.vcp_config_hash:
            return True, "VCP configuration changed"
        started = parse_iso(last.get("started_at"))
        if started is None or started < latest_end + settle:
            return True, f"new completed {timeframe} candle (ended {latest_end.isoformat()})"
        return False, "no new completed candle since the last scan"

    def tick(self, timeframe: str) -> str | None:
        now = now_ist()
        if timeframe in ("1h", "4h") and not self.s.calendar.in_session_window(now, after_close_minutes=15):
            self.last_decisions[timeframe] = f"{now:%Y-%m-%d %H:%M} skipped: outside NSE session window"
            return None
        need, why = self.needs_scan(timeframe, now)
        self.last_decisions[timeframe] = f"{now:%Y-%m-%d %H:%M} {'queued' if need else 'skipped'}: {why}"
        if not need:
            return None
        scan_id, _ = self.s.scans.submit(ScanRequest(universe=SCHEDULED_UNIVERSE, timeframe=timeframe, trigger="scheduler"))
        return scan_id

    def startup_scans(self) -> None:
        for tf in STARTUP_ORDER:
            try:
                need, why = self.needs_scan(tf)
                self.last_decisions[tf] = f"startup {'queued' if need else 'skipped'}: {why}"
                if not need:
                    continue
                scan_id, _ = self.s.scans.submit(ScanRequest(universe=SCHEDULED_UNIVERSE, timeframe=tf, trigger="startup"))
                self.s.scans.wait(scan_id)
            except Exception:
                log.exception("startup scan %s failed", tf)

    def refresh_universe(self) -> None:
        self.s.universe.refresh_nifty500()
        self.s.universe.refresh_fno()
        if self.s.universe.refresh_holidays().get("status") == "ok":
            self.s.reload_calendar()
        # Official NSE 52-week high/low report of the last completed session (optional
        # cross-check shown by the 52-week scanner; failures are only recorded).
        from .signals import nse52w

        res = nse52w.refresh(self.s.engine, self.s.calendar.latest_completed_bar_end("1d", now_ist()).date())
        self.last_decisions["nse_52w_report"] = f"{res.get('report_date')}: {res.get('status')} {res.get('error', '')}".strip()
