from dataclasses import replace
from datetime import datetime

import app.scheduler as sched_mod
from app.scanner import ScanRequest
from app.scheduler import ScanScheduler
from app.timeutil import IST


def test_needs_scan_only_when_new_candle_or_config_change(services_env):
    s, _ = services_env
    sch = ScanScheduler(s)
    need, why = sch.needs_scan("1d")
    assert need and "no completed scan" in why
    s.scans.run_now(ScanRequest("all", "1d", trigger="scheduler"))
    need, why = sch.needs_scan("1d")
    assert not need, why
    s.cfg = replace(s.cfg, touch_tol_atr_frac=0.3)
    need, why = sch.needs_scan("1d")
    assert need and "configuration" in why


def test_intraday_ticks_skip_outside_session_and_holidays(services_env, monkeypatch):
    s, _ = services_env
    sch = ScanScheduler(s)
    monkeypatch.setattr(sched_mod, "now_ist", lambda: datetime(2026, 11, 3, 21, 0, tzinfo=IST))
    assert sch.tick("1h") is None and "outside" in sch.last_decisions["1h"]
    monkeypatch.setattr(sched_mod, "now_ist", lambda: datetime(2026, 11, 10, 11, 0, tzinfo=IST))  # NSE holiday
    assert sch.tick("4h") is None and "outside" in sch.last_decisions["4h"]


def test_jobs_are_never_duplicated(services_env):
    s, _ = services_env
    sch = ScanScheduler(s)
    sch.start(run_startup_scans=False)
    sch.start(run_startup_scans=False)
    try:
        ids = [j.id for j in sch.scheduler.get_jobs()]
        assert sorted(ids) == sorted(set(ids)) == sorted(["scan_1h", "scan_4h", "scan_1d", "scan_1w", "universe_refresh", "index_refresh"])
    finally:
        sch.shutdown()


def test_startup_scan_order(services_env, monkeypatch):
    s, _ = services_env
    sch = ScanScheduler(s)
    submitted = []
    monkeypatch.setattr(s.scans, "submit", lambda req: (submitted.append(req.timeframe), ("id", True))[1])
    monkeypatch.setattr(s.scans, "wait", lambda *a, **k: None)
    sch.startup_scans()
    assert submitted == ["1d", "1w", "4h", "1h"]
