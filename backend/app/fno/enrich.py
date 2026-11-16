"""Informational context for selected F&O rows (never selects, ranks or hides).

Ported from Bhav copy src/enrichment.py ``enrich_signals``. Same metrics and
formulas, with these fixes:

* History comes from the database and is always "previous sessions only"
  (``trade_date < D``). The original appended today's row to its history CSV
  after enrichment, so a second run on the same day counted today twice
  (every "OI streak" read 0) and averages could include today.
* Windows need their full length (10 / 20 previous sessions, 20 for volume and
  delivery averages); the original used whatever rows existed. With the
  one-year backfill the windows are normally full; otherwise the value is
  empty, never computed from a partial window.
* Multi-day OI change and the OI build-up streak use the combined OI of all
  expiries (the front-month OI collapses at every rollover, so 5 / 20-day
  front-month changes compared different contracts).
* The 5-day basis average uses the same futures contract only (the original
  mixed the expiring and the next contract around a rollover).
* Index spot uses the corrected index names (FINNIFTY), and 10 / 20-day ranges
  are also available for index futures (index OHLC from the same NSE file).

Prices here are NSE's raw end-of-day prices (not adjusted for corporate
actions); a bonus / split distorts ranges, averages and basis history until
enough new sessions accumulate. Such contracts are flagged by the service.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from .config import FnoConfig


def _r(v: Any, nd: int = 2) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _prior(df: pd.DataFrame, symbol: str, d: date) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["symbol"] == symbol) & (df["trade_date"] < d.isoformat())].sort_values("trade_date")


def _today(df: pd.DataFrame, symbol: str, d: date) -> dict[str, Any] | None:
    if df.empty:
        return None
    m = df[(df["symbol"] == symbol) & (df["trade_date"] == d.isoformat())]
    return None if m.empty else m.iloc[0].to_dict()


def enrich(row: dict[str, Any], d: date, hist: dict[str, pd.DataFrame], cfg: FnoConfig) -> dict[str, Any]:
    """Enrichment of one selected row (SYMBOL, INSTRUMENT, EXPIRY_DT, CLOSE_today, OPEN_INT_today)."""
    symbol = str(row["SYMBOL"])
    fut_price = _r(row.get("CLOSE_today"), 6)
    und, comb, fut, opt = hist["underlying"], hist["combined_oi"], hist["futures"], hist["options"]
    enr: dict[str, Any] = {}

    today = _today(und, symbol, d)
    prev = _prior(und, symbol, d)
    spot = _r(today.get("close"), 6) if today else None
    close_ref = spot if spot else fut_price

    # ---- basis
    if spot and fut_price:
        enr["spot"] = round(spot, 2)
        enr["basis"] = round(fut_price - spot, 2)
        enr["basis_pct"] = round((fut_price - spot) / spot * 100, 2)

    # ---- volume ratio, delivery, delivery surge, volume rank (stocks)
    today_vol = _r(today.get("volume"), 6) if today else None
    if today_vol and len(prev) >= cfg.volume_avg_sessions:
        avg = pd.to_numeric(prev["volume"].tail(cfg.volume_avg_sessions), errors="coerce").mean()
        if pd.notna(avg) and avg > 0:
            enr["volume_ratio"] = round(today_vol / avg, 2)
    dp = _r(today.get("deliv_per")) if today else None
    if dp is not None:
        enr["delivery_pct"] = dp
        if len(prev) >= cfg.delivery_avg_sessions:
            avg_d = pd.to_numeric(prev["deliv_per"].tail(cfg.delivery_avg_sessions), errors="coerce").mean()
            if pd.notna(avg_d) and avg_d > 0:
                enr["delivery_surge_pct"] = round((dp - avg_d) / avg_d * 100, 2)
    if today_vol and not prev.empty:
        lo = (pd.Timestamp(d) - pd.Timedelta(days=cfg.rank_history_days)).date().isoformat()
        vols = pd.to_numeric(prev[prev["trade_date"] >= lo]["volume"], errors="coerce").dropna()
        if len(vols) >= cfg.rank_min_obs:
            enr["volume_rank_pct"] = round(float((vols < today_vol).mean() * 100), 1)

    # ---- 10D / 20D high-low of previous sessions vs today's spot (futures close as fallback)
    if close_ref and not prev.empty:
        highs = pd.to_numeric(prev["high"], errors="coerce")
        lows = pd.to_numeric(prev["low"], errors="coerce")
        for n in cfg.range_sessions:
            if len(prev) < n:
                continue
            h, low = highs.tail(n).max(), lows.tail(n).min()
            if pd.isna(h) or pd.isna(low) or h == 0 or low == 0:
                continue
            enr[f"high_{n}d"] = round(float(h), 2)
            enr[f"low_{n}d"] = round(float(low), 2)
            enr[f"dist_high_{n}d_pct"] = round((close_ref - h) / h * 100, 2)
            enr[f"dist_low_{n}d_pct"] = round((close_ref - low) / low * 100, 2)

    # ---- option chain of today (nearest expiry)
    o_today = _today(opt, symbol, d)
    if o_today:
        for k in ("pcr", "max_call_strike", "max_call_oi", "max_put_strike", "max_put_oi"):
            v = _r(o_today.get(k))
            if v is not None:
                enr[k] = v
        if o_today.get("expiry"):
            enr["option_expiry"] = o_today["expiry"]

    # ---- OI history on combined OI of all expiries
    c_today = _today(comb, symbol, d)
    c_prev = _prior(comb, symbol, d)
    oi_all = _r(c_today.get("oi"), 0) if c_today else None
    if oi_all is not None:
        enr["oi_all_expiries"] = oi_all
        series = pd.to_numeric(c_prev["oi"], errors="coerce").tolist() if not c_prev.empty else []
        for n in cfg.oi_change_sessions:
            if len(series) >= n:
                base = series[-n]
                if pd.notna(base) and base > 0:
                    enr[f"oi_change_{n}d_pct"] = round((oi_all - base) / base * 100, 2)
        if series:
            all_oi = series + [oi_all]
            streak = 0
            for i in range(len(all_oi) - 1, 0, -1):
                if pd.notna(all_oi[i]) and pd.notna(all_oi[i - 1]) and all_oi[i] > all_oi[i - 1]:
                    streak += 1
                else:
                    break
            enr["oi_build_streak"] = streak

    # ---- basis trend: same contract, previous sessions
    if "basis" in enr and not fut.empty and not prev.empty:
        same = fut[(fut["symbol"] == symbol) & (fut["expiry_label"] == row.get("EXPIRY_DT")) & (fut["trade_date"] < d.isoformat())]
        m = same.merge(prev[["trade_date", "close"]].rename(columns={"close": "spot_close"}), on="trade_date").sort_values("trade_date")
        basis = (pd.to_numeric(m["close"], errors="coerce") - pd.to_numeric(m["spot_close"], errors="coerce")).dropna()
        if len(basis) >= cfg.basis_avg_sessions:
            avg5 = float(basis.tail(cfg.basis_avg_sessions).mean())
            enr["basis_avg"] = round(avg5, 2)
            band = abs(avg5) * cfg.basis_trend_band if avg5 != 0 else cfg.basis_trend_band_zero
            cur = enr["basis"]
            enr["basis_trend"] = "Expanding" if cur > avg5 + band else "Contracting" if cur < avg5 - band else "Neutral"

    # ---- ATM IV, IV rank / percentile (previous sessions)
    if o_today and o_today.get("atm_iv") is not None and pd.notna(o_today.get("atm_iv")):
        iv = float(o_today["atm_iv"])
        enr["atm_iv"] = round(iv, 2)
        enr["iv_expiry"] = o_today.get("iv_expiry")
        o_prev = _prior(opt, symbol, d)
        if not o_prev.empty:
            lo = (pd.Timestamp(d) - pd.Timedelta(days=cfg.rank_history_days)).date().isoformat()
            ivs = pd.to_numeric(o_prev[o_prev["trade_date"] >= lo]["atm_iv"], errors="coerce").dropna()
            if len(ivs) >= cfg.rank_min_obs:
                lo_iv, hi_iv = float(ivs.min()), float(ivs.max())
                if hi_iv > lo_iv:
                    enr["iv_rank"] = round((iv - lo_iv) / (hi_iv - lo_iv) * 100, 1)
                enr["iv_percentile"] = round(float((ivs < iv).mean() * 100), 1)

    enr["history_sessions"] = int(len(prev))
    return enr
