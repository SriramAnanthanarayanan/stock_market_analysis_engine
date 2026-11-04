"""Market data refresh behaviour through MarketDataService (fixture provider)."""

from sqlalchemy import update

from app import database as db


def _age_cache(s, symbol, interval):
    with s.engine.begin() as conn:
        conn.execute(
            update(db.market_data)
            .where((db.market_data.c.symbol == symbol) & (db.market_data.c.interval == interval))
            .values(last_fetch_at="2026-01-01T10:00:00+05:30")
        )


def test_partial_success_and_cooldown_for_unlisted_symbols(services_env):
    s, provider = services_env
    rep = s.market_data.ensure(["FAIL1", "FCHAN"], "1d")
    assert rep.full == 1 and "FAIL1" in rep.failed and s.market_data.last_error is None
    calls = len(provider.calls)
    again = s.market_data.ensure(["FAIL1"], "1d")
    assert again.skipped_recent_failures == 1 and len(provider.calls) == calls  # no new attempt
    forced = s.market_data.ensure(["FAIL1"], "1d", full_refresh=True)
    assert "FAIL1" in forced.failed and len(provider.calls) > calls


def test_provider_error_only_when_every_attempt_fails(services_env):
    s, _ = services_env
    s.market_data.ensure(["NOPE1", "NOPE2", "NOPE3", "NOPE4", "NOPE5"], "1d")
    assert s.market_data.last_error and "all 5 attempted downloads failed" in s.market_data.last_error
    s.market_data.ensure(["FCHAN"], "1d")
    assert s.market_data.last_error is None


def test_fresh_cache_is_not_downloaded_again(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1d")
    calls = len(provider.calls)
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.fresh == 1 and len(provider.calls) == calls


def test_incremental_refresh_overlaps_five_bars_without_duplicates(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1d")
    before = s.store.load("FCHAN", "1d")
    _age_cache(s, "FCHAN", "1d")
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.incremental == 1
    start = provider.calls[-1][3]
    assert start == before.index[-5].date()  # re-fetch starts 5 candles back
    after = s.store.load("FCHAN", "1d")
    assert after.index.is_unique and len(after) == len(before)


def test_readjusted_history_triggers_full_redownload(services_env):
    s, provider = services_env
    s.market_data.ensure(["FCHAN"], "1d")
    frame = provider.frames[("FCHAN", "1d")]
    provider.frames[("FCHAN", "1d")] = frame.assign(open=frame.open / 2, high=frame.high / 2, low=frame.low / 2, close=frame.close / 2)
    _age_cache(s, "FCHAN", "1d")
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.readjusted == 1 and rep.full == 1
    cached = s.store.load("FCHAN", "1d")
    assert abs(cached["close"].iloc[0] - frame["close"].iloc[0] / 2) < 1e-9  # whole series consistent
