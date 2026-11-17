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
* After the close: a fixed weekday job one minute after the daily settle
  (15:30 + ``daily_settle_minutes`` + 1 = 16:01 IST by default) ticks 1D and 1W,
  so the new daily candle is downloaded right away rather than up to 6 hours
  later (the interval jobs are anchored to the moment the app started).
* Sleep / hibernate: a job whose time passed while the computer slept still runs
  once when it wakes (no misfire limit, coalesced), instead of being dropped.
  Every job is idempotent: scans are gated by ``needs_scan``; the index, universe
  and F&O refreshes only fetch what is missing. Windows timers do not count time
  asleep, so a watchdog notices the wall-clock jump on wake and wakes the
  scheduler at once (otherwise it would first finish its pre-sleep wait, up to
  30 minutes).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .data.calendar import SESSION_CLOSE
from .scanner import ScanRequest, find_latest_scan
from .timeutil import IST, now_ist, parse_iso

if TYPE_CHECKING:  # pragma: no cover
    from .services import Services

log = logging.getLogger(__name__)

INTERVALS = {"1h": {"minutes": 30}, "4h": {"hours": 2}, "1d": {"hours": 6}, "1w": {"days": 1}}
STARTUP_ORDER = ("1d", "1w", "4h", "1h")
SCHEDULED_UNIVERSE = "all"
RESUME_CHECK_S = 60  # watchdog period
RESUME_GAP_S = 180  # wall clock ahead of the watchdog by more than this = the computer slept


class ScanScheduler:
    def __init__(self, services: "Services"):
        self.s = services
        self.scheduler = BackgroundScheduler(
            # misfire_grace_time None: a run missed while the computer slept is never dropped;
            # coalesce: it runs once, however many runs were missed.
            timezone=IST, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": None}
        )
        self.last_decisions: dict[str, str] = {}
        self._startup_pending: list[str] = []
        self._stop = threading.Event()

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
        # Daily/weekly candle right after the close has settled (holidays: needs_scan skips).
        at = self.after_close_time()
        self.scheduler.add_job(
            self.after_close, CronTrigger(day_of_week="mon-fri", hour=at.hour, minute=at.minute, timezone=IST), id="after_close", replace_existing=True
        )
        # F&O OI activity: NSE publishes the F&O / equity bhavcopy in the evening;
        # retried every 30 minutes until the latest session is stored.
        self.scheduler.add_job(
            self.fno_refresh, CronTrigger(day_of_week="mon-fri", hour="18-23", minute="5,35", timezone=IST), id="fno_bhavcopy", replace_existing=True
        )
        self.scheduler.start()
        self._stop.clear()
        threading.Thread(target=self._watch_resume, name="scheduler-resume-watch", daemon=True).start()
        if run_startup_scans:
            threading.Thread(target=self.startup_scans, name="startup-scans", daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _watch_resume(self, clock=time.time, period: float = RESUME_CHECK_S) -> None:
        last = clock()
        while not self._stop.wait(period):
            now = clock()
            if now - last > period + RESUME_GAP_S:
                self.resumed(now - last)
            last = now

    def resumed(self, asleep_s: float) -> None:
        """The computer woke up (wall clock jumped ahead): run the missed jobs now."""
        msg = f"{now_ist():%Y-%m-%d %H:%M} computer resumed after ~{asleep_s / 60:.0f} min asleep: missed jobs run now"
        self.last_decisions["resume"] = msg
        log.info(msg)
        if self.scheduler.running:
            self.scheduler.wakeup()

    def fno_refresh(self) -> None:
        try:
            self.last_decisions["fno_bhavcopy"] = f"{now_ist():%Y-%m-%d %H:%M} {self.s.fno.scheduled_refresh()}"
        except Exception:
            log.exception("F&O bhavcopy refresh failed")

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
        if timeframe == "1d" and last.get("ipo_config_hash") != self.s.ipo_config_hash:
            return True, "IPO base configuration changed"
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

    def after_close_time(self):
        """Wall time (IST) of the after-close job: close + daily settle + 1 minute."""
        base = datetime(2000, 1, 3, SESSION_CLOSE.hour, SESSION_CLOSE.minute)
        return (base + timedelta(minutes=self.s.settings.daily_settle_minutes + 1)).time()

    def after_close(self) -> None:
        for tf in ("1d", "1w"):
            try:
                self.tick(tf)
            except Exception:
                log.exception("after-close tick %s failed", tf)

    def startup_pending(self) -> list[str]:
        """Startup timeframes not checked/scanned yet (the one running included)."""
        return list(self._startup_pending)

    def startup_scans(self) -> None:
        self._startup_pending = list(STARTUP_ORDER)
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
            finally:
                self._startup_pending = [x for x in self._startup_pending if x != tf]

    def refresh_universe(self) -> None:
        self.s.universe.refresh_nifty500()
        self.s.universe.refresh_fno()
        # official listing dates (IPO base scanner); a failure keeps the stored dates
        self.s.universe.refresh_listing_dates()
        # recent mainboard IPOs (NSE past issues) - new listings join the next scans
        self.s.universe.refresh_recent_ipos()
        self.s.refresh_ipo_splits()
        if self.s.universe.refresh_holidays().get("status") == "ok":
            self.s.reload_calendar()
        # Official NSE 52-week high/low report of the last completed session (optional
        # cross-check shown by the 52-week scanner; failures are only recorded).
        from .signals import nse52w

        res = nse52w.refresh(self.s.engine, self.s.calendar.latest_completed_bar_end("1d", now_ist()).date())
        self.last_decisions["nse_52w_report"] = f"{res.get('report_date')}: {res.get('status')} {res.get('error', '')}".strip()
