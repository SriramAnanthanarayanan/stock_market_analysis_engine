# Multi-year breakouts

Code: `backend/app/signals/multiyear.py` (detector), run by every **daily** scan (`run_signals` -> `Publisher.multiyear`) and stored as `signal_evidence` (source `multiyear`); API `backend/app/api/multiyear.py`; page **Multi-Year Breakouts** (`frontend/src/pages/MultiYearBreakouts.tsx`); chart chip **Multi-year** in the chart terminal. Parameters: `SignalConfig` **1.2.0** (`multiyear_*`, `GET /api/config/signals`). Synthetic fixtures: `backend/app/synthetic_multiyear.py`.

A **multi-year breakout** is a completed daily close above a resistance the stock had not exceeded for years. The number of years is yours to choose - 5, 10, 15, 20 or any other number: every breakout whose base lasted at least `multiyear_min_years` (1 year) is stored with its length, and the scanner filters by the number you enter.

Why it is in the app: the user's experience is that a stock breaking out after many years often reflects a real change in the business and has delivered exceptional returns. The app does **not** assume this. It finds the breakouts with a fixed rule and shows, for the past ones, what actually happened next (History tab) - descriptive statistics with their caveats, not a promise.

## Definition (completed daily candles; nothing after the candle is used)

For the candle `t` with close `C`:

| Term | Rule |
|---|---|
| last time at this price | `j` = the most recent earlier candle whose high reached `C`; none = an all-time high within the available data |
| resistance (`level`) | the highest high of the candles after `j` up to `t-1` - the price the stock has not exceeded since it was last at `C` |
| breakout | `C > level + multiyear_buffer_atr x ATR(14)` (buffer 0 by default) |
| base start | the first candle after `j` whose high came within `multiyear_zone_pct` (3 %, ASSUMPTION) of the level |
| **years** (`base_years`) | base start -> breakout candle, in calendar years: how long the stock stayed capped below this resistance |
| exact-high age (`level_years`) | the candle that set the level -> breakout (shown; with `multiyear_zone_pct` = 0 the years equal this) |

Why a zone: a long base often has a marginal poke above the old high (say 1 %) years later. Measured from the exact high, that poke would restart the clock; measured from the zone, the stock is (correctly) seen as capped since the first visit to that price area.

Two kinds of breakout are covered:

* **All-time high (within the data)**: the close is above every high since the data starts (daily from about 1996 or the listing). The base may be longer than the data shows (flagged when the base starts at the first candle).
* **Below an older peak**: the stock traded higher long ago (for example a 2000 or 2008 peak), but the close is the highest price since then - "highest since 2008". The level is the highest high since the stock was last at this price.

### One event per base, failure and re-break

* Further closes above the same base (continuation) are not new events.
* **Failed**: `multiyear_fail_closes` (5, ASSUMPTION) consecutive completed closes back below the broken level.
* After a failure, the next close above the base - including the highs of the failed attempt, which stay part of the original base - is a **re-break** event (flagged, linked to the failed one).

### Measured for every breakout

Resistance and the date it was set, base start and years, exact-high age, all-time-high flag or "highest since" date and years, resistance tests (separate visits to the zone at least `multiyear_test_separation_sessions` = 20 sessions apart, the old high included), base depth (% from the level to the lowest low of the base), breakout % and strength in ATR, breakout volume / mean of the previous 50 sessions, re-break flag. At the latest candle: status (Holding above / Back below level / Failed), latest close vs the level, return since the breakout, best and worst move since.

## Scanner page

* **Years**: chips 2 / 3 / 5 / 10 / 15 / 20 and a number field for any value (stored from 1 year). The years are "at least": 5+ includes a 12-year base.
* **Recent breakouts** (default: last 20 sessions, holding or back below; also latest session / 5 / 60 / 250 sessions, failed, all): years, resistance, all-time-high or "since <year>", breakout date and sessions, breakout %, volume, tests, base depth, now vs level, return since, status, earlier breakouts of the same stock.
* A symbol opens the chart preview on the **weekly** chart (base up to 10 years) or the **monthly** chart (longer bases), so the whole base is visible: the resistance line from the base start to the breakout, the breakout marker and the stock's earlier multi-year breakouts.
* **History - what happened next**: every stored past breakout of the universe with at least the chosen years, with price returns 1, 3, 6, 12, 24 and 36 months after the breakout close, the return of the benchmark (NIFTY 50) over the same calendar window, the excess return, the best / worst move within 12 months, and a summary per horizon (events, median, mean, share positive, median vs NIFTY 50, share beating it, windows still too recent).

The **Multi-year** chip in the chart terminal draws every multi-year breakout of the stock's history on 1D, 1W and the multi-month charts (daily times are placed on the bar containing them).

## Event study: read this before the numbers

The History numbers are **descriptive**, not a success rate and not a probability:

* **Survivorship bias**: the universe is today's NIFTY 500 / F&O list. Stocks that broke out years ago and then collapsed or were delisted are missing, so the results look better than a real-time screen would have been.
* Breakouts cluster in bull markets, so events are not independent; a few market phases dominate.
* Returns are price returns on the split / bonus-adjusted close (no dividends), compared with the NIFTY 50 price index (no dividends) over the same calendar window.
* Windows that end after the latest candle are "too recent" and never extrapolated.
* Data starts with the provider (daily from about 1996): an all-time high "in data" may not be one before that.

## Confluence

A fresh multi-year breakout (`ConfluenceConfig` 1.2.0: fresh for 10 sessions, `freshness_sessions['multiyear']`) is trigger evidence in the **same family as the 52-week breakout** - the same move is never counted twice; the stronger one counts. Strength rises from 0.75 (1-year base) to 1.0 (10+ years) (`multiyear_strength_years`, `multiyear_min_strength`, ASSUMPTION). Bullish only. Screen **Multi-year Breakout (5+ years)** (`multiyear_screen_years`); the dashboard's "52W / multi-yr" column shows the years.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/scan/multiyear-breakouts?min_years=10&max_age=20&status=holding,below_level,failed&all_time_high=&min_volume_ratio=&fno=&universe=&asof=` | recent breakouts of the latest daily scan (stored evidence) with each stock's earlier breakouts |
| `GET /api/multiyear/symbol?symbol=&asof=` | every multi-year breakout of one stock (stored history row, else computed on demand and not stored) |
| `GET /api/multiyear/study?min_years=&max_years=&all_time_high=&fno=&universe=&asof=&limit=` | the event study: per-horizon summary, events with forward returns, caveats |

A daily scan run before SignalConfig 1.2.0 has no multi-year evidence: the endpoints say so (after an upgrade the scheduler rescans because the configuration hash changed).

## Parameters (`SignalConfig` 1.2.0, hash `3f732a9d8a9675bd`)

| Parameter | Default | Meaning |
|---|---|---|
| `multiyear_min_years` | 1.0 | shortest base stored (the scanner filters by any larger number) |
| `multiyear_zone_pct` | 3.0 | ASSUMPTION: resistance zone that starts the base (0 = exact high) |
| `multiyear_buffer_atr` | 0.0 | close must clear the level by this x ATR(14) |
| `multiyear_fail_closes` | 5 | ASSUMPTION: consecutive closes back below the level -> Failed |
| `multiyear_volume_lookback` | 50 | breakout volume reference |
| `multiyear_test_separation_sessions` | 20 | ASSUMPTION: separate resistance tests |
| `multiyear_store_sessions` | 250 | breakouts of the last N sessions are stored as events; all older ones stay in the stock's history row |
| `multiyear_horizons_months` | 1, 3, 6, 12, 24, 36 | event-study horizons |
| `multiyear_benchmark` | NIFTY 50 | event-study benchmark (official index history) |

## Tests

`tests/test_multiyear.py` (16): ~10-year base breakout (level, years, tests, volume, status), any number of years, an uptrend's new highs are not multi-year breakouts, continuation is one event, failed breakout and re-break, a level below an older higher peak ("highest since"), resistance zone vs exact high, no look-ahead, buffer and missing volume, forward returns vs a benchmark and pending windows, stored recent events vs history, random walks and speed (30 years in well under a second); API: scanner with any years / window / status, per-symbol history and event study, evidence, confluence family + screen, and the note for scans from before the feature. `tests/test_confluence.py`: multi-year and 52-week breakouts are one family. Self-test: "Multi-year breakout". UI: `scripts/verify-improvements.mjs` (page, custom years, weekly preview with the resistance line, history tab, monthly chart chip, confluence screen).

## Limitations

* Engineering defaults; the zone, failure and test rules are ASSUMPTIONs. Not validated on labelled charts; built and UI-checked on synthetic data only (no Yahoo / NSE access in the build session).
* Provider history: Yahoo daily data from about 1996; early prints are cleaned (`docs/data-sources.md`) but a bad old high can still set a level - check the chart.
* Demergers, mergers and ticker changes can cut a stock's history short, which shortens its bases.
* The event study's survivorship bias (above) is structural: it can only be removed with point-in-time index membership, which the app does not have.
