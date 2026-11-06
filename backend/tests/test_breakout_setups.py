"""10-day level: about-to-break-out states (Approaching, Very Near, Touched,
Breakout, Confirmed Breakout, Rejected, Expired) on the latest completed daily
candle. Hand-built candles with clear margins; fixtures, not market data."""

import numpy as np
import pandas as pd
import pytest

from app.config import SignalConfig
from app.indicators.core import atr_wilder
from app.signals.breakouts import SETUP_LABELS, setup_quality
from app.signals.scan import run_signals
from app.synthetic import trading_day_index

CFG = SignalConfig()


def frame(calendar, tail: list[tuple[float, float]]) -> pd.DataFrame:
    """60 base candles: 49 quiet ones around 90, then 10 candles whose highest high is
    exactly 100 (the 10-day high of the next candle); ``tail`` = (close, high) candles."""
    closes = list(90 + 3 * np.sin(np.arange(49) / 3.0)) + [94, 95, 96, 95, 96, 95, 96, 95, 96, 96]
    highs = [c + 3 for c in closes]
    highs[52] = 100.0
    for c, h in tail:
        closes.append(c)
        highs.append(h)
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(np.asarray(highs, float), np.maximum(o, c))
    l = np.minimum(o, c) - 3.0
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(c), 1e5)}, index=trading_day_index(len(c), calendar))


def setups(f: pd.DataFrame, cfg: SignalConfig = CFG, direction: str = "bullish") -> list[dict]:
    atr = atr_wilder(f["high"].to_numpy(float), f["low"].to_numpy(float), f["close"].to_numpy(float), 14)
    return [s for s in run_signals(f, "1d", atr, None, None, cfg, "T").setups if s["direction"] == direction]


def state(calendar, tail, **kw) -> str | None:
    s = setups(frame(calendar, tail), **kw)
    return s[0]["state"] if s else None


def test_approaching_and_distances(calendar):
    f = frame(calendar, [(98.5, 98.7)])
    s = setups(f)
    assert len(s) == 1 and s[0]["state"] == "approaching" and s[0]["level"] == 100.0
    atr = atr_wilder(f["high"].to_numpy(float), f["low"].to_numpy(float), f["close"].to_numpy(float), 14)[-1]
    assert s[0]["distance_pct"] == pytest.approx((100 - 98.5) / 100 * 100, abs=1e-3)  # (10D high - close) / 10D high x 100
    assert s[0]["distance_atr"] == pytest.approx((100 - 98.5) / atr, abs=1e-3)  # (10D high - close) / ATR(14)
    assert s[0]["level_kind"] == "10D high" and s[0]["reference_end"] < s[0]["time"]  # current candle excluded


def test_very_near_and_touched(calendar):
    assert state(calendar, [(99.4, 99.6)]) == "very_near"
    assert state(calendar, [(99.5, 100.6)]) == "touched"  # intraday through the level, close still very near


def test_rejected_intraday_push(calendar):
    assert state(calendar, [(97.0, 101.0)]) == "rejected"


def test_breakout_and_confirmed_breakout(calendar):
    assert state(calendar, [(101.5, 102.0)]) == "breakout"
    s = setups(frame(calendar, [(101.5, 102.0), (102.5, 103.0)]))
    assert s[0]["state"] == "confirmed_breakout" and s[0]["event_age"] == 1 and s[0]["two_close"] == "confirmed"


def test_breakout_closed_back_inside_is_rejected(calendar):
    s = setups(frame(calendar, [(101.5, 102.0), (98.0, 101.0)]))
    assert s[0]["state"] == "rejected" and s[0]["event_age"] == 1


def test_expired_setup(calendar):
    s = setups(frame(calendar, [(99.4, 99.6), (96.0, 96.2), (95.0, 95.2)]))
    assert s[0]["state"] == "expired" and s[0]["sessions_since_setup"] == 2
    # beyond the expiry window the stock simply drops out
    long_tail = [(99.4, 99.6)] + [(95.0, 95.2)] * (CFG.setup_expiry_sessions + 1)
    assert state(calendar, long_tail) is None


def test_far_from_the_level_is_no_setup(calendar):
    assert state(calendar, [(95.0, 95.2)]) is None


def test_either_rule_and_thresholds_are_configurable(calendar):
    wide = SignalConfig(about_to_breakout_pct=5.0, about_to_breakout_atr=3.0)
    assert state(calendar, [(96.0, 96.2)]) is None and state(calendar, [(96.0, 96.2)], cfg=wide) == "approaching"
    # 'both' needs the ATR distance too: a tiny ATR budget blocks it, 'either' accepts the % distance alone
    tight_atr = SignalConfig(about_to_breakout_atr=0.05, very_near_atr=0.01)
    assert state(calendar, [(98.5, 98.7)], cfg=tight_atr) is None
    either = SignalConfig(about_to_breakout_atr=0.05, very_near_atr=0.01, about_to_breakout_rule="either")
    assert state(calendar, [(98.5, 98.7)], cfg=either) == "approaching"


def test_bearish_mirror(calendar):
    f = frame(calendar, [(98.5, 98.7)])
    m = f.copy()
    m["open"], m["close"] = 200 - f["open"], 200 - f["close"]
    m["high"], m["low"] = 200 - f["low"], 200 - f["high"]
    s = setups(m, direction="bearish")
    assert s and s[0]["state"] == "approaching" and s[0]["level_kind"] == "10D low" and s[0]["level"] == 100.0


def test_no_look_ahead(calendar):
    f = frame(calendar, [(99.4, 99.6), (101.5, 102.0)])
    early = setups(f.iloc[:-1])
    assert early[0]["state"] == "very_near"  # what was known before the breakout candle
    assert setups(f)[0]["state"] == "breakout"


def test_setup_quality_is_explainable(calendar):
    s = setups(frame(calendar, [(99.4, 99.6)]))[0]
    q0, c0 = setup_quality(s)
    assert 0 <= q0 <= 100 and {c["name"] for c in c0} == {"proximity", "tightness", "volume"}
    q1, c1 = setup_quality({**s, "ema20": 97.0, "ema50": 95.0}, rs=0.05, stage="stage2_confirmed")
    names = {c["name"] for c in c1}
    assert {"trend", "relative_strength", "stage"} <= names and q1 > q0
    assert setup_quality({**s, "ema20": 97.0, "ema50": 95.0}, rs=-0.05, stage="other")[0] < q1
    assert set(SETUP_LABELS) == {"approaching", "very_near", "touched", "breakout", "confirmed_breakout", "rejected", "expired"}


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def adv(tmp_path_factory):
    from app.data.calendar import NSECalendar
    from tests.conftest import HOLIDAYS_SRC
    from tests.fixtures_adv import build_adv_app, prepare

    cal = NSECalendar.load(HOLIDAYS_SRC)
    client, app, provider = build_adv_app(tmp_path_factory, cal)
    with client:
        prepare(client, app, cal)
        yield client


def test_setups_api(adv):
    client = adv
    r = client.get("/api/scan/breakout-setups").json()
    rw = [x for x in r["items"] if x["symbol"] == "RWALK"]
    assert rw and rw[0]["state"] == "breakout" and rw[0]["level_kind"] == "10D high"  # RWALK's last candle breaks out
    x = rw[0]
    for k in ("level", "distance_pct", "distance_atr", "ema20", "ema50", "volume_ratio", "setup_quality", "setup_quality_components"):
        assert k in x
    assert x["stage"] is not None and "rs" in x and 0 <= x["setup_quality"] <= 100
    assert r["thresholds"]["approach_pct"] == 2.0 and r["thresholds"]["approach_atr"] == 0.5
    only = client.get("/api/scan/breakout-setups", params={"state": "approaching,very_near"}).json()
    assert all(i["state"] in ("approaching", "very_near") for i in only["items"])
    assert client.get("/api/scan/breakout-setups", params={"state": "bogus"}).status_code == 422
    ev = client.get("/api/evidence", params={"symbol": "RWALK", "source": "fresh_breakout"}).json()["items"]
    assert any(e["kind"] == "state" and e["signal_type"] == "breakout_setup_breakout" for e in ev)
    # the event scanner is unchanged: states never appear as breakout events
    fb = client.get("/api/scan/fresh-breakouts", params={"max_age": 60}).json()["items"]
    assert all(not i.get("kind", "").startswith("breakout_setup_") for i in fb)
