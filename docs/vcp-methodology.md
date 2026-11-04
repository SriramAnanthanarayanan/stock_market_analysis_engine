# Volatility Contraction Pattern (VCP)

Code: `backend/app/vcp/detector.py`; run by every **daily** scan (`ScanManager._store_vcp`), stored in `vcp_formations` / `vcp_contractions`; API `GET /api/scan/vcp`, `GET /api/vcp/symbol`; page **VCP Scanner**; chart overlay **VCP (1D)** in the chart terminal and the chart preview. Parameters: `VCPConfig` (`GET /api/config/vcp`). Synthetic fixtures: `backend/app/synthetic_vcp.py`.

The VCP is a dedicated detector, not a triangle, wedge, flag or rectangle, and it never changes the 17 price-action detectors or their configuration.

## Source principles vs engineering thresholds

| Source-derived principle (Minervini, *Think & Trade Like a Champion*; ChartMill VCP notes) | How it is implemented | Engineering threshold (configurable, not a Minervini rule) |
|---|---|---|
| The base forms within a broader **uptrend** | preceding advance = base's first high / lowest low of the prior 126 sessions - 1 | `prior_advance_min_pct` 25 % (ASSUMPTION), `prior_trend_lookback` 126 |
| A **consolidation / base** | starts at a confirmed swing high that is the highest high of the previous 50 sessions; 15-65 sessions long | `base_start_high_lookback` 50, `min_base_sessions` 15, `max_base_sessions` 65 |
| **Successive pullbacks get smaller** (T1 > T2 > T3 ...) | every contraction depth <= 0.75 x the previous one | `contraction_ratio_max` 0.75 (initial tolerance) |
| Two to four contractions typical, up to six | 2-6 meaningful contractions | `min_contractions` 2, `max_contractions` 6 (SOURCE) |
| **Volume dries up** as the base tightens | mean volume of the final contraction <= 0.85 x T1's; dry-up days recorded; final volume vs the prior 50-day average scored | `volume_final_vs_first_max` 0.85, `volume_dry_up_ratio` 0.6 |
| **Tight final contraction** | final depth <= 10 % | `final_max_depth_pct` 10 |
| **Pivot** = the high of the final contraction | the starting swing high of the final contraction (not automatically the base high) | - |
| **Breakout** above the pivot on **expanding volume** | completed close > pivot + 0 x ATR, volume >= 1.5 x the previous 50-day average | `breakout_buffer_atr` 0, `breakout_volume_ratio` 1.5 (spec initial), `breakout_confirm_closes` 1 |

## Contractions (confirmed swing points, left to right)

* A **contraction** runs from a contraction high to the lowest low before the next contraction high: `depth % = (high - low) / high x 100`. Its volume is measured on the candles after the high through the low.
* Contraction highs are **confirmed swing highs** (k = 3 completed candles each side); a swing high is used only from the candle that confirms it, so the walk never looks ahead.
* A swing high starts the next contraction only if the pullback before it is meaningful (>= `min_contraction_depth_pct` 2 %) **and** it recovered >= `min_recovery_frac` (50 %) of that pullback. A smaller bounce belongs to the same contraction (a mid-decline bounce is not a new contraction); after a negligible dip a higher swing high restarts the contraction from the higher high.
* The final contraction's low may be provisional (not yet a confirmed swing low); it is flagged.
* Per contraction: start / end time and price, depth (price and %), bars, mean and minimum volume, volume / prior 50-day average.

## Statuses

| Status | Meaning |
|---|---|
| Developing VCP | one meaningful contraction so far (hidden by default) |
| Forming VCP | >= 2 contractions, each smaller, but the final contraction is not yet tight or volume has not dried up (the reason is shown) |
| Tightening / Final Contraction | all gates pass (qualified) |
| Near Pivot | qualified and the close is within 3 % below the pivot (`near_pivot_pct`) |
| Breakout | a completed close above the pivot while qualified, on volume >= 1.5x the 50-day average (two-close mode: the next close must hold) |
| Breakout (awaiting 2nd close) | two-close mode, first close only |
| Pivot crossed - volume not confirmed | close above the pivot without the volume expansion - **not** a breakout |
| Failed Breakout | after a breakout, 2 consecutive closes back below the pivot (`failed_breakout_closes`) |
| Invalidated | a contraction deeper than allowed (volatility expanding), a close below the base low, the base high exceeded before the VCP qualified, or after a breakout a close below the final contraction low |

A breakout is reported for 15 sessions (`post_breakout_track_sessions`); an older one is history. A base older than 65 sessions without a breakout is no longer searched. Structures that never contracted (e.g. T2 deeper than 0.75 x T1 from the start) are not reported at all. One VCP per stock: the most advanced status, then the highest quality.

## Quality score (explainable, not a probability)

`Score = 100 x weighted mean of five 0-1 components` (weights from spec 6.9, placeholders):

| Component | Weight | Formula |
|---|---:|---|
| Contraction progression | 30 | 0.7 x clamp((0.75 - mean depth ratio) / (0.75 - 0.50)) + 0.3 x count factor (2 -> 0.8, 3-4 -> 1.0, 5 -> 0.9, 6 -> 0.8) |
| Volume contraction | 25 | mean of: final / T1 volume (1 at <= 0.50, 0 at 0.85); final volume / prior 50-day average (1 at <= 0.6, 0 at >= 1.0); share of contractions with less volume than the previous |
| Final contraction tightness | 20 | 1 at <= 3 %, 0 at 10 % |
| Base structure and trend context | 15 | mean of: preceding advance (25 % -> 0, 60 % -> 1); close > SMA 50; SMA 50 > SMA 200; share of rising contraction lows |
| Pivot quality | 10 | pivot within 3 % of the base's first high -> 1, 15 % or more below -> 0 |

The full breakdown and every gate (PASS / FAIL, value, rule, SOURCE / ENGINEERING) are shown in the VCP panel.

## Stage Analysis context (separate filter)

The VCP's structural status and score use daily candles only; weekly candles are never mixed into the calculation. The scanner shows the stock's **Stage Analysis result from the latest weekly scan** (the user's own `stage_analysis.txt` rules, unchanged) next to each VCP and can filter by it: all stages, Stage 2 only (Early + Confirmed), Stage 1 or 2, exclude Other, or exclude chosen stages. The filter hides rows; it never changes a VCP. The custom Stage 2 is **not** assumed to equal Minervini's full Trend Template.

## Chart

Base box, T1 / T2 / ... contraction areas with depth and volume labels, the decline leg of each contraction, the pivot line with the distance from the latest close, the breakout marker (volume ratio) and a failure / invalidation marker - all from the stored geometry (`schema vcp-1.0`, every point a candle timestamp), never re-detected in the browser.

## Tests

`tests/test_vcp.py` (26): two-, three- and four-contraction VCPs, contraction measurements, non-contracting base, expanding volatility, descending depths with rising volume, insufficient volume contraction, wide and tight final contractions, near pivot, pivot = final contraction high, breakout with / without volume expansion, two-close confirmation, failed breakout, invalidation after / before a breakout, developing, no prior uptrend, score components, geometry and explanation, as-of stability and stable IDs, random walks rarely qualify (0 of 40 here), insufficient history. API: `tests/test_api_vcp.py`. Self-test: 6 VCP checks.

**Not validated on real charts**: there are no labelled VCP examples yet. Treat the thresholds as starting points and label charts (Labels & evaluation) before relying on them.
