"""IPO Base Breakout on completed daily candles (docs/ipo-base.md).

Concept (not an IPO-launch / hype scanner): a newly listed stock goes through
an initial **price discovery** period, volatility settles, it builds its first
**post-listing base** under a resistance level (the pivot), and later breaks out
above it on expanding volume. References: William O'Neil / IBD descriptions of
the IPO base (buy point at the base high, breakout volume well above average);
secondary sources listed in the doc. Every number is a configurable
ENGINEERING default in ``IPOConfig`` (initial values from the task), not a rule.

The listing date is the official first trading date (NSE equity list) when
known; otherwise the first candle of a short provider history is used and
flagged. A history that starts more than ``listing_gap_tolerance_sessions``
after the official listing is not evaluated (the discovery period is missing).

Algorithm (walk over the candles since listing, no look-ahead):

* resistance before candle j = the highest high since listing up to j-1; the
  **base** runs from that high to j-1: base high (= pivot), base low, depth
  ``(high - low) / high x 100``, length, resistance touches (separate highs
  within ``resistance_zone_pct`` of the base high, the first high included);
* the base is **valid** when the stock is at least ``min_age_bars`` old, the
  base is ``min_base_bars``-``max_base_bars`` long and ``min_base_depth_pct``-
  ``max_base_depth_pct`` deep, has ``min_resistance_touches`` touches and
  volatility has settled (mean daily range of the last ``settle_window_bars``
  base sessions <= ``settle_range_ratio`` x that of the discovery period);
* a completed close above a valid base's pivot is a **breakout** when its
  volume is >= ``breakout_volume_ratio`` x the average of the previous
  sessions since listing (up to ``volume_avg_bars``); a close back below the
  pivot within ``failed_breakout_bars`` sessions is a **Failed Breakout**; a
  close below the base low after the breakout **Invalidates** it. A close
  above the resistance without a valid base (or without breakout volume) just
  makes a new high: the next base starts from it.

States at the latest candle: Too Young, Discovery Phase, Base Forming, Base
Validated, Near Pivot, Breakout, Failed Breakout, Invalidated. An IPO base is
not classified as a VCP (that needs successive contractions; app.vcp).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ..config import IPOConfig
from ..timeutil import IST, epoch_s, iso

STATUS_LABELS = {
    "too_young": "Too Young",
    "discovery_phase": "Discovery Phase",
    "base_forming": "Base Forming",
    "base_validated": "Base Validated",
    "near_pivot": "Near Pivot",
    "breakout": "Breakout",
    "failed_breakout": "Failed Breakout",
    "invalidated": "Invalidated",
}
STATUS_RANK = {"breakout": 0, "near_pivot": 1, "base_validated": 2, "base_forming": 3, "discovery_phase": 4, "failed_breakout": 5, "too_young": 6, "invalidated": 7}


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _clamp(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _mean(a: np.ndarray) -> float | None:
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else None


def listing_index(frame: pd.DataFrame, listing_date: date | None, cfg: IPOConfig, calendar=None) -> tuple[int | None, str, str | None]:
    """(index of the listing candle, source, problem). ``None`` index = not evaluable."""
    if not len(frame):
        return None, "none", "no candles"
    idx = frame.index.tz_convert(IST)
    first = idx[0].date()
    if listing_date is not None:
        if first < listing_date:
            pos = int(np.searchsorted(np.array([d.date() for d in idx]), listing_date))
            return (pos if pos < len(frame) else None), "nse_equity_list", None
        gap = _sessions_between(listing_date, first, calendar)
        if gap > cfg.listing_gap_tolerance_sessions:
            return None, "nse_equity_list", f"provider history starts {gap} sessions after the official listing date {listing_date.isoformat()}: the discovery period is missing"
        return 0, "nse_equity_list", None
    if not cfg.first_candle_fallback:
        return None, "none", "no official listing date (NSE equity list not loaded for this symbol)"
    # optional fallback: a short history whose first candle is taken as the listing (flagged)
    if len(frame) <= cfg.max_age_bars + cfg.listing_gap_tolerance_sessions:
        return 0, "first_candle", None
    return None, "first_candle", "long history and no official listing date: not a recent listing"


def _sessions_between(a: date, b: date, calendar=None) -> int:
    """Trading sessions in [a, b) (weekdays when no calendar is given)."""
    from datetime import timedelta

    n, d = 0, a
    while d < b:
        if (calendar.is_trading_day(d) if calendar is not None else d.weekday() < 5):
            n += 1
        d += timedelta(days=1)
    return n


def _range_pct(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (h - l) / c * 100.0


def _touches(h: np.ndarray, a: int, b: int, level: float, cfg: IPOConfig) -> list[int]:
    """Separate highs of candles a..b within resistance_zone_pct below ``level`` (first one = a)."""
    zone = level * (1.0 - cfg.resistance_zone_pct / 100.0)
    out: list[int] = []
    for i in range(a, b + 1):
        if h[i] >= zone and (not out or i - out[-1] >= cfg.touch_separation_bars):
            out.append(i)
    return out


def _base(h, l, c, v, L: int, j: int, cfg: IPOConfig) -> dict[str, Any] | None:
    """Base before candle j (candles res_i..j-1), with its gates. None when j <= L+1."""
    if j - 1 < L + 1:
        return None
    seg = h[L:j]
    res_i = L + int(np.argmax(seg))
    H = float(h[res_i])
    lo_seg = l[res_i:j]
    lo_i = res_i + int(np.argmin(lo_seg))
    low = float(lo_seg.min())
    length = (j - 1) - res_i + 1
    depth = (H - low) / H * 100.0
    touches = _touches(h, res_i, j - 1, H, cfg)
    age = j - L  # completed sessions since listing before candle j (listing candle included)
    rp = _range_pct(h, l, c)
    disc = rp[L : min(L + cfg.discovery_bars, j)]
    right = rp[max(res_i, j - cfg.settle_window_bars) : j]
    disc_r, right_r = _mean(disc), _mean(right)
    settle = (right_r / disc_r) if (disc_r and right_r is not None) else None
    vd = _mean(v[L : min(L + cfg.discovery_bars, j)])
    vr = _mean(v[max(res_i, j - cfg.settle_window_bars) : j])
    vb = _mean(v[res_i:j])
    gates = [
        {"name": "age", "passed": age >= cfg.min_age_bars, "value": age, "rule": f">= {cfg.min_age_bars} completed sessions since listing"},
        {"name": "base_length", "passed": cfg.min_base_bars <= length <= cfg.max_base_bars, "value": length, "rule": f"{cfg.min_base_bars}-{cfg.max_base_bars} sessions from the base high"},
        {"name": "base_depth", "passed": cfg.min_base_depth_pct <= depth <= cfg.max_base_depth_pct, "value": round(depth, 2), "rule": f"{cfg.min_base_depth_pct}-{cfg.max_base_depth_pct} % below the base high"},
        {"name": "resistance_touches", "passed": len(touches) >= cfg.min_resistance_touches, "value": len(touches), "rule": f">= {cfg.min_resistance_touches} separate highs within {cfg.resistance_zone_pct} % of the base high (ASSUMPTION zone)"},
        {"name": "volatility_settled", "passed": None if settle is None else settle <= cfg.settle_range_ratio, "value": _r(settle, 3), "rule": f"mean daily range of the last {cfg.settle_window_bars} base sessions <= {cfg.settle_range_ratio} x the discovery period's (ASSUMPTION)"},
    ]
    return {
        "res_i": res_i, "high": H, "low": low, "low_i": lo_i, "length": length, "depth": depth, "touches": touches, "age": age,
        "settle_ratio": settle, "discovery_range_pct": disc_r, "right_range_pct": right_r,
        "vol_discovery": vd, "vol_right": vr, "vol_base": vb, "gates": gates,
        "valid": all(g["passed"] is True for g in gates),
    }


def _score(b: dict[str, Any], close: float, cfg: IPOConfig) -> tuple[float, list[dict[str, Any]]]:
    w = cfg.weights
    d = b["depth"]
    depth_s = 1.0 if 15.0 <= d <= 30.0 else (0.5 + 0.5 * _clamp((d - cfg.min_base_depth_pct) / (15.0 - cfg.min_base_depth_pct)) if d < 15.0 else _clamp(1.0 - 0.4 * (d - 30.0) / max(1e-9, cfg.max_base_depth_pct - 30.0)))
    n = b["length"]
    len_s = 1.0 if 25 <= n <= 60 else (0.7 if n < 25 else _clamp(1.0 - 0.5 * (n - 60) / max(1, cfg.max_base_bars - 60)))
    t = len(b["touches"])
    res_s = 1.0 if t >= 3 else 0.7 if t == 2 else 0.3
    dry = (b["vol_right"] / b["vol_discovery"]) if (b["vol_right"] is not None and b["vol_discovery"]) else None
    dry_s = 0.0 if dry is None else _clamp((1.0 - dry) / 0.5)
    tight_s = 0.0 if b["settle_ratio"] is None else _clamp((1.0 - b["settle_ratio"]) / 0.5)
    dist = (b["high"] - close) / b["high"] * 100.0
    prox_s = 1.0 if dist <= cfg.near_pivot_pct else _clamp(1.0 - (dist - cfg.near_pivot_pct) / 7.0)
    comps = [
        {"name": "depth", "weight": w["depth"], "raw": _r(d, 2), "score": round(depth_s, 4), "formula": "1 at 15-30 % deep; 0.5-1 from 8 to 15 %; down to 0.6 at 35 % (ASSUMPTION)"},
        {"name": "length", "weight": w["length"], "raw": n, "score": round(len_s, 4), "formula": "1 at 25-60 sessions; 0.7 when shorter; down to 0.5 at the maximum (ASSUMPTION)"},
        {"name": "resistance", "weight": w["resistance"], "raw": t, "score": round(res_s, 4), "formula": "touches of the base high: 2 -> 0.7, 3+ -> 1"},
        {"name": "volume_dry_up", "weight": w["volume_dry_up"], "raw": _r(dry, 3), "score": round(dry_s, 4), "formula": "volume of the last base sessions / discovery-period volume: 1 at <= 0.5, 0 at >= 1"},
        {"name": "tightness", "weight": w["tightness"], "raw": _r(b["settle_ratio"], 3), "score": round(tight_s, 4), "formula": "daily range of the last base sessions / discovery range: 1 at <= 0.5, 0 at >= 1"},
        {"name": "proximity", "weight": w["proximity"], "raw": _r(dist, 2), "score": round(prox_s, 4), "formula": f"close within {cfg.near_pivot_pct} % below the pivot -> 1, 10 % or more below -> 0"},
    ]
    tw = sum(x["weight"] for x in comps)
    return round(100.0 * sum(x["weight"] * x["score"] for x in comps) / tw, 2), comps


def detect_ipo_base(frame: pd.DataFrame, cfg: IPOConfig, listing_date: date | None = None, symbol: str = "", config_hash: str = "", calendar=None) -> dict[str, Any] | None:
    """IPO base state of the latest completed daily candle, or None (not a recent
    listing, or the post-listing history is not evaluable)."""
    n = len(frame)
    L, source, problem = listing_index(frame, listing_date, cfg, calendar)
    if L is None:
        return None
    h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close", "volume"))
    times = frame.index
    t = n - 1
    age = n - L
    if age > cfg.max_age_bars:
        return None
    lst_t = times[L]
    listing = {"date": (listing_date or lst_t.tz_convert(IST).date()).isoformat(), "t": epoch_s(lst_t), "source": source, "first_candle": iso(lst_t)}
    d_end = min(L + cfg.discovery_bars, n) - 1
    discovery = {"t_start": epoch_s(times[L]), "t_end": epoch_s(times[d_end]), "bars": d_end - L + 1, "high": _r(np.max(h[L : d_end + 1])), "low": _r(np.min(l[L : d_end + 1])), "complete": age > cfg.discovery_bars}
    base_rec: dict[str, Any] = {"symbol": symbol, "listing": listing, "age_bars": age, "discovery": discovery, "last_close": _r(c[t]), "last_candle_time": iso(times[t])}
    ipo_id = hashlib.sha1(f"{symbol}|{listing['date']}|{config_hash}".encode()).hexdigest()[:16]
    if age < cfg.min_age_bars:
        return {**base_rec, "ipo_id": ipo_id, "status": "too_young", "status_label": STATUS_LABELS["too_young"], "status_reason": f"{age} completed sessions since listing (needs {cfg.min_age_bars})", "quality": 0.0, "quality_components": [], "gates": [], "base": None, "pivot": None, "breakout": None, "explanation": [f"Too young: listed {listing['date']}, {age} completed sessions so far."], "geometry": _geometry(listing, discovery, None, None, None, None, times, t, c)}

    # ---- walk: breakout events of valid bases (each candle judged on candles before it)
    events: list[dict[str, Any]] = []
    for j in range(L + 2, n):
        b = _base(h, l, c, v, L, j, cfg)
        if b is None:
            continue
        piv = b["high"] * (1.0 + cfg.breakout_buffer_pct / 100.0)
        if c[j] > piv and b["valid"]:
            ref = v[max(L, j - cfg.volume_avg_bars) : j]
            avg = _mean(ref)
            vr = (v[j] / avg) if (avg and np.isfinite(v[j])) else None
            events.append({"i": j, "base": b, "pivot": b["high"], "volume_ratio": vr, "volume_ok": bool(vr is not None and vr >= cfg.breakout_volume_ratio)})
    status = None
    reason = None
    brk = None
    fail_i = None
    ev = events[-1] if events else None
    if ev is not None and ev["volume_ok"] and t - ev["i"] <= cfg.breakout_track_bars + cfg.failed_breakout_bars:
        b = ev["i"]
        piv, base = ev["pivot"], ev["base"]
        for j in range(b + 1, min(n, b + 1 + cfg.failed_breakout_bars)):
            if c[j] < base["low"]:
                status, fail_i, reason = "invalidated", j, f"close {c[j]:.2f} below the base low {base['low']:.2f} after the breakout"
                break
            if c[j] < piv:
                status, fail_i, reason = "failed_breakout", j, f"close {c[j]:.2f} back below the pivot {piv:.2f} within {cfg.failed_breakout_bars} sessions"
                break
        if status is None:
            for j in range(b + 1 + cfg.failed_breakout_bars, n):
                if c[j] < base["low"]:
                    status, fail_i, reason = "invalidated", j, f"close {c[j]:.2f} below the base low {base['low']:.2f}"
                    break
        if status is None and t - b <= cfg.breakout_track_bars:
            status = "breakout"
        elif status is not None and t - (fail_i or b) > cfg.breakout_track_bars:
            status = None  # an old failure is history; evaluate the current base below
            fail_i = None
        if status is not None:
            brk = ev
    cur = None
    if status is None:
        cur = _base(h, l, c, v, L, n, cfg)  # the base including the latest candle
        if cur is None:
            status, reason = "discovery_phase", "no base yet"
        else:
            g = {x["name"]: x for x in cur["gates"]}
            new_high = cur["res_i"] >= t - cfg.discovery_new_high_bars + 1
            low_vol_cross = ev is not None and not ev["volume_ok"] and ev["i"] == t
            if low_vol_cross:
                status, reason = "near_pivot", f"closed above the pivot on {_r(ev['volume_ratio'], 2)}x the average volume (a breakout needs >= {cfg.breakout_volume_ratio}x)"
                cur, brk = ev["base"], ev
            elif cur["depth"] > cfg.max_base_depth_pct:
                status, reason = "invalidated", f"base {cur['depth']:.1f} % deep (> {cfg.max_base_depth_pct} %)"
            elif cur["length"] > cfg.max_base_bars:
                status, reason = "invalidated", f"base {cur['length']} sessions long without a breakout (> {cfg.max_base_bars})"
            elif new_high or (age < cfg.discovery_bars + cfg.settle_window_bars and g["volatility_settled"]["passed"] is not True):
                status, reason = "discovery_phase", "new post-listing high within the last sessions" if new_high else "volatility has not settled after listing"
            elif cur["valid"]:
                dist = (cur["high"] - c[t]) / cur["high"] * 100.0
                status = "near_pivot" if dist <= cfg.near_pivot_pct else "base_validated"
            else:
                status, reason = "base_forming", "not yet: " + ", ".join(x["name"].replace("_", " ") for x in cur["gates"] if x["passed"] is not True)
    base = (brk["base"] if brk is not None else cur)
    quality, comps = _score(base, float(c[t]), cfg) if base is not None else (0.0, [])
    pivot = base["high"] if base is not None else None
    breakout = None
    if brk is not None:
        bi = brk["i"]
        breakout = {"i": bi, "t": epoch_s(times[bi]), "time": iso(times[bi]), "close": _r(c[bi]), "volume": _r(v[bi], 1) if np.isfinite(v[bi]) else None, "volume_ratio": _r(brk["volume_ratio"], 3), "volume_ok": brk["volume_ok"], "age": t - bi}
    base_out = None
    if base is not None:
        bs, be = base["res_i"], (brk["i"] - 1 if brk is not None else t)
        base_out = {
            "start": iso(times[bs]), "t_start": epoch_s(times[bs]), "end": iso(times[be]), "t_end": epoch_s(times[be]),
            "high": _r(base["high"]), "low": _r(base["low"]), "depth_pct": _r(base["depth"], 2), "length": base["length"],
            "touches": [{"t": epoch_s(times[i]), "time": iso(times[i]), "p": _r(h[i])} for i in base["touches"]],
            "settle_ratio": _r(base["settle_ratio"], 3), "discovery_range_pct": _r(base["discovery_range_pct"], 3), "right_range_pct": _r(base["right_range_pct"], 3),
            "volume_discovery": _r(base["vol_discovery"], 1), "volume_base": _r(base["vol_base"], 1), "volume_right": _r(base["vol_right"], 1),
            "volume_dry_up_ratio": _r(base["vol_right"] / base["vol_discovery"], 3) if (base["vol_right"] is not None and base["vol_discovery"]) else None,
        }
    failure = {"i": fail_i, "t": epoch_s(times[fail_i]), "time": iso(times[fail_i])} if fail_i is not None else None
    dist_pct = ((pivot - c[t]) / pivot * 100.0) if pivot else None
    rec = {
        **base_rec,
        "ipo_id": ipo_id,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "status_reason": reason,
        "quality": quality,
        "quality_components": comps,
        "gates": base["gates"] if base is not None else [],
        "base": base_out,
        "pivot": _r(pivot),
        "pivot_distance_pct": _r(dist_pct, 3),
        "breakout": breakout,
        "breakout_age": breakout["age"] if breakout else None,
        "breakout_volume_ratio": breakout["volume_ratio"] if breakout else None,
        "failure": failure,
    }
    rec["explanation"] = _explain(rec, cfg)
    rec["geometry"] = _geometry(listing, discovery, base_out, pivot, breakout, failure, times, t, c)
    return rec


def _geometry(listing, discovery, base, pivot, breakout, failure, times, t, c) -> dict[str, Any]:
    return {
        "schema_version": "ipo-base-1.0",
        "timeframe": "1d",
        "listing": listing,
        "discovery": discovery,
        "base": base,
        "pivot": {"p": _r(pivot), "t": base["t_start"], "t_end": epoch_s(times[t])} if (pivot and base) else None,
        "breakout": breakout,
        "failure": failure,
        "last_candle": {"t": epoch_s(times[t]), "p": _r(c[t])},
    }


def _explain(r: dict[str, Any], cfg: IPOConfig) -> list[str]:
    out = [f"Status: {r['status_label']}" + (f" - {r['status_reason']}" if r.get("status_reason") else "") + "."]
    lst = r["listing"]
    out.append(f"Listed {lst['date']} ({'official NSE equity list' if lst['source'] == 'nse_equity_list' else 'first candle of the provider history - fallback, check the listing date'}); {r['age_bars']} completed sessions since.")
    d = r["discovery"]
    out.append(f"Discovery period: first {d['bars']} sessions, high {d['high']}, low {d['low']}.")
    b = r.get("base")
    if b:
        out.append(f"Base from {b['start'][:10]}: high (pivot) {b['high']}, low {b['low']}, {b['depth_pct']} % deep, {b['length']} sessions, {len(b['touches'])} resistance touches.")
        if b.get("volume_dry_up_ratio") is not None:
            out.append(f"Volume of the last base sessions = {b['volume_dry_up_ratio']} x the discovery-period average.")
    if r.get("breakout"):
        bk = r["breakout"]
        out.append(f"Close {bk['close']} above the pivot on {bk['time'][:10]} with {bk['volume_ratio']}x the average volume ({'meets' if bk['volume_ok'] else 'below'} {cfg.breakout_volume_ratio}x).")
    for g in r.get("gates") or []:
        out.append(f"{'PASS' if g['passed'] else 'FAIL' if g['passed'] is False else 'N/A '} {g['name'].replace('_', ' ')}: {g['value']} (rule: {g['rule']})")
    out.append("Engineering defaults (IPOConfig), not validated on labelled charts; quality is not a probability.")
    return out
