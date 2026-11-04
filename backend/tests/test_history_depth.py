"""Full-history backfill: deeper provider history, NSE index backfill, chart realignment.

Deterministic fixtures only (tests never touch the network)."""

from datetime import date, datetime, time

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import database as db
from app.api.chart import _realign
from app.indices.data import HISTORY_MAX, NseIndexHistory, index_key, parse_history
from app.providers.yahoo import YahooProvider
from app.timeutil import IST
from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings
from tests.fixtures_e2 import FakeHistory, catalogue


def _set_depth(s, symbol, interval, depth):
    with s.engine.begin() as conn:
        conn.execute(
            update(db.market_data)
            .where((db.market_data.c.symbol == symbol) & (db.market_data.c.interval == interval))
            .values(history_period=depth)
        )


# ------------------------------------------------------------------ stocks (Yahoo)
def test_yahoo_daily_and_weekly_ask_for_full_history():
    p = YahooProvider()
    assert p.default_periods("1d")[0] == "max" and p.default_periods("1wk")[0] == "max"
    assert p.default_periods("1h")[0] == "2y"  # Yahoo caps hourly history at ~730 days


def test_shallow_cache_is_backfilled_once_then_fresh(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1d")
    assert s.store.meta("FCHAN", "1d")["history_period"] == "max"
    _set_depth(s, "FCHAN", "1d", "5y")  # a cache made before the switch to full history
    calls = len(provider.calls)
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.backfilled == 1 and rep.full == 1 and rep.fresh == 0
    assert provider.calls[calls][2] == "max" and provider.calls[calls][3] is None  # full download, not incremental
    assert s.store.meta("FCHAN", "1d")["history_period"] == "max"
    again = s.market_data.ensure(["FCHAN"], "1d")
    assert again.fresh == 1 and again.backfilled == 0


def test_failed_backfill_keeps_the_cached_candles(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1d")
    before = s.store.load("FCHAN", "1d")
    _set_depth(s, "FCHAN", "1d", "5y")
    provider.fail.add("FCHAN")
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert "FCHAN" in rep.failed
    meta = s.store.meta("FCHAN", "1d")
    assert meta["status"] == "ok" and meta["history_period"] == "5y"  # retried next refresh
    assert len(s.store.load("FCHAN", "1d")) == len(before)


def test_hourly_cache_keeps_bars_older_than_the_provider_window(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1h")
    full = provider.frames[("FCHAN", "1h")]
    # The provider now serves only the last 100 bars (a rolling ~730-day window).
    provider.frames[("FCHAN", "1h")] = full.iloc[-100:]
    with s.engine.begin() as conn:
        conn.execute(update(db.market_data).where(db.market_data.c.symbol == "FCHAN").values(last_fetch_at="2026-01-01T10:00:00+05:30"))
    rep = s.market_data.ensure(["FCHAN"], "1h")
    assert rep.incremental == 1
    assert len(s.store.load("FCHAN", "1h")) == len(full)  # nothing dropped


def test_migration_7_tags_existing_caches_with_their_old_depth(tmp_path):
    e = db.make_engine(tmp_path / "m.db")
    db.migrate(e)
    rows = [("AAA", "1d"), ("AAA", "1h"), ("AAA", "1wk"), ("IDX:NIFTY 50", "1d")]
    with e.begin() as conn:
        for sym, iv in rows:
            conn.execute(db.market_data.insert().values(symbol=sym, interval=iv, provider="yahoo", status="ok", history_period=None))
        db._migration_7(conn)  # idempotent re-run on rows written before the column existed
    with e.connect() as conn:
        got = {(r.symbol, r.interval): r.history_period for r in conn.execute(select(db.market_data))}
    assert got == {("AAA", "1d"): "5y", ("AAA", "1h"): "2y", ("AAA", "1wk"): "max", ("IDX:NIFTY 50", "1d"): "10y"}
    assert db.current_schema_version(e) == db.SCHEMA_VERSION >= 7


# ------------------------------------------------------------------ NSE indices
def _history_stub():
    class S:
        download_max_attempts = 1
        download_backoff_base_s = 0.0
        index_request_delay_s = 0.0
        index_history_url = "x"
        index_history_page = "x"

    return NseIndexHistory(S())


def _row(d: date, close: float, ohl=True) -> dict:
    v = close if ohl else None
    return {"EOD_TIMESTAMP": d.strftime("%d-%b-%Y").upper(), "EOD_OPEN_INDEX_VAL": v, "EOD_HIGH_INDEX_VAL": v, "EOD_LOW_INDEX_VAL": v, "EOD_CLOSE_INDEX_VAL": close}


def test_close_only_index_rows_are_kept_as_flat_candles():
    df = parse_history([_row(date(2000, 1, 3), 971.73, ohl=False), {**_row(date(2000, 1, 4), 980.0), "EOD_OPEN_INDEX_VAL": 0}])
    assert len(df) == 2
    first = df.iloc[0]
    assert first["open"] == first["high"] == first["low"] == first["close"] == 971.73
    assert df.iloc[1]["open"] == 980.0


def test_backward_fetch_stops_after_consecutive_empty_years():
    h = _history_stub()
    have = {1995, 1997, 1998}  # 1996 empty (a gap / a transient empty answer) must not stop the walk
    calls = []

    def fake_get(name, a, b):  # yearly chunks start on 1998-01-01, 1997-01-01, 1996-01-02, 1995-01-02, ...
        calls.append((a, b))
        return [_row(a, 100.0 + a.year - 1990)] if a.year in have else []

    h._get = fake_get
    df = h.fetch_back("NIFTY 50", date(1999, 1, 1), date(1980, 1, 1), empty_stop=2)
    years = sorted({ts.year for ts in df.index})
    assert 1995 in years and 1997 in years
    assert all((b - a).days <= 364 for a, b in calls)
    assert calls[0][1] == date(1998, 12, 31)
    assert len(calls) < 19  # stopped well before the 1980 floor


@pytest.fixture
def idx_env(tmp_path):
    from app.data.calendar import NSECalendar
    from app.main import create_app
    from tests.conftest import HOLIDAYS_SRC

    calendar = NSECalendar.load(HOLIDAYS_SRC)
    settings = make_settings(tmp_path)
    hist = FakeHistory(calendar)
    app = create_app(settings, provider=FixtureProvider(fixture_frames(calendar)), background=False, index_history=hist)
    with TestClient(app) as client:
        s = app.state.services
        s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
        s.universe.refresh_fno(text=FNO_CSV, source="fixture")
        s.indices.refresh_catalogue(catalogue(calendar), min_rows=5)
        s.indices.refresh_all()
        yield client, s, hist


def test_full_index_download_is_tagged_full_history(idx_env):
    _, s, _ = idx_env
    assert s.store.meta(index_key("NIFTY IT"), "1d")["history_period"] == HISTORY_MAX


def test_shallow_index_cache_gets_older_history_prepended(idx_env):
    _, s, hist = idx_env
    key = index_key("NIFTY IT")
    full = s.store.load(key, "1d")
    cut = full.index[len(full) // 2]
    s.store.save(key, "1d", full[full.index >= cut])  # a cache that stops at the old 10-year floor
    _set_depth(s, key, "1d", "10y")
    hist.calls.clear()
    now = datetime.combine(full.index[-1].date(), time(20), tzinfo=IST)
    mode = s.indices.refresh_one(s.indices.registry.get("NIFTY IT"), now=now)
    assert mode == "incremental + backfill"
    after = s.store.load(key, "1d")
    assert after.index[0] == full.index[0] and len(after) == len(full) and after.index.is_unique
    assert s.store.meta(key, "1d")["history_period"] == HISTORY_MAX
    hist.calls.clear()
    assert s.indices.refresh_one(s.indices.registry.get("NIFTY IT"), now=now) == "fresh"
    assert hist.calls == []


# ------------------------------------------------------------------ chart realignment
def _item(start_i: int, times: list[int]) -> dict:
    p = lambda i, projected=False: {"t": times[i] if i < len(times) else 10_000_000 + i, "i": i, "p": 1.0, "projected": projected}  # noqa: E731
    return {
        "geometry": {
            "formation": {"start": p(start_i), "end": p(start_i + 10)},
            "last_candle": p(start_i + 15),
            "elements": [{"type": "line", "points": [p(start_i), p(start_i + 20, True)]}],
            "lifecycle_model": {"apex_index": start_i + 20.5},
        },
        "lifecycle": [{"index": start_i + 12, "t": times[start_i + 12]}],
        "metrics": {"formation_start_index": start_i, "pole_bars": 7},
    }


def test_stored_geometry_follows_prepended_history():
    old_times = [1_000 + 86_400 * k for k in range(60)]
    item = _item(30, old_times)
    new_times = [1_000 - 86_400 * k for k in range(100, 0, -1)] + old_times  # 100 older bars prepended
    pos = {t: j for j, t in enumerate(new_times)}
    assert _realign(item, pos)
    g = item["geometry"]
    assert g["formation"]["start"]["i"] == 130 and g["formation"]["end"]["i"] == 140 and g["last_candle"]["i"] == 145
    assert g["elements"][0]["points"][1]["i"] == 150  # projected point moves with the same offset
    assert g["lifecycle_model"]["apex_index"] == 150.5
    assert item["lifecycle"][0]["index"] == 142
    assert item["metrics"] == {"formation_start_index": 130, "pole_bars": 7}


def test_realign_refuses_when_bars_inside_the_formation_changed():
    old_times = [1_000 + 86_400 * k for k in range(60)]
    item = _item(30, old_times)
    new_times = old_times[:35] + old_times[36:]  # a bar inside the formation disappeared
    pos = {t: j for j, t in enumerate(new_times)}
    assert not _realign(item, pos)
