# Pattern tuning — DetectionConfig 1.3.0 (chart-quality review, 2026-11-04)

DetectionConfig `1.2.0` (`e224c19b7f658e1b`) → `1.3.0` (`9c33f4bf86016487`). Stage Analysis (`StageConfig 1.3.0`) was **not touched**.

Why: live head-and-shoulders detections were often false positives (multi-peak structures, a "right shoulder" that was a fourth peak, shoulders made of one-bar spikes). The same weaknesses showed up in double tops/bottoms, flags and wedges.

How this was done:

1. Read published H&S and double-top definitions (sources below).
2. Took screenshots of the live detections in the app and listed how each one failed.
3. Added rules from the sources, plus a few disclosed assumptions.
4. Rescanned: live, plus historical as-of scans on 2025-10-29, 2026-02-17, 2026-05-06 and 2026-08-21.
5. Took new screenshots, judged them by eye and repeated steps 3–4.

**This is visual review, not a labelled evaluation.** No precision/recall figures exist yet. Use `docs/validation.md` to measure them.

## Sources

| Source | What it gave |
|---|---|
| Lo, Mamaysky & Wang (2000), *Foundations of Technical Analysis*, J. Finance 55(4), Definition 1 | An H&S is **five consecutive local extrema**: E1 max, E3 > E1 and E3 > E5. The shoulders are within 1.5 % of their average, and so are the troughs. |
| Savin, Weller & Zvingelis (2007), *The Predictive Power of "Head-and-Shoulders" Price Patterns in the U.S. Stock Market*, J. Fin. Econometrics 5(2) — restrictions calibrated on Bulkowski's examples | R4a: shoulders within 4 % of their average. R5a: troughs within 4 % of their average. R6/R7: average shoulder height 0.25–0.70 × head height. R8: head height ≥ 3 % of price. R9: no leg longer than 2.2 × the average leg. Completion needs a neckline cross. |
| Bulkowski, thepatternsite.com (H&S top; Eve & Eve double top) | An uptrend leads into the pattern. Shoulders sit near the same price and are similar distances from the head. The neckline joins the armpits. Wait for a close below the neckline. Double tops: peaks within ~3 %, a valley ≥ 10 % deep. Flags: a steep, quick pole and a flag sloping against the trend. Triangles and wedges: converging lines. |
| StockCharts ChartSchool (H&S top) | The left shoulder marks the high of the prior advance. The head is a new high. The right shoulder is a lower high near the left shoulder's height. The pattern is not complete until the neckline breaks. |

## What failed (1.2.0, screenshots)

| Pattern | Failure mode seen on charts | Example |
|---|---|---|
| H&S / IHS | Shoulders and head were picked from any earlier pivots. A higher peak between the head and the right shoulder (a second head) still passed. | CONCORDBIO, TRENT, JMFINANCIL |
| H&S / IHS | Shoulders, head or armpits were lone one-bar wicks inside a sideways range. | HEGAM, AFCONS, ANANTRAJ, NCC |
| H&S | Lopsided shoulders (RS 5 % above LS) and steep necklines (tilt up to 0.5 × head height). | BHARTIARTL, AEGISVOPAK, CESC |
| Double top/bottom | Peaks or valley made of single spikes; several swings between the peaks (really a range); P1 below an earlier, higher high. | EIHOTEL, PIRAMALFIN, LLOYDSME, AIIL, BALRAMCHIN |
| Flags | Slow 15-bar drifts accepted as poles; "flags" sloping *with* the pole. | NLCINDIA, GRAPHITE, RITES |
| Wedges | Formations narrowing only 40–46 % (CR 0.54–0.57) look parallel. The same structure was also reported as a falling channel. | ASIANPAINT, CMPDI, IOC |

Before: [CONCORDBIO](img/tuning-1.3.0/before-hs-concordbio.png) (a peak at 790 between H and RS) · [TRENT](img/tuning-1.3.0/before-hs-trent.png) · [EIHOTEL double top](img/tuning-1.3.0/before-dt-eihotel.png) (two wicks) · [LLOYDSME double bottom](img/tuning-1.3.0/before-db-lloydsme.png) (a range)

After: [BELRISE H&S](img/tuning-1.3.0/after-hs-belrise.png) · [SHRIRAMFIN inverse H&S](img/tuning-1.3.0/after-ihs-shriramfin.png) · [JSWINFRA double top](img/tuning-1.3.0/after-dt-jswinfra.png) · [BANDHANBNK bear flag](img/tuning-1.3.0/after-flag-bandhanbnk.png) · [GODREJPROP falling wedge](img/tuning-1.3.0/after-wedge-godrejprop.png)

## Rule changes

| Parameter / rule | 1.2.0 | 1.3.0 | Basis |
|---|---|---|---|
| H&S consecutive extrema (`reversal_counter_swing_frac`) | — | No close-to-close counter-swing ≥ 0.33 HH inside LS→T1→H→T2→RS. LS and RS are the highs of their legs. | Lo-Mamaysky-Wang "five consecutive extrema". The 0.33 is an assumption; they smooth prices with a kernel instead. |
| H&S single head (`hs_head_region_swing_frac`) | — | No dip ≥ 0.2 HH while price is above the higher shoulder. | Assumption: a second peak above the shoulders makes a double or complex head. |
| H&S head is the trend extreme | — | Head ≥ every high from (span bars before LS) to RS. | StockCharts, Bulkowski |
| `hs_shoulder_diff_height_frac` / `_price_frac` | 0.30 HH / 5 % | **0.20 HH / 4 %** | Between Lo (3 %) and SWZ R4a (8 %); visual review |
| `hs_neckline_tilt_frac` (+ new `hs_neckline_tilt_price_frac`) | 0.50 HH | **0.25 HH and 8 % of price** | SWZ R5a; Bulkowski: down-sloping necklines underperform |
| `hs_shoulder_max_avg_height_frac` | — | Average shoulder height ≤ 0.70 HH | SWZ R6 |
| `hs_leg_balance_max` | — | Longest leg ≤ 2.2 × average leg | SWZ R9 |
| Close confirmation (`reversal_peak_wick_max_frac`, `reversal_neckline_wick_max_frac`) | — | Best close within 2 bars of each peak within 0.25 × height; of each neckline point within 0.15 × height | Assumption: no lone-wick pattern points and no neckline through a spike. Applied to H&S and doubles. |
| Doubles: consecutive extrema, trend extreme | — | No counter-swing ≥ 0.33 D inside P1→T→P2. P1/P2 are the highs of the prior-trend window. | Lo-Mamaysky-Wang; Bulkowski ("price rises into the pattern") |
| `double_min_depth_move_frac` | 0.5 (3 % daily) | **1.0 (6 % daily, 10 % weekly)** | Bulkowski: valley ≥ 10 % |
| `pole_min_atr_per_bar` | — | Pole height / ATR / bars ≥ 0.5 | Bulkowski: steep, quick pole. The number is an assumption. |
| `flag_move_with_max_frac` | 0.10 H | **0.03 H** | Bulkowski: the flag is flat or slopes against the pole |
| `converging_cr_max` | 0.60 | **0.50** | Visual review: at CR 0.5–0.6 the lines look parallel |

New metrics appear on every H&S detection: `counter_swing_hh`, `head_region_swing_hh`, `max_wick_hh`, `armpit_wick_hh`, `leg_balance`, `avg_shoulder_height_hh`, `rs_ls_volume_ratio`. The last one is informational only: Bulkowski says volume usually recedes into the right shoulder. Doubles report `counter_swing_d`, `peak_wick_d` and `valley_wick_d`.

## Effect (NIFTY 500 + indices; counts include Developing)

| Daily, live (2026-11-04) | 1.2.0 | 1.3.0 |
|---|---|---|
| Head & shoulders / inverse | 34 / 10 | **3 / 0** |
| Double top / bottom | 29 / 25 | **6 / 3** |
| Bearish / bullish flag | 78 / 5 | **27 / 2** |
| Falling / rising wedge | 90 / 3 | **41 / 2** |
| Triangles (asc / desc / sym) | 5 / 16 / 11 | 4 / 11 / 8 |
| Channels and ranges | unchanged rules | unchanged counts |

| Weekly, live | 1.2.0 | 1.3.0 |
|---|---|---|
| H&S / inverse | 15 / 14 | 1 / 3 |
| Double top / bottom | 24 / 25 | 8 / 3 |
| Falling / rising wedge | 33 / 15 | 23 / 6 |

Over twelve daily as-of cut-offs, 25 bars apart (about one year), doubles dropped from 735 to 170 unique detections. The final config gives 22 H&S tops and 10 inverse H&S across the same cut-offs, roughly 2–3 per month across 500 stocks.

**Visual verdict (1.3.0 screenshots).** Most of the remaining H&S are recognisable textbook shapes (BELRISE, TMCV, CHOLAHLDNG, INDUSINDBK, AIAENG, SHRIRAMFIN, TCS). A few are still borderline:

* KOTAKBANK: the head has a small double hump.
* LGEINDIA and CARBORUNIV: the head is a spike inside a range.

These borderline cases tend to score lower on quality. Wedges now converge visibly. Flags have steep poles. Doubles are clean M/W shapes but often small.

## Other fixes found during the review

* **Chart viewport (UI bug).** For wedges and triangles whose apex projects far ahead, the chart opened on the wrong date range. EMAs, sub-panes and overlay lines are added after the candles. Each addition made Lightweight Charts move a visible range that extended into the future whitespace. `PatternChart` now re-applies the formation viewport after those updates until the user pans or zooms. `window.__pasChart.visibleFrom/visibleTo` expose the range for UI checks.
* **Tracks across config changes.** Frozen (broken-out) tracks from 1.2.0 kept appearing in 1.3.0 scans for up to 20 bars, showing patterns the new rules reject. Open tracks whose config hash differs from the scan's are now closed as `config_changed`, and the formation is re-detected under the new rules. Test: `tests/test_tracking.py::test_tracks_of_another_detection_config_are_closed_not_continued`.

## Tests

`tests/test_pattern_quality_130.py` checks the helper functions and gives each new rule a synthetic near-miss that fails for its own reason:

* second head
* head below an earlier high
* lopsided shoulders
* double top with an extra swing
* double top below an earlier high
* slow pole
* flag drifting with the pole

The existing positives, negatives, near-misses and invalidations all still pass.

## Known limits / next step

* Thresholds marked "assumption" (0.33, 0.2, 0.25, 0.15, 0.5 ATR/bar) were set by eye on about 120 screenshots. They should be validated with labelled charts using the Compare/Sweep tools in `docs/validation.md`. Use the tuning period ≤ 2022 and the validation period ≥ 2023.
* Channels, rectangles and the horizontal channel were reviewed and left unchanged; their rules come from the original spec. Two weaknesses remain:
  * The horizontal-channel definition (|M| < 0.6 W) admits visibly sloped lines.
  * A boundary can rest on two isolated wicks (MAHABANK).

  Both would need a spec decision.
