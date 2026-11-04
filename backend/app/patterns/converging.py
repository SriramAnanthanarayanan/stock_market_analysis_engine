"""Converging family: triangles and wedges (spec 8.2).

Independent anchored boundary fits. Shared gates: >= 2 upper, >= 2 lower and
>= 5 total touches; touch spread >= 0.4 L on each boundary; 0.15 <= CR <= converging_cr_max (0.50 since config 1.3.0)
(CR = W1 / W0); containment >= 85%; W0 / first close inside the timeframe width
band. Line state (independent slope movement M = slope x (L-1) vs W0):
Rising if M > +0.15 W0, Falling if M < -0.15 W0, otherwise Flat.

    Flat    / Rising  -> Ascending Triangle
    Falling / Flat    -> Descending Triangle
    Falling / Rising  -> Symmetrical Triangle
    Falling / Falling -> Falling Wedge  (upper falls faster; midline drop >= tf min move)
    Rising  / Rising  -> Rising Wedge   (upper rises slower; midline rise >= tf min move)
    anything else     -> rejected (incl. expanding formations)
"""

from __future__ import annotations

from .base import Candidate, Gate, PatternDetector, QualityComponent, SeriesContext, clamp01, sdiv, quality_score
from .shared import (
    classify_validity,
    containment,
    find_touches,
    fit_independent,
    touch_deficit,
    touch_gates,
    touch_stats,
    widths,
)

ASC, DESC, SYM = "ascending_triangle", "descending_triangle", "symmetrical_triangle"
FWEDGE, RWEDGE = "falling_wedge", "rising_wedge"

_TABLE = {
    ("flat", "rising"): ASC,
    ("falling", "flat"): DESC,
    ("falling", "rising"): SYM,
    ("falling", "falling"): FWEDGE,
    ("rising", "rising"): RWEDGE,
}


def line_state(move: float, w0: float, frac: float) -> str:
    if move > frac * w0:
        return "rising"
    if move < -frac * w0:
        return "falling"
    return "flat"


class ConvergingDetector(PatternDetector):
    family = "converging"
    patterns = (ASC, DESC, SYM, FWEDGE, RWEDGE)

    def detect(self, ctx: SeriesContext) -> list[Candidate]:
        out = []
        for length in ctx.cfg.converging_lengths:
            cand = self.evaluate_window(ctx, length)
            if cand is not None:
                out.append(cand)
        return out

    def evaluate_window(self, ctx: SeriesContext, length: int) -> Candidate | None:
        cfg = ctx.cfg
        e = ctx.fit_end
        s = e - length + 1
        if s < 0 or s < ctx.first_valid_atr():
            return None
        sw = ctx.swings(cfg.swing_k_structure)
        hi, lo = sw.highs_in(s, e), sw.lows_in(s, e)
        if len(hi) < 2 or len(lo) < 2:
            return None

        fit = fit_independent(ctx, s, hi, lo)
        up, low = fit.upper, fit.lower
        w0, w1, wm = widths(up, low, s, e)
        if w0 <= 0 or w1 <= 0:
            return None  # crossed or degenerate boundaries
        cr = w1 / w0
        if cr >= 1.0:
            return None  # parallel or expanding - never a triangle/wedge

        move_u = up.slope * (length - 1)
        move_l = low.slope * (length - 1)
        state_u = line_state(move_u, w0, cfg.line_state_move_frac)
        state_l = line_state(move_l, w0, cfg.line_state_move_frac)
        pattern = _TABLE.get((state_u, state_l))
        if pattern is None:
            return None

        apex_x = (up.intercept - low.intercept) / (low.slope - up.slope)
        apex_index = s + apex_x
        if apex_index <= e:
            return None  # apex already behind the latest fitted bar: not a live formation

        touches = find_touches(ctx, up, low, hi, lo)
        if not touches:
            return None
        first_touch = min(t.index for t in touches)
        cont = containment(ctx, up, low, first_touch, e)
        w0_pct = 100.0 * w0 / ctx.close[s]
        band_lo, band_hi = cfg.width_band_pct(ctx.timeframe)
        min_move = cfg.min_move_pct(ctx.timeframe)
        mid0 = (up.at(s) + low.at(s)) / 2.0
        mid1 = (up.at(e) + low.at(e)) / 2.0
        mid_move_pct = 100.0 * (mid1 - mid0) / mid0 if mid0 > 0 else 0.0

        lead = first_touch - s
        max_lead = cfg.max_lead_before_first_touch_frac * length
        gates: list[Gate] = [
            Gate("bars_before_first_touch", lead <= max_lead, lead, f"<= {max_lead:g} (0.33 x L)"),
            Gate("convergence_ratio_min", cr >= cfg.converging_cr_min, cr, f">= {cfg.converging_cr_min}"),
            Gate("convergence_ratio_max", cr <= cfg.converging_cr_max, cr, f"<= {cfg.converging_cr_max}"),
            Gate("containment", cont >= cfg.converging_min_containment, cont, f">= {cfg.converging_min_containment}"),
            Gate("initial_width_pct_of_first_close", band_lo <= w0_pct <= band_hi, w0_pct, f"{band_lo}-{band_hi}% ({ctx.timeframe})"),
            Gate("upper_line_state", True, state_u, "classification"),
            Gate("lower_line_state", True, state_l, "classification"),
        ]
        if pattern in (FWEDGE, RWEDGE):
            gates.append(
                Gate("upper_slope_below_lower_slope", up.slope < low.slope, up.slope - low.slope, "< 0 (lines converge)")
            )
            if pattern == FWEDGE:
                gates.append(Gate("midline_drop_pct", -mid_move_pct >= min_move, -mid_move_pct, f">= {min_move}% ({ctx.timeframe})"))
            else:
                gates.append(Gate("midline_rise_pct", mid_move_pct >= min_move, mid_move_pct, f">= {min_move}% ({ctx.timeframe})"))

        su = touch_stats(touches, "upper", s, length)
        sl = touch_stats(touches, "lower", s, length)
        min_u, min_l, min_t = cfg.converging_min_upper_touches, cfg.converging_min_lower_touches, cfg.converging_min_total_touches
        gates += touch_gates(su, sl, length, min_u, min_l, min_t, cfg, require_halves=False)
        deficit = touch_deficit(su.count, sl.count, min_u, min_l, min_t)
        verdict = classify_validity(gates, su, sl, min_u, min_l, deficit)
        if verdict == "rejected" and not ctx.collect_rejections:
            return None

        total = su.count + sl.count
        ratios = [t.residual / t.tolerance for t in touches if t.tolerance > 0]
        mean_ratio = sum(ratios) / len(ratios) if ratios else 1.0
        spread_frac = min(su.spread, sl.spread) / length
        w = cfg.quality_weights_converging
        conv_raw = sdiv(cfg.converging_cr_max - cr, cfg.converging_cr_max - cfg.converging_cr_min)
        cont_raw = sdiv(cont - cfg.converging_min_containment, 1 - cfg.converging_min_containment)
        spread_raw = sdiv(spread_frac - cfg.touch_spread_min_frac, 1 - cfg.touch_spread_min_frac)
        comps = [
            QualityComponent("convergence", w["convergence"], conv_raw, clamp01(conv_raw), f"({cfg.converging_cr_max} - CR) / ({cfg.converging_cr_max} - {cfg.converging_cr_min})"),
            QualityComponent("touches", w["touches"], sdiv(total, cfg.quality_touch_target), clamp01(sdiv(total, cfg.quality_touch_target)), "min(total_touches / 8, 1)"),
            QualityComponent("containment", w["containment"], cont_raw, clamp01(cont_raw), "(containment - 0.85) / 0.15"),
            QualityComponent("boundary_fit", w["boundary_fit"], 1 - mean_ratio, clamp01(1 - mean_ratio), "1 - mean(touch_residual / touch_tolerance)"),
            QualityComponent("touch_spread", w["touch_spread"], spread_raw, clamp01(spread_raw), "(min boundary spread / L - 0.40) / 0.60"),
        ]
        metrics = {
            "formation_length": length,
            "width_start": w0,
            "width_end": w1,
            "width_mean": wm,
            "width_pct": 100.0 * w1 / ctx.close[e],
            "initial_width_pct": w0_pct,
            "convergence_ratio": cr,
            "upper_slope": up.slope,
            "lower_slope": low.slope,
            "upper_movement": move_u,
            "lower_movement": move_l,
            "upper_state": state_u,
            "lower_state": state_l,
            "move_pct": mid_move_pct,
            "containment": cont,
            "apex_index": apex_index,
            "apex_bars_after_formation_end": apex_index - e,
            "apex_price": float(up.at(apex_index)),
            "first_touch_index": first_touch,
            "upper_touch_spread": su.spread,
            "lower_touch_spread": sl.spread,
            "atr_at_formation_end": float(ctx.atr[e]),
            "atr_latest": float(ctx.atr[ctx.last]),
        }
        return Candidate(
            pattern=pattern,
            family=self.family,
            start=s,
            end=e,
            upper=up,
            lower=low,
            touches=touches,
            swing_highs=[int(i) for i in hi],
            swing_lows=[int(i) for i in lo],
            fit_highs=[int(i) for i in fit.fit_highs],
            fit_lows=[int(i) for i in fit.fit_lows],
            metrics=metrics,
            gates=gates,
            components=comps,
            quality=quality_score(comps),
            developing=verdict == "developing",
            rejected=verdict == "rejected",
            apex_index=apex_index,
            first_touch=first_touch,
        )
