# Pattern rules (P1 + P2, configuration v1.3.0)

> **v1.3.0 (chart-quality review)** changed the rules marked **[1.3.0]** below. Sources and the visual review are in `docs/pattern-tuning-1.3.0.md`.

Every number below is an **initial engineering default** stored in `DetectionConfig` (`backend/app/config.py`). None is a validated technical-analysis constant. Where P1 had to interpret or extend the specification, the item is marked **[P1 decision]** and explained in `implementation-notes.md`.

## 1. Shared definitions

| Item | Rule |
|---|---|
| Candles | Completed candles only, truncated at the scan cutoff (live: now; as-of: end of that IST day). |
| ATR | ATR(14), Wilder smoothing; TR[0] = high − low. Point-in-time: every tolerance/buffer uses the ATR of the bar it is applied to. |
| Swing high (k) | `high[i] > max(high[i-k:i])` and `high[i] >= max(high[i+1:i+k+1])`; swing low mirrors it. Reported only after k later completed bars exist. k = 3 for this family. |
| Fit window | Ends at `e = newest completed bar − 3`; length `L` from the candidate list; `x = 0` at the window start. The 3 newest bars never influence the geometry; they are used for status. |
| Relevant swing points **[P1 decision]** | All swing points of that side in the window; one refinement pass drops points farther than `2 × touch tolerance` from the initial anchored line from the *slope* estimate (only if ≥ 2 remain). |
| Anchored line | OLS slope through the relevant points; intercept shifted so the line passes through the extreme swing point (upper: `max(high − b·x)`, lower: `min(low − b·x)`), i.e. every swing point is on or inside the line. |
| Parallel (drawing) fit | Pooled OLS: one shared slope, two intercepts, each anchored as above. |
| Width / movement | `W(x) = upper(x) − lower(x)`, `W0 = W(0)`, `W1 = W(L−1)`, `W_mean = (W0 + W1)/2` (exact for straight lines), `CR = W1/W0`, `M = slope × (L − 1)`. |
| Touch | `|line(x) − swing price| ≤ max(0.15 × W(x), 0.25 × ATR)` — an **absolute** distance; a candle far beyond the boundary is not a touch. |
| Touch spread | First-to-last touch on each boundary ≥ 0.4 × L bars (applied to both families **[P1 decision]**). |
| Lead before first touch **[P1 decision]** | The window may start at most `0.33 × L` bars before the first touch, so W0, CR and M are never measured on extrapolated lines price never touched. Candidate lengths are ≤ 1.5× apart, so every formation length stays coverable. |
| Containment | Share of bars from the first touch to `e` with `high ≤ upper + s` and `low ≥ lower − s`, `s = max(0.05 × W(x), 0.1 × ATR)`. |
| Close trend | OLS of closes from the first touch to `e`: slope and R². |
| Break buffer | A close is beyond a boundary only by ≥ 0.1 × ATR. |

Timeframe defaults:

| TF | Minimum move (% of start price) | Width band (% of price) |
|---|---:|---:|
| 1H | 2.5 | 1-15 |
| 4H | 4 | 1.5-22 |
| 1D | 6 | 3-30 |
| 1W | 10 | 5-45 |

## 2. Parallel family — channels and ranges

Candidate lengths 40, 60, 90, 120. Independent fits measure parallelism; the drawn geometry and touches use the shared-slope fit.

Shared gates: ≥ 2 upper, ≥ 2 lower, ≥ 5 total touches (≥ 6 for Horizontal Channel and Rectangle); each boundary has a touch with `x < L/2` and one with `x ≥ L/2`; touch spread ≥ 0.4 L per boundary; `parallelism_error = |b_upper − b_lower| × (L−1) / W_mean ≤ 0.25` (independent fits); containment ≥ 0.88; `W_mean / close[e]` inside the width band; lead before the first touch ≤ 0.33 L.

Classification by the shared-slope movement `M` relative to `W_mean`:

| Pattern | Movement | Additional gates |
|---|---|---|
| Falling Channel | `M ≤ −0.6 W_mean` | drop ≥ tf minimum move (as % of the drawn midline at x=0); close slope < 0; close R² ≥ 0.50 |
| Rising Channel | `M ≥ +0.6 W_mean` | rise ≥ tf minimum move; close slope > 0; close R² ≥ 0.50 |
| Horizontal Channel | `0.2 W_mean < |M| < 0.6 W_mean` | ≥ 6 touches |
| Rectangle | `|M| ≤ 0.2 W_mean` | ≥ 6 touches; close R² < 0.30 |

A structure whose movement says "channel" but whose move is below the minimum is **rejected**, never relabelled.

## 3. Converging family — triangles and wedges

Candidate lengths 30, 45, 60, 90, 120. Independent anchored fits for classification, touches and drawing.

Shared gates: ≥ 2 upper, ≥ 2 lower, ≥ 5 total touches; touch spread ≥ 0.4 L per boundary; `0.15 ≤ CR ≤ 0.50` **[1.3.0: was 0.60 - at CR 0.5-0.6 the lines look parallel; those formations are channels]** (CR ≥ 1 — parallel or expanding — is never a candidate); containment ≥ 0.85; `W0 / close[start]` inside the width band; lead before the first touch ≤ 0.33 L. The apex `x = (a_u − a_l)/(b_l − b_u)` is recorded; a candidate whose apex is at or before `e` is rejected.

Line state (independent `M` per line): Rising if `M > +0.15 W0`, Falling if `M < −0.15 W0`, else Flat.

| Upper | Lower | Pattern | Extra gates |
|---|---|---|---|
| Flat | Rising | Ascending Triangle | — |
| Falling | Flat | Descending Triangle | — |
| Falling | Rising | Symmetrical Triangle | — |
| Falling | Falling | Falling Wedge | upper slope < lower slope; midline drop ≥ tf minimum move |
| Rising | Rising | Rising Wedge | upper slope < lower slope; midline rise ≥ tf minimum move |
| other | | rejected | |

Collapsed formations **[P1 decision]**: if a longer converging candidate with the same boundaries fails `CR ≥ 0.15`, shorter windows of that formation are suppressed (a collapsed triangle is not revived by viewing only its tail).

## 4. Continuation — flags and pennants (P2)

For each consolidation length C ending at the latest fitted bar `e`, `P_end = e − C`. All highs/lows here are **raw bars** (no swing points).

**Pole** (bullish; bearish mirrors it): `P_end` is the highest high of the 15 bars before it and is not exceeded inside the consolidation; pole start = lowest low of those 15 bars (latest if tied); pole length 3-15 bars; `H = high[P_end] − low[start]`; `H ≥ 3 × ATR` measured **at the pole start**; `H / low[start] ≥` timeframe minimum move; ≥ 60 % of pole bars close above the previous close; steep pole `H / ATR / pole bars ≥ 0.5` **[1.3.0: Bulkowski - the flagpole is a steep, quick move; a slow drift is not a pole]**.

**Consolidation** (touches on raw bars, counted ≥ 2 bars apart; containment from the first touch):

| Rule | Flag | Pennant |
|---|---|---|
| Length | 5-25 bars, ≤ 3 × pole length | 5-20 bars, ≤ 3 × pole length |
| Fit | parallel (pooled) | independent |
| Shape | `|1 − CR| ≤ 0.25` (independent fits) | `0.15 ≤ CR ≤ 0.60`, upper falling and lower rising |
| Movement | shared-slope move in `[−0.5 H, +0.03 H]` (bearish `[−0.03 H, +0.5 H]`) **[1.3.0: was ±0.1 H with the pole; Bulkowski - the flag is flat or slopes against the pole]** | — |
| Retracement | ≤ 50 % | ≤ 38 % |
| Width | `W0 ≤ 0.5 H` | `W0 ≤ 0.5 H` |
| Touches / containment | ≥ 2 upper, ≥ 2 lower; ≥ 85 % | same |

Raw-bar boundary fit **[P2 decision]**: OLS through every raw high follows the consolidation's midline, not its edge, so the slope is re-estimated on raw points within `2 × touch tolerance` of the current line (iterated until stable, ≤ 3 times); anchoring uses every bar.

Bearish pennant **[P2 decision]**: the spec text says "upper line rising and lower line falling" but also requires the same convergence `0.15 ≤ CR ≤ 0.60`, which only a converging shape can meet; both pennants therefore converge (upper falling, lower rising).

**Forming**: a valid pole followed by a 3-4 bar consolidation whose preliminary direction (shared-slope move inside the flag band), width (`range ≤ 0.5 H`) and retracement (≤ 50 %) pass is reported as a Flag with status **Forming**; it is never Validated.

Volume contraction (consolidation vs pole mean volume) is a quality component only, never a gate. Formation window = pole start .. `e`; the boundaries are drawn over the consolidation only.

## 5. Reversals — double tops/bottoms, head & shoulders (P2)

Swing lookback k = 5. The latest pivot must lie in the last 30 completed bars. Bottoms use the top rules on negated prices, so every rule is mirrored exactly.

**Double Top**: P2 = most recent confirmed swing high in the window; P1 = an earlier swing high 10-60 bars before; no bar between has a high above `min(P1, P2)` (applied literally **[P2 decision]**: approach bars of a clearly higher peak also fail it); T = lowest low between; `D = min(P1, P2) − T`; `D ≥ 3 ATR` (ATR at P2); `D / average peak ≥ 1.0 ×` tf minimum move (6 % daily, 10 % weekly) **[1.3.0: was 0.5; Bulkowski's ≥ 10 % valley]**; `|P1 − P2| ≤ min(0.25 D, 0.03 × average peak)`; prior trend `P1 − lowest low of the sep bars before P1 ≥ D`; neckline horizontal at T. Several P1 → highest quality, ties to the longer separation.
**[1.3.0]** also: P1/P2 are the highest high of the prior-trend window + pattern (`sep` bars before P1 .. P2) - a double top tops its advance; consecutive extrema - no close-to-close counter-swing ≥ `0.33 D` inside P1→T or T→P2 (one valley, not a range); close confirmation - the best close within 2 bars of P1 and of P2 is within `0.25 D` of the peak, and of T within `0.15 D` (no lone-wick peaks, no neckline through a spike).

**Head & Shoulders**: LS, H, RS swing highs in order, RS = the most recent; LS→RS span 20-120 bars; T1/T2 = lowest lows between LS-H and H-RS; neckline through T1, T2; `HH = H − neckline(H) ≥ 3 ATR` (ATR at the head); prominence `H − max(LS, RS) ≥ max(0.2 HH, 0.03 H)`; `|LS − RS| ≤ min(0.2 HH, 0.04 × average shoulder)` **[1.3.0: was 0.3 HH / 5 %]**; each shoulder ≥ `0.4 HH` above the neckline at its index; time symmetry `bars(LS→H)/bars(H→RS)` in `[0.5, 2.0]`; tilt `|T1 − T2| ≤ min(0.25 HH, 0.08 × average armpit)` **[1.3.0: was 0.5 HH; 8 % = Savin-Weller-Zvingelis R5a]**; prior trend `LS − lowest low of span bars before LS ≥ HH`; the head is the extreme of LS..RS **[P2 decision]**. Several triples → highest quality.
**[1.3.0]** also (all must hold):
* *Five consecutive extrema* (Lo, Mamaysky & Wang 2000): no close-to-close counter-swing ≥ `0.33 HH` inside LS→T1, T1→H, H→T2, T2→RS, and LS / RS are the highest highs of their legs - extra peaks or troughs between the five points (multi-peak structures, a second head, a range) are rejected.
* *Single head*: while price trades above the higher shoulder, no close-to-close dip ≥ `0.2 HH` (a second peak above the shoulders = double / complex head).
* *Trend extreme*: the head is the highest high of the prior-trend window (span bars before LS) + pattern - the pattern tops its advance.
* *Shoulders below the head* (SWZ R6): average shoulder height ≤ `0.7 HH`; *leg balance* (SWZ R9): each of the four legs ≤ `2.2 ×` the average leg.
* *Close confirmation*: the best close within 2 bars of LS, H, RS is within `0.25 HH`; of T1 and T2 within `0.15 HH`.

**Provisional path**: a newer possible pivot with 1-4 confirmation bars (left side `> previous k`, `≥` every later bar) runs the same rules but can only produce **Forming**.

## 6. Support and resistance zones (P2)

k = 3 swing pivots in the last 250 completed bars, each with its own ATR; sorted by (price, bar, type); a pivot joins the current cluster when within `0.5 × its ATR` of the cluster mean. Zone = cluster min..max padded by `0.1 × median ATR`; ≥ 2 touches ≥ 5 bars apart; zones overlapping or closer than `0.25 × median ATR` are merged. Walking forward from the 2nd touch: two closes below `low − 0.1 ATR` break support (two above `high + 0.1 ATR` break resistance) and flip the role; a retest is a later bar whose range intersects the zone and whose close is on the reclaimed side of the midpoint without closing beyond the opposite boundary by the buffer. The nearest 4 zones above and 4 below the latest close are stored and shown (touches, width, last touch, distance % and ATR, role, broken/retested).

## 7. Developing

A candidate is *Developing* when every structural gate passes and it is exactly **one touch short** of the touch-count minimums (the fewest additional touches needed to satisfy upper, lower and total minimums is 1). Applies to line-based patterns, flags and pennants. Developing detections are hidden unless requested.

## 8. Precedence and deduplication

* A window can never be both parallel and converging: CR ≤ 0.50 implies parallelism error ≥ 0.67.
* Groups: parallel, converging, continuation, reversal tops, reversal bottoms. Within a group candidates are ranked by quality (ties → longer window); a candidate is dropped if it overlaps a kept one with formation-window IoU ≥ 0.60, or is nested with the **same boundaries** (both lines within `0.15 × mean width` over the overlap), including across sub-types **[P1 decision]**. Different groups coexist (a flag inside a falling channel; a double top on a triangle's flat top).
* Developing and Forming candidates never displace a valid one.
* Pennant vs Symmetrical Triangle: when a pennant is kept, a symmetrical triangle with IoU ≥ 0.60 on the consolidation, or the same boundaries, is dropped; a genuinely different, larger triangle survives.

## 9. Lifecycle (completed bars after the formation end, frozen geometry)

Band patterns (channels, ranges, triangles, wedges, flags, pennants):

1. At the formation end the initial state is Validated / Near.
2. Converging/pennant, no break yet, bar ≥ `apex − 3` → **Invalidated (`apex_passed`)**.
3. Two consecutive closes above `upper + 0.1 ATR` → **Broke Above** (below `lower − 0.1 ATR` → **Broke Below**); geometry frozen.
4. After a break: three consecutive closes back inside → **Invalidated (`failed_breakout`)**; after 20 bars the detection is archived.
5. Otherwise Near Upper / Near Lower when inside and within `0.15 × W(now)` (closest wins) or a single unconfirmed close beyond **[P1 decision]**; else Validated.

Reversals (neckline only, evaluated from P2 / RS):

* Double Top / H&S: two closes below `neckline − 0.1 ATR` → Broke Below; close within 1 ATR above the neckline (or an unconfirmed close below) → Near Lower Boundary; before the break, a close above the highest peak / head `+ 0.5 ATR` → **Invalidated (`extreme_exceeded`)**; after the break, three closes back above the neckline → `failed_breakout`.
* Double Bottom / Inverse H&S mirror it (Broke Above, Near Upper Boundary).
* Provisional candidates (unconfirmed pivot, 3-4 bar consolidation) are **Forming**.

Precedence: Invalidated > Broke > Near > Validated > Forming > Developing.

## 10. Quality score (geometric fit, not a probability)

`quality = 100 × Σ (w_i / Σw) × clamp(component_i, 0, 1)`.

* Parallel (25/20/20/20/15): parallelism `1 − err/0.25`; touches `min(total/8, 1)`; containment `(c − 0.88)/0.12`; boundary fit `1 − mean_touch_residual / (0.15 W_mean)`; trend fit `(R² − 0.50)/0.40` or flatness `1 − |M|/(0.60 W_mean)`.
* Converging (20/25/20/20/15): convergence `(0.50 − CR)/0.35`; touches; containment `(c − 0.85)/0.15`; boundary fit `1 − mean(residual/tolerance)`; touch spread `(min spread / L − 0.40)/0.60`.
* Flags/pennants (30/25/20/15/10): pole strength `min((H/ATR)/6, 1)`; retracement `1 − r/permitted`; tightness `1 − W0/(0.5 H)`; boundary fit `1 − mean(residual/tolerance)`; volume contraction 1 at ≤ 0.7× pole volume → 0 at ≥ 1.0×.
* Double top/bottom (30/25/15/15/15): similarity `1 − |P1 − P2| / allowed`; depth `min((D/ATR)/6, 1)`; separation 0 at 10, 1 from 15 to 40, 0 at 60 bars; prior trend `min(move/(2D), 1)`; volume 1 when P2 volume ≤ 0.5× P1 → 0 at ≥ P1.
* H&S (25/20/20/15/20): shoulder similarity; time symmetry `1 − |ln ratio| / ln 2`; head prominence `((prom/HH) − 0.2)/0.2`; neckline flatness `1 − |T1 − T2| / allowed tilt`; size `min((HH/ATR)/6, 1)`.

Labels: ≥ 85 Excellent, ≥ 70 Strong, ≥ 50 Moderate, else Weak — presentation only; weights are placeholders.

## 11. Reported columns

* **Width**: line patterns — drawn width at the formation end as % of close; reversals — pattern height (D or HH) as % of price.
* **Move**: line patterns — midline change; flags — pole move; reversals — prior-trend move (signed).
* **Volume ratio** **[P1 decision]**: latest completed bar volume ÷ mean formation volume for every pattern (pattern-specific volume ratios are in the metrics).
* **Upper/Lower touches**: not applicable (blank) for reversals.

## 12. Geometry contract (schema 1.1)

`elements` is a typed list: `line` (2 points; roles `upper_boundary`, `lower_boundary`, `neckline`, `pole`, `invalidation_level`; `dashed` = projection), `band` (formation polygon), `zone`, `marker` (`touch_upper`, `touch_lower`, `swing_high`, `swing_low`, `apex`, `pole_start`, `pole_end`, `peak`, `trough`, `provisional_pivot`, `breakout_up`, `breakout_down`, `invalidation`). Every point carries `t` (candle start, Unix seconds), `i` (bar index in the detector's series), `p` (adjusted price) and `projected`. `lifecycle_model` (`kind` band / neckline_down / neckline_up, `extreme`, `apex_index`) lets stored geometry be re-evaluated without refitting. Schema 1.0 payloads (P1 scans) remain valid. The chart endpoint also returns `future_bars` so projections keep their bar spacing.
