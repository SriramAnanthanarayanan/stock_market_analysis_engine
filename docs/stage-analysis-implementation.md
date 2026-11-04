# Stage Analysis and RS Line - implementation of the source files (E3)

## Source files inspected

| File | Location | Content |
|---|---|---|
| `rs_line.txt` | project root (unchanged) | TradingView Pine Script v6 indicator "Relative Strength" ((c) bharatTrader): RS formula, RS trend, RS mean, price-confirmation bubbles, alerts |
| `stage_analysis.txt` | project root (unchanged) | "Stage Analysis Screener - Logic Specification": weekly base, 30-week EMA, RS configuration (weekly 52 / daily 63), volume, four classifications, metrics, ranking, output, critical requirements |

A recursive, case-insensitive search of the repository found no other Stage Analysis or RS Line code, setup or configuration files.

## Python implementation

| Module | Purpose |
|---|---|
| `backend/app/stage/rs_line.py` | RS Line (`compute_rs_line`), benchmark alignment (`align_benchmark`), period per timeframe (`rs_length_for`), latest state (`latest_state`) |
| `backend/app/stage/analysis.py` | weekly Stage Analysis for every completed week (`compute_stage_series`), latest classification with metrics, gates, reasons, transition date (`evaluate`), ranking (`rank_key`) |
| `backend/app/stage/service.py` | data access for stocks / indices, benchmark series, index stage table |
| `backend/app/stage/compare.py` | CLI printing values for manual comparison with TradingView |
| `backend/app/api/stage.py` | `GET /api/stage`, `GET /api/stage/symbol`, `GET /api/rsline` |
| `backend/app/config.py` | `StageConfig` (version 1.3.0, hash `86f711e9b6b7301b`; 1.2.0 = `9561423e1323042f`, 1.1.0 = `0d4849126e3192f0`, 1.0.0 = `c35e7f222bbd4d33`), every parameter tagged SOURCE or ASSUMPTION |
| UI | Stage Analysis page `/stage`, Stage panel + RS Line pane + weekly Stage overlay in the stock and index explorers, Stage column in Index Analysis |

## RS Line: `rs_line.txt` -> `rs_line.py`

| Pine (line) | Python | Notes |
|---|---|---|
| L8 `source = close` | `frame["close"]` | completed candles, raw basis (stocks) / official levels (indices) |
| L9 `comparativeTickerId = input.symbol('NSE:NIFTY')` | `StageConfig.benchmark = "NIFTY 50"` | `stage_analysis.txt`: "Benchmark: Nifty 50 (^NSEI)". Data: official NSE NIFTY 50 series (its daily closes match Yahoo `^NSEI` - verified, `docs/index-universe.md`) |
| L10 `length = input.int(123)` | `rs_length_for()`: 1W -> 52, 1D -> 63 (stage_analysis.txt s.4), 1H/4H -> 123 (Pine default) | the setup file defines only weekly and daily periods |
| L15 `base = 5`, L17 `lengthRSMA = 50`, L20 `lengthPriceSMA = 50` | `rs_trend_base`, `rs_ma_length`, `price_sma_length` | identical defaults in both files |
| L25-26 `request.security(..., timeframe.period, source)` | `align_benchmark()` | stock bars define the timeline; same bar -> same-bar benchmark close; a bar the benchmark lacks gets the previous benchmark value (Pine default `gaps_off`); benchmark bars the stock lacks are skipped. Carried values capped at 2 consecutive bars (ASSUMPTION), beyond that RS is missing |
| L29 `res = baseSymbol / baseSymbol[length] / (comparativeSymbol / comparativeSymbol[length]) - 1` | `res = c / shift(c, n) / (b / shift(b, n)) - 1` | `[n]` = n stock bars back; na propagates |
| L30 `resColor = res > 0 ? green : red` | `latest_state()["rs_zero_line"]`; chart colours each RS point by sign | |
| L31-35 reference label `RS-<length> reference, d-m-y` of `time[length]` on the last bar | `/api/rsline` `reference.label` | shown in the Stage panel |
| L36 `y0 = res - res[base]`, L37 `angle0 = math.atan(y0 / base)` | `angle = arctan((res - shift(res, 5)) / 5)` | radians; > 0 improving, < 0 deteriorating |
| L38 zero-line colour by `angle0 > 0` | `rs_trend` | |
| L39 `sma_res = ta.sma(res, 50)` | `sma(res, 50)` (missing if any value in the window is missing, like `ta.sma`) | |
| L41 `sma_symb = ta.sma(baseSymbol, 50)` | `sma(close, 50)` | |
| L42 `pos_div = ta.rising(sma_symb, 3) and baseSymbol >= sma_symb` | `rising(sma_symb, 3) & (close >= sma_symb)` | `ta.rising(x, 3)`: x > each of the previous 3 values |
| L43 `neg_div = ta.falling(sma_symb, 3) and baseSymbol < sma_symb` | `falling(sma_symb, 3) & (close < sma_symb)` | |
| L44-45, L54 confirmation bubbles | `bubbles` in `/api/rsline`; circles on the RS pane; `price_confirmation` = bullish / bearish / neutral | "price-trend confirmation, not actual divergence" (s.4D) |
| L46-47 `ma_rising = ta.rising(sma_res, 3)`, `ma_falling = ta.falling(sma_res, 3)` | `ma_rising`, `ma_falling`; `rs_sma_direction` rising / falling / neutral | alerts "Rising Strength" / "Declining Strength" |
| (s.4A "RS crossing above zero") | `crossover(res, 0)` -> `rs_crossed_above_zero` | Stage page filter "RS crossed above zero" |

Plot-only inputs (colours, `showZeroLine`, `showMA`, `showBubbles`, label style) do not affect values; the chart reproduces the RS colouring, zero line and bubbles.

## Stage Analysis: `stage_analysis.txt` -> `analysis.py`

### s.1 Data requirements
| Source | Implementation |
|---|---|
| NSE stocks, Nifty 500 universe | stocks: every weekly scan (`universe=all` by the scheduler; the page filters NIFTY 500 / F&O / non-F&O); indices: all equity price indices of the official catalogue |
| Weekly primary timeframe; completed weekly candles only | weekly candles whose week has closed (last trading day 15:30 IST <= cutoff); the running week is excluded |
| Benchmark Nifty 50 (^NSEI) | official NIFTY 50 weekly series |
| Prefer 3 years of weekly data | stocks: full Yahoo weekly history; benchmark/indices: 10 years official |
| Aligned dates | identical Monday-labelled weeks; see RS alignment above |

### s.2 Weekly base
| Source | Implementation |
|---|---|
| Base lookback 52 weeks; BaseHigh = highest weekly high; BaseLow = lowest weekly low | `base_high = max(high[i-52 .. i-1])`, `base_low = min(low[i-52 .. i-1])` - the **current candle is excluded** (s.10 rule 6 "Do not include the current breakout candle when calculating previous base resistance"), so the same base serves the breakout test |
| BaseRange% = (BaseHigh - BaseLow) / BaseLow x 100; maximum 30 % | `base_range_pct <= max_base_range_pct` (inclusive) |
| Price within 5 % of base high | `distance_to_breakout_pct = (BaseHigh - close) / BaseHigh x 100`; "approaching base resistance" = `0 <= distance <= 5` (inclusive) |
| Base resistance / support | `base_resistance = base_high`, `base_support = base_low` |
| Base duration | weeks since the earliest base extreme (highest high or lowest low) in the 52-week window - **engineering definition** (the source names the metric without a rule) |
| Whether forming higher lows | last two confirmed weekly swing lows (k = 2) inside the base window rising - **ASSUMPTION**, reported only, never a gate |
| "consolidation after a meaningful prior decline" | `prior_decline_pct = (max high of the 52 weeks before the base window - BaseLow) / that high x 100 >= 20` - **ASSUMPTION** (no number in the source); part of base validity |

### s.3 30-week EMA
| Source | Implementation |
|---|---|
| `ema30 = weekly_close.ewm(span=30, adjust=False).mean()` | `ema_pandas(close, 30)` - exactly this pandas call |
| Below trend / within +/-5 % testing / above trend | `ema30_position`: testing if `|close/ema - 1| x 100 <= 5` (takes precedence, the bands overlap), else above / below |
| Direction: current EMA vs EMA four weeks ago; rising / falling / approximately flat | `ema30_change_4w_pct = (ema30 / ema30[4] - 1) x 100`; rising if > +0.5, falling if < -0.5, else flat - the 0.5 % tolerance is an **ASSUMPTION** |

### s.4 Relative Strength (weekly configuration)
52-bar RS vs Nifty 50, 5-bar trend, 50-bar RS SMA (rising/falling over 3 bars), 50-bar price SMA confirmation - exactly as in the RS Line table. RS above zero = outperforming, below = underperforming.

### s.5 Volume
| Source | Implementation |
|---|---|
| Base average volume = recent 26 weeks | `mean(volume[i-25 .. i])` (current completed week included) |
| Previous average = 26 weeks before | `mean(volume[i-51 .. i-26])` |
| Recent volume = last 4 weeks | `mean(volume[i-3 .. i])` |
| Contraction: recent 26-week average < preceding 26-week average | `volume_contraction`, `volume_contraction_ratio = recent26 / prev26` |
| Expansion: last 4-week average >= 1.5 x recent 26-week average | `volume_expansion`, `volume_expansion_ratio = last4 / recent26` |
| Separate signals | both reported; only expansion enters a rule ("supported by volume" for Stage 2) |

Indices without published volume: volume metrics are `n/a`, so "supported by volume" cannot pass (never assumed).

### s.6 Classification (evaluated in this order, first match wins)
| Stage | Conditions (all required) |
|---|---|
| **Stage 2 - Confirmed** | valid base (range <= 30 %, prior decline) AND weekly close > previous base resistance AND volume expansion AND RS > 0 |
| **Early Stage 2 - Candidate** | valid base AND close > 30W EMA AND EMA rising AND RS > 0 AND RS improving (5-week angle > 0) AND 0 <= distance to base high <= 5 % |
| **Stage 1 - Watchlist** | valid base AND price within +/-5 % of the 30W EMA. Improving RS is **preferred, not required** ("Do not reject Stage 1 candidates simply because RS is still negative") - shown in the explanation and used in ranking |
| **Other / Unqualified** | none of the above |
| Insufficient history | fewer than max(52 + 50 + 3, 53) completed weeks with benchmark data (s.10 rule 8), or RS / base not computable |

Interpretations to confirm: "price above rising 30W EMA" = close above the EMA (also inside the +/-5 % band) with the EMA rising; "approaching base resistance" = the s.2 proximity rule; a close above the base without volume expansion is not Stage 2 (and not "approaching" either), so it falls to Stage 1 / Other with the failed gates listed.

**Stage transition date**: the source defines no transition events; every completed week is classified with only the data up to that week, and `stage_since` is the first week of the current uninterrupted classification (`previous_stage` = the one before). The chart panel lists the latest transitions.

### s.7 Metrics
All listed metrics are stored per stock and week: close, weekly high/low; base high/low, range %, duration; 30W EMA, distance %, direction; RS value, zero-line status, 5-week direction; RS 50 SMA and direction; price confirmation; 26-week contraction ratio, 4-week expansion ratio; base resistance and distance to breakout; classification. Plus prior decline %, higher lows, EMA position, benchmark alignment counts.

### s.8 Ranking
`rank_key`: (1) stage in the order Stage 2 Confirmed, Early Stage 2, Stage 1, Other (**ASSUMPTION** - the source lists "stage classification" first without an order), (2) higher RS, (3) stronger 5-week RS improvement (angle), (4) RS SMA rising, (5) 30W EMA rising, (6) lower base range, (7) stronger volume expansion. No supporting indicator is a mandatory filter beyond the s.6 rules.

### s.9 Output
Stage Analysis page: watchlist with symbol, company, current price, stage, 30W EMA, EMA direction, base range %, base resistance, distance to breakout %, RS 52W, RS direction, RS 50 SMA direction, price confirmation, volume contraction, volume expansion, final ranking; summary counts (Stage 1, Early Stage 2, Stage 2 Confirmed, Other / unqualified, insufficient history); per-row gates with pass/fail and the reasons a stock failed each stage (s.10 rule 9).

### s.10 Critical requirements
| Requirement | How it is met |
|---|---|
| 1 RS matches the TradingView formula | bar-by-bar Pine emulation test (below) |
| 2 52 weekly / 63 daily | `rs_length_for` |
| 3 Nifty 50 benchmark | `StageConfig.benchmark`; another benchmark only via `/api/rsline?benchmark=` with a warning that RS is then not comparable |
| 4 Never mix daily and weekly | Stage Analysis uses weekly candles and weekly benchmark only; the daily RS Line is a separate chart series |
| 5 Completed weekly candles only | weekly completion rule; test `test_incomplete_week_is_excluded` |
| 6 Current breakout candle excluded from base resistance | `base_high` uses `i-52 .. i-1`; test asserts it |
| 7 Missing data and date alignment | `align_benchmark` (Pine `gaps_off`, capped); test `test_missing_benchmark_weeks_make_rs_missing_not_invented` |
| 8 History for the 50-week RS SMA | insufficient-history status |
| 9 Individual filter results and reasons | gates + reasons in API and UI |
| 10 Independent of daily strategies | own module, own configuration, weekly scans only |

## Configuration (`StageConfig` 1.3.0)

SOURCE values: benchmark NIFTY 50; base 52 weeks; max range 30 %; proximity 5 %; EMA 30 (pandas ewm, adjust=False); testing band 5 %; EMA direction lookback 4; RS 52 (weekly) / 63 (daily) / 123 (Pine default, intraday); RS trend base 5; RS SMA 50; rising/falling 3 bars; price SMA 50; volume 26 / 4 weeks; expansion 1.5x.

ASSUMPTION values (not specified in the source files; reviewed with the event study below - still yours to confirm or replace):

| Parameter | Default | Effect on 4-Nov-2026 live data |
|---|---|---|
| `prior_decline_min_pct` | 20 % (decline from the highest high of the 52 weeks before the base window to the base low) | 8 stocks with a valid range that test the 30W EMA fail only this rule (7 stocks are Stage 1 with it) |
| `prior_decline_lookback_weeks` | 52 | |
| `ema_flat_tolerance_pct` | 0.5 % over 4 weeks | decides rising vs flat for Early Stage 2 |
| `higher_lows_swing_k` | 3 (1.0.0: 2) | reported only |
| `stage_rank_order` | Early Stage 2 > Stage 2 Confirmed > Stage 1 > Other (1.0.0: Stage 2 Confirmed first) | ranking only |
| `benchmark_max_ffill_bars` | 2 | RS missing after longer benchmark gaps |

## Tests used to verify the translation

`backend/tests/test_stage_e3.py` (18 tests, all passing):

* `test_rs_line_matches_bar_by_bar_pine_emulation` (periods 52, 63, 123): an independent bar-by-bar emulation of `rs_line.txt` with Pine semantics (history operator, na, `ta.sma`, `ta.rising`, `ta.falling`) equals the vectorised Python for RS, angle, RS SMA, MA rising/falling and both bubble conditions on 400 bars with a missing benchmark value (exact to 1e-12; all branches exercised).
* `test_rs_formula_is_the_literal_stage_spec_formula`: equals `((s / s.shift(52)) / (n / n.shift(52))) - 1` and `rs.rolling(50).mean()` from `stage_analysis.txt`.
* EMA: the series is `pd.Series.ewm(span=30, adjust=False).mean()` itself (`ema_pandas`), checked in `test_indicators_e1.py`.
* Stage scenarios: Stage 2 Confirmed (breakout + volume + positive RS, current candle excluded), breakout without volume is not Stage 2, Early Stage 2, Stage 1 with negative RS, wide range is not a base, inclusive boundaries (range = 30 %, distance = 5 %), prior-decline assumption flagged and configurable, insufficient history, missing benchmark weeks, as-of correctness (every earlier week's classification unchanged when later weeks are added), incomplete week excluded, ranking order, benchmark alignment (exact, carried, capped, extra benchmark bars).
* `backend/tests/test_api_e3.py`: weekly scan persistence, watchlist summary/ranking/filters, index stages = Index Analysis stage column, RS Line endpoint = literal formula on the same candles, periods 52/63, benchmark switch warning, validation.
* UI: `frontend/scripts/verify-e3.mjs` (14 checks).

## Comparing with TradingView (manual)

The Pine script cannot be executed here, so exact parity with TradingView's own numbers has **not** been verified. Method:

1. `cd backend; .\.venv\Scripts\python.exe -m app.stage.compare ICICIBANK ITC --timeframe 1w --dates 2026-09-28,2026-10-26` prints close, benchmark close, RS, RS SMA 50, trend angle, MA rising/falling and bubble state per bar (`--index` for indices, `--timeframe 1d` for the 63-bar daily RS).
2. On TradingView open the same symbol and timeframe, add the `rs_line.txt` indicator with Period 52 (weekly) or 63 (daily), Comparative Symbol NSE:NIFTY, RS Mean on (50), and read the Data Window for the same bars.
3. Differences should only come from candles: TradingView prices (split-adjusted, dividends not adjusted by default) correspond to this app's raw basis; weekly bars here are labelled by Monday. A difference in RS with identical closes would indicate a translation error.

## Differences that could not be reproduced exactly

* TradingView's realtime bar: Pine recalculates the still-open bar; this implementation uses completed candles only (no repainting by design).
* Benchmark data source: the app uses the official NSE NIFTY 50 series instead of TradingView's `NSE:NIFTY` feed (same index; values verified against the official catalogue).
* History start: RS needs 52 bars of both series; the official benchmark history here covers 10 years, so RS is undefined for earlier weeks of long-listed stocks (it is never filled).
* The ASSUMPTION parameters above have no counterpart in the source and may not match the user's manual judgement.

## Tuning report - StageConfig 1.1.0 (4-Nov-2026)

Only the ASSUMPTION parameters were examined; every SOURCE value is unchanged.

**Method.** Event study on 450 current NIFTY 500 / F&O stocks, weekly candles (raw basis) vs the official NIFTY 50, weeks where the benchmark history exists (Nov 2016 - Oct 2026; classifications start once 105 weeks are available). Each week is classified with data up to that week only (the engine is causal - tested). Event = first week of a new Stage 1 / Early Stage 2 / Stage 2 Confirmed run. Outcomes over the next 26 weeks: reached Stage 2 Confirmed; excess return vs NIFTY 50 at 13 / 26 weeks on the dividend-adjusted close; break below the base low; worst drawdown. Time split: **tuning** = events before 2023-01-01, **validation** = 2023-01-01 onwards. Unconditional reference (every 4th stock-week): 26-week excess median +0.9 % (tuning) / +4.1 % (validation), reach Stage 2 0.3 % / 0.7 %. The positive reference is **survivorship bias** (today's index members were past winners) - compare against it, not against zero. Scripts: grid over prior decline 0-40 %, lookback 52/104, EMA tolerance 0.25-2 %, then feature buckets with the filters off.

| Parameter | Previous -> new | Evidence (tuning / validation) | Decision |
|---|---|---|---|
| `prior_decline_min_pct` | 20 -> **20** | Stage 1 entries by prior decline: 10-20 % reach Stage 2 0 % / 6.8 %, break the base low 55 % / 30 %; 20-30 % reach 9.4 % / 16.9 %, break 47 % / 15 %. Below 20 % the "bases" behave like Stage 3 tops; weekly charts of the excluded names (EICHERMOT, MARICO, BHARTIARTL, AXISBANK) are consolidations inside uptrends, the included ones (CASTROLIND, UNITDSPR, DRREDDY) are bases after 20-35 % declines | keep |
| `prior_decline_lookback_weeks` | 52 -> **52** | 52 vs 104 weeks: same buckets, differences within noise | keep |
| `ema_flat_tolerance_pct` | 0.5 -> **0.5** | Early Stage 2 by 4-week EMA change: 0.25-1 % median 26w excess -2.7..1.0 / +5.9..6.0, win 36-60 / 67 %; >1 % (already extended) +0.6..8.7 / -0.4..-2.6, win 39-46 % in validation; tolerance 0.25-0.5 is a plateau, >= 1.5 removes the better entries | keep |
| `higher_lows_swing_k` | 2 -> **3** | Stage 1 with vs without higher lows - break the base low: k=2 43 vs 65 % / 17 vs 21 %; **k=3 43 vs 69 % / 14 vs 23 %**; k=4 45 vs 67 % / 15 vs 22 %; median drawdown k=3 -13.5 vs -25.6 % (tuning). Higher lows mark lower downside risk; k=3 separates best in both periods | change (reporting only, no classification changes) |
| `stage_rank_order` | S2 > E2 > S1 -> **E2 > S2 > S1** | By stage: Early Stage 2 13w excess +7.8 / +2.5 %, breakdown 17 / 7 %, drawdown -14.8 / -8.1 %; Stage 2 Confirmed (only 5 / 13 events) 26w excess -11.1 / -3.5 %, breakdown 80 / 15 %; Stage 1 -1.1 / +2.9 %. Early Stage 2 is the best risk-adjusted group and matches the source objective ("before they become significantly extended"); confirmed breakouts stay second because they are time-sensitive | change (ordering only) |
| `benchmark_max_ffill_bars` | 2 -> **2** | data handling; live weekly stock bars align exactly with the official NIFTY 50 weeks (0 carried) | keep |

**Live effect (4-Nov-2026):** classifications unchanged (7 Stage 1, 1 Early Stage 2, 0 Stage 2 Confirmed); the watchlist now lists the Early Stage 2 candidate first and reports higher lows with k = 3.

**Caveats.** Small samples (Early Stage 2: 12 / 56 events, Stage 2 Confirmed: 5 / 13); medians of 26-week excess returns have a standard error of roughly 3 percentage points at n = 100; current constituents only (survivorship bias); one market regime per split; no costs or position sizing. These are descriptive statistics about the classifications, not a backtest of a trading strategy and not a forecast. Observations outside the parameter scope, for your judgement: (1) Early Stage 2 entries whose 30W EMA rose more than 1 % in 4 weeks did worse in validation (already extended) - a cap would be a new rule, so it is not applied; (2) the source's 52-week / 30 % base rule makes Stage 2 Confirmed rare (about 2 events a year across 450 stocks); (3) the excluded names are mostly Stage 2 continuation bases - a separate "continuation base" class would need a rule from you.

## Your entry setup (confluence) - StageConfig 1.2.0 (4-Nov-2026)

Stated by you in the conversation of 4-Nov-2026: *"All the criteria should be matched before we take an entry"* - after a major decline, a weekly base as long as possible, low volume in the base, price above the 30 EMA, RS positive (green), volume high when the stock is about to break out or breaking out. Implemented as a separate **Entry setup** check next to the stage classification (the stages of `stage_analysis.txt` are unchanged). Every check must pass:

| Check | Rule | Basis |
|---|---|---|
| major_decline | decline into the base >= 20 % (`prior_decline_min_pct`) | your rule; number reviewed in 1.1.0 |
| long_base | price inside one 30 % band for >= 52 weeks (true base length reported, uncapped) | your rule + `stage_analysis.txt` s.2 |
| quiet_base_volume | average weekly volume of the 52 weeks before the base / average volume of the base window >= 1.0 | your rule; number = ASSUMPTION |
| price_above_ema | close > 30W EMA (`ewm(span=30, adjust=False)`) | your rule |
| rs_positive | RS (rs_line.txt, 52 weeks vs NIFTY 50) > 0 | your rule |
| near_or_breaking_out | close within 5 % below the base high, or above it | your rule ("about to break out"), 5 % from s.2 |
| volume_pickup | last 4-week average volume / base average volume > 1.0 | your rule; number = ASSUMPTION |

Ranking: entry setups first, **longest base first** (`setup_rank_by_base_length`, your rule), then the stage ranking. The Stage page has an "Entry setups" card / filter and a check counter (e.g. 6/7) per row; the Stage panel lists the checklist and the verdict.

### Evidence (event study, same method as the 1.1.0 report)

| Setup variant (entries) | Tuning 2018-22 | Validation 2023-26 |
|---|---|---|
| Price / RS / base / near-high checks, no volume checks (23 / 85 entries) | 26w excess median +1.0 %, best move within 52w median +21 %, broke the base low 39 % | +0.1 %, +23 %, 7 % |
| + volume pickup > 1.0 and quiet base >= 1.0 (16 / 44) | -0.8 %, +21.5 %, 37.5 % | -3.3 %, +20.6 %, 11.4 % |
| stricter volume (1.2 - 1.5x) | fewer entries, medians not better | same |
| Unconditional reference (survivorship-biased) | +0.9 % | +4.1 % |

* The setup finds stocks that typically make a +21-23 % move at some point in the following year, with a low breakdown rate in 2023-26, but its 26-week excess return was not better than the (inflated) all-stock reference.
* The weekly volume conditions, measured as above, did not improve the outcomes in either period; they are kept because they are part of your setup, at the mildest thresholds (1.0).
* Base length did not predict the size of the move in this sample (Spearman with the 52-week best move -0.04; all qualifying bases were 52-104 weeks). The longest-base-first ranking is your preference, not a data finding.
* Small samples (16-85 entries), current constituents only, no costs - descriptive, not a backtest.

Live (4-Nov-2026 weekly scan): **0 stocks and 0 indices meet all seven checks.** Closest: DRREDDY fails only quiet base volume (0.97x vs >= 1.0; 80-week base, above the 30W EMA, RS +0.26, volume pickup 1.16x); CASTROLIND has the volume profile (1.88x / 1.32x) but closes about 1 % below the 30W EMA.

### Simplified volume rule - StageConfig 1.3.0 (4-Nov-2026)

Your clarification: ignore the decline / crash volume; volume should be low in the sideways base (or downtrend) and **very high in the last weeks, when the stock is about to break out or breaking out** - "during a trending market the volume should be high, during sideways or downtrend it should be low compared to the trending market".

* Removed: `quiet_base_volume` (decline volume vs base volume) and its parameter.
* New single volume check `volume_high_vs_base`: **average volume of the last 4 weeks >= 1.5 x the average volume of the 52 sideways weeks before them** (`setup_volume_pickup_ratio = 1.5`, USER rule; 1.5x is the volume-expansion multiple of `stage_analysis.txt` s.5). The entry setup now has **6 checks**.
* Reported only (not a gate): `base_volume_trend` = average volume of the later 26 / earlier 26 of those sideways weeks (< 1 = volume drying up in the base).

Threshold test (same event study; base = the 52 weeks before the last 4):

| last 4 w / sideways base | entries tune / valid | broke base low 26w (valid) | 26w excess median (valid) | 52w excess median (valid) | best move 52w median (valid) |
|---|---|---|---|---|---|
| no volume check | 23 / 85 | 7.1 % | +0.1 % | +8.4 % | +22.9 % |
| >= 1.0x | 18 / 56 | 8.9 % | -1.7 % | +7.3 % | +23.0 % |
| >= 1.25x | 12 / 37 | 5.4 % | -1.1 % | +6.3 % | +27.1 % |
| **>= 1.5x** | 8 / 24 | **4.2 %** | **+1.1 %** | **+14.1 %** | +25.4 % |
| >= 2.0x | 2 / 8 | 0 % | +11.3 % | +12.1 % | +71.7 % |

2023-26 favours "very high" volume (fewer failed breakouts, better follow-through); 2018-22 shows the opposite on only 2-8 entries, many caught by the 2020 crash. Small samples - descriptive only.

Live (4-Nov-2026): 0 stocks / 0 indices pass all 6 checks. DRREDDY passes 5/6 (80-week base, +9.6 % above the 30W EMA, RS +0.26, 4.5 % below the base high) and fails only volume (1.16x vs 1.5x).

