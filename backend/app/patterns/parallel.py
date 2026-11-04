"""Parallel family: Falling / Rising / Horizontal Channel and Rectangle (spec 8.1).

Independent boundary fits measure parallelism (the family test); the drawn
geometry is a shared-slope (pooled) fit. Sub-type is decided by the shared-slope
movement ``M = slope x (L - 1)`` relative to the mean independent width:

    Falling Channel     M <= -0.6 W_mean   (+ drop >= tf min move, close slope < 0, R^2 >= 0.50)
    Rising Channel      M >= +0.6 W_mean   (+ rise >= tf min move, close slope > 0, R^2 >= 0.50)
    Horizontal Channel  0.2 W_mean < |M| < 0.6 W_mean   (>= 6 touches)
    Rectangle           |M| <= 0.2 W_mean               (>= 6 touches, close R^2 < 0.30)

A structure classified as a channel that misses the minimum move is rejected,
not relabelled.
"""

from __future__ import annotations

from .base import Candidate, Gate, PatternDetector, QualityComponent, SeriesContext, clamp01, sdiv, quality_score
from .shared import (
    classify_validity,
    close_trend,
    containment,
    find_touches,
    fit_independent,
    fit_parallel,
    touch_deficit,
    touch_gates,
    touch_stats,
    widths,
)

FALLING, RISING, HORIZONTAL, RECTANGLE = "falling_channel", "rising_channel", "horizontal_channel", "rectangle"


class ParallelDetector(PatternDetector):
    family = "parallel"
    patterns = (FALLING, RISING, HORIZONTAL, RECTANGLE)

    def detect(self, ctx: SeriesContext) -> list[Candidate]:
        out = []
        for length in ctx.cfg.parallel_lengths:
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

        ind = fit_independent(ctx, s, hi, lo)
        w0i, w1i, wmi = widths(ind.upper, ind.lower, s, e)
        if w0i <= 0 or w1i <= 0:
            return None
        par_err = abs(ind.upper.slope - ind.lower.slope) * (length - 1) / wmi

        par = fit_parallel(ctx, s, hi, lo, ind.fit_highs, ind.fit_lows)
        wp = par.upper.intercept - par.lower.intercept
        if wp <= 0:
            return None
        touches = find_touches(ctx, par.upper, par.lower, hi, lo)
        if not touches:
            return None
        first_touch = min(t.index for t in touches)
        cont = containment(ctx, par.upper, par.lower, first_touch, e)
        c_slope, c_r2 = close_trend(ctx, first_touch, e)

        move = par.upper.slope * (length - 1)
        ratio = move / wmi
        mid0 = (par.upper.at(s) + par.lower.at(s)) / 2.0
        move_pct = 100.0 * move / mid0 if mid0 > 0 else 0.0
        mean_width_pct = 100.0 * wmi / ctx.close[e]
        band_lo, band_hi = cfg.width_band_pct(ctx.timeframe)
        min_move = cfg.min_move_pct(ctx.timeframe)

        if ratio <= -cfg.channel_move_frac:
            pattern = FALLING
        elif ratio >= cfg.channel_move_frac:
            pattern = RISING
        elif abs(ratio) <= cfg.rectangle_move_frac:
            pattern = RECTANGLE
        else:
            pattern = HORIZONTAL
        flat = pattern in (RECTANGLE, HORIZONTAL)
        min_total = cfg.parallel_min_total_touches_flat if flat else cfg.parallel_min_total_touches

        lead = first_touch - s
        max_lead = cfg.max_lead_before_first_touch_frac * length
        gates: list[Gate] = [
            Gate("parallelism_error", par_err <= cfg.parallel_max_error, par_err, f"<= {cfg.parallel_max_error}"),
            Gate("containment", cont >= cfg.parallel_min_containment, cont, f">= {cfg.parallel_min_containment}"),
            Gate("mean_width_pct_of_close", band_lo <= mean_width_pct <= band_hi, mean_width_pct, f"{band_lo}-{band_hi}% ({ctx.timeframe})"),
            Gate("bars_before_first_touch", lead <= max_lead, lead, f"<= {max_lead:g} (0.33 x L)"),
        ]
        if pattern == FALLING:
            gates += [
                Gate("movement_vs_mean_width", ratio <= -cfg.channel_move_frac, ratio, f"<= -{cfg.channel_move_frac}"),
                Gate("total_drop_pct", -move_pct >= min_move, -move_pct, f">= {min_move}% ({ctx.timeframe})"),
                Gate("close_slope", c_slope < 0, c_slope, "< 0"),
                Gate("close_r2", c_r2 >= cfg.close_trend_min_r2, c_r2, f">= {cfg.close_trend_min_r2}"),
            ]
        elif pattern == RISING:
            gates += [
                Gate("movement_vs_mean_width", ratio >= cfg.channel_move_frac, ratio, f">= {cfg.channel_move_frac}"),
                Gate("total_rise_pct", move_pct >= min_move, move_pct, f">= {min_move}% ({ctx.timeframe})"),
                Gate("close_slope", c_slope > 0, c_slope, "> 0"),
                Gate("close_r2", c_r2 >= cfg.close_trend_min_r2, c_r2, f">= {cfg.close_trend_min_r2}"),
            ]
        elif pattern == HORIZONTAL:
            gates.append(
                Gate(
                    "movement_vs_mean_width",
                    cfg.rectangle_move_frac < abs(ratio) < cfg.channel_move_frac,
                    abs(ratio),
                    f"{cfg.rectangle_move_frac} < |M|/W_mean < {cfg.channel_move_frac}",
                )
            )
        else:
            gates += [
                Gate("movement_vs_mean_width", abs(ratio) <= cfg.rectangle_move_frac, abs(ratio), f"<= {cfg.rectangle_move_frac}"),
                Gate("close_r2_max", c_r2 < cfg.rectangle_max_close_r2, c_r2, f"< {cfg.rectangle_max_close_r2}"),
            ]

        su = touch_stats(touches, "upper", s, length)
        sl = touch_stats(touches, "lower", s, length)
        min_u, min_l = cfg.parallel_min_upper_touches, cfg.parallel_min_lower_touches
        gates += touch_gates(su, sl, length, min_u, min_l, min_total, cfg, require_halves=True)
        deficit = touch_deficit(su.count, sl.count, min_u, min_l, min_total)
        verdict = classify_validity(gates, su, sl, min_u, min_l, deficit)
        if verdict == "rejected" and not ctx.collect_rejections:
            return None

        total = su.count + sl.count
        res_all = [t.residual for t in touches]
        mean_res = sum(res_all) / len(res_all)
        w = cfg.quality_weights_parallel
        if flat:
            trend_raw = 1.0 - sdiv(abs(move), cfg.channel_move_frac * wmi)
            trend_formula = "1 - |M| / (0.60 x W_mean)"
        else:
            trend_raw = sdiv(c_r2 - cfg.close_trend_min_r2, cfg.quality_trend_r2_full - cfg.close_trend_min_r2)
            trend_formula = "(R^2 - 0.50) / (0.90 - 0.50)"
        comps = [
            QualityComponent("parallelism", w["parallelism"], 1 - sdiv(par_err, cfg.parallel_max_error), clamp01(1 - sdiv(par_err, cfg.parallel_max_error)), "1 - parallelism_error / 0.25"),
            QualityComponent("touches", w["touches"], sdiv(total, cfg.quality_touch_target), clamp01(sdiv(total, cfg.quality_touch_target)), "min(total_touches / 8, 1)"),
            QualityComponent(
                "containment",
                w["containment"],
                sdiv(cont - cfg.parallel_min_containment, 1 - cfg.parallel_min_containment),
                clamp01(sdiv(cont - cfg.parallel_min_containment, 1 - cfg.parallel_min_containment)),
                "(containment - 0.88) / 0.12",
            ),
            QualityComponent(
                "boundary_fit",
                w["boundary_fit"],
                1 - sdiv(mean_res, cfg.touch_tol_width_frac * wmi),
                clamp01(1 - sdiv(mean_res, cfg.touch_tol_width_frac * wmi)),
                "1 - mean_touch_residual / (0.15 x W_mean)",
            ),
            QualityComponent("trend_fit" if not flat else "flatness", w["trend_or_flatness"], trend_raw, clamp01(trend_raw), trend_formula),
        ]
        metrics = {
            "formation_length": length,
            "width_independent_start": w0i,
            "width_independent_end": w1i,
            "width_mean": wmi,
            "width_drawn": wp,
            "width_pct": 100.0 * wp / ctx.close[e],
            "mean_width_pct": mean_width_pct,
            "parallelism_error": par_err,
            "upper_slope_independent": ind.upper.slope,
            "lower_slope_independent": ind.lower.slope,
            "shared_slope": par.upper.slope,
            "movement": move,
            "movement_vs_mean_width": ratio,
            "move_pct": move_pct,
            "containment": cont,
            "close_slope": c_slope,
            "close_r2": c_r2,
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
            upper=par.upper,
            lower=par.lower,
            touches=touches,
            swing_highs=[int(i) for i in hi],
            swing_lows=[int(i) for i in lo],
            fit_highs=[int(i) for i in ind.fit_highs],
            fit_lows=[int(i) for i in ind.fit_lows],
            metrics=metrics,
            gates=gates,
            components=comps,
            quality=quality_score(comps),
            developing=verdict == "developing",
            rejected=verdict == "rejected",
            first_touch=first_touch,
        )
