# CLAUDE.md: Indian Price Action Screener

Project memory for Claude Code sessions in this repository. It describes what the project is, how to run and verify it, what you must never break, where the project stands, and what comes next.

Last updated: 2026-11-06.

## What this is

This is a personal market-analysis app for NSE stocks (NIFTY 500 plus F&O). It has two parts:

- A **FastAPI backend** finds chart patterns with deterministic numeric rules, then computes indicators, index rotation (RRG), the user's own Stage Analysis setup and the advanced scanners (fresh 10-day breakouts, About to Break Out, Volume Shocker, 52-week breakouts, Multi-Year Breakouts, VCP, IPO Base Breakout), stores every result as normalized evidence and scores stocks on a Confluence Dashboard.
- A **React frontend** draws the backend's exact geometry on interactive TradingView-style charts.

The user mainly uses the frontend to review charts.

It is **not** a trading system or a signal service. A pattern's *quality* score measures geometric fit; it is not a probability of success. Never claim accuracy: no human labels exist yet.

## Sources of truth (read before asking the user)

| File | Content |
|---|---|
| `master_prompt.txt` | Original spec, phases P1 to P3: patterns, lifecycle, geometry, tracking, evaluation |
| `enhancement_prompt.txt` | Second spec, E1 to E3: indicators, Marubozu, breakouts, chart terminal, indices, RRG, Stage Analysis |
| `docs/advanced-features-audit.md` | Third spec (advanced breakout, VCP, confluence, charting; called A1 to A4 here): audit, decisions, schema changes |
| `docs/implementation-notes.md` (A5 section) | Fourth spec "Improve Existing Price-Action Screener" (called A5 here): line chart, running candles, VCP v2, IPO base, About to Break Out, confluence 1.1, drawings per user |
| `stage_analysis.txt`, `rs_line.txt` | The user's own Stage Analysis rules and RS Line Pine Script. Translate them literally. |
| `docs/*.md` | Rules and decisions (see the index at the end) |

When a requirement is unclear, grep these files first. Ask only if they genuinely do not answer it.

## Run it

```powershell
.\start.bat          # or double-click: starts backend :8031 + frontend :5191, opens http://127.0.0.1:5191
.\stop.bat           # stops whatever listens on 8031 / 5191
.\start.ps1 -NoBrowser
```

- Logs go to `logs\backend*.log` and `logs\frontend*.log`.
- The backend does **not** auto-reload. After backend code changes, stop and start it again.
- The frontend is served by the Vite dev server, so changes appear immediately.

First-time setup (details in the README "Windows setup" section):

```powershell
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd frontend; npm install
```

What the first start of a fresh clone does:

- Creates `backend/data/screener.db`, which is not in git.
- Refreshes the universe and the NSE holiday calendar.
- Downloads full index history from NSE, going back to 1990. This takes about 10 minutes and runs in the background.
- The scheduler's startup scans download full stock history from Yahoo into `backend/cache/`, which is not in git (~465 MB).

## Stack and layout

- **Backend:** Python 3.12, FastAPI, SQLAlchemy Core with SQLite, pandas/numpy, yfinance, APScheduler 3. Code is in `backend/app/`:
  - `config.py`: all settings and versioned configs.
  - `database.py`: schema and append-only migrations.
  - `patterns/`: detectors and engine.
  - `lifecycle/`: status state machine and cross-scan tracking.
  - `data/`: calendar, universe, OHLCV cleaning, cache store.
  - `providers/yahoo.py`: Yahoo download provider.
  - `indices/`: NSE index registry and history.
  - `stage/`: Stage Analysis and RS Line.
  - `api/`: HTTP endpoints.
  - `evaluation/`: labels and precision/recall.
  - `analytics/`: support/resistance zones and outcomes.
  - `signals/`: shared crossing engine, fresh 10-day + 52-week breakouts, multi-year breakouts (`multiyear.py`), Volume Shocker, normalized evidence (`evidence.py`), NSE 52-week reference.
  - `vcp/`: VCP detector v2. `ipo/`: IPO base detector. `confluence/`: confluence score + snapshots. `drawings.py`: chart drawings in the DB (per user). `data/periods.py`: 1/3/6/12-month candles and the running (incomplete) 1W / multi-month candle.
  - `synthetic*.py`: deterministic fixtures (`synthetic_vcp.py`, `synthetic_ipo.py`, `synthetic_multiyear.py` for VCP / IPO base / multi-year breakouts).
  - `scripts/synthetic_demo.py`: offline demo server with SYNTHETIC data (for UI checks without network).
- **Frontend:** React 18, Vite 5 (strictPort 5191, `/api` proxied to 8031), Tailwind v3, Lightweight Charts v5, TypeScript.
  - `src/charts/PatternChart.tsx` is the chart component (candles or a close line, `lineData.ts`; running candle grey + INCOMPLETE). `vcpOverlay.ts` draws the VCP structure and the IPO base.
  - `src/charts/debug.ts` exposes `window.__pasChart` for UI checks.
  - Pages are in `src/pages/`. `components/StockChartPreview.tsx` is the preview drawer every table opens (`lib/openChart.tsx`); `lib/drawingStore.ts` syncs drawings with the DB.
- **Data:**
  - Candles are cached as CSV in `backend/cache/ohlcv/<interval>/<SYMBOL>.csv`.
  - Metadata is in the `market_data` table.
  - Committed snapshots are in `backend/data/`: `nse_holidays.json` (tests need it), `nifty500.csv`, `nse_fno.csv`, `index_intraday_candidates.json`.

## Current status (2026-11-06)

| Area | State |
|---|---|
| P1 to P3 | Done. 17 patterns, S/R zones, lifecycle, cross-scan tracks, outcomes, labelling, evaluation, compare, sweep, exports. |
| E1 to E3 | Done. Raw price basis, EMA/RSI/ATR, divergence, Marubozu, 10-day breakouts, chart terminal with drawing tools, NSE index registry, Index Analysis, RRG, Stage Analysis and RS Line. |
| A1 to A4 | Done (merged to main). A1 fresh/first-time 10-day breakouts, Volume Shocker, 52-week breakouts, `signal_evidence`. A2 VCP detector + scanner. A3 chart preview drawer, fullscreen, 1/3/6/12-month candles, performance horizons, Fibonacci retracement/extension, drawings in the DB. A4 Confluence Dashboard. Built and UI-verified on **synthetic** data only (the cloud session had no Yahoo/NSE access); not yet run on a live scan. |
| A5 | Done (merged to main). Candlestick / line chart switch; running (incomplete) 1W / multi-month candle (`is_complete`, grey, never used by detectors, indicators optional); VCP detector v2 (successive contractions, 7 states, 6-part score, structure overlay); IPO Base Breakout scanner (official NSE listing dates); 10-day About to Break Out states; Multi-Year Breakouts (any number of years, failure / re-break, History tab with forward returns vs NIFTY 50; overlays snap daily times onto weekly / monthly bars); confluence 1.2 (setups, IPO base, multi-year, dry-up, Marubozu + volume, sector strength, 12 screens); drawings per user (`X-PAS-User`) + creating timeframe + text notes; universal preview from every scanner. Built and UI-verified on **synthetic** data only. |
| DetectionConfig | **1.3.0**, hash `9c33f4bf86016487`. Tuned by chart review for H&S, doubles, flags and wedges; see `docs/pattern-tuning-1.3.0.md`. |
| StageConfig | **1.3.0**. Implements the user's confluence entry setup. **Do not change.** |
| TechnicalConfig / IndexConfig | 1.0.0 / 1.0.0 |
| SignalConfig / VCPConfig / ConfluenceConfig / IPOConfig | 1.2.0 `3f732a9d8a9675bd` / 2.0.0 `2159765b755d4287` / 1.2.0 `1d78ef4b3c742589` / 1.0.0 `a2f7e09abbcbc9c9`. Engineering defaults (ASSUMPTION-flagged), not validated. Same governance as DetectionConfig (`KNOWN_*_HASHES`, `*_PARAM_META`). |
| DB schema | 9. Migration 9 added IPO config columns on `scans`, `stocks.listing_date` / `listing_date_source`, `chart_drawings.owner` (default `local`) / `created_timeframe`, VCP v2 contraction metrics. Migration 8 added `signal_evidence`, `vcp_formations`, `vcp_contractions`, `confluence_runs`, `confluence_snapshots`, `chart_drawings`, `chart_drawing_history`, `drawing_templates`, `nse_52w_reference` and signal/VCP config columns on `scans`. |
| Data depth | Stocks 1D/1W: full Yahoo history (from 1996 or listing). 1H: Yahoo's ~730-day cap, and the local cache keeps growing. NSE indices: back to their start (NIFTY 50 from 1990). |
| Charts | Send the whole history. Open on the latest bars or the selected formation. **Fit** shows everything; **Log** switches to a log price scale. |
| Tests | pytest 565, `python -m app.selftest` 121, `npm test` 13 (Fibonacci maths, line data), verify-advanced 53 (+3 with `--after-restart`), verify-improvements 47 (+4 with `--after-restart`). Older UI suites need the live DB (see "How to verify"). |
| Live 1D scan | 500 of 502 symbols, 334 detections (before A1-A4). BAGMANE and DUMMYHEG have no Yahoo data; that is expected. The first live scan after A1-A4 adds evidence, VCPs and the confluence snapshot without re-downloading history. |

## Hard rules: do not break

1. **Never modify Stage Analysis** unless the user explicitly asks. That covers `StageConfig`, `backend/app/stage/*` and the RS Line logic. It is the user's own trading setup, and they have repeated this constraint.
2. **DetectionConfig changes:**
   - Change the value and bump `version`.
   - Register `config_hash` in `KNOWN_CONFIG_HASHES`; a test fails until you do.
   - Document the change with sources.
   - Old scans keep their own hash, and open tracks of an old config close as `config_changed`.
   - Every parameter needs a `PARAM_META` entry.
3. **Migrations are append-only** (`MIGRATIONS` in `database.py`). Never edit a released migration and never drop user data.
4. **Price basis:**
   - Charts, patterns and levels use raw OHLC (split/bonus-adjusted only, the TradingView default; Yahoo `auto_adjust=False`).
   - `adj_close` is used only for returns.
   - Never mix bases; `load_current` refuses caches on another basis.
5. **No look-ahead.** Use completed candles only, at or before the cutoff. Cleaning and indicator rules may only look backward.
6. **Nothing manufactured.** Never fill missing sessions or guess volume. Unquantified rules must be configurable and flagged as ASSUMPTION in code, the UI and docs.
7. **The frontend never re-detects.** It renders backend geometry. Every geometry point carries `t` (bar start, Unix seconds) and `i` (bar index). The chart API realigns stored geometry by timestamp.
8. **Tests stay offline.** They use the fixture provider and `FakeHistory`, never the network or the real DB.
9. **Longer history must not change detections.** This was verified: 334 detections were identical before and after the full-history backfill.
10. **One detector per signal.** Scanners publish their results as `signal_evidence` rows (`signals/evidence.py` adapters only re-label). The Confluence engine and dashboards read evidence; they never detect.
11. **Drawings are user data.** They are removed only by the user's delete action; imports never overwrite a newer copy; migrations never drop them.
12. **SignalConfig / VCPConfig / ConfluenceConfig / IPOConfig** follow rule 2 (version bump, `KNOWN_*_HASHES`, `*_PARAM_META`, docs). A changed hash makes the scheduler rescan.

## How to verify changes

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest              # offline, ~2 min
.\.venv\Scripts\python.exe -m app.selftest
cd ..\frontend
npx tsc --noEmit -p .; npm run build; npm test
node scripts/verify-ui.mjs <dir>   # needs both servers + a completed 1D scan
node scripts/verify-e1.mjs <dir>   # E1; needs 1D + 1H scans
node scripts/verify-e2.mjs <dir>   # indices / RRG
node scripts/verify-e3.mjs <dir>   # Stage page; needs a 1W scan
node scripts/verify-advanced.mjs <dir>   # A1-A4; needs 1D + 1W scans; writes and removes test drawings
node scripts/verify-advanced.mjs <dir> --after-restart   # after restarting the backend: drawings survived
node scripts/verify-improvements.mjs <dir>   # A5; needs 1D + 1W scans; writes and removes test drawings
node scripts/verify-improvements.mjs <dir> --after-restart
```

- Without network access (cloud sessions): `backend\.venv\Scripts\python.exe scripts\synthetic_demo.py --port 8031 --data-dir <dir>` serves SYNTHETIC data (16 symbols incl. VCP4T four-contraction VCP, SETUP10 about to break out, IPONEW / IPOBASE listings, MULTI10 / MULTI2 multi-year breakouts) with 1W/1D/1H scans, and `npm run build; npx vite preview --port 5191 --strictPort` serves the UI. verify-advanced (53/53, restart 3/3) and verify-improvements (47/47, restart 4/4) pass there; the older suites expect live data: on the demo verify-ui 27/28, e1 36/37 (`E1_SYMBOL=RWALK E1_MIN_UNIVERSE=5`), e2 21/25, e3 12/14 - every miss is a data gap (no bullish flag, 11 vs 139 indices, no intraday index mapping, no RS divergence).
- Browser for the UI scripts: Edge/Chrome channel, or `PW_EXECUTABLE=/path/to/chromium`.

- `verify-p3.mjs` **writes labels and evaluation runs into the live DB**. Don't run it casually; if you do, delete its test labels afterwards.
- For anything that affects charts, take screenshots with `playwright-core` (`channel: "msedge"`) and actually look at them. The user expects visual confirmation, not just passing tests.
- Trigger a scan with `curl -X POST http://127.0.0.1:8031/api/scan/refresh -H "Content-Type: application/json" -d '{"universe":"all","timeframe":"1d"}'`. Check its status at `GET /api/scan/status/<scan_id>`.

## Environment gotchas

- **Windows 11.** The PowerShell tool is 5.1, which has no `&&`; use `; if ($?) {}`. Git Bash is also available.
- Paths contain spaces (`D:\Learning Material\...`), so always quote them.
- Frontend sources use **CRLF** line endings. Scripted replacements must match `\r\n`.
- Ports are fixed: 8031 for the API and 5191 for the UI (strictPort).
- NSE APIs need browser headers and cookies from a warm-up page (`indices/data.py`, `data/universe.py`). They occasionally return an empty answer for a range that has data, so backward index backfill stops only after 2 consecutive empty years.
- Yahoo:
  - Hourly history is capped at ~730 days.
  - The 09:15 hourly bar has zero volume, which is stored as missing.
  - Pre-2006 daily data has stale flat prints; the cleaner drops them (>20% jump, zero volume, no range).
- The scheduler runs scans only when a new completed candle exists. Index refresh runs weekdays at 16:15 IST.
- Claude Code cloud sessions: the default network policy blocks `query1.finance.yahoo.com` and `www.nseindia.com` / `nsearchives.nseindia.com`; use the synthetic demo or allow those hosts in the environment's network settings.

## Next work (candidates; confirm priorities with the user)

1. **Label charts and run an evaluation.** Precision and recall are unknown. Use the Explorer label panel and the Labels & evaluation page. Tuning period: ≤ 2022. Validation period: ≥ 2023. Aim for at least 10 valid labels per pattern and timeframe, plus negatives.
2. **Validate the 1.3.0 thresholds.** They were set by eye: 0.33, 0.2, 0.25, 0.15 and 0.5 ATR/bar. Use Compare/Sweep (`docs/validation.md`), one parameter at a time.
3. **Spec decisions needed from the user:**
   - The horizontal channel rule `|M| < 0.6 W` admits visibly sloped lines.
   - A boundary can rest on two isolated wicks (example: MAHABANK).
   - The double top/bottom rule "no bar between P1 and P2 above min(P1, P2)" is applied literally.
4. **Quality weights are placeholders.** Calibrate them after labels exist.
5. **Hourly history beyond ~730 days** needs a paid or broker data source (for example a broker historical-data API). The provider interface is `providers/base.py`.
6. **Chart payload is 1 to 2 MB** per full-history chart, with a ~4 to 6 s load. If it feels slow, add gzip or lazy-load older candles.
7. Tracks start with P3 and have no back-fill of earlier scans.
8. **Run the first live 1D + 1W scans with A1-A5** and look at the new scanners, the VCP v2 structure, IPO bases, About to Break Out and the Confluence Dashboard on real charts (all A1-A5 UI checks so far ran on synthetic data). The first live universe refresh also loads the NSE listing dates (EQUITY_L) the IPO base scanner needs.
9. **Validate VCP v2, the IPO base and multi-year breakouts on labelled real charts** (thresholds are Minervini / O'Neil principles + engineering defaults; `docs/vcp-methodology.md`, `docs/ipo-base.md`). No accuracy may be claimed before that.
10. **Confluence weights, bands, strength mappings and freshness windows are placeholders**; calibrate after labelled outcomes exist (`docs/confluence-engine.md`). The industry -> sector index map is an ASSUMPTION.

## Docs index

- `README.md`: setup, usage and troubleshooting.
- `docs/pattern-rules.md`: every pattern's gates and the geometry contract.
- `docs/pattern-tuning-1.3.0.md`: sources and screenshots behind config 1.3.0.
- `docs/data-sources.md`: Yahoo/NSE, price basis, cache, history depth and hygiene.
- `docs/validation.md`: labelling and evaluation workflow.
- `docs/implementation-notes.md`: decisions per phase.
- `docs/indicators.md`, `docs/marubozu-breakout.md`, `docs/charting-tools.md`: E1.
- `docs/index-universe.md`, `docs/rrg.md`: E2.
- `docs/stage-analysis-implementation.md`: E3.
- `docs/advanced-features-audit.md`: A1-A4 audit and decisions.
- `docs/first-time-breakout.md`, `docs/volume-shocker.md`, `docs/52-week-breakouts.md`: A1.
- `docs/vcp-methodology.md`: A2.
- `docs/chart-preview.md`, `docs/multi-month-timeframes.md`, `docs/fibonacci-tools.md`, `docs/chart-drawing-persistence.md`: A3.
- `docs/confluence-engine.md`: A4.
- `docs/about-to-breakout.md`, `docs/ipo-base.md`, `docs/multi-year-breakouts.md`, `docs/vcp-methodology.md` (v2), plus the A5 sections of `docs/charting-tools.md` (line chart), `docs/multi-month-timeframes.md` (running candle), `docs/chart-drawing-persistence.md` (users, text notes): A5.
