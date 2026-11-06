# Volatility Contraction Pattern (VCP) - detector v2

Code: `backend/app/vcp/detector.py`; run by every **daily** scan (`ScanManager._store_vcp`), stored in `vcp_formations` / `vcp_contractions`; API `GET /api/scan/vcp`, `GET /api/vcp/symbol`; page **VCP Scanner**; chart overlay **VCP (1D)** in the chart terminal and the chart preview. Parameters: `VCPConfig` **2.0.0**, hash `2159765b755d4287` (`GET /api/config/vcp`). Synthetic fixtures: `backend/app/synthetic_vcp.py`.

The VCP is a dedicated **structure** detector. It is not a triangle, wedge, flag, rectangle or "low-volatility box", and it never changes the 17 price-action detectors or their configuration. Version 2 replaced the 1.0 detector (which reported a base box with contraction areas) with a time-ordered walk that must find **successive, shrinking contractions T1 > T2 > ... > final contraction -> pivot**.

## Methodology vs engineering thresholds

The methodology is Mark Minervini's (*Trade Like a Stock Market Wizard*, 2013, chapter "Mastering the Volatility Contraction Pattern"; *Think & Trade Like a Champion*, 2017). The books were not available in this session; the principles below were cross-checked against secondary write-ups (listed under Sources), which agree on the principles but **do not agree on numbers**. Every number in the detector is therefore a configurable ENGINEERING default (initial values from the task), flagged in `VCP_PARAM_META`, and **not** a Minervini rule.

| Methodology principle | Implementation | Engineering default (`VCPConfig`) |
|---|---|---|
| The base forms inside an established **uptrend** | preceding advance = base's first high / lowest low of the prior 126 sessions - 1; at least 50 completed sessions before the base | `prior_advance_min_pct` 25 % (ASSUMPTION), `prior_trend_lookback` 126, `min_prior_bars` 50 |
| A base / consolidation that starts at a high | the first contraction high is a confirmed swing high and the highest high of the previous 50 sessions | `base_start_high_lookback` 50 |
| **Successive contractions**, each pullback smaller than the previous (often about half) | every contraction depth <= 0.75 x the previous depth | `max_next_contraction_ratio` 0.75 |
| Two to four contractions typical, occasionally five or six | 2-6 contractions | `min_contractions` 2, `max_contractions` 6 (SOURCE) |
| A contraction is a meaningful pullback, not noise | depth >= 3 % of its high | `min_contraction_pct` 3.0 |
| Base length (weeks to months) | 15-120 completed sessions from the first contraction high | `min_base_bars` 15, `max_base_bars` 120 |
| **Tight final contraction** | final depth <= 10 % | `max_final_contraction_pct` 10.0 |
| **Volume dries up** as the base tightens | mean volume of the final contraction <= 0.70 x the 50-session average before it, and <= 0.85 x T1's mean volume | `final_volume_ratio` 0.70, `volume_final_vs_first_max` 0.85 |
| **Pivot** = high of the final contraction | the swing high that starts the final contraction (not automatically the base high) | - |
| **Breakout** above the pivot on **expanding volume** | completed close > pivot (+ 0 x ATR) on volume >= 1.5 x the previous 50-session average | `breakout_volume_ratio` 1.5, `breakout_buffer_atr` 0, `breakout_confirm_closes` 1 |
| Near the pivot | close within 3 % below the pivot | `near_pivot_pct` 3.0 |

## Algorithm (completed daily candles, no look-ahead)

For every candidate base start `s0` (a confirmed swing high, k = 3, that is the highest high of the previous 50 sessions, after a preceding advance >= 25 %) the detector walks the candles from left to right:

1. A swing high is used only from the candle that **confirms** it (k = 3 later candles), so nothing is read from the future.
2. A **contraction** runs from a contraction high to the lowest low before the next contraction high: `depth % = (high - low) / high x 100`.
3. A confirmed swing high starts the **next** contraction only when the pullback before it is meaningful (>= 3 %) and the swing high recovered at least half of that pullback (`min_recovery_frac` 0.5). A smaller bounce belongs to the same contraction (a bounce inside a decline is not a new contraction); after a negligible dip a higher high restarts the contraction from the higher high.
4. The running (final) contraction's low is **provisional** until it is a confirmed swing low; it is flagged.
5. Invalidation while the base is forming: the running contraction becomes deeper than 0.75 x the previous one (volatility expanding), a close below the base low, or the base high exceeded by more than 3 % (`base_high_tolerance_pct`) before the structure qualified.
6. A close above the pivot is evaluated with the gates of the base up to the candle **before** it (the breakout candle never qualifies its own base).

One VCP per stock: the most advanced status, then the highest quality, then the latest base. A base older than 120 sessions without a breakout is not searched; a breakout is reported for 15 sessions (`post_breakout_track_sessions`), then it is history. A structure that never contracted (e.g. T2 deeper than 0.75 x T1 from the start) is not reported at all.

### Gates (all must pass for a qualified VCP)

| Gate | Rule | Basis |
|---|---|---|
| contraction_count | 2-6 contractions, each >= 3 % | SOURCE count, ENGINEERING minimum depth |
| progressive_contraction | each depth <= 0.75 x the previous | ENGINEERING |
| tight_final_contraction | final depth <= 10 % | ENGINEERING |
| volume_dry_up | final contraction mean volume <= 0.70 x the 50-session average before it | ENGINEERING |
| volume_contracting | final contraction mean volume <= 0.85 x T1's | ENGINEERING |
| base_length | 15-120 sessions from the first contraction high | ENGINEERING |

Every gate is shown in the VCP panel as PASS / FAIL / N/A with its value, rule and basis.

## Per-contraction measurements

Stored in `vcp_contractions` and returned as `contraction_list`: label (T1, T2, ...), final flag, start (high) time / index / price, end (low) time / index / price, high, low, depth (price and %), duration in sessions, recovery sessions to the next contraction high, mean / median / minimum volume (candles after the high through the low), volume vs the 50-session average before the contraction, volume vs the previous contraction, ATR-normalised range (`depth / ATR(14)` at the contraction high), provisional low.

## States

| State | Meaning |
|---|---|
| Developing | one meaningful contraction so far (hidden by default) |
| Forming | >= 2 contractions, each smaller than the previous, but a gate is still open (final not tight yet, volume not dried up, base too short); the open gates are listed |
| Final Contraction | all gates pass (qualified); close more than 3 % below the pivot |
| Near Pivot | qualified and the close is within 3 % below the pivot. Also used, **flagged** (`pivot_crossed_low_volume`), when a qualified VCP closed above the pivot **without** breakout volume - that is not a breakout |
| Breakout | completed close above the pivot while qualified, on volume >= 1.5 x the 50-session average (two-close mode: pending until the next close holds) |
| Failed Breakout | after a breakout, 2 consecutive closes back below the pivot (`failed_breakout_closes`), or the second close fails in two-close mode |
| Invalidated | volatility expanding (a contraction deeper than allowed), contractions not getting smaller, a close below the base low, the base high exceeded before the VCP qualified, or after a breakout a close below the final contraction low |

## Quality score (explainable, not a probability)

`Quality = 100 x weighted mean of six 0-1 components`; weights from the task, placeholders until labelled charts exist.

| Component | Weight | Formula |
|---|---:|---|
| Contraction progression | 30 | 0.6 x shrink (mean depth ratio 0.50 -> 1, 0.75 -> 0) + 0.25 x count factor (2 -> 0.8, 3-4 -> 1.0, 5 -> 0.9, 6 -> 0.8) + 0.15 x share of shrinking steps |
| Final contraction | 20 | mean of: depth (3 % -> 1, 10 % -> 0); range / ATR (1.5 -> 1, 4.0 -> 0) |
| Volume behaviour | 20 | mean of: final / 50-day average and final / T1 volume (0.5 -> 1, 1.0 -> 0); share of contractions quieter than the previous; dry-up days (3+ -> 1) |
| Base structure | 10 | mean of: T1 depth inside 10-35 % (ASSUMPTION); base length inside 20-80 sessions; share of rising contraction lows |
| Trend context | 10 | mean of: preceding advance (25 % -> 0, 60 % -> 1); close > SMA 50; SMA 50 > SMA 150 > SMA 200; SMA 200 rising over 21 sessions (Trend Template style, ENGINEERING) |
| Pivot quality | 10 | pivot within 3 % of the base's first high -> 1, 15 % or more below -> 0 |

The full breakdown (raw value, 0-1 score, weight, formula) is shown in the VCP panel and the chart preview. The score measures how closely the structure matches the rules; it is **not** a probability of a successful breakout.

## Stage Analysis context (separate filter)

The VCP's status and score use daily candles only; weekly candles are never mixed in. The scanner shows the stock's **Stage Analysis result from the latest weekly scan** (the user's own `stage_analysis.txt` rules, unchanged) next to each VCP and can filter by it (all stages, Stage 2 only, Stage 1 or 2, exclude Other, exclude chosen stages). The filter hides rows; it never changes a VCP. The custom Stage 2 is **not** assumed to equal Minervini's full Trend Template.

## Chart (geometry schema `vcp-2.0`)

Drawn from the stored geometry (every point a candle timestamp and index), never re-detected in the browser (`frontend/src/charts/vcpOverlay.ts`):

* the **contraction structure**: a zig-zag of each contraction's decline leg (high -> low) and the dashed recovery leg to the next contraction high;
* **swing points** (contraction highs and lows) and a **T1 / T2 / T3 ... label with the depth %** at every contraction;
* the **final contraction** highlighted;
* the **pivot line** from the final contraction high to the latest candle (or the breakout);
* **volume dry-up**: the final contraction's volume bars tinted, with the ratio to the 50-day average;
* the **breakout candle** marker with its volume ratio and the breakout volume bar highlighted; failure / invalidation marker.

The overlay is a structure, not a rectangle: `tests/test_vcp.py::test_geometry_is_a_contraction_structure_not_a_box` and the UI check in `scripts/verify-improvements.mjs` (overlay kinds include zig-zag legs and swing points) guard this.

## Tests

`tests/test_vcp.py` (25 collected): 2-6 successive contractions (parametrised), per-contraction measurements, an equal-depth box and a rectangle are never VCPs, invalid contraction sequence, volume dry-up required, Final Contraction state and wide final contraction, pivot = final contraction high (not the base high), breakout needs volume, two-close confirmation, failed breakout, invalidations, no future leakage, score components and weights, geometry is a structure not a box, developing / no uptrend / insufficient history, old breakout no longer reported, random walks rarely qualify, state labels, spec default thresholds. API: `tests/test_api_vcp.py` (5). Self-test: VCP checks including "four successive contractions".

## Sources

Secondary sources used to cross-check the principles (search results; no primary IBD / Minervini text was available here, and the write-ups disagree on numbers):

* [LuxAlgo - Volatility Contraction Pattern](https://www.luxalgo.com/library/concept/volatility-contraction-pattern/)
* [TraderLion - Volatility Contraction Pattern](https://traderlion.com/technical-analysis/volatility-contraction-pattern/)
* [TrendSpider - Volatility Contraction Pattern (VCP)](https://trendspider.com/learning-center/volatility-contraction-pattern-vcp/)
* [Trade Ideas - VCP explained](https://www.trade-ideas.com/learning-center/chart-patterns/volatility-contraction-pattern-vcp-explained/)
* [ChartMill - Volatility Contraction Pattern](https://www.chartmill.com/documentation/technical-analysis/chart-patterns/531-Volatility-Contraction-Pattern-VCP-How-to-Spot-and-Trade-It)
* [The Right Trader - Volatility Contraction Pattern](https://therighttrader.com/learn/trading-strategies/volatility-contraction-pattern)
* [Finer Market Points - the VCP chapter of *Trade Like a Stock Market Wizard*](https://www.finermarketpoints.com/post/trade-like-stock-market-wizard-vcp-chapter)
* [Sakatas - What really is a VCP pattern](https://sakatas.substack.com/p/what-really-is-a-vcp-pattern)

**Not validated on real charts**: there are no labelled VCP examples yet, and every UI check ran on synthetic data. Treat the thresholds and the score as starting points; label charts (Labels & evaluation) before relying on them. No accuracy is claimed.
