# NSE index universe and index data (E2)

Code: `backend/app/indices/registry.py` (catalogue + classification), `backend/app/indices/data.py` (official index history, verification, weekly bars, intraday mapping), `backend/app/indices/analysis.py` (Index Analysis), API `backend/app/api/indices.py`. Configuration: `IndexConfig` (version 1.0.0, hash `a00ce2b234cd470b`) in `backend/app/config.py`.

## Index registry (spec 9.1)

* **Source of truth**: the official NSE index catalogue `https://www.nseindia.com/api/allIndices` (NSE Indices data published on nseindia.com). Refreshed on startup and on weekdays at 16:15 IST; `POST /api/indices/refresh` on demand.
* Verified on 4-Nov-2026: **139 indices** in six NSE groups - Broad market 18, Sectoral 21, Thematic 41, Strategy 42, Fixed income 11, plus 6 in "Indices eligible in derivatives".
* Stored per index (`index_registry`): official name, official symbol (`indexSymbol`, used as the key), short name, category, subcategory, NSE catalogue group, index type, return basis (PR), active flag, analysis flag, source URL, discovery / last-seen times, the official reference closes (previous day, 1 week, 30 days, 1 year ago), data provider and status (+ reason), verification result, last refresh, first/last date, rows, intraday provider/ticker/status/details.
* New indices appear automatically on the next refresh; indices that disappear are marked **inactive**, never deleted.

### Classification (deterministic, `classify()`)

| NSE group | Category |
|---|---|
| BROAD MARKET INDICES | broad |
| SECTORAL INDICES | sector |
| THEMATIC INDICES | thematic |
| STRATEGY INDICES | strategy (factor / strategy) |
| FIXED INCOME INDICES | fixed_income |
| INDICES ELIGIBLE IN DERIVATIVES | NIFTY BANK, NIFTY FIN SERVICE -> sector; NIFTY 50, NEXT 50, MIDCAP SELECT, INDIA FPI 150 -> broad |

Index type from the official name: VIX -> volatility; LEVERAGE -> leveraged; INVERSE -> inverse; DIVIDEND POINTS -> dividend_points; USD -> currency; fixed income; otherwise **equity**. Only equity price indices (121 of 139 on 4-Nov-2026) are analysed (returns, indicators, RRG); the others are listed with the reason.

Subcategories come from keywords in the official name: strategy -> momentum, quality, value, low_volatility, alpha (two or more factors or MULTIFACTOR -> multi_factor), equal_weight, dividend, high_beta, growth; broad -> largecap, midcap, smallcap, microcap, mid_small, large_mid, broad_multi, volatility; sector -> banking, financial_services, it, auto, fmcg, pharma, healthcare, metal, realty, oil_gas, media, consumer_durables, chemicals, cement; thematic -> consumption, infrastructure, manufacturing, public_sector, esg_shariah, digital, defence, tourism, corporate_group, commodities_energy, liquidity, other_theme. 4-Nov-2026 factor coverage: 9 multi-factor, 6 quality, 6 equal weight, 3 each momentum / value / alpha / low volatility.

## Index data

| Timeframe | Source | Notes |
|---|---|---|
| 1D | **Official NSE index history** `https://www.nseindia.com/api/historicalOR/indicesHistory?indexType=<official name>&from&to&csv=true` | One request per calendar year (the API returns at most one year; 70 rows without `csv=true`). First download: the last 10 years, then one year at a time further back until two consecutive years are empty (before the index existed; a single empty answer can be a transient NSE glitch) or 1990 (`index_history_floor_year`; NIFTY 50 history starts in July 1990, NIFTY BANK in 2000). Caches made before this backfill (`market_data.history_period` = `10y`) are extended back the same way on the next index refresh. Then incremental with a 5-day overlap; a revised overlap triggers a full re-download. Years before ~2000 publish only the close for some indices: those days become flat close-only candles (open = high = low = close), never dropped. Cookies from the NSE historical-data page are required. |
| 1W | aggregated from completed official daily candles | Monday-labelled NSE weeks (09:15 IST), O = first, H = max, L = min, C = last, volume = sum; a week is used only after its last trading day closed. |
| 1H / 4H | Yahoo Finance, **only verified mappings** | Candidate tickers are configured explicitly in `backend/data/index_intraday_candidates.json`. A candidate is used only if its daily closes match the official reference closes (previous day, 1 week, 30 days, 1 year ago) within 0.3 %. Otherwise the index has no intraday data and the chart says so - nothing is substituted (no ETF, stock or similar index). 4H is built from 1H like for stocks. |

* **One basis for every index**: official price-return (PR) index levels. Price indices have no corporate-action adjustment; total-return (TRI) series are not available from the implemented providers, so RRG and returns are price-return and labelled as such.
* **Verification of the official data itself**: after each download the cached closes are compared with the official reference closes of the catalogue; a mismatch sets `data_status = error` with the differences.
* Volume = constituents' traded quantity where NSE publishes it (missing for some indices, never filled).
* Some official records carry 0 for open/high/low on days without an intraday range; those fields are set to the close (no range is invented).

## Index Analysis (spec 9.3)

`GET /api/indices/overview?timeframe=1d|1w&benchmark=NIFTY 500|NIFTY 50&asof=`

| Field | Definition |
|---|---|
| Latest close | last completed official daily close |
| 1W / 1M / 3M / 6M / 1Y return | `close / close_base - 1`, base = last trading day on or before (latest date - 7 days / 1 / 3 / 6 / 12 months); `null` when the index is younger |
| RSI, ATR % | RSI(14), ATR(14)/close on the selected timeframe (same functions as stocks) |
| EMA 50 / EMA 200 | close above / below on the selected timeframe |
| RS vs benchmark | `(1 + r_index) / (1 + r_benchmark) - 1` over 3 months, same dates |
| RRG quadrant | from the weekly RRG (`docs/rrg.md`) |
| Stage | Stage Analysis (E3) |

Filters: category, subcategory, availability, relative strength (outperforming / underperforming), trend (EMA 200), stage, timeframe, benchmark, search. Every row opens the Index Explorer.

## Index Explorer (spec 9.4)

`/index/<official symbol>`: candlesticks (official data), EMA 10-200, RSI, ATR, volume, S/R zones, daily 10-day levels, breakout markers (daily), drawing tools (stored under chart identity `index`), RRG position and trail, returns and relative performance vs the selected benchmark, registry and verification details, data-source panel. 1H/4H buttons are dimmed when no verified intraday mapping exists; requesting them returns `data_unavailable`.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/indices?category&subcategory&q&status` | registry list + counts per category + refresh job |
| `GET /api/indices/status` | registry counts, refresh job, IndexConfig parameters |
| `POST /api/indices/refresh` | catalogue -> official history -> intraday mapping checks (background) |
| `GET /api/indices/{symbol}` | one index with verification and intraday details |
| `GET /api/indices/overview` | Index Analysis table |
| `GET /api/index/chart?code&timeframe&asof` | Index Explorer candles, S/R zones, data source |
| `GET /api/indicators?...&instrument=index`, `GET /api/technical?...&instrument=index` | indicators / overlays for an index |
| `GET /api/symbols?q=` | common search for stocks and indices |

## Tests

`tests/test_indices_e2.py` (deterministic fixtures): classification rules, catalogue validation, inactive instead of delete, history parsing (timestamps, zero OHLC), year-split requests, reference verification (match and mismatch), full then incremental refresh, weekly aggregation and completed-week exclusion, unavailable intraday, intraday mapping accepted only when closes match, calendar returns and relative performance, registry/overview/chart/indicator/symbol APIs, unsupported index types (`not_applicable`).

## Limitations

* NSE APIs are not a documented public interface; field names or limits can change (the refresh records errors and keeps the last good data).
* Intraday index data exists only for verified Yahoo mappings; Yahoo is not exchange-authoritative.
* Historical constituents and index methodology changes are not modelled; the official series is used as published.
