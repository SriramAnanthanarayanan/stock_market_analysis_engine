"""52-week high breakout / low breakdown (spec 5 / 15.3) and the official NSE
52-week reference. Synthetic fixtures, not market data."""

from datetime import date

import numpy as np
import pytest

from app.config import SignalConfig
from app.indicators.core import atr_wilder
from app.signals import nse52w
from app.signals.breakouts import crossing_events, touch_records, w52_kind, w52_params, week52_state
from tests.fixtures_e1 import frame_from

CFG = SignalConfig()


def series(calendar, n=300, last=None, spike_at=None, spike_high=None, low_spike_at=None, low_spike=None):
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    c = np.clip(c, 95, 105)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + 0.5
    l = np.minimum(o, c) - 0.5
    if spike_at is not None:
        h[spike_at] = spike_high
    if low_spike_at is not None:
        l[low_spike_at] = low_spike
    if last is not None:
        o[-1], h[-1], l[-1], c[-1] = last
    return frame_from(o, h, l, c, calendar)


def run(f, cfg=CFG):
    atr = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
    evs, walks = crossing_events(f, atr, w52_params(cfg), symbol="T", kind_of=w52_kind, config_hash="h", since=0, volume_lookback=20, first_lookback=20)
    return evs, walks, atr


def test_252_session_high_breakout(calendar):
    f = series(calendar, last=(105.0, 112.5, 104.8, 112.0))
    evs, walks, atr = run(f)
    hi = float(f["high"].iloc[-253:-1].max())
    last = [e for e in evs if e["index"] == len(f) - 1]
    assert len(last) == 1 and last[0]["kind"] == "week52_high_breakout" and last[0]["level"] == pytest.approx(hi)
    st = week52_state(f, atr, CFG, walks)
    assert st["status"] == "ok" and st["close_above_high"] and st["high_52w"] == pytest.approx(hi)
    assert st["fresh_cross_high"] is True and st["distance_from_high_pct"] > 0
    assert st["reference_end"] < st["time"]  # current candle excluded


def test_252_session_low_breakdown(calendar):
    f = series(calendar, last=(95.0, 95.2, 87.0, 87.5))
    evs, walks, atr = run(f)
    lo = float(f["low"].iloc[-253:-1].min())
    last = [e for e in evs if e["index"] == len(f) - 1]
    assert last and last[0]["kind"] == "week52_low_breakdown" and last[0]["level"] == pytest.approx(lo)
    assert week52_state(f, atr, CFG, walks)["close_below_low"] is True


def test_current_candle_excluded_and_window_is_252(calendar):
    # A higher high 253 sessions back is outside the window; the one 252 back is inside.
    f = series(calendar, n=300, spike_at=300 - 1 - 253, spike_high=130.0, last=(105.0, 112.5, 104.8, 112.0))
    _, walks, atr = run(f)
    st = week52_state(f, atr, CFG, walks)
    assert st["high_52w"] < 130 and st["close_above_high"]
    f2 = series(calendar, n=300, spike_at=300 - 1 - 252, spike_high=130.0, last=(105.0, 112.5, 104.8, 112.0))
    st2 = week52_state(f2, atr_wilder(f2["high"].to_numpy(), f2["low"].to_numpy(), f2["close"].to_numpy(), 14), CFG)
    assert st2["high_52w"] == pytest.approx(130.0) and not st2["close_above_high"]


def test_intraday_touch_versus_confirmed_close(calendar):
    base = series(calendar)
    hi = float(base["high"].iloc[-253:-1].max())
    f = series(calendar, last=(hi - 1.0, hi + 8.0, hi - 1.5, hi - 0.5))  # high pierces the 52-week high, close inside
    evs, walks, atr = run(f)
    assert not [e for e in evs if e["index"] == len(f) - 1]
    st = week52_state(f, atr, CFG, walks)
    assert st["touch_high"] is True and st["close_above_high"] is False
    t = [x for x in touch_records(f, walks, w52_kind, since=len(f) - 1) if x["direction"] == "bullish"]
    assert t and t[0]["kind"] == "week52_high_touch" and t[0]["close_status"] == "closed_inside"


def test_insufficient_history_is_never_a_52_week_breakout(calendar):
    f = series(calendar, n=200, last=(105.0, 140.0, 104.8, 139.0))
    evs, walks, atr = run(f)
    assert evs == []
    st = week52_state(f, atr, CFG, walks)
    assert st["status"] == "insufficient_history" and st["available_sessions"] == 199
    assert st["available_high"] == pytest.approx(float(f["high"].iloc[:-1].max())) and "high_52w" not in st


def test_price_basis_is_reported_and_consistent(calendar):
    f = series(calendar, last=(105.0, 112.5, 104.8, 112.0))
    _, walks, atr = run(f)
    st = week52_state(f, atr, CFG, walks)
    assert st["price_basis"] == "raw_split_adjusted" and "split" in st["adjustment_basis"] and "unadjusted" in st["adjustment_basis"]


def test_historical_asof_is_unchanged_by_later_candles(calendar):
    rng = np.random.default_rng(4)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, 700)))
    o = np.concatenate([[c[0]], c[:-1]])
    f = frame_from(o, np.maximum(o, c) * 1.005, np.minimum(o, c) * 0.995, c, calendar)
    full, _, _ = run(f)
    cut, _, _ = run(f.iloc[:500])
    keys = ("event_id", "index", "level", "kind", "first_in_lookback")
    assert [{k: e[k] for k in keys} for e in full if e["index"] < 500] == [{k: e[k] for k in keys} for e in cut]


# ------------------------------------------------------------------ official NSE reference
NSE_SAMPLE = '''"Disclaimer - The Data provided in the adjusted 52 week high and adjusted 52 week low columns  are adjusted for corporate actions (bonus, splits & rights).For actual (unadjusted) 52 week high & low prices, kindly refer bhavcopy."
"Effective for 03-Nov-2026"
"SYMBOL","SERIES","Adjusted 52_Week_High","52_Week_High_DT","Adjusted 52_Week_Low","52_Week_Low_DT"
"ABC","EQ","112.50","27-OCT-2026","80.40","20-JUN-2026"
"XYZ","EQ","2,450.00","01-FEB-2026","1,100.00","02-MAR-2026"
"BONDX","N1","101.00","01-FEB-2026","99.00","02-MAR-2026"
'''


def test_nse_report_parser_is_tolerant_and_strict():
    rows = nse52w.parse_report(NSE_SAMPLE)
    by = {r["symbol"]: r for r in rows}
    assert by["ABC"]["adj_high"] == 112.5 and by["ABC"]["adj_high_date"] == "2026-10-27" and by["ABC"]["adj_low_date"] == "2026-06-20"
    assert by["XYZ"]["adj_high"] == 2450.0 and by["XYZ"]["adj_low"] == 1100.0
    with pytest.raises(nse52w.ReportError):
        nse52w.parse_report("a,b,c\n1,2,3\n")


def test_nse_reference_store_lookup_and_compare(services_env):
    s, _ = services_env
    res = nse52w.refresh(s.engine, date(2026, 11, 3), text=NSE_SAMPLE, source="fixture")
    assert res["status"] == "ok" and res["rows"] == 2  # the N1 (bond) series is not an equity row
    ref = nse52w.lookup(s.engine, "2026-11-03")
    assert set(ref) == {"ABC", "XYZ"} and nse52w.latest_report_date(s.engine) == "2026-11-03"
    ok = nse52w.compare(112.4, 80.4, ref["ABC"])
    assert ok["status"] == "match"
    bad = nse52w.compare(140.0, 80.4, ref["ABC"])  # e.g. a corporate action the provider did not adjust
    assert bad["status"] == "mismatch" and bad["nse_adjusted_high"] == 112.5
    assert nse52w.compare(1.0, 1.0, None)["status"] == "unavailable"
    err = nse52w.refresh(s.engine, date(2026, 11, 4), text="garbage", source="fixture")
    assert err["status"] == "error"
