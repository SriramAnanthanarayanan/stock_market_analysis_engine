"""Self-test: ``python -m app.selftest`` (run from ``backend/``).

Runs deterministic checks of the shared engine and, for each P1 detector, a
positive, negative, near-miss and invalidated synthetic example, plus the
Falling Channel regression cases. Prints every result; exits 1 on failure.

Synthetic results show that the code follows the configured formulas. They are
NOT evidence of real-world chart accuracy (that requires human labels, P3).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, DetectionConfig, config_hash
from .data.calendar import NSECalendar
from .data.ohlcv import prepare_series, resample_4h
from .indicators import atr_wilder, swing_points
from .patterns.base import SPEC_BY_SLUG, Line, SeriesContext
from .patterns.engine import IMPLEMENTED_PATTERNS, detect_series, diagnose_series, track_detection
from .patterns.shared import find_touches
from .synthetic import (
    EXPANDING,
    FALLING_CHANNEL_BREAK_ABOVE,
    INVALIDATION,
    NEAR_MISS,
    NEGATIVE,
    POSITIVE,
    BandSpec,
    arch_path,
    band_series,
    path_series,
    pattern_window,
)
from .timeutil import IST

HOLIDAYS = Path(__file__).resolve().parent.parent / "data" / "nse_holidays.json"


class Runner:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, bool, str]] = []

    def check(self, section: str, name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # report, never hide
            ok, detail = False, f"exception: {type(exc).__name__}: {exc}"
        self.results.append((section, name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<46} {detail}")


def _ctx(df: pd.DataFrame, cfg: DetectionConfig = DEFAULT_CONFIG) -> SeriesContext:
    return SeriesContext("SYNTH", "1d", df, cfg)


def _summ(dets) -> str:
    if not dets:
        return "no detections"
    return "; ".join(f"{d.pattern}[{d.formation_start_index}-{d.formation_end_index}] q={d.quality:.1f} {d.status}" for d in dets[:3])


def engine_checks(r: Runner, cal: NSECalendar) -> None:
    print("\nShared engine")

    def atr_check():
        h = np.array([10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 21.0])
        l = h - 2
        c = h - 1
        atr = atr_wilder(h, l, c, 14)
        tr = np.maximum.reduce([h - l, np.abs(h - np.r_[np.nan, c[:-1]]), np.abs(l - np.r_[np.nan, c[:-1]])])
        tr[0] = h[0] - l[0]
        exp13 = tr[:14].mean()
        exp14 = (exp13 * 13 + tr[14]) / 14
        ok = np.isnan(atr[12]) and abs(atr[13] - exp13) < 1e-12 and abs(atr[14] - exp14) < 1e-12
        return ok, f"ATR[13]={atr[13]:.4f} ATR[14]={atr[14]:.4f} (Wilder)"

    def swing_check():
        h = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 9, 9, 2, 1, 1.0])
        sp = swing_points(h, h - 0.5, 3)
        highs = [int(i) for i in sp.highs]
        return highs == [3, 9], f"swing highs at {highs} (plateau -> first bar; last k bars unconfirmed)"

    def resample_check():
        idx = pd.DatetimeIndex(
            [pd.Timestamp(f"2026-10-29 {t}", tz=IST) for t in ("09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15")]
        )
        df = pd.DataFrame({"open": range(1, 8), "high": range(2, 9), "low": range(0, 7), "close": range(1, 8), "volume": [np.nan, 10, 10, 10, 10, 10, 10]}, index=idx).astype(float)
        out = resample_4h(df)
        ok = [t.strftime("%H:%M") for t in out.index] == ["09:15", "13:15"] and out["high"].tolist() == [5, 8] and out["close"].tolist() == [4, 7]
        return ok, f"bins {[t.strftime('%H:%M') for t in out.index]} high={out['high'].tolist()} close={out['close'].tolist()}"

    def weekly_holiday_check():
        # Week of 2026-03-30: Tue 31-Mar and Fri 03-Apr are NSE holidays -> week ends Thu 02-Apr 15:30.
        ends = cal.bar_ends(pd.DatetimeIndex([pd.Timestamp("2026-03-30 09:15", tz=IST)]), "1w")
        return ends[0] == pd.Timestamp("2026-04-02 15:30", tz=IST), f"week ending {ends[0]}"

    def incomplete_check():
        idx = pd.DatetimeIndex([pd.Timestamp(f"2026-10-{d} 09:15", tz=IST) for d in (27, 28, 29, 30)])
        df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
        during = prepare_series(df, "X", "1d", datetime(2026, 10, 30, 11, 0, tzinfo=IST), cal)
        after = prepare_series(df, "X", "1d", datetime(2026, 10, 30, 15, 31, tzinfo=IST), cal)
        return len(during.frame) == 3 and len(after.frame) == 4, f"bars during session={len(during.frame)}, after close={len(after.frame)}"

    def touch_overshoot_check():
        df = band_series(POSITIVE["rectangle"], cal)
        i = 70
        df.iloc[i, df.columns.get_loc("high")] = 1080 + 60  # a candle far ABOVE the boundary
        ctx = _ctx(df)
        up, lo = Line(0, 1080.0, 0.0), Line(0, 1000.0, 0.0)
        far = find_touches(ctx, up, lo, np.array([i]), np.array([], dtype=int))
        df.iloc[i, df.columns.get_loc("high")] = 1080 - 2  # just below: a genuine touch
        near = find_touches(_ctx(df), up, lo, np.array([i]), np.array([], dtype=int))
        return len(far) == 0 and len(near) == 1, "60 above the line: no touch; 2 below: touch (absolute distance)"

    def no_future_leak_check():
        df = band_series(POSITIVE["falling_channel"], cal)
        n = len(df)
        a = detect_series(_ctx(df.iloc[: n - 2]), cal)
        changed = df.copy()
        changed.iloc[n - 2 :, :4] = changed.iloc[n - 2 :, :4] * 1.3  # wild future bars
        b = detect_series(_ctx(changed.iloc[: n - 2]), cal)
        same = [(d.detection_id, d.quality, d.status) for d in a] == [(d.detection_id, d.quality, d.status) for d in b]
        return same and len(a) > 0, f"{len(a)} detections identical whatever happens after the cutoff"

    r.check("engine", "ATR Wilder smoothing", atr_check)
    r.check("engine", "swing points (k=3, confirmed)", swing_check)
    r.check("engine", "4H NSE session resampling", resample_check)
    r.check("engine", "weekly completion across holidays", weekly_holiday_check)
    r.check("engine", "incomplete daily candle excluded", incomplete_check)
    r.check("engine", "touch overshoot rejected", touch_overshoot_check)
    r.check("engine", "no future leakage", no_future_leak_check)


def pattern_checks(r: Runner, cal: NSECalendar) -> None:
    for slug in POSITIVE:  # P1 line patterns
        name = SPEC_BY_SLUG[slug].name
        print(f"\n{name}")

        def positive(slug=slug):
            spec = POSITIVE[slug]
            dets = detect_series(_ctx(band_series(spec, cal)), cal)
            hit = [d for d in dets if d.pattern == slug and not d.developing]
            s, e = pattern_window(spec)
            ok = bool(hit) and hit[0].status in ("validated", "near_upper", "near_lower") and hit[0].formation_end_index == e
            return ok, _summ(dets)

        def positive_noisy(slug=slug):
            spec = BandSpec(**{**POSITIVE[slug].__dict__, "noise_frac": 0.015, "seed": 21})
            dets = detect_series(_ctx(band_series(spec, cal)), cal)
            return any(d.pattern == slug and not d.developing for d in dets), _summ(dets)

        def negative(slug=slug):
            label, spec = NEGATIVE[slug]
            dets = detect_series(_ctx(band_series(spec, cal)), cal)
            return all(d.pattern != slug for d in dets), f"{label}: {_summ(dets)}"

        def near_miss(slug=slug):
            label, spec, expected = NEAR_MISS[slug]
            ctx = _ctx(band_series(spec, cal))
            dets = detect_series(ctx, cal)
            reported = [d for d in dets if d.pattern == slug]
            if expected == "developing":
                ok = bool(reported) and all(d.status == "developing" for d in reported)
                ok = ok and not any(d.pattern == slug for d in detect_series(ctx, cal, include_developing=False))
                return ok, f"{label}: reported only as Developing ({_summ(reported)})"
            s, e = pattern_window(spec)
            diag = [c for c in diagnose_series(ctx) if c.start == s and c.end == e and c.pattern == slug]
            failed = [g.name for c in diag for g in c.gates if not g.passed]
            ok = not any(d.pattern == slug and not d.developing for d in reported) and expected in failed
            return ok, f"{label}: failing gates {failed or '(not classified)'}"

        def invalidated(slug=slug):
            label, spec, reason = INVALIDATION[slug]
            df = band_series(spec, cal)
            s, e = pattern_window(spec)
            cut = e + 1 + DEFAULT_CONFIG.fit_exclude_last_bars
            first = [d for d in detect_series(_ctx(df.iloc[:cut]), cal) if d.pattern == slug and not d.developing]
            if not first:
                return False, "not detected as of the formation end"
            life = track_detection(first[0], _ctx(df))
            return life.status == "invalidated" and life.reason == reason, f"{label}: {life.status} ({life.reason})"

        r.check(slug, "positive example detected", positive)
        r.check(slug, "positive example with noise detected", positive_noisy)
        r.check(slug, "negative example not reported", negative)
        r.check(slug, "near-miss example rejected", near_miss)
        r.check(slug, "invalidated example (frozen geometry)", invalidated)


def p2_pattern_checks(r: Runner, cal: NSECalendar) -> None:
    from .synthetic_p2 import (
        DOUBLE_TOP_BROKE,
        DOUBLE_TOP_FORMING,
        FLAG_FORMING,
        P2_INVALIDATION,
        P2_NEAR_MISS,
        P2_NEGATIVE,
        P2_POSITIVE,
        FlagSpec,
        flag_windows,
        formation_end_bar,
        series_for,
    )

    for slug in P2_POSITIVE:
        print(f"\n{SPEC_BY_SLUG[slug].name}")

        def positive(slug=slug):
            spec = P2_POSITIVE[slug]
            dets = [d for d in detect_series(_ctx(series_for(spec, cal)), cal) if d.pattern == slug]
            ok = bool(dets) and dets[0].status in ("validated", "near_upper", "near_lower") and dets[0].formation_end_index == formation_end_bar(slug, spec)
            return ok, _summ(dets)

        def negative(slug=slug):
            label, spec = P2_NEGATIVE[slug]
            dets = detect_series(_ctx(series_for(spec, cal)), cal)
            return all(d.pattern != slug for d in dets), f"{label}: no {slug}"

        def near_miss(slug=slug):
            label, spec, expected = P2_NEAR_MISS[slug]
            ctx = _ctx(series_for(spec, cal))
            end = formation_end_bar(slug, spec)
            reported = [d for d in detect_series(ctx, cal) if d.pattern == slug and d.status not in ("developing", "forming")]
            if isinstance(spec, FlagSpec):
                cons = flag_windows(spec)["cons_start"]
                diag = [c for c in diagnose_series(ctx) if c.pattern == slug and c.region_start == cons and c.end == end]
            else:
                diag = [c for c in diagnose_series(ctx) if c.pattern == slug and c.end == end]
            failed = sorted({g.name for c in diag for g in c.gates if not g.passed})
            return not reported and expected in failed, f"{label}: failing gates {failed}"

        def invalidated(slug=slug):
            label, spec, reason = P2_INVALIDATION[slug]
            df = series_for(spec, cal)
            end = formation_end_bar(slug, spec)
            cut = end + 1 + (DEFAULT_CONFIG.fit_exclude_last_bars if isinstance(spec, FlagSpec) else DEFAULT_CONFIG.swing_k_reversal + 3)
            first = [d for d in detect_series(_ctx(df.iloc[:cut]), cal) if d.pattern == slug and d.status not in ("developing", "forming")]
            if not first:
                return False, "not detected as of the formation end"
            life = track_detection(first[0], _ctx(df))
            return (life.status, life.reason) == ("invalidated", reason), f"{label}: {life.status} ({life.reason})"

        r.check(slug, "positive example detected", positive)
        r.check(slug, "negative example not reported", negative)
        r.check(slug, "near-miss example rejected", near_miss)
        r.check(slug, "invalidated example (frozen geometry)", invalidated)

    print("\nProvisional states, precedence and S/R")

    def flag_forming():
        dets = [d for d in detect_series(_ctx(series_for(FLAG_FORMING, cal)), cal) if d.family == "continuation"]
        return bool(dets) and all(d.status == "forming" for d in dets), f"4-bar consolidation after a valid pole: {[d.status for d in dets]}"

    def pivot_forming():
        dets = [d for d in detect_series(_ctx(series_for(DOUBLE_TOP_FORMING, cal)), cal) if d.pattern == "double_top"]
        return bool(dets) and dets[0].status == "forming", f"P2 with 3 confirmation bars: {[d.status for d in dets]}"

    def neckline_break():
        dets = [d for d in detect_series(_ctx(series_for(DOUBLE_TOP_BROKE, cal)), cal) if d.pattern == "double_top"]
        return bool(dets) and dets[0].status == "broke_below", f"status={dets[0].status if dets else None}"

    def pennant_not_triangle():
        dets = detect_series(_ctx(series_for(P2_POSITIVE["bullish_pennant"], cal)), cal)
        pats = {d.pattern for d in dets}
        return "bullish_pennant" in pats and "symmetrical_triangle" not in pats, f"patterns={sorted(pats)}"

    def sr_zones():
        from .analytics.sr import compute_zones
        from .synthetic_p2 import sr_series

        zones = compute_zones(_ctx(sr_series(cal)))
        upper = max(zones, key=lambda z: z.low)
        ok = len(zones) >= 2 and upper.role == "support" and upper.status == "retested"
        return ok, f"{len(zones)} zones; broken resistance -> {upper.role}, {upper.status}"

    r.check("p2", "flag 3-4 bar consolidation -> Forming", flag_forming)
    r.check("p2", "unconfirmed pivot -> Forming only", pivot_forming)
    r.check("p2", "double top neckline break -> Broke Below", neckline_break)
    r.check("p2", "pennant takes precedence over triangle", pennant_not_triangle)
    r.check("p2", "S/R break flips role, retest confirmed", sr_zones)


def falling_channel_regressions(r: Runner, cal: NSECalendar) -> None:
    print("\nFalling Channel regressions")

    def has_fc(df) -> list:
        return [d for d in detect_series(_ctx(df), cal) if d.pattern == "falling_channel"]

    r.check("fc_regression", "clean falling channel detected", lambda: (bool(has_fc(band_series(POSITIVE["falling_channel"], cal))), "detected"))

    def broke_above():
        d = has_fc(band_series(FALLING_CHANNEL_BREAK_ABOVE, cal))
        return bool(d) and d[0].status == "broke_above", f"status={d[0].status if d else None}"

    r.check("fc_regression", "last two closes above upper -> Broke Above", broke_above)
    r.check("fc_regression", "converging wedge rejected as Falling Channel", lambda: (not has_fc(band_series(POSITIVE["falling_wedge"], cal)), "no falling channel"))
    r.check("fc_regression", "flat range rejected", lambda: (not has_fc(band_series(POSITIVE["rectangle"], cal)), "no falling channel"))
    r.check("fc_regression", "rising channel rejected", lambda: (not has_fc(band_series(POSITIVE["rising_channel"], cal)), "no falling channel"))

    def arch():
        df = path_series(arch_path(), cal, wick=4.0, lead=80)
        dets = detect_series(_ctx(df), cal)
        return not any(d.pattern == "falling_channel" for d in dets), _summ(dets)

    r.check("fc_regression", "arch-shaped up-then-down rejected", arch)

    def expanding():
        dets = detect_series(_ctx(band_series(EXPANDING, cal)), cal)
        return not any(d.family == "converging" for d in dets), f"broadening formation: {_summ(dets)}"

    r.check("fc_regression", "expanding formation never a triangle/wedge", expanding)


# ---------------------------------------------------------------- E1
_RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
_RSI_REF = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]
_EMA_CLOSES = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39, 22.38, 22.61, 23.36, 24.05, 23.75, 23.83, 23.95, 23.63, 23.82, 23.87, 23.65, 23.19, 23.10, 23.33, 22.68, 23.10, 22.40, 22.17]
_EMA_REF = [22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34, 23.43, 23.51, 23.54, 23.47, 23.40, 23.39, 23.26, 23.23, 23.08, 22.92]


def technical_checks(r: Runner, cal: NSECalendar) -> None:
    """E1: indicator reference data, Marubozu, 10-day breakouts, RSI divergence."""
    from .breakouts import breakout_events
    from .candlesticks import classify_marubozu
    from .config import DEFAULT_TECH_CONFIG as T
    from .indicators.core import atr_wilder
    from .indicators.divergence import find_divergences
    from .indicators.technical import ema, rsi
    from .synthetic_e1 import bearish_divergence_closes, bullish_divergence_closes, closes_frame, frame_from

    print("\nE1 technical analysis")

    def rsi_ref():
        got = np.round(rsi(_RSI_CLOSES, 14)[14:], 2)
        diff = float(np.max(np.abs(got - np.array(_RSI_REF))))
        return diff < 0.006, f"Wilder RSI(14) vs StockCharts reference, max |diff| {diff:.3f}"

    def ema_ref():
        got = np.round(ema(_EMA_CLOSES, 10)[9:], 2)
        diff = float(np.max(np.abs(got - np.array(_EMA_REF))))
        return diff <= 0.0101 and np.isnan(ema(_EMA_CLOSES, 10)[:9]).all(), f"EMA(10), SMA seed, vs StockCharts reference, max |diff| {diff:.3f}; warm-up NaN"

    def atr_ref():
        a = atr_wilder([10, 11, 12, 11, 13], [9, 10, 10, 10, 11], [9.5, 10.5, 11.5, 10.5, 12.5], 3)
        return abs(a[2] - 1.5) < 1e-12 and abs(a[4] - (1.5 * 2 + 2.5) / 3) < 1e-12, f"Wilder ATR(3) hand example {a[2]:.4f}, {a[3]:.4f}, {a[4]:.4f}"

    def rsi_flat():
        f = rsi([100.0] * 30, 14)
        return bool((f[14:] == 50).all() and np.isnan(f[:14]).all()), "flat prices -> 50 (documented), warm-up NaN"

    r.check("e1", "RSI reference data", rsi_ref)
    r.check("e1", "EMA reference data", ema_ref)
    r.check("e1", "ATR reference example", atr_ref)
    r.check("e1", "RSI flat prices / warm-up", rsi_flat)

    def maru():
        k = classify_marubozu([100, 110, 100, 100], [110.2, 110.2, 111, 100], [99.8, 99.8, 100, 100], [110, 100, 110, 100], T)
        return k.tolist() == [1, -1, 0, 0], f"bull / bear / long upper wick / zero range -> {k.tolist()}"

    r.check("e1", "Marubozu rules", maru)

    def brk(tail, mode):
        c = np.concatenate([np.full(25, 100.5), np.asarray(tail, dtype=float)])
        o = np.concatenate([[100.0], c[:-1]])
        h, l = np.maximum(o, c) + 1.0, np.minimum(o, c) - 1.0
        h[:25], l[:25] = 101.5, 99.0
        f = frame_from(o, h, l, c, cal)
        return breakout_events(f, atr_wilder(h, l, c, T.atr_period), 10, mode, T)

    def b1():
        e = [x for x in brk([103.0], 1) if x["direction"] == "bullish"]
        return len(e) == 1 and e[0]["prev_high"] == 101.5 and e[0]["status"] == "confirmed", f"close 103 vs prev 10D high {e[0]['prev_high'] if e else '-'} (own high excluded)"

    def b2():
        ok = [x["status"] for x in brk([103.0, 102.6], 2) if x["direction"] == "bullish"]
        bad = [x["status"] for x in brk([103.0, 100.8], 2) if x["direction"] == "bullish"]
        return ok == ["confirmed"] and bad == ["failed"], f"2-close: {ok} / false break: {bad}"

    def b3():
        e = [x for x in brk([101.55], 1) if x["direction"] == "bullish"]
        return not e, "close above the level but inside the 0.1 x ATR buffer -> no breakout"

    r.check("e1", "10-day high breakout", b1)
    r.check("e1", "two-close confirmation / false breakout", b2)
    r.check("e1", "break buffer", b3)

    def div(closes, direction):
        f = closes_frame(closes, cal)
        h, l, c = (f[k].to_numpy() for k in ("high", "low", "close"))
        ds = [d for d in find_divergences(h, l, c, rsi(c, 14), atr_wilder(h, l, c, 14), T) if d.direction == direction]
        return ds, f

    def d_bull():
        ds, _ = div(bullish_divergence_closes(), "bullish")
        d = ds[0] if ds else None
        return bool(d and d.status == "confirmed" and d.confirmed_index == d.p2.i + T.div_swing_k), (
            f"P1 {d.p1.price:.1f}/RSI {d.p1.rsi:.1f} -> P2 {d.p2.price:.1f}/RSI {d.p2.rsi:.1f}, confirmed at P2+{T.div_swing_k}" if d else "none"
        )

    def d_bear():
        ds, _ = div(bearish_divergence_closes(), "bearish")
        return len(ds) == 1 and ds[0].status == "confirmed", f"{len(ds)} bearish divergence(s)"

    def d_dev():
        ds, f = div(bullish_divergence_closes(), "bullish")
        p2 = ds[0].p2.i
        part = closes_frame(f["close"].to_numpy()[: p2 + 3], cal)
        h, l, c = (part[k].to_numpy() for k in ("high", "low", "close"))
        got = [d.status for d in find_divergences(h, l, c, rsi(c, 14), atr_wilder(h, l, c, 14), T) if d.direction == "bullish"]
        return "confirmed" not in got and "developing" in got, f"2 bars after P2: {got}"

    r.check("e1", "bullish RSI divergence", d_bull)
    r.check("e1", "bearish RSI divergence", d_bear)
    r.check("e1", "unconfirmed pivot -> developing only", d_dev)


def advanced_checks(r: Runner, cal: NSECalendar) -> None:
    """Fresh 10-day breakouts, Volume Shocker, 52-week, VCP (synthetic; proves the rules, not accuracy)."""
    print("\n== Advanced scanners: fresh breakouts, Volume Shocker, 52-week, VCP")
    from .config import DEFAULT_SIGNAL_CONFIG as S, DEFAULT_VCP_CONFIG as V
    from .signals.breakouts import crossing_events, fresh_kind, fresh_params, w52_kind, w52_params, week52_state
    from .signals.volume import volume_shocks
    from .synthetic_e1 import closes_frame
    from .synthetic_vcp import VCPSpec, vcp_frame
    from .vcp import detect_vcp

    def fresh(closes):
        f = closes_frame(closes, cal, wick=1.0)
        a = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
        ev, _ = crossing_events(f, a, fresh_params(S), symbol="S", kind_of=lambda d: fresh_kind(d, 10), config_hash="h", since=0, volume_lookback=20, first_lookback=20)
        return [e for e in ev if e["direction"] == "bullish"]

    def fb_pos():
        ev = fresh([100.0] * 40 + [103.0, 104.0, 105.0])
        return [e["index"] for e in ev] == [40], f"events at {[e['index'] for e in ev]} (one event, no daily repeats)"

    def fb_rearm():
        ev = fresh([100.0] * 40 + [103.0, 100.5, 100.2, 100.4] + [100.0] * 12 + [104.0])
        return len(ev) == 2 and ev[0]["episode_state"] == "reset", f"{len(ev)} events, first episode {ev[0]['episode_state'] if ev else '-'}"

    def vs_2x():
        c = np.full(80, 100.0)
        f = closes_frame(c, cal, wick=1.0, v=np.r_[np.full(79, 1e5), 2e5])
        a = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
        sh = volume_shocks(f, "1d", a, None, None, S, since=79)
        return len(sh) == 1 and abs(sh[0]["rvol"] - 2.0) < 1e-9, f"RVOL {sh[0]['rvol'] if sh else None}"

    def w52_insufficient():
        f = closes_frame(np.linspace(100, 130, 200), cal, wick=1.0)
        a = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
        ev, walks = crossing_events(f, a, w52_params(S), symbol="S", kind_of=w52_kind, config_hash="h", since=0, volume_lookback=20, first_lookback=20)
        st = week52_state(f, a, S, walks)
        return st["status"] == "insufficient_history" and not ev, f"{st['status']}, {len(ev)} events with 199 previous sessions"

    def vcp(spec, want, flag=None):
        def run():
            r = detect_vcp(vcp_frame(spec, cal), V, symbol="S")
            got = r["status"] if r else None
            ok = got in want and (flag is None or bool(r and r.get(flag)))
            return ok, f"status {got}, depths {r['depths_pct'] if r else '-'}, quality {r['quality'] if r else '-'}"
        return run

    r.check("adv", "fresh 10-day breakout: one event per episode", fb_pos)
    r.check("adv", "fresh breakout re-armed after 3 closes inside", fb_rearm)
    r.check("adv", "volume shock: 2x the previous 20 candles", vs_2x)
    r.check("adv", "52-week: < 252 sessions -> insufficient history", w52_insufficient)
    r.check("adv", "VCP: 3 contractions near the pivot", vcp(VCPSpec(), {"near_pivot", "final_contraction"}))
    r.check("adv", "VCP: non-contracting base rejected", vcp(VCPSpec(depths=(15, 15, 14)), {None, "invalidated"}))
    r.check("adv", "VCP: rising volume not qualified", vcp(VCPSpec(volumes=(0.5e5, 0.7e5, 1.0e5)), {"forming"}))
    r.check("adv", "VCP: breakout with volume expansion", vcp(VCPSpec(tail=[(1.02, 2.5e5)]), {"breakout"}))
    r.check("adv", "VCP: pivot cross without volume is not a breakout", vcp(VCPSpec(tail=[(1.02, 0.6e5)]), {"near_pivot"}, "pivot_crossed_low_volume"))
    r.check("adv", "VCP: failed breakout", vcp(VCPSpec(tail=[(1.02, 2.5e5), (0.97, 0.6e5), (0.965, 0.6e5)]), {"failed_breakout"}))
    four = dict(depths=(28, 15, 8, 4), recover=(0.99, 0.99, 0.99, 0.985), down_bars=(8, 6, 4, 3), up_bars=(8, 6, 5, 4), volumes=(1e5, 0.8e5, 0.6e5, 0.4e5))
    r.check("adv", "VCP: four successive contractions T1 > T2 > T3 > T4", vcp(VCPSpec(**four), {"near_pivot", "final_contraction"}))

    from .config import DEFAULT_IPO_CONFIG as I
    from .ipo import detect_ipo_base
    from .synthetic_ipo import IPOSpec, ipo_frame

    def ipo_bo():
        f = ipo_frame(IPOSpec(tail=[(125.0, 3.0, 3.0e6)]), cal)
        x = detect_ipo_base(f, I, f.index[0].date(), calendar=cal)
        return bool(x and x["status"] == "breakout" and x["breakout"]["volume_ok"]), f"status {x and x['status']}, pivot {x and x['pivot']}"

    def setup_very_near():
        from .signals.scan import run_signals

        closes = list(90 + 3 * np.sin(np.arange(49) / 3.0)) + [94, 95, 96, 95, 96, 95, 96, 95, 96, 96, 99.4]
        f = closes_frame(closes, cal, wick=3.0)
        f.iloc[52, f.columns.get_loc("high")] = 100.0
        f.iloc[-1, f.columns.get_loc("high")] = 99.6
        a = atr_wilder(f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy(), 14)
        st = [x for x in run_signals(f, "1d", a, None, None, S, "T").setups if x["direction"] == "bullish"]
        return bool(st and st[0]["state"] == "very_near"), f"{[x['state'] for x in st]}"

    r.check("adv", "IPO base: breakout above the post-listing base on volume", ipo_bo)
    r.check("adv", "10-day level: Very Near before the breakout", setup_very_near)


def main() -> int:
    t0 = time.time()
    cal = NSECalendar.load(HOLIDAYS)
    cfg = DEFAULT_CONFIG
    print("Indian Price Action Screener - self-test")
    print(f"python {sys.version.split()[0]}  numpy {np.__version__}  pandas {pd.__version__}")
    print(f"detection config version {cfg.version}  hash {config_hash(cfg)}")
    from .config import DEFAULT_TECH_CONFIG

    print(f"technical config version {DEFAULT_TECH_CONFIG.version}  hash {config_hash(DEFAULT_TECH_CONFIG)}")
    print(f"holiday calendar years: {sorted(cal.covered_years)}")
    print(f"detectors ({len(IMPLEMENTED_PATTERNS)}):", ", ".join(SPEC_BY_SLUG[p].name for p in IMPLEMENTED_PATTERNS))
    r = Runner()
    engine_checks(r, cal)
    pattern_checks(r, cal)
    p2_pattern_checks(r, cal)
    falling_channel_regressions(r, cal)
    technical_checks(r, cal)
    advanced_checks(r, cal)
    passed = sum(1 for x in r.results if x[2])
    failed = [x for x in r.results if not x[2]]
    print(f"\nSummary: {passed} passed, {len(failed)} failed, {len(r.results)} total in {time.time() - t0:.1f}s")
    for sec, name, _, detail in failed:
        print(f"  FAILED {sec}: {name} -> {detail}")
    print("Note: synthetic checks verify the formulas, not real-chart accuracy (needs human labels and an evaluation run, see docs/validation.md).")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
