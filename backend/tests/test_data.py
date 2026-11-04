from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.data.ohlcv import clean_ohlcv, prepare_series, resample_4h
from app.data.store import OhlcvStore, merge_incremental
from app.providers.yahoo import normalize_frame
from app.timeutil import IST, asof_cutoff, epoch_s


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=IST)


def frame(rows: list[tuple]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([ts(r[0]) for r in rows], name="ts")
    return pd.DataFrame([r[1:] for r in rows], index=idx, columns=["open", "high", "low", "close", "volume"]).astype(float)


# ----------------------------------------------------------- timestamps / IST


def test_daily_provider_dates_become_ist_session_open():
    raw = pd.DataFrame(
        {"Open": [1.0, 2.0], "High": [2.0, 3.0], "Low": [0.5, 1.5], "Close": [1.5, 2.5], "Volume": [10, 20]},
        index=pd.DatetimeIndex(["2026-10-29", "2026-10-30"]),
    )
    out = normalize_frame(raw, "1d")
    assert str(out.index.tz) == "Asia/Kolkata"
    assert [t.strftime("%Y-%m-%d %H:%M") for t in out.index] == ["2026-10-29 09:15", "2026-10-30 09:15"]
    # 09:15 IST == 03:45 UTC on the same calendar date (no double conversion).
    assert epoch_s(out.index[0]) == int(pd.Timestamp("2026-10-29 03:45", tz="UTC").timestamp())


def test_intraday_provider_times_converted_not_relabelled():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-29 03:45", tz="UTC")])
    raw = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [5]}, index=idx)
    out = normalize_frame(raw, "1h")
    assert out.index[0] == ts("2026-10-29 09:15")


# --------------------------------------------------------------- hygiene


def test_clean_drops_duplicates_invalid_and_phantom_bars(calendar):
    df = frame(
        [
            ("2026-01-13 09:15", 10, 11, 9, 10.5, 100),
            ("2026-01-14 09:15", 10, 11, 9, 10.5, 100),
            ("2026-01-14 09:15", 10, 12, 9, 11.5, 150),  # duplicate -> keep last
            ("2026-01-15 09:15", 11.5, 11.5, 11.5, 11.5, 0),  # NSE holiday phantom (flat, zero volume)
            ("2026-01-16 09:15", 11, 10, 12, 11, 100),  # high < low -> invalid
            ("2026-01-19 09:15", np.nan, 11, 9, 10, 100),  # NaN price
            ("2025-02-01 09:15", 10, 11, 9, 10.5, 500),  # budget Saturday session (real volume) -> kept
            ("2025-10-21 09:15", 10, 11, 9, 10.5, 400),  # Muhurat session on a holiday (volume) -> kept
            ("2026-01-17 09:15", 10, 10, 10, 10, 0),  # Saturday zero-volume phantom
        ]
    )
    out, rep = clean_ohlcv(df, "1d", calendar)
    kept = [t.strftime("%Y-%m-%d") for t in out.index]
    assert kept == ["2025-02-01", "2025-10-21", "2026-01-13", "2026-01-14"]
    assert out.loc[ts("2026-01-14 09:15"), "close"] == 11.5
    assert rep.dropped_duplicates == 1 and rep.dropped_invalid == 1 and rep.dropped_nan == 1 and rep.dropped_phantom == 2


def test_stale_flat_daily_prints_are_dropped_real_quiet_days_kept(calendar):
    # Shape of Yahoo's RELIANCE 28-Jul-2005 print: flat, no volume, ~2x the price around it.
    df = frame(
        [
            ("2005-07-25 09:15", 110, 112, 109, 111, 1000),
            ("2005-07-26 09:15", 111, 113, 110, 112, 1000),
            ("2005-07-27 09:15", 112, 112, 112, 112, 0),  # no trades, unchanged price -> kept
            ("2005-07-28 09:15", 218.31, 218.31, 218.31, 218.31, 0),  # stale print -> dropped
            ("2005-07-29 09:15", 112, 114, 111, 113, 1000),
            ("2005-08-01 09:15", 80, 82, 78, 80, 5000),  # real -29 % day with volume -> kept
            ("2005-08-02 09:15", 120, 120, 120, 120, np.nan),  # flat, no volume, +50 % -> dropped
        ]
    )
    out, rep = clean_ohlcv(df, "1d", calendar)
    kept = [t.strftime("%m-%d") for t in out.index]
    assert kept == ["07-25", "07-26", "07-27", "07-29", "08-01"]
    assert rep.dropped_stale_flat == 2
    hourly, rep_h = clean_ohlcv(df.iloc[[0, 3]], "1h", calendar)  # daily-only rule
    assert rep_h.dropped_stale_flat == 0


def test_tiny_rounding_inconsistency_is_repaired_not_dropped(calendar):
    df = frame([("2026-10-29 09:15", 100.0, 100.00001 - 0.00002, 99.0, 100.0, 10)])
    out, rep = clean_ohlcv(df, "1d", calendar)
    assert len(out) == 1 and rep.repaired == 1 and out["high"].iloc[0] == 100.0


def test_intraday_zero_volume_marked_missing_and_out_of_session_dropped(calendar):
    df = frame(
        [
            ("2026-10-29 09:15", 10, 11, 9, 10, 0),  # Yahoo reports 0 for the first hour
            ("2026-10-29 10:15", 10, 11, 9, 10, 500),
            ("2026-10-29 18:15", 10, 11, 9, 10, 50),  # outside regular session
        ]
    )
    out, rep = clean_ohlcv(df, "1h", calendar)
    assert len(out) == 2 and np.isnan(out["volume"].iloc[0]) and rep.dropped_outside_session == 1


# ------------------------------------------------------------ resampling


def test_4h_bins_are_nse_session_aligned():
    rows = [(f"2026-10-29 {t}", i, i + 1, i - 1, i + 0.5, 10) for i, t in enumerate(["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"], start=1)]
    rows += [("2026-10-30 09:15", 20, 21, 19, 20.5, np.nan), ("2026-10-30 10:15", 21, 22, 20, 21.5, 10)]
    out = resample_4h(frame(rows))
    assert [t.strftime("%m-%d %H:%M") for t in out.index] == ["10-29 09:15", "10-29 13:15", "10-30 09:15"]
    first = out.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (1, 5, 0, 4.5, 40)
    second = out.iloc[1]  # 13:15-15:30, the shorter partial bar
    assert (second.open, second.high, second.close, second["source_bars"]) == (5, 8, 7.5, 3)
    assert out.iloc[2].volume == 10  # missing volume is not invented
    assert all(t.strftime("%H:%M") in ("09:15", "13:15") for t in out.index)


# ------------------------------------------------------ completion / as-of


def test_bar_ends_by_timeframe(calendar):
    one = lambda s, tf: calendar.bar_ends(pd.DatetimeIndex([ts(s)]), tf)[0]  # noqa: E731
    assert one("2026-10-29 14:15", "1h") == ts("2026-10-29 15:15")
    assert one("2026-10-29 15:15", "1h") == ts("2026-10-29 15:30")
    assert one("2026-10-29 09:15", "4h") == ts("2026-10-29 13:15")
    assert one("2026-10-29 13:15", "4h") == ts("2026-10-29 15:30")
    assert one("2026-10-29 09:15", "1d") == ts("2026-10-29 15:30")
    assert one("2026-10-26 09:15", "1w") == ts("2026-10-30 15:30")


@pytest.mark.parametrize(
    "monday,expected",
    [
        ("2026-03-30", "2026-04-02 15:30"),  # Tue 31-Mar and Fri 03-Apr holidays
        ("2026-12-21", "2026-12-24 15:30"),  # Fri 25-Dec Christmas
        ("2026-10-19", "2026-10-23 15:30"),  # Tue 20-Oct holiday, Friday trades
    ],
)
def test_weekly_completion_across_holidays(calendar, monday, expected):
    assert calendar.bar_ends(pd.DatetimeIndex([ts(f"{monday} 09:15")]), "1w")[0] == ts(expected)


def test_incomplete_candles_are_excluded(calendar):
    hourly = frame([(f"2026-10-29 {t}", 10, 11, 9, 10, 5) for t in ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]])
    at = datetime(2026, 10, 29, 12, 40, tzinfo=IST)
    assert len(prepare_series(hourly, "X", "1h", at, calendar).frame) == 3  # 12:15 bar still open
    assert len(prepare_series(hourly, "X", "4h", at, calendar).frame) == 0  # morning 4H bin ends 13:15
    assert len(prepare_series(hourly, "X", "4h", datetime(2026, 10, 29, 13, 15, tzinfo=IST), calendar).frame) == 1
    daily = frame([("2026-10-28 09:15", 10, 11, 9, 10, 5), ("2026-10-29 09:15", 10, 11, 9, 10, 5)])
    assert len(prepare_series(daily, "X", "1d", at, calendar).frame) == 1
    assert len(prepare_series(daily, "X", "1d", datetime(2026, 10, 29, 15, 30, tzinfo=IST), calendar).frame) == 2
    weekly = frame([("2026-10-19 09:15", 10, 11, 9, 10, 5), ("2026-10-26 09:15", 10, 11, 9, 10, 5)])
    assert len(prepare_series(weekly, "X", "1w", datetime(2026, 10, 30, 15, 0, tzinfo=IST), calendar).frame) == 1
    assert len(prepare_series(weekly, "X", "1w", datetime(2026, 10, 30, 15, 31, tzinfo=IST), calendar).frame) == 2


def test_asof_cutoff_end_of_day_and_capped():
    now = datetime(2026, 11, 3, 11, 0, tzinfo=IST)
    assert asof_cutoff(date(2026, 10, 1), now) == datetime(2026, 10, 1, 23, 59, 59, tzinfo=IST)
    assert asof_cutoff(date(2026, 11, 3), now) == now
    assert asof_cutoff(None, now) == now


def test_latest_completed_bar_end(calendar):
    f = calendar.latest_completed_bar_end
    assert f("1d", datetime(2026, 11, 3, 11, 0, tzinfo=IST)) == datetime(2026, 11, 2, 15, 30, tzinfo=IST)
    assert f("1d", datetime(2026, 11, 3, 16, 0, tzinfo=IST)) == datetime(2026, 11, 3, 15, 30, tzinfo=IST)
    assert f("1h", datetime(2026, 11, 3, 11, 20, tzinfo=IST)) == datetime(2026, 11, 3, 11, 15, tzinfo=IST)
    assert f("4h", datetime(2026, 11, 3, 11, 20, tzinfo=IST)) == datetime(2026, 11, 2, 15, 30, tzinfo=IST)
    assert f("1w", datetime(2026, 11, 3, 11, 0, tzinfo=IST)) == datetime(2026, 10, 30, 15, 30, tzinfo=IST)


def test_projected_bar_starts_follow_trading_calendar(calendar):
    nxt = calendar.project_bar_starts(ts("2026-11-06 09:15"), "1d", 3)  # Mon 09-Nov, (Tue 10-Nov holiday), Wed 11, Thu 12
    assert [t.strftime("%Y-%m-%d %H:%M") for t in nxt] == ["2026-11-09 09:15", "2026-11-11 09:15", "2026-11-12 09:15"]
    hours = calendar.project_bar_starts(ts("2026-11-04 14:15"), "1h", 2)
    assert [t.strftime("%m-%d %H:%M") for t in hours] == ["11-04 15:15", "11-05 09:15"]
    four = calendar.project_bar_starts(ts("2026-11-04 13:15"), "4h", 2)
    assert [t.strftime("%m-%d %H:%M") for t in four] == ["11-05 09:15", "11-05 13:15"]


# --------------------------------------------------------------- caching


def test_incremental_merge_replaces_overlap_without_duplicates():
    old = frame([(f"2026-10-{d} 09:15", 10, 11, 9, 10 + d / 100, 5) for d in (26, 27, 28, 29)])
    new = frame([("2026-10-28 09:15", 10, 11, 9, 10.285, 7), ("2026-10-29 09:15", 10, 11, 9, 10.29, 9), ("2026-10-30 09:15", 10, 11, 9, 10.3, 9)])
    merged, mismatch = merge_incremental(old, new, tolerance=0.005)
    assert not mismatch
    assert merged.index.is_unique and len(merged) == 5
    assert merged.loc[ts("2026-10-28 09:15"), "volume"] == 7  # provider value replaced the cached one


def test_incremental_merge_flags_readjusted_history():
    old = frame([("2026-10-29 09:15", 10, 11, 9, 100, 5)])
    new = frame([("2026-10-29 09:15", 5, 5.5, 4.5, 50, 10)])  # 2:1 split re-adjustment
    _, mismatch = merge_incremental(old, new, tolerance=0.005)
    assert mismatch


def test_store_roundtrip_preserves_ist(tmp_path):
    from app.database import make_engine, migrate

    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    store = OhlcvStore(tmp_path, engine, "fixture")
    df = frame([("2026-10-29 09:15", 10, 11, 9, 10.5, np.nan), ("2026-10-29 10:15", 10.5, 11.5, 10, 11, 100)])
    store.save("M&M", "1h", df)
    back = store.load("M&M", "1h")
    assert list(back.index) == list(df.index) and str(back.index.tz) == "Asia/Kolkata"
    pd.testing.assert_frame_equal(back[list(df.columns)].reset_index(drop=True), df.reset_index(drop=True))
    assert back["adj_close"].isna().all()  # no adjusted close from this source: missing, never copied from close
