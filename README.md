# Indian Price Action Screener

A personal market-analysis application for NSE-listed stocks. It finds predefined chart structures with deterministic numerical rules, draws the backend's exact geometry on interactive candlestick charts, and explains every detection (candles, swing points, touches, boundaries, gates and metrics).

It is **not** a trading system, an advisory service or a signal generator. A pattern's *quality* is a geometric fit score, not a probability of success.

> **Phase status: P1, P2 and P3 are implemented.** All 17 patterns, support/resistance zones, cross-scan pattern tracking, chart labelling, exports and the precision/recall evaluation tooling exist. **No label has been created yet, so real-chart precision and recall are unknown** - every threshold is still an initial default.

| Phase | Scope | State |
|---|---|---|
| P1 | Foundation, NSE universe + F&O snapshots, data ingestion, 1H/4H/1D/1W completed-candle handling, 9 line-based patterns, lifecycle framework, geometry contract, dashboard, Pattern Explorer | **Done** |
| P2 | Flags, pennants, double tops/bottoms, H&S (8 patterns), support/resistance zones, full lifecycle for all 17 | **Done** |
| P3 | Pattern history and lifecycle tracking across scans, outcome analytics, chart labelling, exports, precision/recall, configuration comparison, parameter sweeps, tuning/validation split | **Done** |
| E1 | Raw vs adjusted price basis, Marubozu, 10-day breakouts, EMA 10-200 / RSI / ATR, EMA alignment, RSI divergence, chart terminal (panes, drawing tools), Candlestick / Breakout / Indicator scanners | **Done** |
| E2 | NSE index registry (official catalogue, 139 indices), official index history, Index Analysis, Index Explorer, Relative Rotation Graph | **Done** |
| E3 | Stage Analysis + RS Line translated from `stage_analysis.txt` / `rs_line.txt` (stocks and indices), Stage Analysis page, chart overlays | **Done** |

Patterns: Falling / Rising / Horizontal Channel, Rectangle, Ascending / Descending / Symmetrical Triangle, Falling / Rising Wedge (P1); Bullish / Bearish Flag, Bullish / Bearish Pennant, Double Top / Bottom, Head & Shoulders, Inverse Head & Shoulders (P2). Support/resistance zones are a shared feature shown in the Explorer.

---

## Architecture

A modular monolith: one FastAPI process, one SQLite database, CSV candle cache, one React single-page app.

```
NSE archives ──► UniverseService ──► SQLite (stocks, dated NIFTY 500 / F&O snapshots)
NSE holiday API ─► NSECalendar (sessions, holidays, bar ends, completion)
Yahoo (yfinance) ─► MarketDataProvider ─► OhlcvStore (cache/ohlcv/*.csv + market_data metadata)
                                              │
ScanManager (thread pool, no overlapping identical scans)
   └─ per symbol: clean ► (4H resample) ► completed bars ≤ cutoff ► SeriesContext
        └─ detectors (parallel, converging, continuation, reversal) ► dedup/precedence
             ► lifecycle ► geometry v1.1 ► Tracker (live full scans: track_id, frozen geometry,
               events, outcomes) ► S/R zones ► SQLite
Labels (SQLite) ─► Evaluator (as-of re-detection per labelled chart, IoU matching) ─► evaluation_runs
APScheduler v3 ─► ScanScheduler (session-aware, runs only when a new completed candle exists)
FastAPI /api/* ◄── React + Vite + Tailwind v3 + Lightweight Charts v5 (renders backend geometry only)
```

* The backend is the single source of truth for detections and coordinates; the frontend never re-detects or refits.
* Detectors receive completed candles only (truncated to the as-of cutoff), so no future bar can influence a pivot, fit, status or score.
* Every scan and detection stores the configuration version **and** a reproducible hash of all parameter values.

## Technology stack

* Backend: Python 3.11+ (developed on 3.12), FastAPI, Uvicorn, Pydantic v2, pandas, NumPy, yfinance, requests, lxml, certifi, truststore, APScheduler 3.x, SQLAlchemy Core (no ORM), SQLite.
* Frontend: React 18, Vite 5 (`strictPort`, port 5191, `/api` proxied to 8031), Tailwind CSS v3, Lightweight Charts v5 (`addSeries`, `createSeriesMarkers`, one reusable `ISeriesPrimitive`), TypeScript.
* Tests: pytest + httpx (backend), production build + `playwright-core` driving the locally installed Edge/Chrome (UI verification; no browser download).

## Folder structure

```
backend/
  app/
    main.py            FastAPI app factory (uvicorn app.main:create_app --factory)
    __main__.py        python -m app  -> serves on 127.0.0.1:8031
    config.py          Settings + versioned DetectionConfig + PARAM_META + KNOWN_CONFIG_HASHES
    database.py        SQLAlchemy Core schema + append-only migrations (schema_version)
    models.py          Pydantic request/response models, geometry contract v1.0/v1.1
    net.py             optional TLS trust (certifi + Windows store + truststore)
    timeutil.py        Asia/Kolkata helpers, as-of cutoff
    services.py        service container + startup bootstrap
    scanner.py         ScanManager + persisted result queries
    scheduler.py       APScheduler jobs
    selftest.py        python -m app.selftest
    synthetic.py       deterministic synthetic fixtures (tests / self-test)
    synthetic_p2.py    fixtures for flags, pennants, doubles, H&S, S/R
    api/               routes.py, chart.py (Pattern Explorer payload), p3.py (tracks, outcomes, labels, evaluation, exports)
    data/              calendar.py, universe.py, ohlcv.py (hygiene, 4H, completion), store.py (cache + refresh)
    providers/         base.py (interface), yahoo.py
    indicators/        core.py (ATR Wilder, confirmed swing points)
    patterns/          base.py, shared.py, parallel.py, converging.py, continuation.py, reversals.py, engine.py
    lifecycle/         status.py (state machine, band/neckline models), tracking.py (cross-scan tracks)
    indicators/        core.py (ATR, swings), technical.py (EMA/RSI/SMA/RMA, Pine helpers), divergence.py
    candlesticks/      marubozu.py          breakouts/   nday.py (10-day daily breakouts)
    technical.py       E1 orchestration (scan snapshots/signals, chart overlays)
    indices/           registry.py (official catalogue), data.py (official index history), analysis.py
    rrg.py             RRG-style rotation
    stage/             rs_line.py, analysis.py, service.py, compare.py (Stage Analysis + RS Line)
    api/               technical.py, indices.py, stage.py (E1-E3 endpoints)
    analytics/         sr.py (support/resistance zones), outcomes.py (post-breakout returns, MFE, MAE)
    evaluation/        labels.py (label validation, dataset split), engine.py (matching, metrics, compare, sweep)
  data/                screener.db, nifty500.csv, nse_fno.csv, nse_holidays.json
  cache/               ohlcv/<interval>/<SYMBOL>.csv, yfinance tz cache
  tests/               pytest suite (363 tests)
frontend/
  src/api              typed client mirroring backend models
  src/charts           PatternChart.tsx, OverlayPrimitive.ts, debug.ts
  src/components       scan controls, overview, filters/table, badges, LabelPanel.tsx
  src/pages            Dashboard.tsx, Explorer.tsx, History.tsx, Evaluation.tsx, CandlestickScanner.tsx, BreakoutScanner.tsx,
                       IndicatorScanner.tsx, IndexAnalysis.tsx, IndexExplorer.tsx, RRG.tsx, StageAnalysis.tsx
  scripts/verify-ui.mjs  automated UI verification against the running app (scanner + Explorer)
  scripts/verify-p3.mjs  automated UI verification of labelling, evaluation and history
docs/                  pattern-rules.md, data-sources.md, validation.md, implementation-notes.md, indicators.md, marubozu-breakout.md,
                       charting-tools.md, rrg.md, index-universe.md, stage-analysis-implementation.md
```

---

## Windows setup

Prerequisites: Python 3.11 or newer (`py -0p` lists installed versions), Node.js 18+ (developed on 22), a network connection to `nseindia.com` and Yahoo Finance.

```powershell
cd "D:\Learning Material\Quants_Projects\price_action_detection"

# Backend: virtual environment + dependencies
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt   # runtime + pytest/httpx

# Frontend dependencies (Tailwind is pinned to v3)
cd frontend
npm install
cd ..
```

### Database initialisation

Nothing to run by hand. On every start the backend applies pending numbered migrations from `backend/app/database.py` (recorded in `schema_version`). Existing data is never dropped or recreated. The database lives at `backend/data/screener.db` (override with `PAS_DB_PATH`).

### Start everything with one command

```powershell
cd "D:\Learning Material\Quants_Projects\price_action_detection"
.\start.bat                     # or: powershell -ExecutionPolicy Bypass -File .\start.ps1
```

`start.bat` (also works by double-click) starts the backend and the frontend hidden, waits until both answer, and opens `http://127.0.0.1:5191` in the browser. Ctrl+C in that window stops both. A server that is already running is reused. Logs go to `logs\backend.log` and `logs\frontend.log`. `-NoBrowser` skips the browser. `stop.bat` (or `start.ps1 -Stop`) ends whatever listens on ports 8031 / 5191, for example after the window was closed abruptly. The frontend needs the backend running, because every chart comes from the API.

The two sections below start each part by hand.

### Start the backend (port 8031)

```powershell
cd backend
.\.venv\Scripts\python.exe -m app
# equivalent: .\.venv\Scripts\uvicorn.exe app.main:create_app --factory --host 127.0.0.1 --port 8031
```

On first start the backend downloads the NIFTY 500 and F&O lists and this year's NSE holidays, then runs the startup scans 1D → 1W → 4H → 1H for the whole universe (about 500 symbols; ~2-3 minutes on a normal connection). Later starts restore the last results from SQLite and only scan when a new completed candle exists.

Environment switches: `PAS_SCHEDULER=0` (no scheduler), `PAS_STARTUP_SCANS=0`, `PAS_NETWORK=0` (cache only), `PAS_PORT`, `PAS_DATA_DIR`, `PAS_CACHE_DIR`, `PAS_DB_PATH`.

### Start the frontend (port 5191)

```powershell
cd frontend
npm run dev          # http://127.0.0.1:5191  (strictPort; /api -> http://127.0.0.1:8031)
# production bundle: npm run build ; npm run preview
```

### Universe refresh

Automatic at startup (if no snapshot exists) and on weekdays at 08:30 IST. Manual:

```powershell
curl.exe -X POST http://127.0.0.1:8031/api/universe/refresh
curl.exe "http://127.0.0.1:8031/api/universe?universe=all"
```

A failed or malformed download never replaces the previous list; the error is recorded and shown in `/api/health`.

### Market data refresh

Data is refreshed by scans: missing symbols are downloaded in full (1H: 2y = Yahoo's limit, falling back to 1y; 1D and 1W: full history, from 1996 or the listing date), stale ones incrementally with a 5-candle overlap. Hourly caches keep growing past Yahoo's 2-year window because refreshes only append. Caches made with a shorter history are backfilled once by the next scan, or when their chart is opened. NSE index history goes back to the index's start, which is 1990 for NIFTY 50. Force a full re-download with `"full_refresh": true` on a manual scan. 4H is always built from the cached 1H data.

### Trigger a manual scan

* UI: choose universe / timeframe / optional as-of date, press **Refresh scan**; progress is shown live.
* API:

```powershell
curl.exe -X POST http://127.0.0.1:8031/api/scan/refresh -H "Content-Type: application/json" -d "{\"universe\":\"nifty500\",\"timeframe\":\"1d\"}"
curl.exe http://127.0.0.1:8031/api/scan/status/<scan_id>
curl.exe "http://127.0.0.1:8031/api/scan?universe=nifty500&timeframe=1d&min_quality=70"
```

Historical scan: add `"asof": "2026-09-30"`; only candles completed by the end of that IST day are used.

### Pattern Explorer

Click any row in the results table, or open `http://127.0.0.1:5191/explorer/<SYMBOL>?tf=1d` (optionally `&pattern_id=<id>&asof=YYYY-MM-DD`). The chart shows candles, a volume pane, the formation band, boundaries (dashed = projection), touch and swing markers, poles (flags/pennants), necklines and pivots (reversals), breakout/invalidation markers, the apex and the nearest support/resistance zones (toggle **S/R zones**). Side panels show metrics, the quality breakdown and lifecycle events; below the chart are the gate-by-gate explanation, boundary coordinates, swing points, the S/R zone table and related patterns.

### Technical analysis (E1)

* **Price basis**: candles, patterns, S/R, Marubozu and breakout levels use raw OHLC (split/bonus adjusted, not dividend adjusted - TradingView's default). The dividend-adjusted close is kept only for returns. The Explorer's *Data source* panel shows the basis. See `docs/data-sources.md`.
* **Candlestick Scanner** (`/candlesticks`): bullish/bearish Marubozu (body >= 90 %, wicks <= 5 %) with range/ATR and relative volume filters.
* **Breakout Scanner** (`/breakouts`): close beyond the previous 10 completed daily candles' high/low by 0.1 x ATR; one- or two-close confirmation, failed/pending states, volume and strength filters.
* **Indicator Scanner** (`/indicators`): EMA 10/20/30/50/100/200 full/partial alignment, price vs EMA 200, EMA 20/50 and 50/200 crossovers, RSI zones, regular RSI divergence.
* **Chart terminal**: EMA overlays, RSI/ATR panes, divergence lines, Marubozu/breakout markers, daily 10-day levels (daily-derived on 1H/4H), drawing tools (horizontal line, trendline, ray, zone, vertical line, Fibonacci, crosshair inspection) saved per symbol/timeframe in the browser, layout save/restore, PNG export.

All three scanners read the latest scan (the same scan as the Pattern Scanner). Details: `docs/indicators.md`, `docs/marubozu-breakout.md`, `docs/charting-tools.md`.

```powershell
curl.exe "http://127.0.0.1:8031/api/scan/breakouts?universe=fno&confirm=1"
curl.exe "http://127.0.0.1:8031/api/indicators?symbol=ITC&timeframe=1d&ema=20,50,200"
curl.exe "http://127.0.0.1:8031/api/technical?symbol=ITC&timeframe=1h"
```

### Indices and rotation (E2)

* **Index registry**: every index of the official NSE catalogue (`/api/allIndices`; 139 on 4-Nov-2026: broad, sectoral, thematic, factor/strategy, fixed income) with official name and symbol, deterministic category/subcategory/type rules, data status and verification. Refreshed on startup and weekdays 16:15 IST (`POST /api/indices/refresh`).
* **Index data**: official NSE index history (daily, back to the index's start or 1990; weekly aggregated from completed days) for all 121 equity price indices, each verified against the catalogue's official reference closes. 1H/4H only where a configured Yahoo ticker matches the official closes (23 verified); otherwise the chart says intraday data is unavailable.
* **Index Analysis** (`/indices`): latest close, 1W-1Y returns, RSI, ATR %, EMA 50/200 position, 3-month RS vs NIFTY 500/50, RRG quadrant; filters by category, subcategory, availability, relative strength, trend, stage, timeframe.
* **Index Explorer** (`/index/<symbol>`): the same chart terminal (EMAs, RSI, ATR, S/R, 10-day levels, drawings) plus RRG position/trail and relative performance.
* **RRG** (`/rrg`): internally implemented RRG-style rotation (RS-Ratio = 100 x RS / EMA(RS,14w), RS-Momentum = 100 x RS-Ratio / EMA(RS-Ratio,14w)) on completed weekly closes, benchmark NIFTY 500 or NIFTY 50, 4/8/12-week trails, as-of dates, category filters, search, show/hide, click-through. Not the proprietary JdK RRG.

Details: `docs/index-universe.md`, `docs/rrg.md`.

### Stage Analysis and RS Line (E3)

* Implements **your** `stage_analysis.txt` with the RS Line of **your** `rs_line.txt` (both files unchanged at the project root): weekly candles, RS 52 vs NIFTY 50, 30-week EMA (`ewm(span=30, adjust=False)`), 52-week base (current candle excluded), volume contraction/expansion, Stage 1 / Early Stage 2 / Stage 2 Confirmed / Other, ranking, individual gate results and reasons.
* **Stage Analysis page** (`/stage`): stocks (from every weekly scan) or indices, summary counts, ranked watchlist, filters by stage and RS condition, per-row explanation, "open weekly chart".
* **Charts**: *RS Line* pane on any timeframe (52 on 1W, 63 on 1D, Pine default 123 on 1H/4H; coloured by sign, confirmation bubbles, zero line) and *Stage (1W)* overlay (30W EMA, base resistance/support) plus a Stage panel with every metric.
* Values the source files do not specify (prior-decline threshold, EMA flat tolerance, higher-lows rule, stage ranking order, benchmark carry cap) are configurable and labelled **ASSUMPTION** everywhere. StageConfig 1.1.0 reviewed them with a no-look-ahead event study (2018-2026, tuning/validation split) and chart review: 20 % prior decline, 0.5 % EMA tolerance kept; higher lows k = 3; Early Stage 2 ranked first. Report: `docs/stage-analysis-implementation.md`.
* **Entry setup (your confluence rule, StageConfig 1.3.0)**: major decline + base of 52+ weeks + close above the 30W EMA + RS > 0 + at/near the base high + last 4 weeks' volume >= 1.5x the sideways base - all six at once; setups rank first, longest base first. Stage page card/filter "Entry setups".
* Mapping, tests and the TradingView comparison method: `docs/stage-analysis-implementation.md` (`python -m app.stage.compare ITC --timeframe 1w`).

### Pattern history and outcomes (History page)

Every live, full-catalogue scan links its detections to **tracks**: one `track_id` per formation across scans (the detection ID changes as the fit window slides; the track does not). After the first confirmed break the track keeps the frozen geometry and re-evaluates it on every later scan until it fails, is archived (20 bars) or the provider history changes. The History page lists tracks with filters (symbol, pattern, timeframe, track state, breakouts only; the API also filters by status, F&O status and dates) and shows each track's lifecycle events and post-breakout outcomes (return, MFE, MAE at 5/10/20 bars, signed in the break direction). The outcome summary flags groups with fewer than 30 complete observations; it is descriptive and never feeds detection.

```powershell
curl.exe "http://127.0.0.1:8031/api/tracks?timeframe=1d&breakouts_only=true"
curl.exe http://127.0.0.1:8031/api/tracks/<track_id>
curl.exe "http://127.0.0.1:8031/api/outcomes/summary?timeframe=1d"
```

`/api/history` still returns the per-scan detection records of a symbol.

### Labelling charts

In the Pattern Explorer press **Label chart**. Pick the pattern type, then click candles on the chart to set formation start and end, optionally the upper/lower line points and swing points, choose a verdict (`valid`, `invalid`, `near_miss`, `false_detection`), add notes and save. **Start from detection** copies the selected detection's window and lines as a starting point; the verdict is always yours. Labels are stored with the chart's as-of date and are validated against the cached candles. The dataset (tuning / validation) defaults to a deterministic split by stock (about 1 stock in 5 goes to validation) and can be overridden per label. The panel lists the labels of the open chart with **Edit** / **Delete**.

### Evaluation, comparison, sweeps (Labels & evaluation page)

* **Run evaluation**: re-runs the detector on every labelled chart as of its label date and reports precision, recall, duplicates and support for the tuning set, the validation set and all labels, by pattern, timeframe, pattern × timeframe and F&O segment, with the FP / FN cases.
* **Compare**: configuration A vs B (current + parameter overrides), with per-group deltas and the cases added/removed.
* **Sweep**: one parameter over up to 20 candidate values; tuning and validation metrics per value. It does not choose a value.

None of these change or deploy a configuration; see [Changing the detection configuration](#changing-the-detection-configuration) and `docs/validation.md`.

```powershell
curl.exe -X POST http://127.0.0.1:8031/api/evaluation/run -H "Content-Type: application/json" -d "{}"
curl.exe -X POST http://127.0.0.1:8031/api/evaluation/sweep -H "Content-Type: application/json" -d "{\"param\":\"converging_cr_max\",\"values\":[0.5,0.55,0.6,0.65,0.7]}"
curl.exe http://127.0.0.1:8031/api/evaluation/runs
```

### Exports

| Data | Endpoint |
|---|---|
| Scan results | `/api/export/detections?universe=all&timeframe=1d&format=csv` (or `scan_id=`, `asof=`; `format=json`) - also the **Export detector results: CSV / JSON** links below the dashboard table |
| Labels | `/api/labels/export?format=csv\|json` |
| Evaluation run + cases | `/api/evaluation/runs/<id>/export?format=csv\|json&cases=all\|tp\|fp\|fn` |

### Tests, self-test, UI verification

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.selftest        # 107 checks: engine formulas, 4 cases x 17 patterns, Forming, precedence, S/R, regressions, E1 indicator references
.\.venv\Scripts\python.exe -m pytest              # 363 tests, offline (fixture provider)

cd ..\frontend
npm run build                                     # strict TypeScript check + production bundle
node scripts/verify-ui.mjs ui-screenshots         # needs backend + frontend running and a completed 1D scan
node scripts/verify-p3.mjs ui-screenshots         # labelling, evaluation, compare, sweep, history (creates labels/runs in the live DB)
node scripts/verify-e1.mjs ui-screenshots         # scanners, indicators, panes, overlays, drawing tools (needs 1D + 1H scans)
node scripts/verify-e2.mjs ui-screenshots         # index analysis, index explorer, RRG (needs the index data download)
node scripts/verify-e3.mjs ui-screenshots         # Stage Analysis page, RS Line pane, weekly Stage overlays (needs a 1W scan)
```

`verify-p3.mjs` writes two test labels and three evaluation runs into the live database (runs are an append-only record); delete the test labels afterwards (label panel in the Explorer, or `curl.exe -X DELETE http://127.0.0.1:8031/api/labels/<id>`).

---

## Changing the detection configuration

All thresholds live in `DetectionConfig` (`backend/app/config.py`), each with an entry in `PARAM_META` (description, unit, patterns, timeframes). They are **initial engineering defaults, not validated values**.

1. Change the value(s) and bump `DetectionConfig.version`.
2. Run `python -c "from app.config import DEFAULT_CONFIG, config_hash; print(config_hash(DEFAULT_CONFIG))"` and register the hash in `KNOWN_CONFIG_HASHES` (a test fails until you do).
3. Before that, evaluate the change against human-labelled charts (**Compare** / **Sweep** on the tuning set, then the untouched validation set) and record before/after precision and recall per pattern and timeframe - see `docs/validation.md`.

Old scans keep their own config hash; a parameter change never rewrites earlier results, and open lifecycle tracks of an older configuration are closed as `config_changed`. Version 1.3.0 (chart-quality review of H&S, doubles, flags and wedges, with sources) is documented in `docs/pattern-tuning-1.3.0.md`. The scheduler rescans automatically when the configuration hash changes. `GET /api/config` lists every parameter with its metadata.

## Lifecycle statuses

| Status | Meaning |
|---|---|
| Developing | All structural gates pass but the formation is exactly one touch short. Hidden by default ("Show Developing"). |
| Forming | Provisional: a valid pole with only a 3-4 bar consolidation, or a reversal whose latest pivot has fewer than 5 confirmation bars. Never Validated. |
| Validated | All gates pass; the latest close is inside the boundaries and not near either. |
| Near Upper / Near Lower Boundary | Latest close within 15 % of the current width of that boundary (closest wins), or a single unconfirmed close beyond it. |
| Broke Above / Broke Below | Two consecutive completed closes beyond the boundary by at least 0.1 × ATR. Geometry is frozen from that moment. |
| Invalidated | Terminal: `failed_breakout` (3 closes back inside after a break), `apex_passed` (triangle/wedge/pennant reached apex − 3 bars without a break) or `extreme_exceeded` (reversal: close beyond the peak/head/trough by 0.5 ATR before the neckline break). |

Reversals use the neckline only: Double Top / H&S can Break Below (Near Lower = within 1 ATR above the neckline); Double Bottom / Inverse H&S mirror it.

Precedence: Invalidated > Broke > Near > Validated > Forming > Developing.

## Pattern Quality

`100 × Σ weight × component` with every component clamped to 0-1; the breakdown is shown in the Explorer. Labels: 85-100 Excellent, 70-84 Strong, 50-69 Moderate, below 50 Weak. These are presentation bands for geometric fit (parallelism/convergence, touches, containment, boundary fit, trend/flatness or touch spread) with **placeholder weights**. They are not probabilities, breakout likelihoods or expected returns.

## Known data-provider limitations (Yahoo Finance via yfinance)

* Not exchange-authoritative; data may be delayed, incomplete, revised or adjusted. The NSE official close is published after 15:30 IST and Yahoo may update the last daily bar afterwards (the 5-candle overlap refresh replaces it).
* Yahoo serves hourly history for roughly the last 730 days only. The local cache keeps older hourly bars from then on, but 1H/4H as-of scans before the first download have no data.
* Yahoo reports the 09:15 hourly bar with zero volume; it is stored as *missing*, so 4H morning volume under-counts the first hour.
* Holidays occasionally appear as flat zero-volume bars (e.g. 15-Jan-2026); these phantom bars are dropped. Special sessions with real volume (Muhurat, budget Saturdays) are kept.
* Candles are raw OHLC adjusted for splits/bonus issues only (`auto_adjust=False`, the TradingView default); the dividend-adjusted close is kept separately for returns. Historical as-of scans use today's split basis. A re-adjustment detected during an incremental refresh triggers a full re-download of that symbol.
* A few NIFTY 500 entries have no Yahoo data (e.g. placeholder `DUMMY*` symbols, very new listings); they are reported as failed symbols and retried after 6 hours.
* Historical NIFTY 500 membership is not reconstructed (survivorship bias, flagged in as-of scans). F&O membership before the first stored snapshot is `UNKNOWN`.

Details: `docs/data-sources.md`.

## Known limitations of the initial thresholds

* All numbers are initial defaults from the specification plus documented engineering additions (`docs/implementation-notes.md`). None has been tuned or validated on labelled charts.
* Candidate windows have fixed lengths and all end three bars before the newest completed bar, so a formation's reported start can precede its first touch by up to a third of the window.
* Movement thresholds are relative to the window length: a short window inside a slowly drifting range can read as a Rectangle while the longer one reads as a Horizontal Channel (deduplication keeps one label per formation).
* The double top/bottom rule "no bar between P1 and P2 above min(P1, P2)" is applied literally; it also rejects some doubles a human would accept. Flagged for evaluation once labels exist.
* Tracks start with P3 (no back-fill of earlier scans). Historical as-of scans and pattern-filtered scans are snapshots and do not update tracks. If the provider revises candles inside a frozen formation, the track is closed as `data_changed` instead of being re-fitted.
* Outcome statistics are descriptive (small samples, survivorship bias, adjusted prices) and are not success rates.
* Real-chart precision and recall are **unknown** until you create labels; the evaluation tooling reports nothing meaningful before that.

## Where to continue

* Label charts across patterns, timeframes, sectors and F&O / non-F&O stocks (aim for at least 10 `valid` labels per pattern and timeframe, plus negatives and near-misses).
* Run an evaluation, read the FN/FP cases, then test one parameter change at a time with Compare or Sweep on the tuning set; confirm on the validation set before adopting it as a new configuration version.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Port 5191 is already in use` | Another Vite instance is running; stop it (strictPort is intentional). |
| Backend port 8031 busy | `Get-NetTCPConnection -LocalPort 8031` → stop that process. |
| SSL / certificate errors on Windows | `net.py` builds `backend/.cache/ca_bundle.pem` from certifi + the Windows store and injects truststore; check `/api/health` → `ssl.errors`. Verification is never disabled. |
| Empty dashboard | The startup scan may still be running (progress bar), or run a scan with **Refresh scan**. Check `/api/health` → `universe` and `market_data`. |
| `F&O ... UNKNOWN` warnings on as-of dates | Expected before the first F&O snapshot (3-Nov-2026 on this install). |
| Many failed symbols | `/api/health` → `market_data.last_error`; Yahoo may be rate-limiting - retry later. |
| Chart note "provider history changed" | The cache was re-downloaded after the scan; re-run the scan for that timeframe. |

---

*Market data may be delayed, incomplete or adjusted by the provider. Pattern detections are rule-based chart analyses, not investment advice. Verify the underlying data and make independent decisions.*
