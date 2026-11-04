"""Continuation patterns: Bullish/Bearish Flag and Pennant (spec 8.3).

Every candidate needs a valid preceding impulse pole. For each consolidation
length C (ending at the latest fitted bar ``e``), ``P_end = e - C``:

Pole (bullish; bearish mirrors it)
    * P_end is the highest high of the ``pole_lookback_bars`` bars before it
      and of the consolidation (no new high inside the consolidation);
    * pole start = lowest low in the 15 bars preceding P_end (latest if tied);
    * pole length 3-15 bars; H = high[P_end] - low[start];
    * H >= 3 x ATR measured at the pole start; H / low[start] >= tf minimum move;
    * >= 60 % of pole bars close above the previous close.

Consolidation (raw bar highs/lows, touches >= 2 bars apart)
    * Flag: 5-25 bars and <= 3 x pole length; parallel (pooled) fit;
      |1 - CR| <= 0.25 on independent fits; shared-slope move within
      [-0.5 H, +0.1 H] (bearish [-0.1 H, +0.5 H]); retracement <= 50 %;
      W0 <= 0.5 H; >= 2 upper and 2 lower touches; containment >= 85 %.
    * Pennant: 5-20 bars, independent fit, 0.15 <= CR <= 0.60, upper falling
      and lower rising, retracement <= 38 %, same width/touch/containment.
      (The spec's bearish text "upper rising, lower falling" contradicts its own
      convergence requirement; both pennants converge - see implementation notes.)
    * Provisional: a valid pole followed by 3-4 bars whose direction, width and
      retracement pass is reported as Forming (never Validated).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .base import Candidate, Gate, Line, PatternDetector, QualityComponent, SeriesContext, Touch, clamp01, sdiv, quality_score
from .shared import anchor_intercept, classify_validity, containment, ols, pooled_slope, touch_deficit, touch_stats

BULL_FLAG, BEAR_FLAG = "bullish_flag", "bearish_flag"
BULL_PENNANT, BEAR_PENNANT = "bullish_pennant", "bearish_pennant"


@dataclass
class Pole:
    bull: bool
    start: int
    end: int
    base: float  # pole start price (low for bull, high for bear)
    top: float  # P_end price (high for bull, low for bear)
    height: float
    atr: float
    directional: float
    gates: list[Gate]

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def valid(self) -> bool:
        return all(g.passed for g in self.gates)


def find_pole(ctx: SeriesContext, p_end: int, bull: bool) -> Pole | None:
    cfg = ctx.cfg
    s0 = p_end - cfg.pole_lookback_bars
    if s0 < 0:
        return None
    hi, lo, cl = ctx.high, ctx.low, ctx.close
    if bull:
        if hi[p_end] < hi[s0:p_end].max():
            return None  # P_end is not the highest high before the consolidation
        seg = lo[s0:p_end]
        start = s0 + int(np.flatnonzero(seg == seg.min())[-1])
        base, top = float(lo[start]), float(hi[p_end])
        height = top - base
        moves = cl[start + 1 : p_end + 1] > cl[start:p_end]
    else:
        if lo[p_end] > lo[s0:p_end].min():
            return None
        seg = hi[s0:p_end]
        start = s0 + int(np.flatnonzero(seg == seg.max())[-1])
        base, top = float(hi[start]), float(lo[p_end])
        height = base - top
        moves = cl[start + 1 : p_end + 1] < cl[start:p_end]
    atr = float(ctx.atr[start])
    if not np.isfinite(atr) or height <= 0:
        return None
    length = p_end - start
    directional = float(moves.mean()) if len(moves) else 0.0
    move_pct = 100.0 * height / base
    min_move = cfg.min_move_pct(ctx.timeframe)
    gates = [
        Gate("pole_bars", cfg.pole_min_bars <= length <= cfg.pole_max_bars, length, f"{cfg.pole_min_bars}-{cfg.pole_max_bars}"),
        Gate("pole_height_atr", height >= cfg.pole_min_atr_mult * atr, height / atr, f">= {cfg.pole_min_atr_mult} (ATR at pole start)"),
        Gate("pole_move_pct", move_pct >= min_move, move_pct, f">= {min_move}% ({ctx.timeframe})"),
        Gate("pole_directional_closes", directional >= cfg.pole_min_directional_frac, directional, f">= {cfg.pole_min_directional_frac}"),
        Gate("pole_steepness", length > 0 and height / atr / length >= cfg.pole_min_atr_per_bar, height / atr / max(length, 1), f">= {cfg.pole_min_atr_per_bar} ATR per bar"),
    ]
    return Pole(bull, start, p_end, base, top, height, atr, directional, gates)


def _fit(ctx: SeriesContext, c0: int, e: int, parallel: bool, max_iter: int = 3) -> tuple[Line, Line]:
    """Anchored boundary fit on raw bar highs/lows.

    OLS through *every* raw high follows the consolidation's midline, not its
    edge (bars oscillate across the whole band). As for the line patterns, the
    slope is re-estimated on the raw points within ``fit_refine_tolerance_mult``
    x touch tolerance of the current line (iterated until stable, <= 3 times);
    anchoring always uses every bar, so the lines bound the whole consolidation.
    """
    cfg = ctx.cfg
    x = np.arange(e - c0 + 1, dtype=float)
    hi, lo = ctx.high[c0 : e + 1], ctx.low[c0 : e + 1]
    atr = ctx.atr[c0 : e + 1]
    keep_u = np.ones(len(x), dtype=bool)
    keep_l = np.ones(len(x), dtype=bool)
    for _ in range(max_iter + 1):
        if parallel:
            bu = bl = pooled_slope(x[keep_u], hi[keep_u], x[keep_l], lo[keep_l])
        else:
            bu, bl = ols(x[keep_u], hi[keep_u])[0], ols(x[keep_l], lo[keep_l])[0]
        au, al = anchor_intercept(x, hi, bu, "upper"), anchor_intercept(x, lo, bl, "lower")
        width = (au + bu * x) - (al + bl * x)
        tol = cfg.fit_refine_tolerance_mult * np.maximum(cfg.touch_tol_width_frac * width, cfg.touch_tol_atr_frac * atr)
        new_u = np.abs(au + bu * x - hi) <= tol
        new_l = np.abs(al + bl * x - lo) <= tol
        if new_u.sum() < 2 or new_l.sum() < 2 or (np.array_equal(new_u, keep_u) and np.array_equal(new_l, keep_l)):
            break
        keep_u, keep_l = new_u, new_l
    return Line(c0, au, bu), Line(c0, al, bl)


def raw_touches(ctx: SeriesContext, upper: Line, lower: Line, c0: int, e: int) -> list[Touch]:
    """Raw-bar touches (absolute distance), counted at least ``gap`` bars apart."""
    cfg = ctx.cfg
    gap = cfg.continuation_touch_min_gap_bars
    out: list[Touch] = []
    for side, line, prices in (("upper", upper, ctx.high), ("lower", lower, ctx.low)):
        last = -(10**9)
        for i in range(c0, e + 1):
            w = float(upper.at(i) - lower.at(i))
            tol = max(cfg.touch_tol_width_frac * w, cfg.touch_tol_atr_frac * float(ctx.atr[i]))
            r = abs(float(line.at(i)) - float(prices[i]))
            if r <= tol and i - last >= gap:
                out.append(Touch(i, float(prices[i]), side, r, tol))
                last = i
    out.sort(key=lambda t: (t.index, t.boundary))
    return out


def _volume_ratio(ctx: SeriesContext, pole: Pole, c0: int, e: int) -> float | None:
    pv = ctx.volume[pole.start + 1 : pole.end + 1]
    cv = ctx.volume[c0 : e + 1]
    pv, cv = pv[np.isfinite(pv)], cv[np.isfinite(cv)]
    if not len(pv) or not len(cv) or pv.mean() <= 0:
        return None
    return float(cv.mean() / pv.mean())


class ContinuationDetector(PatternDetector):
    family = "continuation"
    patterns = (BULL_FLAG, BEAR_FLAG, BULL_PENNANT, BEAR_PENNANT)

    def detect(self, ctx: SeriesContext) -> list[Candidate]:
        cfg = ctx.cfg
        e = ctx.fit_end
        out: list[Candidate] = []
        for bull in (True, False):
            for n_cons in range(cfg.forming_min_bars, cfg.flag_max_bars + 1):
                c0 = e - n_cons + 1
                pole = find_pole(ctx, c0 - 1, bull)
                if pole is None or (not pole.valid and not ctx.collect_rejections):
                    continue
                if n_cons < cfg.flag_min_bars:
                    if n_cons <= cfg.forming_max_bars and pole.valid:
                        cand = self._provisional(ctx, pole, c0, e)
                        if cand is not None:
                            out.append(cand)
                    continue
                for pennant in (False, True):
                    if pennant and n_cons > cfg.pennant_max_bars:
                        continue
                    cand = self._evaluate(ctx, pole, c0, e, pennant)
                    if cand is not None:
                        out.append(cand)
        return out

    # ------------------------------------------------------------- shared
    def _common(self, ctx: SeriesContext, pole: Pole, c0: int, e: int) -> tuple[float, float]:
        """(retracement, no-new-extreme ok)."""
        if pole.bull:
            retr = (pole.top - float(ctx.low[c0 : e + 1].min())) / pole.height
            ok = float(ctx.high[c0 : e + 1].max()) <= pole.top
        else:
            retr = (float(ctx.high[c0 : e + 1].max()) - pole.top) / pole.height
            ok = float(ctx.low[c0 : e + 1].min()) >= pole.top
        return retr, ok

    @staticmethod
    def _move_band(cfg, pole: Pole) -> tuple[float, float]:
        cfg_ag, cfg_with = cfg.flag_move_against_max_frac, cfg.flag_move_with_max_frac
        h = pole.height
        return (-cfg_ag * h, cfg_with * h) if pole.bull else (-cfg_with * h, cfg_ag * h)

    def _provisional(self, ctx: SeriesContext, pole: Pole, c0: int, e: int) -> Candidate | None:
        cfg = ctx.cfg
        n_cons = e - c0 + 1
        upper, lower = _fit(ctx, c0, e, parallel=True)
        move = upper.slope * (n_cons - 1)
        lo_m, hi_m = self._move_band(cfg, pole)
        retr, no_new = self._common(ctx, pole, c0, e)
        rng = float(ctx.high[c0 : e + 1].max() - ctx.low[c0 : e + 1].min())
        gates = pole.gates + [
            Gate("no_new_extreme_in_consolidation", no_new, no_new, "P_end stays the extreme"),
            Gate("consolidation_vs_pole_bars", n_cons <= cfg.consolidation_max_pole_mult * pole.length, n_cons, f"<= {cfg.consolidation_max_pole_mult} x pole bars"),
            Gate("preliminary_direction", lo_m <= move <= hi_m, move / pole.height, f"{lo_m / pole.height:g}..{hi_m / pole.height:g} x H"),
            Gate("preliminary_width", rng <= cfg.consolidation_max_width_pole_frac * pole.height, rng / pole.height, f"<= {cfg.consolidation_max_width_pole_frac} x H"),
            Gate("preliminary_retracement", retr <= cfg.flag_max_retracement, retr, f"<= {cfg.flag_max_retracement}"),
        ]
        if not all(g.passed for g in gates):
            return None
        touches = raw_touches(ctx, upper, lower, c0, e)
        comps = self._components(ctx, pole, c0, e, retr, cfg.flag_max_retracement, rng, touches)
        return self._candidate(
            ctx, BULL_FLAG if pole.bull else BEAR_FLAG, pole, c0, e, upper, lower, touches, gates, comps,
            {"consolidation_range": rng, "retracement": retr, "movement": move, "provisional": True}, provisional=True,
        )

    def _evaluate(self, ctx: SeriesContext, pole: Pole, c0: int, e: int, pennant: bool) -> Candidate | None:
        cfg = ctx.cfg
        n_cons = e - c0 + 1
        ind_u, ind_l = _fit(ctx, c0, e, parallel=False)
        w0i, w1i = float(ind_u.at(c0) - ind_l.at(c0)), float(ind_u.at(e) - ind_l.at(e))
        if w0i <= 0 or w1i <= 0:
            return None
        cr = w1i / w0i
        retr, no_new = self._common(ctx, pole, c0, e)
        if pennant:
            upper, lower = ind_u, ind_l
            max_bars, max_retr = cfg.pennant_max_bars, cfg.pennant_max_retracement
            if upper.slope >= lower.slope:
                return None  # not converging
            apex_index = c0 + (upper.intercept - lower.intercept) / (lower.slope - upper.slope)
            if apex_index <= e:
                return None
        else:
            upper, lower = _fit(ctx, c0, e, parallel=True)
            max_bars, max_retr = cfg.flag_max_bars, cfg.flag_max_retracement
            apex_index = None
        w0 = float(upper.at(c0) - lower.at(c0))
        if w0 <= 0:
            return None
        touches = raw_touches(ctx, upper, lower, c0, e)
        if not touches:
            return None
        first_touch = min(t.index for t in touches)
        cont = containment(ctx, upper, lower, first_touch, e)
        gates = list(pole.gates) + [
            Gate("no_new_extreme_in_consolidation", no_new, no_new, "P_end stays the extreme"),
            Gate("consolidation_bars", cfg.flag_min_bars <= n_cons <= max_bars, n_cons, f"{cfg.flag_min_bars}-{max_bars}"),
            Gate("consolidation_vs_pole_bars", n_cons <= cfg.consolidation_max_pole_mult * pole.length, n_cons, f"<= {cfg.consolidation_max_pole_mult} x pole bars ({pole.length})"),
        ]
        if pennant:
            gates += [
                Gate("convergence_ratio_min", cr >= cfg.pennant_cr_min, cr, f">= {cfg.pennant_cr_min}"),
                Gate("convergence_ratio_max", cr <= cfg.pennant_cr_max, cr, f"<= {cfg.pennant_cr_max}"),
                Gate("upper_falling", upper.slope < 0, upper.slope, "< 0"),
                Gate("lower_rising", lower.slope > 0, lower.slope, "> 0"),
            ]
            move = 0.5 * (upper.slope + lower.slope) * (n_cons - 1)
        else:
            move = upper.slope * (n_cons - 1)
            lo_m, hi_m = self._move_band(cfg, pole)
            gates += [
                Gate("flag_shape_cr_deviation", abs(1 - cr) <= cfg.flag_max_cr_deviation, abs(1 - cr), f"<= {cfg.flag_max_cr_deviation}"),
                Gate("flag_movement", lo_m <= move <= hi_m, move / pole.height, f"{lo_m / pole.height:g}..{hi_m / pole.height:g} x H"),
            ]
        gates += [
            Gate("retracement", retr <= max_retr, retr, f"<= {max_retr}"),
            Gate("initial_width_vs_pole", w0 <= cfg.consolidation_max_width_pole_frac * pole.height, w0 / pole.height, f"<= {cfg.consolidation_max_width_pole_frac} x H"),
            Gate("containment", cont >= cfg.continuation_min_containment, cont, f">= {cfg.continuation_min_containment}"),
        ]
        su = touch_stats(touches, "upper", c0, n_cons)
        sl = touch_stats(touches, "lower", c0, n_cons)
        mu, ml = cfg.continuation_min_upper_touches, cfg.continuation_min_lower_touches
        gates += [
            Gate("upper_touches", su.count >= mu, su.count, f">= {mu} (raw bars, >= {cfg.continuation_touch_min_gap_bars} bars apart)", "touch"),
            Gate("lower_touches", sl.count >= ml, sl.count, f">= {ml} (raw bars, >= {cfg.continuation_touch_min_gap_bars} bars apart)", "touch"),
        ]
        verdict = classify_validity(gates, su, sl, mu, ml, touch_deficit(su.count, sl.count, mu, ml, mu + ml))
        if verdict == "rejected" and not ctx.collect_rejections:
            return None
        comps = self._components(ctx, pole, c0, e, retr, max_retr, w0, touches)
        if pennant:
            pattern = BULL_PENNANT if pole.bull else BEAR_PENNANT
        else:
            pattern = BULL_FLAG if pole.bull else BEAR_FLAG
        metrics = {
            "convergence_ratio": cr,
            "width_start": w0,
            "width_end": float(upper.at(e) - lower.at(e)),
            "movement": move,
            "movement_vs_pole": move / pole.height,
            "retracement": retr,
            "containment": cont,
            "first_touch_index": first_touch,
        }
        if apex_index is not None:
            metrics.update({"apex_index": apex_index, "apex_bars_after_formation_end": apex_index - e, "apex_price": float(upper.at(apex_index))})
        cand = self._candidate(ctx, pattern, pole, c0, e, upper, lower, touches, gates, comps, metrics)
        cand.developing = verdict == "developing"
        cand.rejected = verdict == "rejected"
        cand.apex_index = apex_index
        cand.first_touch = first_touch
        return cand

    def _components(self, ctx, pole: Pole, c0: int, e: int, retr: float, max_retr: float, width: float, touches: list[Touch]) -> list[QualityComponent]:
        cfg = ctx.cfg
        w = cfg.quality_weights_continuation
        ratios = [t.residual / t.tolerance for t in touches if t.tolerance > 0]
        fit_raw = 1 - (sum(ratios) / len(ratios)) if ratios else 0.0
        vr = _volume_ratio(ctx, pole, c0, e)
        if vr is None:
            vol_raw = 0.0
        else:
            span = cfg.volume_contraction_zero - cfg.volume_contraction_full
            vol_raw = (cfg.volume_contraction_zero - vr) / span if span > 0 else float(vr <= cfg.volume_contraction_full)
        strength = sdiv(pole.height / pole.atr, cfg.quality_size_atr_full)
        return [
            QualityComponent("pole_strength", w["pole_strength"], strength, clamp01(strength), "min((H / ATR) / 6, 1)"),
            QualityComponent("retracement", w["retracement"], 1 - sdiv(retr, max_retr), clamp01(1 - sdiv(retr, max_retr)), "1 - retracement / permitted"),
            QualityComponent(
                "tightness", w["tightness"], 1 - sdiv(width, cfg.consolidation_max_width_pole_frac * pole.height),
                clamp01(1 - sdiv(width, cfg.consolidation_max_width_pole_frac * pole.height)), "1 - W0 / (0.5 x H)",
            ),
            QualityComponent("boundary_fit", w["boundary_fit"], fit_raw, clamp01(fit_raw), "1 - mean(touch_residual / touch_tolerance)"),
            QualityComponent(
                "volume_contraction", w["volume_contraction"], vol_raw, clamp01(vol_raw),
                "1 at consolidation/pole volume <= 0.7, linear to 0 at >= 1.0 (soft factor, never a gate)",
            ),
        ]

    def _candidate(self, ctx, pattern, pole: Pole, c0, e, upper, lower, touches, gates, comps, extra, provisional=False) -> Candidate:
        vr = _volume_ratio(ctx, pole, c0, e)
        metrics = {
            "pole_start_index": pole.start,
            "pole_end_index": pole.end,
            "pole_bars": pole.length,
            "pole_height": pole.height,
            "pole_move_pct": 100.0 * pole.height / pole.base,
            "pole_atr": pole.atr,
            "pole_height_atr": pole.height / pole.atr,
            "pole_directional_closes": pole.directional,
            "consolidation_start_index": c0,
            "consolidation_bars": e - c0 + 1,
            "consolidation_pole_volume_ratio": vr,
            "width_pct": 100.0 * float(upper.at(e) - lower.at(e)) / float(ctx.close[e]),
            "move_pct": (1 if pole.bull else -1) * 100.0 * pole.height / pole.base,
            "atr_at_formation_end": float(ctx.atr[e]),
            "atr_latest": float(ctx.atr[ctx.last]),
            **extra,
        }
        return Candidate(
            pattern=pattern,
            family=self.family,
            start=pole.start,
            end=e,
            upper=upper,
            lower=lower,
            touches=touches,
            swing_highs=[],
            swing_lows=[],
            fit_highs=list(range(c0, e + 1)),
            fit_lows=list(range(c0, e + 1)),
            metrics=metrics,
            gates=gates,
            components=comps,
            quality=quality_score(comps),
            provisional=provisional,
            pole=(pole.start, pole.base, pole.end, pole.top),
            region_start=c0,
        )
