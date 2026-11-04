"""Fixture app for the advanced scanners (VCP, confluence): fixture universe, the
fake official index history (Stage Analysis / RRG benchmarks) and a 1W + 1D scan.

RWALK's daily series is replaced by a synthetic VCP whose last candle breaks out
above the pivot as a bullish Marubozu on ~4x volume - the same session is then a
fresh 10-day breakout, a volume shock, a Marubozu and a VCP breakout.
Fixtures, not market data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.synthetic_vcp import VCPSpec, pivot_high, vcp_frame


def combo_frame(calendar) -> pd.DataFrame:
    spec = VCPSpec()
    f = vcp_frame(spec, calendar)
    piv = pivot_high(spec) + spec.wick
    prev_close = float(f["close"].iloc[-1])
    idx = f.index
    # one more trading day for the breakout candle
    from app.synthetic import trading_day_index

    new_idx = trading_day_index(len(f) + 1, calendar)
    o = prev_close
    c = piv * 1.025
    rng = c - o
    row = pd.DataFrame({"open": [o], "high": [c + 0.02 * rng], "low": [o - 0.02 * rng], "close": [c], "volume": [3.0e5]})
    out = pd.concat([f.reset_index(drop=True), row], ignore_index=True)
    out.index = new_idx
    out.index.name = idx.name
    return out


def build_adv_app(tmp_path_factory, calendar):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import FNO_CSV, NIFTY_CSV, FixtureProvider, fixture_frames, make_settings
    from tests.fixtures_e2 import FakeHistory, catalogue

    frames = fixture_frames(calendar)
    frames[("RWALK", "1d")] = combo_frame(calendar)
    settings = make_settings(tmp_path_factory.mktemp("adv"))
    provider = FixtureProvider(frames, fail={"FAIL1"})
    app = create_app(settings, provider=provider, background=False, index_history=FakeHistory(calendar))
    return TestClient(app), app, provider


def prepare(client, app, calendar):
    from tests.fixtures_e2 import catalogue
    from tests.conftest import FNO_CSV, NIFTY_CSV
    from tests.test_api import refresh, wait_scan

    s = app.state.services
    s.universe.refresh_nifty500(text=NIFTY_CSV, source="fixture")
    s.universe.refresh_fno(text=FNO_CSV, source="fixture")
    s.indices.refresh_catalogue(catalogue(calendar), min_rows=5)
    s.indices.refresh_all()
    w = wait_scan(client, refresh(client, timeframe="1w")["scan_id"])
    d = wait_scan(client, refresh(client, timeframe="1d")["scan_id"])
    assert w["state"] == "completed" and d["state"] == "completed"
    return s, w, d


def is_finite(x) -> bool:
    return x is not None and np.isfinite(float(x))
