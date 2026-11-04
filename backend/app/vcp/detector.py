"""Volatility Contraction Pattern (VCP) on completed daily candles.

Source principles (Mark Minervini, *Think & Trade Like a Champion*; ChartMill VCP
notes): a base inside an uptrend whose successive pullbacks get smaller (two to
four contractions typical, up to six), with volume generally drying up, a tight
final contraction, a pivot at the final contraction's high and a breakout above
it on expanding volume. Every number below is a configurable engineering
threshold in ``VCPConfig`` (docs/vcp-methodology.md separates the two).

Algorithm for one candidate base start ``s0`` (a confirmed swing high that is
the highest high of the previous ``base_start_high_lookback`` sessions, after a
preceding advance of at least ``prior_advance_min_pct``), walking the candles
left to right and using a swing high only once it is confirmed (k later bars):

* A **contraction** runs from a contraction high to the lowest low before the
  next contraction high: ``depth % = (high - low) / high x 100``.
* A confirmed swing high starts the **next** contraction only when the pullback
  before it is meaningful (``>= min_contraction_depth_pct``) and the swing high
  recovered at least ``min_recovery_frac`` of that pullback. A smaller bounce is
  part of the same contraction; after a negligible dip a higher swing high
  simply restarts the contraction from the higher high.
* The **pivot** is the high of the final contraction (its starting swing high),
  not automatically the highest price of the base.
* **Qualified** (all gates): 2-6 meaningful contractions; every depth <=
  ``contraction_ratio_max`` x the previous one; final depth <=
  ``final_max_depth_pct``; mean volume of the final contraction <=
  ``volume_final_vs_first_max`` x that of T1; base length within
  [min_base_sessions, max_base_sessions].
* **Invalidated** before a breakout: a contraction deeper than allowed
  (expanding volatility) or a close below the base low; the base high exceeded
  (``base_high_tolerance_pct``) before the structure qualified.
* **Breakout**: a completed close above the pivot (+ buffer) while qualified, on
  volume >= ``breakout_volume_ratio`` x the previous 50-session average
  (one-close or two-close mode). Without that volume: *Pivot crossed - volume
  not confirmed*. After a breakout: *Failed Breakout* after
  ``failed_breakout_closes`` consecutive closes back below the pivot,
  *Invalidated* on a close below the final contraction low.

Statuses at the latest candle: developing (one contraction so far), forming
(contracting structure, final contraction not yet tight or volume not yet
dried up), tightening (qualified), near_pivot (qualified, close within
``near_pivot_pct`` below the pivot), breakout, breakout_pending (two-close mode),
pivot_cross_low_volume, failed_breakout, invalidated.

Weekly candles are never mixed into this calculation; Stage Analysis is a
separate trend-context filter applied by the API.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import VCPConfig
from ..indicators.core import atr_wilder, swing_points
from ..timeutil import epoch_s, iso

STATUS_LABELS = {
    "developing": "Developing VCP",
    "forming": "Forming VCP",
    "tightening": "Tightening / Final Contraction",
    "near_pivot": "Near Pivot",
    "breakout": "Breakout",
    "breakout_pending": "Breakout (awaiting 2nd close)",
    "pivot_cross_low_volume": "Pivot crossed - volume not confirmed",
    "failed_breakout": "Failed Breakout",
    "invalidated": "Invalidated",
}
STATUS_RANK = {"breakout": 0, "breakout_pending": 1, "near_pivot": 2, "tightening": 3, "pivot_cross_low_volume": 4, "forming": 5, "failed_breakout": 6, "developing": 7, "invalidated": 8}
QUALIFIED_STATUSES = ("tightening", "near_pivot", "breakout", "breakout_pending", "pivot_cross_low_volume", "failed_breakout")


def _r(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _clamp(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


@dataclass
class Contraction:
    hi_i: int
    hi: float
    lo_i: int
    lo: float
    provisional: bool = False

    @property
    def depth(self) -> float:
        return self.hi - self.lo

    @property
    def depth_pct(self) -> float:
        return (self.hi - self.lo) / self.hi * 100.0


@dataclass
class _Ctx:
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    atr: np.ndarray
    times: pd.DatetimeIndex
    swing_lows: set[int]
    cfg: VCPConfig


def _avg_prev(v: np.ndarray, i: int, n: int) -> float | None:
    """Mean of the n completed sessions before i (known volumes only, at least half known)."""
    win = v[max(0, i - n) : i]
    win = win[np.isfinite(win)]
    if len(win) < max(1, n // 2) or win.mean() <= 0:
        return None
    return float(win.mean())


def _vol_stats(ctx: _Ctx, a: int, b: int) -> tuple[float | None, float | None]:
    """Mean / min known volume of candles a..b (a contraction: the candles after its high through its low)."""
    w = ctx.v[a : b + 1]
    w = w[np.isfinite(w)]
    return (float(w.mean()), float(w.min())) if len(w) else (None, None)


def _gates(ctx: _Ctx, cons: list[Contraction], s0: int, end: int) -> list[dict[str, Any]]:
    """Structural gates of the base s0..end with these contractions."""
    cfg = ctx.cfg
    g: list[dict[str, Any]] = []

    def add(name: str, passed: bool | None, value: Any, rule: str, basis: str = "engineering") -> None:
        g.append({"name": name, "passed": passed, "value": value, "rule": rule, "basis": basis})

    nc = len(cons)
    add("contraction_count", cfg.min_contractions <= nc <= cfg.max_contractions, nc, f"{cfg.min_contractions}-{cfg.max_contractions} meaningful contractions", "source")
    ratios = [cons[i + 1].depth_pct / cons[i].depth_pct for i in range(nc - 1)]
    add("progressive_contraction", bool(ratios) and all(r <= cfg.contraction_ratio_max for r in ratios), [round(r, 3) for r in ratios], f"each depth <= {cfg.contraction_ratio_max} x the previous (T1 > T2 > ...)")
    final = cons[-1] if cons else None
    add("tight_final_contraction", final is not None and final.depth_pct <= cfg.final_max_depth_pct, _r(final.depth_pct, 2) if final else None, f"final depth <= {cfg.final_max_depth_pct} %")
    vf = _vol_stats(ctx, final.hi_i + 1, final.lo_i)[0] if final else None
    v1 = _vol_stats(ctx, cons[0].hi_i + 1, cons[0].lo_i)[0] if cons else None
    vr = (vf / v1) if (vf is not None and v1) else None
    add("volume_dry_up", None if vr is None else vr <= cfg.volume_final_vs_first_max, _r(vr, 3), f"final contraction mean volume <= {cfg.volume_final_vs_first_max} x T1 mean volume")
    base_len = end - s0
    add("base_length", cfg.min_base_sessions <= base_len <= cfg.max_base_sessions, base_len, f"{cfg.min_base_sessions}-{cfg.max_base_sessions} sessions from the first contraction high")
    return g


def _qualified(gates: list[dict[str, Any]]) -> bool:
    return all(x["passed"] is True for x in gates)


def _current(ctx: _Ctx, hi_i: int, hi: float, upto: int) -> Contraction | None:
    """The contraction running from hi_i to the lowest low of bars hi_i+1 .. upto."""
    if upto <= hi_i:
        return None
    seg = ctx.l[hi_i + 1 : upto + 1]
    k = int(np.argmin(seg))
    lo_i = hi_i + 1 + k
    confirmed = lo_i in ctx.swing_lows and lo_i + ctx.cfg.swing_k <= upto
    return Contraction(hi_i, hi, lo_i, float(seg[k]), provisional=not confirmed)


def _walk(ctx: _Ctx, s0: int, highs: list[int]) -> dict[str, Any]:
    """Time-ordered evaluation of one candidate base start (see module docstring)."""
    cfg = ctx.cfg
    h, l, c = ctx.h, ctx.l, ctx.c
    n = len(c)
    k = cfg.swing_k
    H0 = float(h[s0])
    cap = H0 * (1.0 + cfg.base_high_tolerance_pct / 100.0)
    closed: list[Contraction] = []
    cur_i, cur = s0, H0
    piv = [i for i in highs if i > s0]
    ptr = 0
    out: dict[str, Any] = {"s0": s0, "closed": closed, "breakout": None, "invalid": None}
    for j in range(s0 + 1, n):
        while ptr < len(piv) and piv[ptr] + k <= j:
            i = piv[ptr]
            ptr += 1
            p = float(h[i])
            con = _current(ctx, cur_i, cur, i - 1)
            if con is None:
                if p >= cur:
                    cur_i, cur = i, p
                continue
            if p > cap:
                # A breakout would have stopped the walk first: the base was exceeded unqualified.
                out.update(pivot=cur, pivot_i=cur_i, live=con)
                out["invalid"] = (j, f"a swing high {p:.2f} exceeded the base high {H0:.2f} by more than {cfg.base_high_tolerance_pct} % before the VCP qualified")
                return out
            if con.depth_pct < cfg.min_contraction_depth_pct:
                if p >= cur:
                    cur_i, cur = i, p
                continue
            if (p - con.lo) / con.depth >= cfg.min_recovery_frac:
                closed.append(con)
                cur_i, cur = i, p
        run = _current(ctx, cur_i, cur, j - 1)
        cons = closed + ([run] if run is not None and run.depth_pct >= cfg.min_contraction_depth_pct else [])
        # Expanding volatility: the running contraction is deeper than allowed.
        live = _current(ctx, cur_i, cur, j)
        if closed and live is not None and live.depth_pct >= cfg.min_contraction_depth_pct and live.depth_pct > cfg.contraction_ratio_max * closed[-1].depth_pct:
            out.update(pivot=cur, pivot_i=cur_i, live=live)
            out["invalid"] = (j, f"contraction from {cur:.2f} reached {live.depth_pct:.1f} % > {cfg.contraction_ratio_max} x the previous {closed[-1].depth_pct:.1f} % (volatility expanding)")
            return out
        base_low = float(np.min(l[s0 + 1 : j])) if j > s0 + 1 else float(l[s0])
        inv_buf = cfg.invalidation_buffer_atr * (float(ctx.atr[j]) if np.isfinite(ctx.atr[j]) else 0.0)
        if closed and c[j] < base_low - inv_buf:
            out.update(pivot=cur, pivot_i=cur_i, live=live)
            out["invalid"] = (j, f"close {c[j]:.2f} below the base low {base_low:.2f}")
            return out
        buf = cfg.breakout_buffer_atr * (float(ctx.atr[j]) if np.isfinite(ctx.atr[j]) else 0.0)
        if c[j] > cur + buf:
            gates = _gates(ctx, cons, s0, j)
            if _qualified(gates):
                out["breakout"] = {"i": j, "pivot": cur, "pivot_i": cur_i, "contractions": cons, "gates": gates, "buffer": buf}
                return out
            if c[j] > cap:
                out.update(pivot=cur, pivot_i=cur_i, live=run)
                out["invalid"] = (j, f"close {c[j]:.2f} broke above the base (high {H0:.2f}) before the VCP qualified")
                return out
    out["pivot"], out["pivot_i"] = cur, cur_i
    return out


def _post_breakout(ctx: _Ctx, b: int, pivot: float, final: Contraction) -> tuple[str, int | None, str | None]:
    cfg = ctx.cfg
    c = ctx.c
    n = len(c)
    if cfg.breakout_confirm_closes >= 2:
        if b == n - 1:
            return "breakout_pending", None, "first close above the pivot; waiting for the second"
        buf = cfg.breakout_buffer_atr * (float(ctx.atr[b]) if np.isfinite(ctx.atr[b]) else 0.0)
        if c[b + 1] <= pivot + buf:
            return "failed_breakout", b + 1, "second close back at or below the pivot"
    below = 0
    for j in range(b + 1, n):
        inv_buf = cfg.invalidation_buffer_atr * (float(ctx.atr[j]) if np.isfinite(ctx.atr[j]) else 0.0)
        if c[j] < final.lo - inv_buf:
            return "invalidated", j, f"close {c[j]:.2f} below the final contraction low {final.lo:.2f}"
        below = below + 1 if c[j] < pivot else 0
        if below >= cfg.failed_breakout_closes:
            return "failed_breakout", j, f"{below} consecutive closes back below the pivot {pivot:.2f}"
    return "breakout", None, None


def _sma(c: np.ndarray, n: int) -> float | None:
    if len(c) < n:
        return None
    return float(np.mean(c[-n:]))


def _score(ctx: _Ctx, cons: list[Contraction], H0: float, pivot: float, advance_pct: float, end: int) -> tuple[float, list[dict[str, Any]], dict[str, Any]]:
    cfg = ctx.cfg
    w = cfg.weights
    comps: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    nc = len(cons)
    ratios = [cons[i + 1].depth_pct / cons[i].depth_pct for i in range(nc - 1)]
    if ratios:
        mean_r = float(np.mean(ratios))
        shrink = _clamp((cfg.contraction_ratio_max - mean_r) / (cfg.contraction_ratio_max - cfg.score_ratio_ideal))
    else:
        mean_r, shrink = None, 0.0
    count_f = {1: 0.3, 2: 0.8, 3: 1.0, 4: 1.0, 5: 0.9, 6: 0.8}.get(nc, 0.5)
    prog = 0.7 * shrink + 0.3 * count_f
    comps.append({"name": "contraction_progression", "weight": w["contraction_progression"], "raw": _r(mean_r, 3), "score": round(prog, 4), "formula": f"0.7 x clamp(({cfg.contraction_ratio_max} - mean depth ratio) / ({cfg.contraction_ratio_max} - {cfg.score_ratio_ideal})) + 0.3 x count factor ({nc} contractions -> {count_f})"})
    vols = [_vol_stats(ctx, x.hi_i + 1, x.lo_i)[0] for x in cons]
    v_first, v_final = (vols[0], vols[-1]) if vols else (None, None)
    trend = (v_final / v_first) if (v_first and v_final is not None) else None
    a50 = _avg_prev(ctx.v, cons[-1].hi_i, cfg.volume_avg_lookback) if cons else None
    final_vs_avg = (v_final / a50) if (a50 and v_final is not None) else None
    parts = []
    if trend is not None:
        parts.append(_clamp((cfg.volume_final_vs_first_max - trend) / (cfg.volume_final_vs_first_max - cfg.score_volume_trend_full)))
    if final_vs_avg is not None:
        parts.append(_clamp((cfg.score_final_vs_avg_zero - final_vs_avg) / (cfg.score_final_vs_avg_zero - cfg.score_final_vs_avg_full)))
    steps = [vols[i + 1] < vols[i] for i in range(len(vols) - 1) if vols[i] is not None and vols[i + 1] is not None]
    if steps:
        parts.append(sum(steps) / len(steps))
    vol_score = float(np.mean(parts)) if parts else 0.0
    comps.append({"name": "volume_contraction", "weight": w["volume_contraction"], "raw": _r(trend, 3), "score": round(vol_score, 4), "formula": "mean of: final/T1 volume (1 at <= {:.2f}, 0 at {:.2f}); final volume / prior 50-day average (1 at <= {:.2f}, 0 at {:.2f}); share of contractions with lower volume than the previous".format(cfg.score_volume_trend_full, cfg.volume_final_vs_first_max, cfg.score_final_vs_avg_full, cfg.score_final_vs_avg_zero)})
    fd = cons[-1].depth_pct if cons else None
    tight = _clamp((cfg.final_max_depth_pct - fd) / (cfg.final_max_depth_pct - cfg.score_tight_full_pct)) if fd is not None else 0.0
    comps.append({"name": "final_tightness", "weight": w["final_tightness"], "raw": _r(fd, 2), "score": round(tight, 4), "formula": f"1 at final depth <= {cfg.score_tight_full_pct} %, 0 at {cfg.final_max_depth_pct} %"})
    c_end = ctx.c[: end + 1]
    s50, s200 = _sma(c_end, 50), _sma(c_end, 200)
    tparts = [_clamp((advance_pct - cfg.prior_advance_min_pct) / (cfg.score_advance_full_pct - cfg.prior_advance_min_pct))]
    if s50 is not None:
        tparts.append(1.0 if c_end[-1] > s50 else 0.0)
    if s50 is not None and s200 is not None:
        tparts.append(1.0 if s50 > s200 else 0.0)
    lows = [x.lo for x in cons]
    if len(lows) >= 2:
        tparts.append(sum(lows[i + 1] > lows[i] for i in range(len(lows) - 1)) / (len(lows) - 1))
    trend_score = float(np.mean(tparts))
    comps.append({"name": "base_trend", "weight": w["base_trend"], "raw": _r(advance_pct, 2), "score": round(trend_score, 4), "formula": f"mean of: preceding advance ({cfg.prior_advance_min_pct} % -> 0, {cfg.score_advance_full_pct} % -> 1); close > SMA50; SMA50 > SMA200; share of rising contraction lows"})
    gap = max(0.0, (H0 - pivot) / H0 * 100.0)
    piv_score = _clamp((cfg.score_pivot_gap_zero_pct - gap) / (cfg.score_pivot_gap_zero_pct - cfg.score_pivot_gap_full_pct))
    comps.append({"name": "pivot_quality", "weight": w["pivot_quality"], "raw": _r(gap, 2), "score": round(piv_score, 4), "formula": f"pivot within {cfg.score_pivot_gap_full_pct} % of the base's first high -> 1, {cfg.score_pivot_gap_zero_pct} % or more below -> 0"})
    total_w = sum(x["weight"] for x in comps)
    quality = 100.0 * sum(x["weight"] * x["score"] for x in comps) / total_w if total_w else 0.0
    extra.update(volumes=vols, volume_trend=trend, final_vs_avg50=final_vs_avg, sma50=s50, sma200=s200, mean_depth_ratio=mean_r, pivot_gap_pct=gap, higher_lows=(all(lows[i + 1] > lows[i] for i in range(len(lows) - 1)) if len(lows) >= 2 else None))
    return round(quality, 2), comps, extra


def detect_vcp(frame: pd.DataFrame, cfg: VCPConfig, symbol: str = "", atr: np.ndarray | None = None, config_hash: str = "") -> dict[str, Any] | None:
    """VCP of the latest completed daily candle: the best candidate base, or None."""
    n = len(frame)
    if n < cfg.min_base_sessions + cfg.swing_k + 2:
        return None
    h, l, c, v = (frame[k].to_numpy(dtype=float) for k in ("high", "low", "close", "volume"))
    if atr is None:
        atr = atr_wilder(h, l, c, cfg.atr_period)
    sp = swing_points(h, l, cfg.swing_k)
    ctx = _Ctx(h, l, c, v, np.asarray(atr, dtype=float), frame.index, set(int(i) for i in sp.lows), cfg)
    highs = [int(i) for i in sp.highs]
    lo_start = max(0, n - 1 - cfg.max_base_sessions - cfg.post_breakout_track_sessions)
    cands = []
    for s0 in highs:
        if s0 < lo_start or n - 1 - s0 < cfg.min_base_sessions:
            continue
        lb = h[max(0, s0 - cfg.base_start_high_lookback) : s0]
        if len(lb) and h[s0] < lb.max():
            continue  # the base starts at a high
        prior = l[max(0, s0 - cfg.prior_trend_lookback) : s0]
        if len(prior) < cfg.swing_k:
            continue
        advance = (h[s0] / prior.min() - 1.0) * 100.0
        if advance < cfg.prior_advance_min_pct:
            continue
        res = _finish(ctx, s0, _walk(ctx, s0, highs), advance, symbol, config_hash)
        if res is not None:
            cands.append(res)
    if not cands:
        return None
    cands.sort(key=lambda r: (STATUS_RANK[r["status"]], -r["quality"], -r["base_start_t"]))
    best = cands[0]
    best["candidates"] = len(cands)
    return best


def _finish(ctx: _Ctx, s0: int, walk: dict[str, Any], advance: float, symbol: str, chash: str) -> dict[str, Any] | None:
    cfg = ctx.cfg
    c, times = ctx.c, ctx.times
    n = len(c)
    H0 = float(ctx.h[s0])
    status: str
    reason: str | None = None
    brk = walk["breakout"]
    breakout_info = None
    event_i = None
    if brk is not None:
        b = brk["i"]
        cons: list[Contraction] = brk["contractions"]
        pivot, pivot_i, gates = brk["pivot"], brk["pivot_i"], brk["gates"]
        end = b - 1
        a50 = _avg_prev(ctx.v, b, cfg.volume_avg_lookback)
        vr = (ctx.v[b] / a50) if (a50 and np.isfinite(ctx.v[b])) else None
        vol_ok = bool(vr is not None and vr >= cfg.breakout_volume_ratio)
        if n - 1 - b > cfg.post_breakout_track_sessions:
            return None  # an old breakout is history, not a current VCP
        if vol_ok:
            status, event_i, reason = _post_breakout(ctx, b, pivot, cons[-1])
        else:
            status, reason = "pivot_cross_low_volume", f"close above the pivot on {vr if vr is None else round(vr, 2)}x the 50-day average volume (needs >= {cfg.breakout_volume_ratio}x)"
            st2, ei2, rs2 = _post_breakout(ctx, b, pivot, cons[-1])
            if st2 in ("failed_breakout", "invalidated"):
                status, event_i, reason = st2, ei2, rs2
        breakout_info = {"i": b, "t": epoch_s(times[b]), "time": iso(times[b]), "close": _r(c[b]), "volume_ratio": _r(vr, 3), "volume_ok": vol_ok, "buffer": _r(brk["buffer"])}
    elif walk["invalid"] is not None:
        j, why = walk["invalid"]
        live = walk.get("live")
        cons = list(walk["closed"]) + ([live] if live is not None and live.depth_pct >= cfg.min_contraction_depth_pct else [])
        prior = walk["closed"]
        was_forming = len(prior) >= cfg.min_contractions and all(prior[i + 1].depth_pct <= cfg.contraction_ratio_max * prior[i].depth_pct for i in range(len(prior) - 1))
        if not was_forming:
            return None  # never a contracting structure: not reported as a (failed) VCP
        status, reason, event_i = "invalidated", why, j
        pivot, pivot_i = walk["pivot"], walk["pivot_i"]
        end = j
        gates = _gates(ctx, cons, s0, end)
    else:
        pivot, pivot_i = walk["pivot"], walk["pivot_i"]
        run = _current(ctx, pivot_i, pivot, n - 1)
        cons = list(walk["closed"]) + ([run] if run is not None and run.depth_pct >= cfg.min_contraction_depth_pct else [])
        end = n - 1
        if n - 1 - s0 > cfg.max_base_sessions or not cons:
            return None  # base older than the search range without a breakout, or no pullback yet
        gates = _gates(ctx, cons, s0, end)
        progressive = next(g for g in gates if g["name"] == "progressive_contraction")["passed"]
        if _qualified(gates):
            dist = (pivot - c[-1]) / pivot * 100.0
            status = "near_pivot" if 0.0 <= dist <= cfg.near_pivot_pct else "tightening"
        elif len(cons) >= cfg.min_contractions and progressive:
            status = "forming"
            reason = "not yet: " + ", ".join(g["name"].replace("_", " ") for g in gates if g["passed"] is not True)
        elif len(cons) >= cfg.min_contractions:
            status, reason = "invalidated", "contractions are not getting smaller"
        else:
            status, reason = "developing", f"{len(cons)} contraction so far (needs {cfg.min_contractions})"
    qualified = _qualified(gates)
    quality, comps, extra = _score(ctx, cons, H0, pivot, advance, end)
    final = cons[-1]
    base_end_i = (breakout_info["i"] - 1) if breakout_info else end
    base_low = float(np.min(ctx.l[s0 : base_end_i + 1]))
    base_high = float(np.max(ctx.h[s0 : base_end_i + 1]))
    vcp_id = hashlib.sha1(f"{symbol}|1d|{epoch_s(times[s0])}|{chash}".encode()).hexdigest()[:16]
    clist = []
    for k_, x in enumerate(cons):
        avg, mn = _vol_stats(ctx, x.hi_i + 1, x.lo_i)
        a50 = _avg_prev(ctx.v, x.hi_i, cfg.volume_avg_lookback)
        clist.append(
            {
                "seq": k_ + 1, "label": f"T{k_ + 1}",
                "start_i": x.hi_i, "start_t": epoch_s(times[x.hi_i]), "start_time": iso(times[x.hi_i]), "start_price": _r(x.hi),
                "end_i": x.lo_i, "end_t": epoch_s(times[x.lo_i]), "end_time": iso(times[x.lo_i]), "end_price": _r(x.lo),
                "depth": _r(x.depth), "depth_pct": _r(x.depth_pct, 2), "bars": x.lo_i - x.hi_i,
                "avg_volume": _r(avg, 1), "min_volume": _r(mn, 1), "volume_vs_avg50": _r(avg / a50, 3) if (avg is not None and a50) else None,
                "provisional": bool(x.provisional),
            }
        )
    a50_final = _avg_prev(ctx.v, final.hi_i, cfg.volume_avg_lookback)
    fin_end = base_end_i
    fin_vol = ctx.v[final.hi_i + 1 : fin_end + 1]
    dry_days = int(np.sum(np.isfinite(fin_vol) & (fin_vol <= cfg.volume_dry_up_ratio * a50_final))) if a50_final else None
    last = n - 1
    dist_pct = (pivot - c[last]) / pivot * 100.0
    explanation = _explain(cfg, status, reason, cons, gates, advance, H0, pivot, breakout_info, dry_days, extra)
    geometry = {
        "schema_version": "vcp-1.0",
        "vcp_id": vcp_id,
        "timeframe": "1d",
        "price_basis": "raw_split_adjusted",
        "base": {"i_start": s0, "t_start": epoch_s(times[s0]), "i_end": base_end_i, "t_end": epoch_s(times[base_end_i]), "p_high": _r(base_high), "p_low": _r(base_low)},
        "contractions": [{"seq": x["seq"], "label": x["label"], "i_high": x["start_i"], "t_high": x["start_t"], "p_high": x["start_price"], "i_low": x["end_i"], "t_low": x["end_t"], "p_low": x["end_price"], "depth_pct": x["depth_pct"], "volume_vs_avg50": x["volume_vs_avg50"], "provisional": x["provisional"]} for x in clist],
        "pivot": {"i": pivot_i, "t": epoch_s(times[pivot_i]), "p": _r(pivot), "t_end": epoch_s(times[last]), "i_end": last},
        "breakout": breakout_info,
        "event": {"i": event_i, "t": epoch_s(times[event_i]), "status": status} if event_i is not None else None,
        "last_candle": {"i": last, "t": epoch_s(times[last]), "p": _r(c[last])},
    }
    return {
        "vcp_id": vcp_id,
        "symbol": symbol,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "status_reason": reason,
        "qualified": qualified,
        "quality": quality,
        "quality_components": comps,
        "gates": gates,
        "contractions": len(cons),
        "contraction_list": clist,
        "depths_pct": [x["depth_pct"] for x in clist],
        "base_start": iso(times[s0]),
        "base_start_t": epoch_s(times[s0]),
        "base_end": iso(times[base_end_i]),
        "base_sessions": base_end_i - s0,
        "base_high": _r(base_high),
        "base_low": _r(base_low),
        "first_high": _r(H0),
        "pivot": _r(pivot),
        "pivot_time": iso(times[pivot_i]),
        "final_depth_pct": _r(final.depth_pct, 2),
        "final_low": _r(final.lo),
        "last_close": _r(c[last]),
        "last_candle_time": iso(times[last]),
        "pivot_distance_pct": _r(dist_pct, 3),
        "breakout_index": breakout_info["i"] if breakout_info else None,
        "breakout_time": breakout_info["time"] if breakout_info else None,
        "breakout_age": (last - breakout_info["i"]) if breakout_info else None,
        "breakout_volume_ratio": breakout_info["volume_ratio"] if breakout_info else None,
        "event_time": iso(times[event_i]) if event_i is not None else None,
        "metrics": {
            "prior_advance_pct": _r(advance, 2),
            "dry_up_days_final": dry_days,
            "volume_trend_final_vs_first": _r(extra.get("volume_trend"), 3),
            "final_volume_vs_avg50": _r(extra.get("final_vs_avg50"), 3),
            "contraction_volumes": [_r(x, 1) for x in extra.get("volumes", [])],
            "mean_depth_ratio": _r(extra.get("mean_depth_ratio"), 3),
            "pivot_gap_from_first_high_pct": _r(extra.get("pivot_gap_pct"), 2),
            "higher_lows": extra.get("higher_lows"),
            "sma50": _r(extra.get("sma50")),
            "sma200": _r(extra.get("sma200")),
            "atr": _r(ctx.atr[last]),
        },
        "explanation": explanation,
        "geometry": geometry,
    }


def _explain(cfg: VCPConfig, status: str, reason: str | None, cons: list[Contraction], gates: list[dict[str, Any]], advance: float, H0: float, pivot: float, brk: dict[str, Any] | None, dry_days: int | None, extra: dict[str, Any]) -> list[str]:
    out = [f"Status: {STATUS_LABELS[status]}" + (f" - {reason}" if reason else "") + "."]
    out.append(f"Preceding advance into the base: {advance:.1f} % (needs >= {cfg.prior_advance_min_pct} %, ENGINEERING assumption).")
    depths = " > ".join(f"T{i + 1} {x.depth_pct:.1f} %" for i, x in enumerate(cons))
    out.append(f"Contractions: {depths}" + (" (final low provisional)" if cons and cons[-1].provisional else "") + ".")
    vols = extra.get("volumes") or []
    if vols and all(x is not None for x in vols):
        out.append("Mean volume per contraction: " + " -> ".join(f"{x:,.0f}" for x in vols) + (f"; final / T1 = {extra['volume_trend']:.2f}" if extra.get("volume_trend") is not None else "") + ".")
    if dry_days is not None:
        out.append(f"Dry-up days in the final contraction (volume <= {cfg.volume_dry_up_ratio} x prior 50-day average): {dry_days}. A single quiet day is never sufficient on its own.")
    out.append(f"Pivot {pivot:.2f} = high of the final contraction (base's first high {H0:.2f}).")
    if brk:
        out.append(f"Close {brk['close']} above the pivot on {brk['time'][:10]} with volume {brk['volume_ratio']}x the prior 50-day average ({'meets' if brk['volume_ok'] else 'below'} the {cfg.breakout_volume_ratio}x requirement).")
    for g in gates:
        out.append(f"{'PASS' if g['passed'] else 'FAIL' if g['passed'] is False else 'N/A '} {g['name'].replace('_', ' ')}: {g['value']} (rule: {g['rule']}; {g['basis'].upper()})")
    return out
