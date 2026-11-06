# Confluence engine and dashboard

Code: `backend/app/confluence/engine.py` (score), `backend/app/confluence/service.py` (snapshots, screens), `backend/app/api/confluence.py`; normalized evidence `backend/app/signals/evidence.py` (table `signal_evidence`); page **Confluence Dashboard** (`frontend/src/pages/ConfluenceDashboard.tsx`, `components/ConfluencePanel.tsx`). Parameters: `ConfluenceConfig` **1.1.0**, hash `5a8786a62bf4fbfc` (`GET /api/config/confluence`; 1.0.0 `b1b29d836e9d6c12` is the first release).

The dashboard ranks stocks supported by several **independent** kinds of evidence. It never detects anything: every scanner (patterns, Marubozu, fresh 10-day breakouts, 10-day about-to-break-out states, 52-week breakouts, Volume Shocker, VCP, IPO base, EMA alignment, Stage Analysis, RS Line, RRG, sector index strength) stores its own results as normalized evidence rows during the scan, and the confluence score reads those rows. The score is an explainable screening aid, **not a probability of success**; weights and bands are placeholders until labelled outcomes exist.

## Evidence (spec 8.2)

One row per signal and scan: evidence ID (stable for the same signal, candle and source config), symbol, timeframe, source scanner, signal type, category, kind (`event` / `state` / `formation`), direction, signal candle time and age in completed candles, detector status, lifecycle (`active` / `invalidated`), valid-until time + expiry rule, quality / strength, underlying detection or event ID, config version + hash, metrics and an explanation. `GET /api/evidence?symbol=&source=` lists them.

Inputs of a snapshot: the latest completed scan of the timeframe (1D by default; 1W also works) and the latest completed **weekly** scan as context (your Stage Analysis, RS Line, RRG are weekly). Without a weekly scan the trend and RS categories use what the daily scan has (EMA alignment) and RS is unavailable.

## Freshness (spec 8.3)

Events count only while fresh; older events are listed as **expired** with strength 0, invalidated ones as **invalidated** - neither is scored and neither appears as a current signal in the table.

| Evidence | Lifecycle | Fresh for (completed candles after the signal candle) |
|---|---|---|
| Fresh 10-day breakout | event | 5 |
| 52-week breakout | event | 5 |
| Volume shock | event | 3 |
| Marubozu | event (one candle) | 1 |
| Pattern breakout (broke above / below) | event | 5 |
| VCP pivot breakout | event | 5 |
| IPO base breakout | event | 5 |
| 10-day about to break out (Approaching / Very Near / Touched) | state of the latest candle | - |
| VCP, IPO base, price-action pattern | formation: active while structurally valid | - |
| EMA alignment, Stage Analysis, RS Line, RRG, sector index RRG | state: valid until the next scan | - |

A breakout whose episode was reset or whose two-close confirmation failed is `invalidated`. All windows are `ConfluenceConfig.freshness_sessions`.

## Score (spec 8.4)

`Confluence = min(100, 100 x sum(w_c x s_c) / sum(w_c) + combination points)`, sums over the **available** categories, each `s_c` in 0-1.

| Category | Weight | Evidence | Rule inside the category |
|---|---:|---|---|
| Trend quality | 20 | Stage Analysis class (weekly), EMA 10/20/30/50/100/200 alignment + price vs EMA 50/200 (EMA score = 0.7 x alignment + 0.3 x share of EMA 50/200 the close is beyond) | max(Stage, EMA) + 0.10 when both >= 0.6 (correlated: agreement bonus only) |
| Setup / structure | 25 | VCP (quality; forming x 0.6, pivot crossed on low volume x 0.8), IPO base (quality x state factor: Base Validated 0.8, Near Pivot / Breakout 1.0, Base Forming 0.5), price-action patterns (quality; forming x 0.6) | strongest structure + 0.15 x one structure of **another family**, cap 1 |
| Breakout / trigger | 20 | fresh 10-day breakout, 10-day about to break out (Approaching 0.35, Very Near 0.5, Touched 0.55; same family as the 10-day breakout), 52-week breakout, confirmed pattern breakout, VCP pivot breakout, IPO base breakout | strongest fresh trigger + 0.15 x one trigger of another type, cap 1 |
| Volume + candlestick | 25 | volume shock RVOL, breakout-candle volume ratio, VCP final-contraction dry-up, IPO base dry-up; Marubozu | 0.6 x volume strength (strongest measure + 0.15 x one of another family: volume expansion vs dry-up) + 0.4 x Marubozu strength |
| Relative strength | 10 | RS Line (rs_line.txt) vs NIFTY 50, RRG quadrant vs NIFTY 500, sector / thematic index RRG quadrant | max(RS Line, RRG) + 0.10 when both >= 0.6; + 0.15 x sector strength (independent); the sector alone is not scored |

Strengths (ASSUMPTION mappings, configurable): breakout = 0.5 at the level to 1.0 at 1.5 ATR beyond it (x 0.8 when it is not the first breakout in its lookback); volume = 0 at RVOL 1.5 to 1 at RVOL 4.0 (neutral-candle shocks x 0.5); Marubozu = 0.8 + 0.2 x (range / ATR - 1), capped; Stage 2 Confirmed 1.0, Early Stage 2 0.8, Stage 1 0.4, other 0; EMA bullish full 1.0, partial 0.7, mixed 0.2; RRG leading 1.0, improving 0.6, weakening 0.4, lagging 0; RS Line > 0: 0.6 + 0.2 if improving + 0.2 if its SMA rises; volume dry-up = 1 at <= 0.5x, 0 at >= 1.0x the reference volume (`dry_up_full`, `dry_up_zero`); sector index quadrant as RRG. The stock's sector index comes from its NIFTY 500 industry (`sector_index_map`, ASSUMPTION mapping, e.g. Information Technology -> NIFTY IT, Metals & Mining -> NIFTY METAL); an industry without a mapped index has no sector evidence. Bearish mirrors every rule with bearish evidence (Stage: below a falling EMA 30 = 1.0) and is scored and listed **separately** (direction toggle; screen 9).

### Combination bonus (spec 8.4, category 4)

Your key combination - **fresh breakout + bullish Marubozu + volume shock on the same completed session** - adds up to 10 points; fresh breakout + volume shock adds 5, fresh breakout + Marubozu 4, **Marubozu + volume shock** (no breakout needed) 3 (the best one applies). All parts must be on the **same candle**, at most 2 completed sessions old; a Marubozu from another session is never matched. The total stays <= 100. The breakdown shows which session earned it.

## No double counting (spec 8.5)

* The score is never "number of positive scanners x points": each category is normalized to 0-1 first.
* Inside a category the strongest evidence counts; **one** more piece of a different family adds a capped 0.15 x its strength; everything else is listed as *overlap - not added*. Three patterns of three families give `best + 0.15 x second`, not their sum.
* Correlated pairs: Stage vs EMA alignment and RS Line vs RRG get an agreement bonus only; a 10-day and a 52-week breakout of the same move are never two full triggers (`best + 0.15 x other`, cap 1); an about-to-break-out state and the 10-day breakout of the same level are one family (never both counted); a VCP pivot breakout and the 10-day breakout of the same candle are different families, so at most `best + 0.15 x other`; breakout-candle volume and the volume shock of the same candle are one measurement (only the stronger counts); the VCP and IPO base dry-up measures are one family (the stronger counts); sector strength only adds a capped bonus and never stands in for the stock's own RS.
* Duplicate rows (the same signal twice) change nothing (tested).
* The breakdown panel labels every piece of evidence: **counted**, **capped bonus**, **overlap - not added**, **expired**, **invalidated**.

## Coverage and ranking

A category without the data it needs is **unavailable**, not zero: no Stage / EMA state (trend), no weekly scan (RS), unknown volume on the latest candle (volume + candlestick). Coverage = available weight / total weight; the score is normalized over the available categories. A stock is **ranked** only when coverage >= `min_coverage` (0.6); unranked stocks are hidden unless "Include unranked" is ticked and are always marked.

## Bands (spec 8.6, presentation only)

85-100 Very High, 70-84 High, 55-69 Moderate, below 55 Low Confluence. Not calibrated success probabilities.

## Snapshots

A snapshot is computed once per input set - primary scan, weekly context scan, confluence config hash, timeframe - and stored (`confluence_runs`, `confluence_snapshots`, both directions of every stock with the full explanation). It is built automatically right after a 1D or 1W scan completes, on first use, or by **Recompute** (`POST /api/confluence/refresh`, re-reads the stored evidence; no scanner runs, no download). Opening the dashboard never re-runs a scanner; a request that arrives while a snapshot is being built waits for it instead of building a second one. A config change creates a new snapshot; old ones stay for reference.

## Dashboard (spec 8.7)

Columns: symbol, company, F&O, close, score (+ combination badge), band, coverage, active signals, VCP status, IPO base, 10-day breakout (or the about-to-break-out state and distance), 52-week breakout, Marubozu, volume shock, pattern, EMA alignment, Stage Analysis, RS / RRG (+ sector index quadrant), latest signal. Sort by any column (score by default). Filters: screen, direction, timeframe (1D / 1W), universe, as-of, Stage Analysis, VCP status, F&O, band, minimum score, fresh breakout, volume shock, include unranked, symbol / company search. Filters live in the URL. A row click opens the evidence breakdown; a symbol click opens the chart preview (levels of the breakouts, combination marker, VCP overlay) without leaving the page.

## Screens (spec 8.8 - analytical filters, not trade recommendations)

| Screen | Direction | Rule |
|---|---|---|
| Fresh Breakout + Volume Shock | bullish | combination `breakout_volume` or better (same session) |
| Fresh Breakout + Bullish Marubozu + Volume Shock | bullish | combination `breakout_marubozu_volume` |
| VCP + Near Pivot | bullish | VCP status Near Pivot |
| VCP Breakout + Volume Expansion | bullish | VCP status Breakout (close above the pivot on >= 1.5x the 50-day volume) |
| 52-week High + Strong Relative Strength | bullish | fresh 52-week breakout and RS category >= 0.6 |
| Stage 2 + EMA Bullish Alignment | bullish | Early Stage 2 / Stage 2 Confirmed and EMA alignment bullish (full or partial) |
| Price Action Pattern + Volume Expansion | bullish | active bullish pattern (setup >= 0.5) and volume strength > 0 |
| Multiple Independent Bullish Evidence | bullish | ranked and >= 3 categories scoring >= 0.5 |
| 10D About to Break Out + Bullish Trend | bullish | Approaching / Very Near / Touched the previous 10-day high and EMA alignment bullish |
| IPO Base Near Pivot / Breakout | bullish | IPO base status Near Pivot or Breakout |
| Bearish Breakdown Confluence | bearish | bearish trigger >= 0.5 and volume + candlestick >= 0.3 |

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/confluence?universe=&timeframe=1d\|1w&asof=&direction=&screen=&min_score=&band=&include_unranked=&fno=&stage=&vcp=&fresh_breakout=&volume_shock=&q=&limit=` | ranked rows of the current snapshot + snapshot / scan info, weights, bands |
| `GET /api/confluence/symbol?symbol=&timeframe=&asof=` | bullish and bearish rows of one stock |
| `POST /api/confluence/refresh` `{universe, timeframe, asof}` | recompute the snapshot from stored evidence |
| `GET /api/confluence/screens` | predefined screens |
| `GET /api/config/confluence` | parameters with descriptions (ASSUMPTION flags) |

## Example (offline fixture, `tests/fixtures_adv.py`)

RWALK's last daily candle closes above its VCP pivot as a bullish Marubozu on ~5x volume, which is also a fresh 10-day and 52-week breakout. Snapshot (ConfluenceConfig 1.1.0): trend 1.0 (EMA alignment counted; Stage "Other" listed as overlap), setup 0.90 (VCP Breakout, quality 90), trigger 1.0 (VCP pivot breakout counted, 10-day breakout as capped bonus, 52-week breakout as overlap), volume + candlestick 1.0 (shock RVOL 5.1 counted, breakout-candle volume as overlap, VCP volume dry-up 0.44x as capped bonus of another family; Marubozu counted), RS 1.0 (RRG leading counted, RS Line listed as overlap) -> base 97.5 + 10 combination points -> **100** (capped), Very High. The same stock's bearish score is listed separately.

## Limitations

* The industry -> sector index map is an ASSUMPTION (NIFTY 500 industry names to NSE sectoral / thematic indices); sector evidence needs the index's weekly history (none on the synthetic demo for most sectors).
* Weights, bands, strength mappings and freshness windows are engineering placeholders (flagged ASSUMPTION in `CONFLUENCE_PARAM_META`); calibrate them after labelled outcomes exist.
* Stage Analysis, RS Line and RRG come from the latest weekly scan; with an `asof` date the weekly scan must have the same `asof` to be used as context.
* A 1W snapshot has no VCP (daily-only detector) and uses weekly candles for every event.

## Tests

`backend/tests/test_confluence.py` (28; new in 1.1.0: about-to-break-out and the 10-day breakout never double count, IPO base setup + breakout trigger, volume dry-up vs expansion capped (best + one bonus; a second dry-up overlaps), sector strength a capped bonus and never scored alone; the Marubozu + volume combination is covered in the same-session combination test): full stack and 100 cap, duplicates and "count of scanners" rule, overlapping patterns of one family, Marubozu + volume same session, breakout + volume, same-session rule and max age, VCP near pivot / forming / developing, VCP breakout fresh and expired, 10-day + 52-week correlation, breakout volume vs shock, stale expiry, invalidated evidence, missing categories and coverage gate, trend / RS correlated pairs, bearish mirror, bands and configurable weights; API: snapshot stored after the scan and reused, ranking and explanation, screens and filters, bearish rows, symbol endpoint, recompute without scanners or downloads, concurrent requests build one snapshot, config change -> new snapshot, weekly snapshot, no scan. UI: `scripts/verify-advanced.mjs` (Confluence Dashboard section).
