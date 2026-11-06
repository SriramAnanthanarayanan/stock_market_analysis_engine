"""Offline demo server with SYNTHETIC data, for UI verification without network access.

    python scripts/synthetic_demo.py [--port 8031] [--data-dir <dir>]

Everything is synthetic (fixtures, not market data): a small universe with a
VCP breakout that is also a fresh 10-day breakout + Marubozu + volume shock, a
VCP near its pivot, a 52-week breakout, channel / triangle / rectangle bands and
random walks, plus the fake NSE index history used by the tests. The database
and cache live in ``--data-dir`` (default: a new temporary directory) - never in
backend/data or backend/cache. Scans 1D, 1W and 1H run at startup.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.data.calendar import NSECalendar  # noqa: E402
from app.indices.data import resample_weekly  # noqa: E402
from app.synthetic import POSITIVE, band_series, intraday_index, random_walk, with_index  # noqa: E402
from app.synthetic_vcp import VCPSpec, vcp_frame  # noqa: E402
from tests.conftest import FixtureProvider, make_settings  # noqa: E402
from tests.fixtures_adv import combo_frame  # noqa: E402
from tests.fixtures_e2 import FakeHistory, catalogue  # noqa: E402

UNIVERSE = [
    ("RWALK", "Synthetic VCP Breakout Ltd.", True),
    ("VCPNEAR", "Synthetic VCP Near Pivot Ltd.", True),
    ("BREAK52", "Synthetic 52-Week Breakout Ltd.", False),
    ("FCHAN", "Synthetic Falling Channel Ltd.", True),
    ("ATRI", "Synthetic Ascending Triangle Ltd.", False),
    ("RECT", "Synthetic Rectangle Ltd.", True),
    ("WALKA", "Synthetic Walk A Ltd.", True),
    ("WALKB", "Synthetic Walk B Ltd.", False),
    ("WALKC", "Synthetic Walk C Ltd.", True),
    ("WALKD", "Synthetic Walk D Ltd.", False),
    ("VCP4T", "Synthetic Four-Contraction VCP Ltd.", True),
    ("SETUP10", "Synthetic About-to-Break-Out Ltd.", True),
    ("IPONEW", "Synthetic IPO Breakout Ltd.", False),
    ("IPOBASE", "Synthetic IPO Base Ltd.", False),
]
# industries mapped to sector indices of the fixture catalogue (sector-strength evidence)
INDUSTRY = {"RWALK": "Information Technology", "VCPNEAR": "Metals & Mining", "VCP4T": "Information Technology", "SETUP10": "Metals & Mining"}
IPO_SYMBOLS = ("IPONEW", "IPOBASE")


def _prepend_history(f: pd.DataFrame, cal: NSECalendar, bars: int, seed: int) -> pd.DataFrame:
    """Older random-walk history ending at the series' first close (keeps the pattern at the end)."""
    from app.synthetic import trading_day_index

    rng = np.random.default_rng(seed)
    first = float(f["close"].iloc[0])
    rets = rng.normal(0, 0.012, bars)
    path = first / np.exp(np.cumsum(rets[::-1]))[::-1]
    o = np.concatenate([[path[0]], path[:-1]])
    spread = np.abs(rng.normal(0, 0.006, bars)) * path
    old = pd.DataFrame({"open": o, "high": np.maximum(o, path) + spread, "low": np.minimum(o, path) - spread, "close": path, "volume": 1e5 * (1 + 0.3 * rng.random(bars))})
    out = pd.concat([old, f.reset_index(drop=True)], ignore_index=True)
    out.index = trading_day_index(len(out), cal)
    out.index.name = "ts"
    return out


def break52(cal: NSECalendar) -> pd.DataFrame:
    f = random_walk(700, cal, seed=21, vol=0.01).copy()
    hi = float(f["high"].iloc[-253:-1].max())
    o = float(f["close"].iloc[-2])
    c = hi * 1.03
    f.iloc[-1, f.columns.get_loc("open")] = o
    f.iloc[-1, f.columns.get_loc("close")] = c
    f.iloc[-1, f.columns.get_loc("high")] = c * 1.002
    f.iloc[-1, f.columns.get_loc("low")] = min(o, c) * 0.995
    f.iloc[-1, f.columns.get_loc("volume")] = float(f["volume"].iloc[-21:-1].mean()) * 2.6
    return f


def setup10(cal: NSECalendar) -> pd.DataFrame:
    """A walk whose latest candle closes 0.3 % under its previous 10-day high (about to break out)."""
    f = random_walk(700, cal, seed=41, vol=0.012).copy()
    i = len(f) - 1
    hi = float(f["high"].iloc[i - 10 : i].max())
    o = float(f["close"].iloc[i - 1])
    c = hi * 0.997
    f.iloc[i, f.columns.get_loc("open")] = c * 0.996
    f.iloc[i, f.columns.get_loc("close")] = c
    f.iloc[i, f.columns.get_loc("high")] = c * 1.0005
    f.iloc[i, f.columns.get_loc("low")] = c * 0.993
    return f


def frames(cal: NSECalendar) -> dict[tuple[str, str], pd.DataFrame]:
    from app.synthetic_ipo import IPOSpec, ipo_frame

    four = dict(depths=(28, 15, 8, 4), recover=(0.99, 0.99, 0.99, 0.985), down_bars=(8, 6, 4, 3), up_bars=(8, 6, 5, 4), volumes=(1e5, 0.8e5, 0.6e5, 0.4e5))
    daily = {
        "RWALK": _prepend_history(combo_frame(cal), cal, 900, 1),
        "VCPNEAR": _prepend_history(vcp_frame(VCPSpec(), cal), cal, 900, 2),
        "BREAK52": break52(cal),
        "FCHAN": _prepend_history(band_series(POSITIVE["falling_channel"], cal), cal, 600, 3),
        "ATRI": _prepend_history(band_series(POSITIVE["ascending_triangle"], cal), cal, 600, 4),
        "RECT": _prepend_history(band_series(POSITIVE["rectangle"], cal), cal, 600, 5),
        "WALKA": random_walk(1500, cal, seed=31),
        "WALKB": random_walk(1500, cal, seed=32, vol=0.02),
        "WALKC": random_walk(1500, cal, seed=33),
        "WALKD": random_walk(1500, cal, seed=34, vol=0.015),
        "VCP4T": _prepend_history(vcp_frame(VCPSpec(**four), cal), cal, 900, 6),
        "SETUP10": setup10(cal),
        "IPONEW": ipo_frame(IPOSpec(tail=[(125.0, 3.0, 3.0e6)]), cal),
        "IPOBASE": ipo_frame(IPOSpec(tail=[(118.5, 1.0, 0.5e6), (119.0, 1.0, 0.5e6)]), cal),
    }
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for sym, d in daily.items():
        out[(sym, "1d")] = d
        out[(sym, "1wk")] = resample_weekly(d)
        tail = d.iloc[-700:]
        out[(sym, "1h")] = with_index(tail, intraday_index(len(tail), cal))
    return out


def universe_csv() -> tuple[str, str]:
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"] + [f"{c},{INDUSTRY.get(s, 'Synthetic')},{s},EQ,INE{i:09d}" for i, (s, c, _) in enumerate(UNIVERSE)]
    fno = ["UNDERLYING                          ,SYMBOL    ,NOV-26     ,DEC-26     ,JAN-27", "Derivatives on Individual Securities,Symbol    ,NOV-26     ,DEC-26     ,JAN-27"]
    fno += [f"{c.upper():36},{s:10},500        ,500        ,500" for s, c, f in UNIVERSE if f]
    return "\n".join(lines) + "\n", "\n".join(fno) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8031)
    ap.add_argument("--data-dir", type=Path, default=None)
    args = ap.parse_args()
    import uvicorn

    from app.main import create_app

    root = args.data_dir or Path(tempfile.mkdtemp(prefix="pas-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    cal = NSECalendar.load(BACKEND / "data" / "nse_holidays.json")
    settings = make_settings(root, port=args.port)
    app = create_app(settings, provider=FixtureProvider(frames(cal)), background=False, index_history=FakeHistory(cal))
    s = app.state.services
    nifty, fno = universe_csv()

    def boot() -> None:
        time.sleep(1.0)
        if not s.universe.has_snapshot("nifty500"):
            s.universe.refresh_nifty500(text=nifty, source="synthetic-demo")
            s.universe.refresh_fno(text=fno, source="synthetic-demo")
        # official listing dates of the two synthetic IPOs (the NSE equity list format)
        fr = frames(cal)
        eq = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n" + "".join(f"{sym},{sym} Ltd,EQ,{fr[(sym, '1d')].index[0].strftime('%d-%b-%Y').upper()}\n" for sym in IPO_SYMBOLS)
        s.universe.refresh_listing_dates(text=eq, source="synthetic-demo", min_rows=1)
        s.indices.refresh_catalogue(catalogue(cal), min_rows=5)
        s.indices.refresh_all()
        from app.scanner import ScanRequest

        for tf in ("1w", "1d", "1h"):
            sid = s.scans.run_now(ScanRequest(universe="all", timeframe=tf, trigger="startup"), timeout=600)
            print(f"[demo] scan {tf}: {sid}", flush=True)
        print(f"[demo] ready - SYNTHETIC data in {root}", flush=True)

    threading.Thread(target=boot, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
