"""E1 data consistency: raw (split-adjusted) OHLC vs dividend-adjusted close.

Live finding behind this (2026-11-04): with yfinance ``auto_adjust=True`` the
P1-P3 caches were dividend-adjusted, e.g. ITC 9-May-2024 close 409.34 raw vs
370.45 adjusted (~10 %); TradingView shows the raw basis by default."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app import database as db
from app.data.ohlcv import clean_ohlcv, resample_4h
from app.data.store import OhlcvStore, merge_incremental
from app.providers.base import PRICE_BASIS_LEGACY, PRICE_BASIS_RAW
from app.providers.yahoo import normalize_frame
from app.timeutil import IST


def test_yahoo_frame_keeps_raw_ohlc_and_adjusted_close_separately():
    raw = pd.DataFrame(
        {"Open": [400.0], "High": [412.0], "Low": [398.0], "Close": [409.34], "Adj Close": [370.45], "Volume": [1e6]},
        index=pd.DatetimeIndex(["2024-05-09"]),
    )
    f = normalize_frame(raw, "1d")
    assert f["close"].iloc[0] == 409.34 and f["adj_close"].iloc[0] == 370.45  # never merged
    assert str(f.index[0]) == "2024-05-09 09:15:00+05:30"
    no_adj = normalize_frame(raw.drop(columns=["Adj Close"]), "1d")
    assert np.isnan(no_adj["adj_close"].iloc[0])  # missing, not copied from close


def test_cleaning_and_4h_resample_carry_adjusted_close(calendar):
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-10-29 {h}", tz=IST) for h in ("09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15")])
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1e4, "adj_close": np.arange(7) + 90.0}, index=idx)
    clean, _ = clean_ohlcv(df, "1h", calendar)
    assert "adj_close" in clean.columns and len(clean) == 7
    four = resample_4h(clean)
    assert four["adj_close"].tolist() == [93.0, 96.0]  # last 1H adjusted close of each NSE 4H bin
    assert four["close"].tolist() == [100.5, 100.5]


def test_incremental_merge_detects_dividend_readjustment():
    idx = pd.date_range("2026-10-20 09:15", periods=5, freq="D", tz=IST)
    old = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [10.0] * 5, "volume": 1.0, "adj_close": [9.0] * 5}, index=idx)
    same = old.copy()
    _, mismatch = merge_incremental(old, same, 1e-4)
    assert not mismatch
    div = old.copy()
    div["adj_close"] = 8.8  # provider re-adjusted history after an ex-dividend date; raw closes unchanged
    _, mismatch = merge_incremental(old, div, 1e-4)
    assert mismatch


def test_legacy_cache_is_never_analysed_and_is_redownloaded(services_env):
    s, provider = services_env
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.full == 1
    assert s.store.basis("FCHAN", "1d") == PRICE_BASIS_RAW and s.store.load_current("FCHAN", "1d") is not None
    # Simulate a P1-P3 cache: same file, tagged as dividend-adjusted.
    with s.engine.begin() as conn:
        conn.execute(db.market_data.update().where(db.market_data.c.symbol == "FCHAN").values(price_basis=PRICE_BASIS_LEGACY))
    assert s.store.load("FCHAN", "1d") is not None  # the file still exists ...
    assert s.store.load_current("FCHAN", "1d") is None  # ... but analysis never reads it
    provider.calls.clear()
    rep = s.market_data.ensure(["FCHAN"], "1d")
    assert rep.full == 1 and rep.incremental == 0 and provider.calls[0][2] is not None  # full download (period), not incremental
    assert s.store.basis("FCHAN", "1d") == PRICE_BASIS_RAW


def test_scans_record_the_price_basis(services_env):
    s, _ = services_env
    from app.scanner import ScanRequest, get_scan

    scan_id, _ = s.scans.submit(ScanRequest(universe="all", timeframe="1d", trigger="test"))
    s.scans.wait(scan_id)
    assert get_scan(s.engine, scan_id)["price_basis"] == PRICE_BASIS_RAW


def test_migration_4_tags_legacy_rows_and_closes_open_tracks(tmp_path):
    e = db.make_engine(tmp_path / "m.db")
    db.migrate(e)
    now = db.now_ist_iso()
    with e.begin() as conn:
        conn.execute(
            db.pattern_tracks.insert().values(
                track_id="t1", symbol="X", timeframe="1d", pattern="rectangle", family="parallel", state="frozen", status="broke_above",
                first_scan_id="s", last_scan_id="s", first_detected_at=now, last_detected_at=now, first_detection_id="d", current_detection_id="d",
                formation_start=now, formation_end=now, quality_first=50, quality_last=50, quality_max=50, config_hash="h", config_version="v",
                geometry_json="{}", detection_json="{}", updated_at=now, price_basis=None,
            )
        )
        conn.execute(db.scans.insert().values(scan_id="old", universe="all", timeframe="1d", config_hash="h", config_version="v", trigger="t", state="completed", created_at=now, price_basis=None))
        db._migration_4(conn)  # idempotent re-run on rows written before E1
    with e.connect() as conn:
        tr = conn.execute(select(db.pattern_tracks)).mappings().first()
        ev = conn.execute(select(db.track_events)).mappings().all()
        sc = conn.execute(select(db.scans.c.price_basis)).scalar()
    assert tr["state"] == "closed" and tr["closed_reason"] == "data_changed" and tr["price_basis"] == PRICE_BASIS_LEGACY
    assert ev[-1]["event"] == "closed" and "price basis" in ev[-1]["detail_json"]
    assert sc == PRICE_BASIS_LEGACY
    assert db.current_schema_version(e) == db.SCHEMA_VERSION >= 4


def test_outcome_returns_use_dividend_adjusted_closes():
    from app.analytics.outcomes import compute_outcome
    from app.config import DEFAULT_CONFIG
    from app.patterns.base import SeriesContext

    idx = pd.date_range("2026-09-01 09:15", periods=8, freq="D", tz=IST)
    close = np.array([100.0, 100, 100, 95, 95, 95, 95, 95])  # raw: ex-dividend drop of 5 on bar 3
    adj = np.array([95.0, 95, 95, 95, 95, 95, 95, 95])  # adjusted: no economic change
    f = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1e5, "adj_close": adj}, index=idx)
    out = compute_outcome(SeriesContext("X", "1d", f, DEFAULT_CONFIG), 1, "up", (5,))
    assert out["return_basis"] == "dividend_adjusted_close"
    assert out["horizons"]["5"]["return_pct"] == pytest.approx(0.0, abs=1e-9)  # raw closes would show -5 %
    raw_only = f.drop(columns=["adj_close"])
    out2 = compute_outcome(SeriesContext("X", "1d", raw_only, DEFAULT_CONFIG), 1, "up", (5,))
    assert out2["return_basis"].startswith("raw_close") and out2["horizons"]["5"]["return_pct"] == pytest.approx(-5.0)
