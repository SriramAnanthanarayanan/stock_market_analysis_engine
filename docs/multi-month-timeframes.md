# 1 / 3 / 6 / 12-month candles and performance horizons

Code: `backend/app/data/periods.py`; used by `prepare_series` (`backend/app/data/ohlcv.py`), `/api/chart`, `/api/indicators`, `/api/technical`, `/api/index/chart`. Frontend: `frontend/src/lib/timeframes.ts`.

## Naming

Internal values `1mo`, `3mo`, `6mo`, `12mo` (never `1M`, which could be read as one minute); labels "1 Month", "3 Months", "6 Months", "12 Months". They are **chart timeframes**: the scan timeframes stay 1H, 4H, 1D, 1W.

## Aggregation (from completed daily candles; nothing is filled in)

| Timeframe | Period |
|---|---|
| 1 Month | calendar month |
| 3 Months | calendar quarter (Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec) |
| 6 Months | January-June, July-December |
| 12 Months | calendar year |

`open` = first daily open of the period, `high` = max high, `low` = min low, `close` = last daily close, `volume` = sum of daily volume (missing when no daily volume is known), `adj_close` = last. The candle is labelled with the first calendar day of the period at 09:15 IST. Missing provider sessions are simply absent (the candle reports how many sessions it contains).

**Completion**: a period is complete once its last NSE trading day has closed (15:30 IST) at the cutoff (NSE holiday calendar; uncovered years assume weekdays trade, which errs towards "not complete yet"). The running period is returned separately (`partial_candle`), built from the completed daily candles so far, drawn muted on the chart and labelled "incomplete period: N sessions to <date>". It is never used for indicators or any calculation.

## Which analysis runs where (`DETECTOR_TIMEFRAMES`)

| Analysis | 1H / 4H / 1D / 1W | Multi-month |
|---|---|---|
| 17 price-action patterns, S/R zones, Marubozu, RSI divergence, EMA crossovers | yes | **not run** (not validated) |
| Fresh breakouts, 52-week, VCP | daily only | not run |
| Volume Shocker | yes | not run |
| Stage Analysis | weekly only (source methodology) | not run |
| RS Line | yes | not run |
| EMA / RSI / ATR | yes | shown on completed candles, flagged "not validated on this timeframe" |

RRG is weekly by definition and is unchanged. Index charts support the multi-month timeframes from the official daily index history.

## Performance horizons (rolling returns, not candles)

`performance` in the chart payload: 1M, 3M, 6M, 12M returns = latest completed daily close / close of the last trading day on or before (latest date - N months) - 1, on the dividend-adjusted close when available (returns only; never chart prices), otherwise the raw close (stated). Too little history -> `insufficient_history`. Shown as a strip in the chart terminal and the preview.

## Tests

`tests/test_multimonth.py` (12): period boundaries, OHLCV aggregation rules, quarter / half-year / year partitions, missing sessions not filled and missing volume not guessed, incomplete current period split off and completed after the last trading day, performance horizons (prior trading day, adjusted basis, insufficient history), chart API runs no detectors on multi-month candles, `max_candles`, indicators / technical on multi-month, index chart on 12 months.
