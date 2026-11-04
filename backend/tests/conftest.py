"""Shared test fixtures. Tests never touch the network or the real database."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.config import Settings
from app.data.calendar import NSECalendar
from app.providers.base import FetchResult, MarketDataProvider
from app.synthetic import POSITIVE, band_series, intraday_index, random_walk, weekly_index, with_index

BACKEND = Path(__file__).resolve().parents[1]
HOLIDAYS_SRC = BACKEND / "data" / "nse_holidays.json"


@pytest.fixture(scope="session")
def calendar() -> NSECalendar:
    return NSECalendar.load(HOLIDAYS_SRC)


def make_settings(tmp_path: Path, **over) -> Settings:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copy(HOLIDAYS_SRC, data / "nse_holidays.json")
    base = dict(
        backend_dir=BACKEND,
        data_dir=data,
        cache_dir=tmp_path / "cache",
        ssl_cache_dir=tmp_path / ".cache",
        db_path=data / "test.db",
        scheduler_enabled=False,
        startup_scans=False,
        network_enabled=True,
        nifty500_min_rows=1,
        fno_min_active=1,
        download_backoff_base_s=0.0,
    )
    base.update(over)
    return Settings(**base)


class FixtureProvider(MarketDataProvider):
    """Deterministic offline provider. Results are fixtures, not market data."""

    name = "fixture"

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame], fail: set[str] | None = None):
        self.frames = frames
        self.fail = set(fail or ())
        self.calls: list[tuple] = []

    def default_periods(self, interval: str) -> list[str]:
        return ["max"]

    def fetch(self, symbols, interval, *, period=None, start=None) -> FetchResult:
        self.calls.append((tuple(symbols), interval, period, start))
        res = FetchResult(period_used=period)
        for s in symbols:
            if s in self.fail:
                res.errors[s] = "fixture: simulated provider failure"
                continue
            df = self.frames.get((s, interval))
            if df is None:
                res.errors[s] = "fixture: no data"
                continue
            if start is not None:
                df = df[df.index.date >= start]
            res.data[s] = df.copy()
        return res


def fixture_frames(calendar: NSECalendar) -> dict[tuple[str, str], pd.DataFrame]:
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    series = {
        "FCHAN": band_series(POSITIVE["falling_channel"], calendar),
        "ATRI": band_series(POSITIVE["ascending_triangle"], calendar),
        "RECT": band_series(POSITIVE["rectangle"], calendar),
        "RWALK": random_walk(300, calendar, seed=5),
    }
    for sym, d in series.items():
        frames[(sym, "1d")] = d
        frames[(sym, "1h")] = with_index(d, intraday_index(len(d), calendar))
        frames[(sym, "1wk")] = with_index(d, weekly_index(len(d), calendar))
    return frames


NIFTY_CSV = """Company Name,Industry,Symbol,Series,ISIN Code
Falling Channel Ltd.,Synthetic,FCHAN,EQ,INE000000001
Ascending Triangle Ltd.,Synthetic,ATRI,EQ,INE000000002
Rectangle Ltd.,Synthetic,RECT,EQ,INE000000003
Random Walk Ltd.,Synthetic,RWALK,EQ,INE000000004
Always Failing Ltd.,Synthetic,FAIL1,EQ,INE000000005
"""

FNO_CSV = """UNDERLYING                          ,SYMBOL    ,NOV-26     ,DEC-26     ,JAN-27
NIFTY 50                            ,NIFTY     ,65         ,65         ,65
Derivatives on Individual Securities,Symbol    ,NOV-26     ,DEC-26     ,JAN-27
FALLING CHANNEL LTD                 ,FCHAN     ,500        ,500        ,500
ASCENDING TRIANGLE LTD              ,ATRI      ,250        ,250        ,250
RECTANGLE LTD                       ,RECT      ,           ,           ,300
"""


@pytest.fixture
def services_env(tmp_path, calendar):
    """Services + fixture universe + fixture provider, no HTTP layer."""
    from app.services import Services

    settings = make_settings(tmp_path)
    provider = FixtureProvider(fixture_frames(calendar), fail={"FAIL1"})
    s = Services(settings, provider=provider)
    s.startup(background=False)
    s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
    s.universe.refresh_fno(text=FNO_CSV, source="fixture")
    yield s, provider
    s.shutdown()


@pytest.fixture
def client_env(tmp_path, calendar):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = make_settings(tmp_path)
    provider = FixtureProvider(fixture_frames(calendar), fail={"FAIL1"})
    app = create_app(settings, provider=provider, background=False)
    with TestClient(app) as client:
        s = app.state.services
        s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        yield client, s, provider, settings
