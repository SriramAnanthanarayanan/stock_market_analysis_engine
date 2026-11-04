"""Reversal patterns: Double Top/Bottom, Head & Shoulders and Inverse (spec 8.4).

Swing lookback k = 5; the latest pivot (P2 / RS) must lie within the last 30
completed bars. Two paths:

* confirmed: the latest *confirmed* swing pivot (k later bars exist) can give a
  Validated detection;
* provisional: a possible newer pivot with fewer than k confirmation bars; it
  can only produce Forming and is never treated as a confirmed fact.

Bottoms are computed with the top logic on negated prices (``P = -low``,
``Q = -high``), so every rule is mirrored exactly. Necklines are drawn in real
prices; the neckline is the only breakout boundary.
"""

from __future__ import annotations

import math

import numpy as np

from .base import Candidate, Gate, Line, PatternDetector, QualityComponent, SeriesContext, clamp01, sdiv, quality_score
from ..indicators.core import provisional_pivot  # re-exported: patterns.reversals.provisional_pivot

DT, DB, HS, IHS = "double_top", "double_bottom", "head_shoulders", "inverse_head_shoulders"


def _ramp(x: float, a: float, b: float, c: float, d: float) -> float:
    if x <= a or x >= d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def _counter_swing(c: np.ndarray, up: bool) -> float:
    """Largest move against the leg direction inside close series ``c``.

    For a falling leg (``up`` False) this is the biggest rally (later close minus an
    earlier lower close); for a rising leg the biggest pull-back."""
    if len(c) < 2:
        return 0.0
    if up:
        return float(np.max(np.maximum.accumulate(c) - c))
    return float(np.max(c - np.minimum.accumulate(c)))


def _head_region_swing(C: np.ndarray, P: np.ndarray, t1: int, h: int, t2: int, level: float) -> float:
    """Largest close-to-close dip while price trades above ``level`` around the head.

    The head region runs from the first bar after T1 whose extreme exceeds the
    higher shoulder to the last such bar before T2; a deep dip inside it means a
    second peak above the shoulders (double / complex head)."""
    above = np.flatnonzero(P[t1 : t2 + 1] > level)
    if not len(above):
        return 0.0
    f, l = t1 + int(above[0]), t1 + int(above[-1])
    rise, fall = C[f : h + 1], C[h : l + 1]
    a = float(np.max(np.maximum.accumulate(rise) - rise)) if len(rise) > 1 else 0.0
    b = float(np.max(fall - np.minimum.accumulate(fall))) if len(fall) > 1 else 0.0
    return max(a, b)


def _wick(C: np.ndarray, P: np.ndarray, i: int, peak: bool) -> float:
    """Distance from the extreme at ``i`` to the best close within 2 bars (top space)."""
    seg = C[max(0, i - 2) : i + 3]
    return float(P[i] - seg.max()) if peak else float(seg.min() - P[i])


def _leg_volume_ratio(vol: np.ndarray, ls: int, t1: int, t2: int, rs: int) -> float | None:
    """Mean volume T2..RS / mean volume LS..T1 (informational; Bulkowski: volume usually recedes)."""
    a, b = vol[ls : t1 + 1], vol[t2 : rs + 1]
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if not len(a) or not len(b) or a.mean() <= 0:
        return None
    return float(b.mean() / a.mean())


def _mean_volume(vol: np.ndarray, i: int) -> float | None:
    seg = vol[max(0, i - 1) : i + 2]
    seg = seg[np.isfinite(seg)]
    return float(seg.mean()) if len(seg) else None


class ReversalDetector(PatternDetector):
    family = "reversal"
    patterns = (DT, DB, HS, IHS)

    def detect(self, ctx: SeriesContext) -> list[Candidate]:
        cfg = ctx.cfg
        k = cfg.swing_k_reversal
        sw = ctx.swings(k)
        recent = ctx.n - cfg.reversal_recency_bars
        out: list[Candidate] = []
        for top in (True, False):
            pivots = [int(i) for i in (sw.highs if top else sw.lows)]
            P = ctx.high if top else -ctx.low
            Q = ctx.low if top else -ctx.high
            confirmed = [i for i in pivots if i >= recent]
            last = max(confirmed) if confirmed else None
            if last is not None:
                out += self._doubles(ctx, last, top, pivots, P, Q, provisional=False)
                out += self._head_shoulders(ctx, last, top, pivots, P, Q, provisional=False)
            prov = provisional_pivot(P, k, cfg.provisional_min_confirm_bars)
            if prov is not None and prov >= recent and (last is None or prov > last):
                out += self._doubles(ctx, prov, top, pivots, P, Q, provisional=True)
                out += self._head_shoulders(ctx, prov, top, pivots, P, Q, provisional=True)
        return out

    # ------------------------------------------------------------ doubles
    def _doubles(self, ctx, p2, top, pivots, P, Q, provisional) -> list[Candidate]:
        cfg = ctx.cfg
        s = 1.0 if top else -1.0
        w = cfg.quality_weights_double
        min_move = cfg.min_move_pct(ctx.timeframe)
        results: list[Candidate] = []
        C = s * ctx.close
        for p1 in pivots:
            sep = p2 - p1
            if not cfg.double_min_separation <= sep <= cfg.double_max_separation:
                continue
            a, b = float(P[p1]), float(P[p2])
            between = float(P[p1 + 1 : p2].max())
            t = p1 + 1 + int(np.argmin(Q[p1 + 1 : p2]))
            trough = float(Q[t])
            depth = min(a, b) - trough
            atr = float(ctx.atr[p2])
            if not math.isfinite(atr) or depth <= 0:
                continue
            avg = abs((a + b) / 2.0)
            allowed = min(cfg.double_peak_diff_depth_frac * depth, cfg.double_peak_diff_price_frac * avg)
            diff = abs(a - b)
            w0 = p1 - sep
            prior_move = a - float(Q[w0:p1].min()) if w0 >= 0 else float("nan")
            trend_high = float(P[max(0, w0) : p2 + 1].max())
            swing = max(_counter_swing(C[p1 : t + 1], up=False), _counter_swing(C[t : p2 + 1], up=True))
            peak_wick = max(_wick(C, P, p1, True), _wick(C, P, p2, True))
            valley_wick = _wick(C, Q, t, False)
            name = "peak" if top else "trough"
            gates = [
                Gate("separation_bars", True, sep, f"{cfg.double_min_separation}-{cfg.double_max_separation}"),
                Gate(f"no_bar_beyond_lower_{name}_between", between <= min(a, b), s * between, f"{'<=' if top else '>='} {s * min(a, b):.4g}"),
                Gate("depth_atr", depth >= cfg.reversal_min_height_atr * atr, depth / atr, f">= {cfg.reversal_min_height_atr} (ATR at P2)"),
                Gate("depth_pct", 100 * depth / avg >= cfg.double_min_depth_move_frac * min_move, 100 * depth / avg, f">= {cfg.double_min_depth_move_frac * min_move:g}% ({ctx.timeframe})"),
                Gate("peak_similarity", diff <= allowed, diff, f"<= min(0.25 D, 0.03 avg) = {allowed:.4g}"),
                Gate("prior_trend", math.isfinite(prior_move) and prior_move >= depth, prior_move, f">= D = {depth:.4g} over the {sep} bars before P1"),
                Gate(f"{name}s_are_trend_extreme", max(a, b) >= trend_high, s * trend_high, f"P1/P2 are the extreme of the prior-trend window + pattern ({sep} bars before P1 .. P2)"),
                Gate("consecutive_extrema", swing < cfg.reversal_counter_swing_frac * depth, swing / depth, f"no counter-swing >= {cfg.reversal_counter_swing_frac} x D inside P1-T-P2"),
                Gate(f"close_confirmed_{name}s", peak_wick <= cfg.reversal_peak_wick_max_frac * depth, peak_wick / depth, f"P1 and P2 each have a close within {cfg.reversal_peak_wick_max_frac} x D (2 bars)"),
                Gate("close_confirmed_neckline", valley_wick <= cfg.reversal_neckline_wick_max_frac * depth, valley_wick / depth, f"T has a close within {cfg.reversal_neckline_wick_max_frac} x D (2 bars)"),
            ]
            valid = all(g.passed for g in gates)
            if not valid and not ctx.collect_rejections:
                continue
            v1, v2 = _mean_volume(ctx.volume, p1), _mean_volume(ctx.volume, p2)
            if v1 and v2 is not None:
                ratio = v2 / v1
                vol_raw = sdiv(1 - ratio, 1 - cfg.double_volume_full_ratio)
            else:
                ratio, vol_raw = None, 0.0
            ra, rb, rc, rd = cfg.double_separation_ramp
            comps = [
                QualityComponent("similarity", w["similarity"], 1 - diff / allowed if allowed > 0 else 0.0, clamp01(1 - diff / allowed) if allowed > 0 else 0.0, "1 - |P1 - P2| / allowed difference"),
                QualityComponent("depth", w["depth"], sdiv(depth / atr, cfg.quality_size_atr_full), clamp01(sdiv(depth / atr, cfg.quality_size_atr_full)), "min((D / ATR) / 6, 1)"),
                QualityComponent("separation", w["separation"], _ramp(sep, ra, rb, rc, rd), _ramp(sep, ra, rb, rc, rd), "0 at 10, 1 from 15 to 40, 0 at 60 bars"),
                QualityComponent("prior_trend", w["prior_trend"], prior_move / (2 * depth) if math.isfinite(prior_move) else 0.0, clamp01(prior_move / (2 * depth)) if math.isfinite(prior_move) else 0.0, "min(prior move / (2 D), 1)"),
                QualityComponent("volume", w["volume"], vol_raw, clamp01(vol_raw), "1 when P2 volume <= 0.5 x P1, 0 when >= P1 (soft factor)"),
            ]
            neck = Line(p1, s * trough, 0.0)
            pattern = DT if top else DB
            metrics = {
                "p1_index": p1,
                "p2_index": p2,
                "trough_index": t,
                "p1_price": s * a,
                "p2_price": s * b,
                "neckline": s * trough,
                "depth": depth,
                "depth_atr": depth / atr,
                "peak_difference": diff,
                "allowed_difference": allowed,
                "separation": sep,
                "prior_trend_move": prior_move,
                "counter_swing_d": swing / depth,
                "peak_wick_d": peak_wick / depth,
                "valley_wick_d": valley_wick / depth,
                "p2_p1_volume_ratio": ratio,
                "width_pct": 100 * depth / avg,
                "move_pct": s * 100 * prior_move / avg if math.isfinite(prior_move) else float("nan"),
                "atr_at_formation_end": atr,
                "atr_latest": float(ctx.atr[ctx.last]),
                "provisional_pivot": provisional,
            }
            results.append(
                self._candidate(
                    ctx, pattern, top, p1, p2, neck, s * max(a, b), [("P1", p1, s * a), ("T", t, s * trough), ("P2", p2, s * b)],
                    gates, comps, metrics, provisional, rejected=not valid,
                )
            )
        return self._best(results, key=lambda c: (c.quality, c.metrics["separation"]))

    # ------------------------------------------------------------ H & S
    def _head_shoulders(self, ctx, rs, top, pivots, P, Q, provisional) -> list[Candidate]:
        cfg = ctx.cfg
        s = 1.0 if top else -1.0
        w = cfg.quality_weights_hs
        results: list[Candidate] = []
        earlier = [i for i in pivots if i < rs]
        C = s * ctx.close
        for h in earlier:
            if rs - h < 2:
                continue
            for ls in earlier:
                if ls >= h - 1:
                    continue
                span = rs - ls
                if not cfg.hs_min_span <= span <= cfg.hs_max_span:
                    continue
                t1 = ls + 1 + int(np.argmin(Q[ls + 1 : h]))
                t2 = h + 1 + int(np.argmin(Q[h + 1 : rs]))
                slope = (float(Q[t2]) - float(Q[t1])) / (t2 - t1)
                neck = lambda i: float(Q[t1]) + slope * (i - t1)  # noqa: E731
                head, lsv, rsv = float(P[h]), float(P[ls]), float(P[rs])
                hh = head - neck(h)
                atr = float(ctx.atr[h])
                if not math.isfinite(atr) or hh <= 0:
                    continue
                prom = head - max(lsv, rsv)
                sh_diff = abs(lsv - rsv)
                allowed = min(cfg.hs_shoulder_diff_height_frac * hh, cfg.hs_shoulder_diff_price_frac * abs((lsv + rsv) / 2))
                ratio = (h - ls) / (rs - h)
                tilt = abs(float(Q[t1]) - float(Q[t2]))
                tilt_allowed = min(cfg.hs_neckline_tilt_frac * hh, cfg.hs_neckline_tilt_price_frac * abs((float(Q[t1]) + float(Q[t2])) / 2))
                w0 = ls - span
                prior_move = lsv - float(Q[w0:ls].min()) if w0 >= 0 else float("nan")
                trend_high = float(P[max(0, w0) : rs + 1].max())
                ls_h, rs_h = lsv - neck(ls), rsv - neck(rs)
                avg_sh = (ls_h + rs_h) / 2
                legs = np.array([t1 - ls, h - t1, t2 - h, rs - t2], dtype=float)
                leg_ratio = float(legs.max() / legs.mean())
                swing = max(
                    _counter_swing(C[ls : t1 + 1], up=False),
                    _counter_swing(C[t1 : h + 1], up=True),
                    _counter_swing(C[h : t2 + 1], up=False),
                    _counter_swing(C[t2 : rs + 1], up=True),
                )
                ends_ok = lsv >= float(P[ls : t1 + 1].max()) and rsv >= float(P[t2 : rs + 1].max())
                head_swing = _head_region_swing(C, P, t1, h, t2, max(lsv, rsv))
                wick = max(_wick(C, P, ls, True), _wick(C, P, h, True), _wick(C, P, rs, True))
                arm_wick = max(_wick(C, Q, t1, False), _wick(C, Q, t2, False))
                gates = [
                    Gate("span_bars", True, span, f"{cfg.hs_min_span}-{cfg.hs_max_span}"),
                    Gate("head_is_extreme", head >= float(P[ls : rs + 1].max()), s * head, "head is the extreme of LS..RS"),
                    Gate("head_is_trend_extreme", head >= trend_high, s * trend_high, f"head is the extreme of the prior-trend window + pattern ({span} bars before LS .. RS)"),
                    Gate("consecutive_extrema", swing < cfg.reversal_counter_swing_frac * hh and ends_ok, swing / hh, f"no counter-swing >= {cfg.reversal_counter_swing_frac} x HH inside LS-T1-H-T2-RS; LS / RS are the highs of their legs"),
                    Gate("single_head", head_swing < cfg.hs_head_region_swing_frac * hh, head_swing / hh, f"no dip >= {cfg.hs_head_region_swing_frac} x HH while above the higher shoulder"),
                    Gate("close_confirmed_peaks", wick <= cfg.reversal_peak_wick_max_frac * hh, wick / hh, f"LS, H and RS each have a close within {cfg.reversal_peak_wick_max_frac} x HH (2 bars)"),
                    Gate("close_confirmed_neckline", arm_wick <= cfg.reversal_neckline_wick_max_frac * hh, arm_wick / hh, f"T1 and T2 each have a close within {cfg.reversal_neckline_wick_max_frac} x HH (2 bars)"),
                    Gate("head_height_atr", hh >= cfg.reversal_min_height_atr * atr, hh / atr, f">= {cfg.reversal_min_height_atr} (ATR at head)"),
                    Gate("head_prominence", prom >= cfg.hs_prominence_height_frac * hh and prom >= cfg.hs_prominence_price_frac * abs(head), prom, f">= max(0.2 HH, 0.03 H) = {max(cfg.hs_prominence_height_frac * hh, cfg.hs_prominence_price_frac * abs(head)):.4g}"),
                    Gate("shoulder_similarity", sh_diff <= allowed, sh_diff, f"<= min({cfg.hs_shoulder_diff_height_frac} HH, {cfg.hs_shoulder_diff_price_frac} avg) = {allowed:.4g}"),
                    Gate("left_shoulder_height", lsv - neck(ls) >= cfg.hs_shoulder_min_height_frac * hh, (lsv - neck(ls)) / hh, f">= {cfg.hs_shoulder_min_height_frac} x HH"),
                    Gate("right_shoulder_height", rsv - neck(rs) >= cfg.hs_shoulder_min_height_frac * hh, (rsv - neck(rs)) / hh, f">= {cfg.hs_shoulder_min_height_frac} x HH"),
                    Gate("shoulders_below_head", avg_sh <= cfg.hs_shoulder_max_avg_height_frac * hh, avg_sh / hh, f"average shoulder height <= {cfg.hs_shoulder_max_avg_height_frac} x HH"),
                    Gate("leg_balance", leg_ratio <= cfg.hs_leg_balance_max, leg_ratio, f"longest leg <= {cfg.hs_leg_balance_max} x mean leg"),
                    Gate("time_symmetry", cfg.hs_time_symmetry_min <= ratio <= cfg.hs_time_symmetry_max, ratio, f"{cfg.hs_time_symmetry_min}-{cfg.hs_time_symmetry_max}"),
                    Gate("neckline_tilt", tilt <= tilt_allowed, tilt, f"<= min({cfg.hs_neckline_tilt_frac} HH, {cfg.hs_neckline_tilt_price_frac} avg armpit) = {tilt_allowed:.4g}"),
                    Gate("prior_trend", math.isfinite(prior_move) and prior_move >= hh, prior_move, f">= HH = {hh:.4g} over the {span} bars before LS"),
                ]
                valid = all(g.passed for g in gates)
                if not valid and not ctx.collect_rejections:
                    continue
                sym_raw = 1 - abs(math.log(ratio)) / math.log(2) if ratio > 0 else 0.0
                comps = [
                    QualityComponent("shoulder_similarity", w["shoulder_similarity"], 1 - sh_diff / allowed if allowed > 0 else 0.0, clamp01(1 - sh_diff / allowed) if allowed > 0 else 0.0, "1 - |LS - RS| / allowed difference"),
                    QualityComponent("time_symmetry", w["time_symmetry"], sym_raw, clamp01(sym_raw), "1 - |ln(ratio)| / ln(2)"),
                    QualityComponent("head_prominence", w["head_prominence"], (prom / hh - 0.2) / 0.2, clamp01((prom / hh - 0.2) / 0.2), "((prominence / HH) - 0.20) / 0.20"),
                    QualityComponent("neckline_flatness", w["neckline_flatness"], 1 - sdiv(tilt, tilt_allowed), clamp01(1 - sdiv(tilt, tilt_allowed)), "1 - |T1 - T2| / allowed tilt"),
                    QualityComponent("size", w["size"], sdiv(hh / atr, cfg.quality_size_atr_full), clamp01(sdiv(hh / atr, cfg.quality_size_atr_full)), "min((HH / ATR) / 6, 1)"),
                ]
                neckline = Line(t1, s * float(Q[t1]), s * slope)
                metrics = {
                    "ls_index": ls,
                    "head_index": h,
                    "rs_index": rs,
                    "t1_index": t1,
                    "t2_index": t2,
                    "head_height": hh,
                    "head_height_atr": hh / atr,
                    "head_prominence": prom,
                    "shoulder_difference": sh_diff,
                    "allowed_shoulder_difference": allowed,
                    "time_symmetry_ratio": ratio,
                    "neckline_tilt": tilt,
                    "allowed_neckline_tilt": tilt_allowed,
                    "counter_swing_hh": swing / hh,
                    "head_region_swing_hh": head_swing / hh,
                    "max_wick_hh": wick / hh,
                    "armpit_wick_hh": arm_wick / hh,
                    "leg_balance": leg_ratio,
                    "avg_shoulder_height_hh": avg_sh / hh,
                    "rs_ls_volume_ratio": _leg_volume_ratio(ctx.volume, ls, t1, t2, rs),
                    "neckline_at_head": s * neck(h),
                    "prior_trend_move": prior_move,
                    "width_pct": 100 * hh / abs(neck(h)),
                    "move_pct": s * 100 * prior_move / abs(lsv) if math.isfinite(prior_move) else float("nan"),
                    "atr_at_formation_end": float(ctx.atr[rs]),
                    "atr_latest": float(ctx.atr[ctx.last]),
                    "provisional_pivot": provisional,
                }
                pivots_out = [("LS", ls, s * lsv), ("T1", t1, s * float(Q[t1])), ("H", h, s * head), ("T2", t2, s * float(Q[t2])), ("RS", rs, s * rsv)]
                results.append(
                    self._candidate(
                        ctx, HS if top else IHS, top, ls, rs, neckline, s * head, pivots_out, gates, comps, metrics, provisional, rejected=not valid,
                    )
                )
        return self._best(results, key=lambda c: (c.quality, c.length))

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _best(results: list[Candidate], key) -> list[Candidate]:
        valid = [c for c in results if not c.rejected]
        rejected = [c for c in results if c.rejected]
        best = [max(valid, key=key)] if valid else []
        return best + rejected

    def _candidate(self, ctx, pattern, top, start, end, neckline, extreme, pivots, gates, comps, metrics, provisional, rejected) -> Candidate:
        return Candidate(
            pattern=pattern,
            family=self.family,
            start=start,
            end=end,
            upper=None if top else neckline,
            lower=neckline if top else None,
            touches=[],
            swing_highs=[],
            swing_lows=[],
            fit_highs=[],
            fit_lows=[],
            metrics=metrics,
            gates=gates,
            components=comps,
            quality=quality_score(comps),
            rejected=rejected,
            provisional=provisional,
            group="reversal_top" if top else "reversal_bottom",
            lifecycle_kind="neckline_down" if top else "neckline_up",
            extreme=extreme,
            pivots=pivots,
            region_start=start,
        )
