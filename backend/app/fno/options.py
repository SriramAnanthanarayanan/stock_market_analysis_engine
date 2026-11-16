"""Per-symbol option summary of one F&O bhavcopy (informational only).

Ported from Bhav copy src/enrichment.py (``_calc_option_metrics``,
``_calc_atm_iv``):

* PCR = total put OI / total call OI of the nearest option expiry.
* Call / put wall = strike with the highest call / put OI of that expiry.
* ATM IV, Brenner-Subrahmanyam approximation:
  ``IV = mean(CE, PE settlement at the strike nearest spot) * sqrt(2 pi) / (spot * sqrt(T))``,
  ``T`` = calendar days to expiry / 365. An approximation (spot, not forward;
  settlement prices of illiquid strikes are NSE theoretical prices).

Fix: the original returned no IV for the last 0-1 days before an expiry (and so
nothing on expiry days). Now the nearest expiry with at least
``iv_min_days_to_expiry`` days left is used.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from .config import FnoConfig
from .parse import OPTIONS


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def atm_iv(near: pd.DataFrame, spot: float, days: int) -> tuple[float | None, float | None]:
    """(IV in %, ATM strike) for one expiry's option rows, or (None, None)."""
    if days <= 0 or not spot or spot <= 0:
        return None, None
    opts = near.dropna(subset=["STRIKE_PR"])
    if opts.empty:
        return None, None
    dist = (opts["STRIKE_PR"] - spot).abs()
    strike = float(opts.loc[dist.idxmin(), "STRIKE_PR"])
    atm = opts[opts["STRIKE_PR"] == strike]
    prices = []
    for typ in ("CE", "PE"):
        p = atm[atm["OPTION_TYP"] == typ]["SETTLE_PR"]
        if not p.empty and pd.notna(p.iloc[0]) and p.iloc[0] > 0:
            prices.append(float(p.iloc[0]))
    if not prices:
        return None, strike
    t = days / 365.0
    iv = (sum(prices) / len(prices)) * math.sqrt(2 * math.pi) / (spot * math.sqrt(t))
    return round(iv * 100, 2), strike


def option_summaries(fo: pd.DataFrame, spot: dict[str, float], trade_date: date, cfg: FnoConfig) -> list[dict[str, Any]]:
    opts = fo[fo["INSTRUMENT"].isin(OPTIONS)].copy()
    if opts.empty:
        return []
    opts["_exp"] = pd.to_datetime(opts["EXPIRY_DT"], format="%d-%b-%Y", errors="coerce")
    opts = opts.dropna(subset=["_exp"])
    opts["OPEN_INT"] = pd.to_numeric(opts["OPEN_INT"], errors="coerce").fillna(0)
    out: list[dict[str, Any]] = []
    for symbol, g in opts.groupby("SYMBOL", sort=True):
        near_exp = g["_exp"].min()
        near = g[g["_exp"] == near_exp]
        calls = near[near["OPTION_TYP"] == "CE"]
        puts = near[near["OPTION_TYP"] == "PE"]
        call_oi = float(calls["OPEN_INT"].sum())
        put_oi = float(puts["OPEN_INT"].sum())
        row: dict[str, Any] = {
            "symbol": str(symbol),
            "instrument": str(g["INSTRUMENT"].iloc[0]),
            "expiry": near_exp.date().isoformat(),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "pcr": round(put_oi / call_oi, 2) if call_oi > 0 else None,
            "max_call_strike": None,
            "max_call_oi": None,
            "max_put_strike": None,
            "max_put_oi": None,
            "spot": spot.get(str(symbol)),
            "iv_expiry": None,
            "iv_days": None,
            "atm_strike": None,
            "atm_iv": None,
        }
        if not calls.empty and calls["OPEN_INT"].max() > 0:
            best = calls.loc[calls["OPEN_INT"].idxmax()]
            row["max_call_strike"], row["max_call_oi"] = _num(best["STRIKE_PR"]), float(best["OPEN_INT"])
        if not puts.empty and puts["OPEN_INT"].max() > 0:
            best = puts.loc[puts["OPEN_INT"].idxmax()]
            row["max_put_strike"], row["max_put_oi"] = _num(best["STRIKE_PR"]), float(best["OPEN_INT"])
        s = row["spot"]
        if s:
            for exp in sorted(g["_exp"].unique()):
                days = (pd.Timestamp(exp).date() - trade_date).days
                if days < cfg.iv_min_days_to_expiry:
                    continue
                iv, strike = atm_iv(g[g["_exp"] == exp], float(s), days)
                row.update(iv_expiry=pd.Timestamp(exp).date().isoformat(), iv_days=days, atm_strike=strike, atm_iv=iv)
                break
        out.append(row)
    return out
