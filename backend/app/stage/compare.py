"""Print RS Line / Stage values for manual comparison with TradingView.

    python -m app.stage.compare ITC ICICIBANK --timeframe 1w --dates 2026-09-28,2026-10-26
    python -m app.stage.compare "NIFTY IT" --index --timeframe 1d --last 5

On TradingView: open the symbol on the same timeframe, add the "Relative
Strength" indicator from rs_line.txt with Period = 52 (weekly) / 63 (daily) and
Comparative Symbol = NSE:NIFTY, turn on "RS Mean" (50), and read RS, MA and the
bubble state in the Data Window for the same bars (weekly bars are labelled by
the week's Monday here; TradingView labels the weekly bar by its first trading
day). Values should agree to rounding when both use the same closes; any
difference points to different candles (provider, adjustment) rather than the
formula, which tests/test_stage_e3.py checks bar by bar against a Pine emulation.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from ..config import load_settings
from ..timeutil import now_ist
from .rs_line import align_benchmark, compute_rs_line, rs_length_for


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--index", action="store_true", help="symbols are official NSE index symbols")
    ap.add_argument("--timeframe", default="1w", choices=["1h", "4h", "1d", "1w"])
    ap.add_argument("--dates", default="", help="comma-separated bar dates (YYYY-MM-DD)")
    ap.add_argument("--last", type=int, default=5, help="print the last N bars when --dates is empty")
    args = ap.parse_args(argv)

    from ..services import Services

    s = Services(load_settings())
    cfg = s.stage_cfg
    cut = now_ist()
    bench = s.stage.benchmark(args.timeframe, cut)
    length = rs_length_for(args.timeframe, cfg)
    print(f"benchmark {cfg.benchmark} (official NSE series), timeframe {args.timeframe}, RS period {length}, RS SMA {cfg.rs_ma_length}, price SMA {cfg.price_sma_length}, completed candles <= {cut:%Y-%m-%d %H:%M} IST")
    for sym in args.symbols:
        frame = s.stage.frame(sym if args.index else sym.upper(), "index" if args.index else "stock", args.timeframe, cut)
        al = align_benchmark(frame.index, bench, cfg.benchmark_max_ffill_bars)
        rs = compute_rs_line(frame["close"].to_numpy(dtype=float), al.values, length, cfg, al)
        df = pd.DataFrame(
            {
                "close": np.round(frame["close"].to_numpy(dtype=float), 2),
                "benchmark": np.round(al.values, 2),
                "RS": np.round(rs.res, 6),
                "RS_SMA50": np.round(rs.sma_res, 6),
                "trend_angle": np.round(rs.angle, 6),
                "MA_rising": rs.ma_rising,
                "MA_falling": rs.ma_falling,
                "bubble": np.where(rs.pos_div, "bullish", np.where(rs.neg_div, "bearish", "")),
            },
            index=frame.index.date,
        )
        if args.dates:
            wanted = [pd.Timestamp(d).date() for d in args.dates.split(",") if d.strip()]
            df = df[[d in wanted for d in df.index]]
        else:
            df = df.tail(args.last)
        print(f"\n{sym}  (alignment: {al.exact} exact, {al.carried} carried, {al.missing} missing)")
        print(df.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
