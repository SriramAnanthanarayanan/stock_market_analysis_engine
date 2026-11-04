# Data sources

All sources were fetched and their schemas checked on 3-Nov-2026 before relying on them.

## NIFTY 500 constituents

* URL: `https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` (browser-like headers, `Referer: https://www.nseindia.com/`).
* Validated schema: `Company Name, Industry, Symbol, Series, ISIN Code`; 501 data rows on 3-Nov-2026 (series EQ 496, BE 2, RR 3). The list includes a placeholder `DUMMY*` symbol that has no market data.
* Accepted only if all columns exist, 400-600 non-empty rows and no duplicate symbols. Otherwise the previous list is kept and the error is logged in `universe_refresh_log` (visible in `/api/health`).
* Stored at `backend/data/nifty500.csv`, in `stocks`, and as a dated snapshot in `universe_snapshots` (one per IST day).
* No alternate Nifty Indices URL is used (none was validated).
* Yahoo tickers are `<SYMBOL>.NS` (symbols such as `M&M`, `BAJAJ-AUTO`, `GVT&D` are used verbatim).

## F&O membership

* URL: `https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv`.
* Format: header `UNDERLYING, SYMBOL, <expiry months...>`, index rows (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYFPI, NIFTYNXT50), a separator row `Derivatives on Individual Securities`, then one row per stock with lot sizes per expiry month.
* Definition used: a stock is **F&O = YES** in a snapshot when it has a lot size for the **nearest listed expiry month**. 218 stocks on 3-Nov-2026 (UJJIVANSFB is the only one outside the NIFTY 500). Stocks with lots only in later months are stored with their month list but are not YES.
* The market-lot file is a lot-size file, not an official membership register; this definition is an interpretation and is documented as such.
* Each refresh stores a dated snapshot. Historical queries use the latest snapshot on or before the date; **before the first snapshot the status is `UNKNOWN`** and the F&O / Non-F&O segments are empty rather than inferred. Membership is never inferred from volume, liquidity or market capitalisation.
* Stored at `backend/data/nse_fno.csv` and in `universe_snapshots`.

## NSE trading holidays

* API: `https://www.nseindia.com/api/holiday-master?type=trading`, capital-market segment `CM`. It returns only the **current year** (20 entries for 2026, including weekend dates and the 15-Jan-2026 municipal-election holiday).
* 2024 and 2025 are a bundled transcription of the NSE circulars in `backend/data/nse_holidays.json`, marked as such. Cross-check against Yahoo daily bars (RELIANCE, TCS, HDFCBANK, Nov-2024 → Nov-2026): every weekday without bars is a listed holiday; two listed holidays have bars - 21-Oct-2025 (Muhurat session, real volume) and 15-Jan-2026 (a flat zero-volume provider artefact); one Saturday (1-Feb-2025, budget session) has real bars.
* The current year is refreshed at startup when older than 7 days and on weekdays at 08:30 IST. For a year without a list, weekdays are assumed to trade, which errs towards treating a week as not yet complete.

## Market data: Yahoo Finance (yfinance)

Provider interface: `backend/app/providers/base.py`; detectors never call the provider.

| Timeframe | Download | Notes |
|---|---|---|
| 1H | `interval=1h`, `period=2y` (fallback `1y`) | Bars start 09:15, 10:15, ... 15:15 IST; the last is 15 minutes. Yahoo serves only ~730 days; the local cache keeps every older bar it already has (incremental refreshes only append), so 1H/4H history grows by itself from the first download on. |
| 4H | derived from cached 1H | Never downloaded separately. |
| 1D | `interval=1d`, `period=max` (fallback `10y`, `5y`) | Naive exchange dates → 09:15 IST bar start. Full history: from 1996 for the oldest NSE stocks (Yahoo's start), otherwise from listing. |
| 1W | `interval=1wk`, `period=max` (fallback `10y`) | Monday-labelled weeks → Monday 09:15 IST. Full history: weekly EMA 200 and the Stage Analysis indicators need it. |

Batch downloads of ~100 tickers (`group_by="ticker"`, `auto_adjust=False`, `threads=True`, `progress=False`), bounded retries with exponential backoff + jitter, each failed/empty symbol retried individually once, partial success allowed, per-symbol errors in `download_errors`. Symbols with no provider data are retried after a 6-hour cool-down. Provider-level failure is flagged only when every attempted download in a refresh fails (≥ 5 symbols).

### Price basis: raw vs adjusted (E1 audit)

| Field | Source | Basis | Used for |
|---|---|---|---|
| `open, high, low, close` | Yahoo `Open/High/Low/Close` with `auto_adjust=False` | **raw**: adjusted for splits and bonus issues only, *not* for dividends - the basis TradingView shows by default | candlestick charts, price-action patterns, S/R zones, Marubozu, N-day breakout levels, chart annotations, EMA/RSI/ATR (they are drawn on the same candles) |
| `adj_close` | Yahoo `Adj Close` | split **and** dividend adjusted | returns only: post-breakout outcome returns/MFE/MAE (factor `adj_close / close` per bar). Never used for levels or drawn on the chart. |
| `volume` | Yahoo `Volume` | split-adjusted share count | volume pane, volume ratios |

* **Why**: P1-P3 used `auto_adjust=True`, i.e. dividend-adjusted OHLC. Historical levels were therefore *below* TradingView's: ITC on 9-May-2024 closed at 409.34 raw but 370.45 adjusted (-9.5 %). That is the reported ~1,550 vs ~1,450 support/resistance mismatch.
* **Tagging**: every cache file has `market_data.price_basis` (`raw_split_adjusted`); scans, tracks and labels store the basis they were computed on. P1-P3 rows are `adjusted_legacy` (migration 4).
* **Never mixed**: analysis reads caches through `OhlcvStore.load_current`, which returns nothing for a cache on another basis (it is fully re-downloaded on the next refresh; if that fails the symbol is skipped, not analysed). Open P1-P3 tracks were closed as `data_changed` because their frozen geometry is on the old basis. A chart of a legacy scan says so in its notes.
* **Chart indicator**: the Explorer's *Data source* panel shows provider, price basis, the scan's basis and whether an adjusted close exists.
* **Intraday**: Yahoo's 1H `Adj Close` equals `Close` (no dividend adjustment intraday); 4H takes the last 1H value.
* **Indices**: price indices have no dividends, so raw and adjusted coincide (total-return series are a separate index, see `docs/index-universe.md`).
* Truly unadjusted (as-traded) prices before a split are not available from this provider.

### Cache strategy

* Candles: `backend/cache/ohlcv/<interval>/<SYMBOL>.csv` (ISO-8601 IST timestamps), metadata in `market_data` (first/last bar, rows, last fetch, status, error).
* Fresh: fetched after the latest completed bar end (live) or after the as-of cutoff (historical) → no download.
* Stale: incremental download starting at the 5th-last cached candle; overlapping candles are replaced by the new provider values; timestamps deduplicated.
* Re-adjustment: if any overlapping close (split) or adjusted close (dividend) differs by > 0.5 %, the provider re-adjusted history and the symbol is fully re-downloaded.
* Basis change: a cache on another price basis is fully re-downloaded (never merged).
* `full_refresh: true` forces a complete re-download.
* History depth: `market_data.history_period` records the period a cache was fully downloaded for. A usable cache with a different depth than the provider's preferred period (e.g. the 5-year daily caches made before 1D switched to `max`) is re-downloaded once on the next refresh (scan report `backfilled`) or when its chart is opened; a failed backfill keeps the old cache and retries later. A symbol whose full history Yahoo cannot serve keeps its fallback depth without being retried every scan (the fallback is in `market_data.notes`).
* Longer history does not change detections: patterns, S/R (last 250 bars) and indicator signals work on the recent bars. Checked on 148 NIFTY 500 stocks (5-year cache vs full history, 2026-11-04): 103 detections, identical. Detection time on ~7,700 daily bars: ~0.1 s per symbol.
* Charts send the whole cached history (`chart_max_candles` = 20,000) and still open on the latest bars or the selected formation; scroll or zoom out (or *Fit*) to see the rest. Stored scan geometry is moved onto the longer series by candle timestamp (older bars only shift the bar numbers).

### Hygiene (nothing is manufactured)

* Duplicate timestamps → keep the last provider value; NaN prices dropped; tiny float inconsistencies (≤ 0.01 %) between high/low and open/close repaired, larger ones dropped.
* Intraday bars outside 09:15-15:30 IST dropped; zero intraday volume stored as missing (Yahoo reports the 09:15 hour as 0).
* Holiday/weekend bars without volume are dropped as phantom bars; special sessions with volume are kept.
* Stale daily prints: a daily bar with no volume and no range (open = high = low = close) whose close is more than 20 % away from the previous kept close is dropped (a day without trades keeps the previous price). Compared only with earlier bars (no look-ahead). Live: 248 such bars in 68 stocks, all on or before 5-Dec-2005 (28-Jul-2005 appears in many symbols, e.g. RELIANCE at 218.31 amid ~110); none in the last 20 years.
* Missing sessions are never filled.

### Timezone and completion

* All session logic uses `Asia/Kolkata`; the machine's local time zone is never used. The API emits Unix seconds (true UTC instants) and ISO strings with `+05:30`; the chart formats both in IST via `Intl` with an explicit time zone.
* Bar end: 1H `start + 1h` capped at 15:30; 4H 13:15 or 15:30; 1D 15:30; 1W 15:30 on the last trading day of that Mon-Fri week. A bar is used only when its end ≤ the cutoff.
* As-of cutoff: end of the selected IST day (capped at now).

### Limitations

* Yahoo is not exchange-authoritative and may be delayed, incomplete or revised; the official NSE close appears after 15:30 and the daily bar may change shortly after (scheduler waits 30 minutes; the overlap refresh corrects it).
* Hourly history from Yahoo covers only ~730 days. The cache keeps older hourly bars, but a split/bonus re-adjustment re-downloads the symbol and the hourly history then restarts at Yahoo's 730-day window (older bars on the old basis are not merged: that would mean guessing the adjustment).
* Early Yahoo daily data (late 1990s) has more zero-volume days and gaps than recent years; such bars are cleaned as below, never filled.
* Split/bonus adjustment means historical as-of scans use today's split basis (a later split rescales earlier candles); dividends no longer shift historical prices.
* Historical NIFTY 500 membership is not reconstructed: as-of scans before the first snapshot use the earliest stored list and say so (survivorship bias).
* Muhurat sessions outside the regular window are excluded from intraday timeframes.
