# Implementation notes (P1-P3, E1-E3)

Decisions and assumptions made while implementing the specification. Each is configurable or isolated so it can be revisited with labelled data.

## Interpretations of the specification

| Topic | Decision | Why |
|---|---|---|
| "Relevant swing points" for anchored lines | All swing points of the side inside the window, plus one deterministic refinement (`fit_refine_tolerance_mult = 2.0`) that drops far points from the slope estimate; anchoring still uses every point. | Internal swings in the middle of a range otherwise tilt the OLS slope and destroy genuine touches. |
| Parallel family `W_mean` | Independent-fit mean width for parallelism, movement classification and the width band; drawn (shared-slope) width for touch tolerance, containment and the reported width. | Matches the spec's definitions (`W_mean` is defined on independent boundaries; touches are judged on the drawn lines the user sees). |
| Touch spread for channels | Applied to both families, together with the first/second-half rule for channels. | The spread is listed under shared definitions; without it an arch passes as a range. |
| Developing | "Exactly one touch short" = the minimum number of extra touches needed for all touch-count gates is 1; all structural gates must pass. | Single deterministic reading of the rule. |
| Close beyond a boundary without confirmation | Reported as Near Upper / Near Lower. | The spec defines Near only for closes inside; this avoids an undefined state. |
| Volume ratio column | Latest completed bar volume / mean volume over the formation window. | Not defined by the spec for line patterns; documented and shown with a tooltip. |
| Universe "Non-F&O" | NIFTY 500 members with F&O = NO; UNKNOWN never counts as NO. | Never infer membership. |
| As-of cutoff | End of the selected IST day, capped at now. | "Data available as of the selected date". |
| Daily settle | Scheduler judges a daily/weekly candle "new" 30 minutes after 15:30. | NSE publishes the official close after the session; the provider updates later. |
| Startup scans | Run in 1D, 1W, 4H, 1H order only when no scan exists for the current configuration or a new completed candle exists, also outside session hours. | The session-window rule targets repeated intraday scans; a one-off startup catch-up behaves like a manual refresh. |

## Additions beyond the specification (all configurable)

| Parameter | Default | Purpose |
|---|---|---|
| `max_lead_before_first_touch_frac` | 0.33 | Rejects windows that start far before the first touch, where W0/CR/M come from extrapolated lines (found by the near-miss tests: a CR 0.68 triangle passed at L=90 because the window began 33 bars before the formation). |
| `suppress_collapsed_subwindows` | true | A collapsed triangle (CR < 0.15 on its full window) is not reported through a shorter tail window. |
| `dedup_geometry_tolerance_frac` | 0.15 | Nested windows with the same boundaries are one formation, including across sub-types (Rectangle vs Horizontal Channel), so each formation gets one label. |
| `failed_symbol_retry_minutes` (Settings) | 360 | Symbols with no provider data are not re-requested on every scan. |

## P2 decisions (configuration 1.1.x)

| Topic | Decision | Why |
|---|---|---|
| Bearish pennant shape | Converging like the bullish one (upper falling, lower rising). | The spec text "upper rising, lower falling" is unsatisfiable together with its own `0.15 ≤ CR ≤ 0.60` requirement. |
| Raw-bar fits (flags/pennants) | Iterative refinement (≤ 3 passes) on raw points within 2 × touch tolerance; anchoring on all bars. | OLS through every raw high follows the zig-zag midline, so converging pennants were fitted as flags (found by the positive pennant fixture). |
| "P_end is the highest high before the consolidation" | P_end is the extreme of the 15 bars before it **and** is not exceeded inside the consolidation (`no_new_extreme_in_consolidation`). | A higher bar inside the "consolidation" would make it part of the pole. |
| Provisional flag type | A 3-4 bar consolidation is reported as Bullish/Bearish *Flag* with status Forming. | Flag vs pennant cannot be told apart on 3-4 bars; Forming is never Validated. |
| "No bar between P1 and P2 above min(P1, P2)" | Applied literally. | Spec wording; it also rejects doubles whose higher peak is approached by bars above the lower peak. Flagged for P3 evaluation. |
| Head is the extreme of LS..RS | Added gate `head_is_extreme`. | Otherwise a higher bar between the shoulders could sit above the "head". |
| Provisional pivot | Left-side swing rule plus `>=` every later bar, with 1-4 later bars (`provisional_min_confirm_bars = 1`). | Spec: "possible latest pivot that has not yet received five confirmation bars". |
| Reversal lifecycle start | From P2 / RS (the latest pivot). | The neckline break follows the second peak; earlier bars are part of the formation. |
| Reversal width / move columns | Width = pattern height (D or HH) % of price; move = signed prior-trend move %. | Channel-style width/move do not exist for reversals. |
| Dedup groups | Continuation, reversal tops and reversal bottoms are separate groups; tops and bottoms never dedup each other. | A W and an M can overlap legitimately. |
| S/R merged width cap (1.1.1) | `sr_max_zone_width_atr = 2.0`: no merge that would exceed 2 x median ATR. | Live QA: the literal sequential merge chained six adjacent zones on OIL into one 5-ATR band (407-465). Clusters themselves stayed ≤ ~1 ATR. |
| S/R initial role | Role at the zone's second touch: support if the close is above the zone midpoint, else resistance. | The spec defines role flips but not the initial role. |
| Stored S/R zones | Only the nearest 4 above + 4 below per symbol and scan. | What the Explorer displays; keeps SQLite small. |

## P3 decisions (configuration 1.2.0)

| Topic | Decision | Why |
|---|---|---|
| Track identity | A `track_id` follows one formation across live full scans; a fresh detection continues the best-overlapping open track of the same pattern when formation-window IoU ≥ `track_match_iou` (0.5). | Fixed-length windows slide one bar per scan; detection IDs therefore change every bar, the track does not. |
| Frozen geometry | At the first confirmed break the track stores the detection's geometry; every later scan remaps it onto the current candles **by timestamp** and re-evaluates the lifecycle from the formation end (deterministic, no incremental state). Fresh detections of the same pattern overlapping a frozen track are suppressed as refits. | Spec 10.1: a broken formation must not vanish or be refitted by the breakout bars. |
| History changes | If a formation candle disappears or the bar count inside the formation changes (provider revision), the frozen track is closed with `data_changed` rather than evaluated on mismatched geometry. | Never invent or silently shift data. |
| Track matching | Fresh detections and open tracks of the same pattern are paired greedily by IoU over all pairs (frozen wins ties). A detection whose best match is a frozen track is suppressed as a refit; a distinct formation that dedup kept next to a broken one continues its own track. | Live QA (CHOLAHLDNG 1D): with "suppress anything overlapping a frozen track first", a validated wedge at IoU 0.5 with a broken wedge vanished on the next scan with identical data. |
| Terminal states | Invalidated and archived tracks are closed and never reactivated. A detection that is already terminal on first sight closes its track immediately, with `invalidated_time` / `archived_time` = the event bar. A terminal detection overlapping such a closed track is the same failed structure seen again and is linked to it; any non-terminal later formation starts a new track. Tracks without a matching detection in a scan are closed as `disappeared`. | Spec 10.4 / 17. Live QA (1D rescan without a new candle): reversals stay in the 30-bar recency window after failing, so 18 already-invalidated formations got a duplicate closed track per scan, and an already-archived double top was stored as frozen. |
| Tracking scope | Only live, full-catalogue scans update tracks. Historical as-of scans and pattern-filtered scans are snapshots. | Tracks describe what was seen over time; as-of replays would rewrite that history. |
| Outcomes | Returns, MFE, MAE at 5/10/20 bars after the confirming close, signed in the break direction; `pending` until enough bars exist; groups < 30 flagged. Horizons live in `Settings`, not `DetectionConfig`. | Analytics must not change detection hashes or IDs. |
| Label dataset split | Default by stock hash (≈ 20 % validation); overridable per label. | "Different stocks and later time periods"; deterministic and reproducible. |
| Evaluation FP scope | Default: unmatched detections count as FP only for pattern types labelled on that chart; `fp_scope = all` counts every type. | A chart labelled for one pattern says nothing about others. |
| Forming / Developing in evaluation | Developing never counted; Forming only with `include_forming`. | Developing is hidden; Forming is explicitly provisional. |
| Variants | Only scalar numeric/boolean parameters can be overridden for compare/sweep; version label `x.y.z+variant.<hash>`. | Keeps variants explicit, typed and never deployed. |
| Degenerate variants | Quality formulas use a safe division; a variant that zeroes a denominator scores 0 instead of crashing (found by the sweep test: `close_trend_min_r2 = quality_trend_r2_full`). | Sweeps must be safe for any candidate value. |
| Chart clicks for labelling | Captured from the DOM `click` event and converted with `coordinateToTime` / `coordinateToPrice`. | Lightweight Charts drops a second click within 500 ms at a different position (held back as a potential double click); found by the P3 UI test. |

## Chart-quality decisions (DetectionConfig 1.3.0)

| Topic | Decision | Why |
|---|---|---|
| Consecutive extrema | H&S and doubles reject candidates with a close-to-close counter-swing >= 0.33 x pattern height inside any leg; LS/RS (P1/P2) must be the highs of their legs. | Lo, Mamaysky & Wang (2000) define H&S as five *consecutive* extrema; picking shoulders from any earlier pivots produced multi-peak false positives. Closes (not wicks) mirror their close-price smoothing. |
| Single head | No dip >= 0.2 HH while price is above the higher shoulder. | A second peak above the shoulders is a double / complex head, not a textbook H&S. |
| Close confirmation | Peaks need a close within 0.25 x height (2 bars); neckline points within 0.15 x height. | Lone-wick shoulders / armpits drew patterns in sideways ranges and put the breakout line on a spike. |
| SWZ restrictions | Shoulder similarity 4 % / 0.2 HH, neckline <= min(0.25 HH, 8 %), average shoulder <= 0.7 HH, leg <= 2.2 x mean leg. | Savin, Weller & Zvingelis (2007) calibrated on Bulkowski's examples. |
| Flags | Pole >= 0.5 ATR per bar; flag drift with the pole <= 0.03 H. | Bulkowski: steep, quick pole; flag flat or sloping against it. |
| Wedges / triangles | CR <= 0.50. | CR 0.5-0.6 formations look parallel (and duplicated channel detections). |
| Config change and tracks | Open tracks of another config hash are closed as `config_changed`. | Frozen tracks would otherwise keep showing patterns the new rules reject for up to 20 bars. |
| Chart viewport | Re-apply the formation viewport after EMA / pane / overlay series updates until the user interacts. | Adding series moved a visible range that extends into future whitespace (far apexes). |

Full report with sources, counts and screenshots: `docs/pattern-tuning-1.3.0.md`.

## E1 decisions (TechnicalConfig 1.0.0, schema 4)

| Topic | Decision | Why |
|---|---|---|
| Price basis | yfinance `auto_adjust=False`: OHLC raw (split/bonus adjusted), `Adj Close` stored separately; caches/scans/tracks/labels tagged; legacy caches re-downloaded, never analysed; open P1-P3 tracks closed `data_changed`. | Spec 3.1 and the TradingView S/R mismatch (dividend adjustment: ITC 409.34 raw vs 370.45 adjusted). |
| Separate TechnicalConfig | Own version, hash, metadata and `KNOWN_TECH_CONFIG_HASHES`; `atr_period` must equal the pattern ATR period (test). | Indicator settings must not change pattern detection IDs; one ATR everywhere. |
| EMA seed | SMA of the first n closes (TradingView `ta.ema`); `ema_pandas` (`ewm(adjust=False)`) kept for sources that prescribe it. | Chart values comparable with TradingView; Stage Analysis spec uses the pandas form. |
| History length | 1D 5 years, 1W full history. | EMA 200 warm-up (infinite memory). |
| Flat RSI | 50. | Wilder formula undefined for zero gains and losses. |
| Partial EMA alignment | 20 > 50 > 200 (or <) without full alignment. | Spec names the state but not the rule. |
| Divergence pairing | Latest two confirmed pivots of a side; 5-60 bars apart; invalidation = close beyond P2's extreme. | Spec 6.3; the 60-bar maximum and zero buffer are engineering defaults. |
| Breakout ATR | ATR(14) of the breakout candle for the buffer; two-close mode fixes level and buffer at the first close. | Same convention as the pattern lifecycle. |
| Scanning | Technical analysis runs inside every scan on the same prepared candles; snapshots + signals of the last 5 bars persisted (`tech_snapshots`, `tech_signals`). | Same cutoff, universe and schedule as patterns; no second data path. |
| Indicator cache | In-process LRU keyed by symbol, timeframe, basis, parameters and candle identity (count, first/last time, close sums). | Spec 13 invalidation rules without duplicating OHLCV in SQLite. |
| Drawings | Browser `localStorage` per chart/symbol/timeframe with JSON export/import. | Allowed by spec 7.2; personal annotations, not detector data. |
| Outcome returns | Use `adj_close / close` factors when available. | Spec 3.1: returns on the adjusted basis, levels on raw. |

## E2 decisions (IndexConfig 1.0.0, schema 5)

| Topic | Decision | Why |
|---|---|---|
| Index universe | Official NSE catalogue `/api/allIndices`; key = official `indexSymbol`; inactive instead of deleted. | Spec 9.1: source-backed, maintainable, no hard-coded list. |
| "Indices eligible in derivatives" group | NIFTY BANK / FIN SERVICE -> sector, others -> broad. | The group cuts across categories; mapping documented in `docs/index-universe.md`. |
| Daily index data | Official NSE index history (`historicalOR/indicesHistory`, `csv=true`, one request per year), 10 years, verified against the catalogue's reference closes. | Authoritative, covers every index incl. factor indices; one consistent basis for RRG. |
| Intraday index data | Yahoo only for explicitly configured candidates whose closes match the official reference closes (0.3 %, >= 3 dates). | Spec: never assume a Yahoo ticker from a name; never substitute a proxy. Live: 23 of 44 candidates verified. |
| Weekly index bars | Aggregated from completed official daily candles into Monday-labelled weeks. | Same week convention as stock weekly bars; no second weekly source. |
| Special index types | VIX, leveraged, inverse, dividend points, USD and fixed income listed but excluded from price analysis. | Not equity price indices; mixing them would distort returns and rotation. |
| RRG EMA / alignment | SMA-seeded EMA; inner join on identical weeks; > 1 consecutive missing week -> `data_gap`; exactly 100 = weak side. | Transparent, documented; never forward-fill. |
| Returns | Calendar windows on official daily closes; `null` when the index is younger than the window. | Spec 9.3 / 14.3. |
| Performance | Cleaned daily/weekly frames cached per index (keyed by cache-file identity); overview/RRG results memoised by latest completed bar + data version. | Live: overview 12-17 s -> 3.3 s cold / 9 ms warm. |

## E3 decisions (StageConfig 1.0.0, schema 6)

| Topic | Decision | Why |
|---|---|---|
| Source of truth | `stage_analysis.txt` + `rs_line.txt`; every parameter tagged SOURCE or ASSUMPTION (test-enforced). | Spec 10.1: no generic Weinstein rules, no invented thresholds without disclosure. |
| RS period by timeframe | 1W 52, 1D 63 (setup file), 1H/4H 123 (Pine default). | The setup file configures weekly and daily only. |
| Benchmark data | Official NSE NIFTY 50 series, aligned like Pine `request.security` (gaps_off, carry capped at 2 bars). | Same index as `NSE:NIFTY` / `^NSEI`; verified data; no silent long forward-fill. |
| Base window | Previous 52 weeks, current candle excluded, for all stage rules. | s.10 rule 6; one consistent base for proximity and breakout. |
| Unquantified phrases | Prior decline >= 20 %, EMA flat +/-0.5 %/4 weeks, higher lows k=2, rank order, carry cap: ASSUMPTION, configurable, shown in UI. | Live: the prior-decline rule alone rejects 8 Stage 1 candidates - the user must confirm it. |
| Volume for indices | Missing volume -> "supported by volume" cannot pass. | Never assume volume support. |
| Transition date | Each completed week classified with data up to that week; `stage_since` = start of the current run. | Source defines no transition events; no look-ahead. |
| Persistence | Stock stages stored per weekly scan (`stage_results`); index stages computed on demand (memoised per completed week + data version). | Weekly scans already prepare the candles; indices are few. |

## Full-history data (schema 7, 2026-11-04)

| Topic | Decision | Why |
|---|---|---|
| Stock depth | 1D and 1W `period=max` (Yahoo: from 1996 or listing); 1H stays `2y` (Yahoo's ~730-day cap) but the cache only appends, so hourly history grows from now on. | User reviews charts over long history. No configuration (Detection/Technical/Stage) changed. |
| Existing caches | `market_data.history_period` (migration 7 tags old caches 1d `5y`, 1h `2y`, 1w `max`, indices `10y`); a usable cache whose depth differs from the provider's preferred period is fully re-downloaded once (scan report `backfilled`, or when its chart is opened). Failure keeps the old cache. | One-time backfill without a manual step; never loses cached candles. |
| Index depth | Official NSE history: last 10 years, then one year per request going back until 2 consecutive empty years or 1990. Close-only early rows kept as flat candles (open/high/low were dropped by a `float(None)` before). | NIFTY 50 from July 1990, NIFTY BANK from 2000; one empty answer can be an NSE glitch (seen live for Nov 2015-Nov 2016, which has data). |
| Detections | Unchanged by depth: 148 stocks, 5-year cache vs full history, 103 identical detections; ~0.1 s per 7,700-bar series. | Detectors, S/R (250 bars) and signals work on recent bars. |
| Charts | `chart_max_candles` 500 -> 20,000: the payload carries the whole history; the view still opens on the latest bars / formation. Stored scan geometry is realigned by candle timestamp when older bars were prepended (pure offset; otherwise the old "does not align" note). | Scroll back without re-scanning; old scans stay viewable. |
| Stale daily prints | 1D bars with no volume, no range and a > 20 % jump from the previous kept close are dropped (trailing check only). 248 bars / 68 stocks, all <= 2005. | Yahoo's early NSE history has stale/mis-scaled prints that showed as isolated dots once 1996+ was charted; a no-trade day keeps the previous price. |
| Long-range viewing | `minBarSpacing` 0.05 px so Fit reaches the first bar (default 0.5 px stopped at ~2,800 bars); Log price-scale toggle. | 30 years on a linear axis flattens early decades. |
| Stage Analysis | Untouched. Its NIFTY 50 benchmark now also covers 1990-2016, so RS values exist for those older weeks; recent values are unchanged (finite RS lookback). | User constraint: never change the Stage setup. |

## A5 decisions ("Improve Existing Price-Action Screener", schema 9, 2026-11-05)

Source: the user's fourth specification (line chart, running candles, VCP rebuild, IPO base, 10-day about-to-breakout, confluence, universal preview, drawings, tests). Nothing existing was removed; Stage Analysis, DetectionConfig and the 17 pattern detectors are unchanged.

| Topic | Decision | Why |
|---|---|---|
| Line chart | Lightweight Charts `LineSeries` of the candles' closes added next to the candlestick series; in line mode the candles become fully transparent instead of being removed. | Overlay primitives, markers and drawings stay attached to the candle series, so every (time, price) anchor maps to the same pixel in both modes - no re-anchoring code, nothing recomputed (`docs/charting-tools.md`). |
| Running candle | 1W and 1 / 3 / 6 / 12-month running period built from **completed daily candles** (`partial_week`, `running_period`); `is_complete` on every candle; display only. Indicators include it only on request (`include_partial`). | Spec: always show the current partial candle, never let detection see it; building it from daily candles keeps the latest price / time identical across D / W / M (`docs/multi-month-timeframes.md`). |
| VCP v2 | Rewritten as a time-ordered walk over confirmed swing highs (k = 3) producing successive contractions T1 > T2 > ... -> pivot; spec defaults (base 15-120, >= 3 %, next <= 0.75 x, final <= 10 %, final volume <= 0.70 x the 50-day average, near pivot 3 %, breakout volume 1.5 x); 7 states; 6-part score 30/20/20/10/10/10; geometry `vcp-2.0` drawn as a structure (legs, swing points, depth labels, pivot, dry-up, breakout volume). VCPConfig 2.0.0. | The 1.0 detector's chart read as a box; the spec asks for the contraction structure. Engineering thresholds are flagged as such (`docs/vcp-methodology.md`). |
| VCP prior history | `min_prior_bars` 50: a base needs 50 completed sessions before it. | A synthetic fresh listing's first base qualified as a VCP with only 5 prior bars, so no preceding uptrend could be measured; the IPO base scanner covers that case. |
| VCP low-volume pivot cross | A close above the pivot without breakout volume stays **Near Pivot**, flagged `pivot_crossed_low_volume`. | Spec: breakout needs volume; the spec's state list has no "crossed without volume" state. |
| IPO listing date | Official NSE `EQUITY_L.csv` (`DATE OF LISTING`) in `stocks.listing_date`; first-candle fallback **off** by default; a provider history that starts > 5 sessions after listing is not evaluated. | Spec: use the actual listing date. A renamed / re-listed ticker restarts its provider history and would look like an IPO (`docs/ipo-base.md`). |
| IPO base vs VCP | Separate detector and evidence; an IPO base is never labelled a VCP. | Spec. |
| About to break out | Separate state of the latest candle on the existing crossing engine (`breakout_setups`), published as `fresh_breakout` evidence of kind `state`; "both" rule (within 2 % **and** 0.5 ATR) by default; Very Near / expiry / rejection numbers are ASSUMPTIONs. SignalConfig 1.1.0. | The existing 10-day breakout rule must not change; "both" yields fewer, closer setups (spec: prefer fewer, explainable signals) (`docs/about-to-breakout.md`). |
| Confluence 1.1 | Setup states in the 10-day breakout's family; IPO base (setup) and IPO breakout (trigger); VCP / IPO dry-up in the volume category (best + one capped bonus); Marubozu + volume combination (3 points); sector index RRG as a capped RS bonus that never scores alone (ASSUMPTION industry -> index map); 11 screens. | Spec 6: combine the new evidence without double counting (`docs/confluence-engine.md`). |
| Confluence hook | The post-scan snapshot is built after the scan lock is released (`ScanManager._run`). | The hook held the lock and blocked an identical follow-up scan request (test `test_scan_locking_prevents_identical_overlap`). |
| Sector cache | A per-scan dict passed into `_store_signals`. | A shared attribute raced between concurrent scans. |
| Drawings per user | `chart_drawings.owner` (default `local`), `X-PAS-User` header, `created_timeframe`, `text` drawing type; a payload `owner` is accepted and ignored. | Spec 8: store by symbol + timeframe + user. The app has no login, so the user field separates people sharing one installation; it is not access control (`docs/chart-drawing-persistence.md`). |
| Verification | `scripts/verify-improvements.mjs` (40 checks + 4 after a restart) on the synthetic demo; legacy suites made robust (stale debug state reset per chart, waits for the new chart). | No Yahoo / NSE access in the build session. |

## Phase boundaries

* A5: line chart, running candles, VCP v2, IPO base (`ipo/`), about-to-breakout states, confluence 1.1, drawings per user (migration 9).
* A1-A4: signal evidence, fresh / 52-week breakouts, Volume Shocker, VCP, chart preview, multi-month candles, Fibonacci, database drawings, Confluence Dashboard (migration 8).
* P1: line patterns, per-scan lifecycle, `patterns.engine.track_detection` (frozen-geometry re-evaluation).
* P2: flags, pennants, reversals, Forming, `extreme_exceeded`, S/R zones (`sr_zones`, migration 2).
* E3: Stage Analysis + RS Line, stage results (migration 6), Stage page, RS pane, weekly overlays.
* E2: index registry, official index history, Index Analysis, Index Explorer, RRG (migration 5).
* E1: price basis, TechnicalConfig, indicators, Marubozu, N-day breakouts, divergence, chart terminal and drawings (migration 4).
* P3: cross-scan tracks (`pattern_tracks`, `track_events`, `detections.track_id`), outcomes, labels, evaluation runs (migration 3). `/api/history` still returns per-scan detection records; `/api/tracks` is the linked history.

## Verified behaviour worth knowing

* The 4H and 1H scans share one hourly download: the 1H scan after a 4H scan found all 498 symbols fresh.
* An F&O-only scan and the all-universe scan filtered to F&O produced identical detection IDs, statuses and qualities (146 = 146).
* After a restart the last results are served from SQLite and startup scans are skipped when no new candle exists.
