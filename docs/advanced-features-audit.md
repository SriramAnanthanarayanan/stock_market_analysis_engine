# Advanced features audit (breakouts, VCP, confluence, charting)

Audit of the repository before the "Advanced Breakout, VCP, Confluence & Charting" extension (4-Nov-2026, schema 7, DetectionConfig 1.3.0, TechnicalConfig 1.0.0, IndexConfig 1.0.0, StageConfig 1.3.0). Baseline: pytest 363 passed, `python -m app.selftest` 107 checks passed.

## Existing functionality (reused, not rebuilt)

| Area | Where | State | Reuse |
|---|---|---|---|
| Scan pipeline | `scanner.py` (`ScanManager._execute`) | one loop per symbol: data refresh -> `prepare_series` -> patterns -> tracker -> S/R -> stage (1W) -> technical | new detectors run in the same loop on the same completed candles (no extra downloads, no extra scheduler job) |
| Completed candles, cleaning | `data/ohlcv.py`, `data/calendar.py` | raw split/bonus-adjusted OHLC, `adj_close` for returns only; 4H session resampling; bar ends from the NSE calendar | multi-month candles are aggregated from the same cleaned daily candles |
| ATR / swings | `indicators/core.py` | Wilder ATR, confirmed k-bar swings | breakout buffers, VCP contractions |
| EMA / RSI / SMA | `indicators/technical.py` | TradingView-style formulas | volume-shock context (EMA 20/50), VCP trend context (SMA 50/200) |
| Indicator cache | `technical.py` (`_LRU`, `indicator_series`) | key = symbol, timeframe, basis, parameters, candle identity | reused by charts and scanners |
| Marubozu | `candlesticks/marubozu.py` | body >= 90 %, wicks <= 5 %; stored in `tech_signals` (category `candlestick`) | confluence evidence (not re-detected) |
| 10-day breakout | `breakouts/nday.py` | close beyond previous 10 completed daily highs/lows + 0.1 ATR; 1/2-close modes; `first_of_run` flag | the fresh-breakout engine extends it (same reference definition); the E1 scanner stays as it is |
| 52-week levels | - | **missing** (only Stage Analysis' 52-week *base*, a different weekly concept) | - |
| Volume Shocker | - | **missing** (only `prior_volume_ratio` as supporting information) | `prior_volume_ratio` semantics (missing volume never guessed) |
| VCP | - | **missing** | - |
| Confluence | - | **missing** (Stage Analysis has the user's own weekly "entry setup"; untouched) | Stage results as trend / RS evidence |
| Stage Analysis / RS Line | `stage/*` | weekly scans store `stage_results` | read-only context (hard rule: never modified) |
| RRG | `rrg.py`, `indices/analysis.py` | weekly RS-Ratio / RS-Momentum for NSE indices | `rrg_series` reused for a stock's RRG quadrant vs NIFTY 500 (weekly scans) |
| Charts | `charts/PatternChart.tsx` (Lightweight Charts **5.x**), `OverlayPrimitive.ts` | one chart for every page; backend geometry only | preview drawer, fullscreen and VCP overlay use the same component |
| Drawing tools | `charts/drawings.ts`, `useDrawingTools.ts` | h-line, v-line, trend, ray, zone, 7-level Fibonacci retracement | extended (Fibonacci extension, levels, styles, log mode, editing) |
| Drawing storage | `lib/prefs.ts` | **browser localStorage only**, keyed per chart/symbol/timeframe | migrated once into the database (legacy keys are kept) |
| Result tables | `DataTable.tsx`, `ResultsTable.tsx` | row click navigates to the Explorer (filters in the URL; pagination/scroll lost) | symbol/row click opens the shared preview instead |
| Timeframes | `config.TIMEFRAMES` | 1H, 4H, 1D, 1W | scan timeframes unchanged; multi-month are chart timeframes |
| Persistence | `database.py` | 7 append-only migrations, SQLAlchemy Core | migration 8 adds the new tables/columns |
| Scheduler | `scheduler.py` | scans only on a new completed candle or a config change | also rescans on a signal/VCP config change; no new jobs |

## Missing functionality (implemented by this extension)

1. Fresh 10-day breakout events: genuine crossing, previous bar judged on its own window, first-in-lookback (N = 20), event IDs, episode de-duplication and re-arming, touch / close / 1-close / 2-close modes.
2. Volume Shocker: RVOL vs the previous 20 completed bars (current excluded), median / max / 60-bar percentile / z-score / traded value / 50-bar ratio, direction classification, liquidity filter, same-time-of-day RVOL on 1H/4H.
3. 52-week high breakout / low breakdown (252 completed sessions, current excluded), touch vs close, `INSUFFICIENT_HISTORY`, price-basis documentation, optional official NSE adjusted 52-week reference.
4. VCP detector (daily): contractions T1..T6, depth progression, volume dry-up, final contraction and pivot, statuses, breakout confirmation with volume, Stage Analysis as a separate context filter, explainable score, chart geometry.
5. Universal chart preview drawer + fullscreen terminal over any table (filters, pagination and scroll stay).
6. Normalized signal evidence and the Confluence Dashboard (five categories, no double counting, coverage, same-session breakout + Marubozu + volume shock bonus, predefined screens).
7. 1 / 3 / 6 / 12-month candles (calendar month / quarter / half-year / year from daily candles; incomplete period flagged) and 1 / 3 / 6 / 12-month performance horizons.
8. TradingView-style Fibonacci retracement and trend-based extension (custom levels, linear / log, styling, editing, templates).
9. Database-backed drawings with versions, autosave, conflict detection, timeframe visibility, export / restore, history.

## Schema changes (migration 8, append-only)

New tables: `signal_evidence` (normalized evidence published by every scanner), `vcp_formations`, `vcp_contractions`, `confluence_runs`, `confluence_snapshots`, `chart_drawings`, `chart_drawing_history`, `drawing_templates`, `nse_52w_reference`. New `scans` columns: `signal_config_version/hash`, `vcp_config_version/hash`. Existing rows are never rewritten.

## New versioned configurations

`SignalConfig` (fresh breakouts, volume shocker, 52-week), `VCPConfig`, `ConfluenceConfig`: each with `PARAM_META`-style documentation, a pinned hash in its `KNOWN_*_HASHES` table and a test. `DetectionConfig`, `TechnicalConfig`, `IndexConfig` and `StageConfig` are **unchanged** (their hashes stay pinned), so existing detections, tracks and stage results keep their identity.

## Compatibility notes

* Existing API contracts are unchanged; new parameters are optional (`/api/chart` gains optional `max_candles` and multi-month timeframes, scan endpoints are new).
* Scans created before this extension have no evidence / VCP rows; the new pages say "run a new scan" instead of showing empty results as facts. The scheduler queues a new scan automatically because the scan's signal/VCP hash is missing.
* Pattern detectors, S/R zones, Marubozu, divergence, Stage Analysis and the RS Line are **not** run on 1 / 3 / 6 / 12-month candles (not validated there); the chart says so. Indicators (EMA/RSI/ATR) are shown on completed multi-month candles with an "unvalidated on this timeframe" note.
* Browser-only drawings are imported into the database once; the localStorage keys are left in place (nothing deleted).
* Live NSE/Yahoo data could not be reached from the build environment (network policy), so verification uses offline fixtures and synthetic series - not market data.
