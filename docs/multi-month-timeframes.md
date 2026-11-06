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

**Completion**: a period is complete once its last NSE trading day has closed (15:30 IST) at the cutoff (NSE holiday calendar; uncovered years assume weekdays trade, which errs towards "not complete yet"). Every candle in the chart payload carries `is_complete: true`.

## Running (incomplete) candle - 1W and 1 / 3 / 6 / 12 months

The current period is always shown, as `partial_candle` next to the completed candles (`running_period` / `partial_record` in `periods.py`; `running_candle` in `api/chart.py`, `index_running_candle` in `api/indices.py` for indices):

| Field | Rule |
|---|---|
| `t` | period label: Monday 09:15 IST for weeks (like every weekly candle), first calendar day 09:15 IST for months |
| `o` | open of the period's first completed daily candle |
| `h` / `l` | highest high / lowest low so far |
| `c` | latest completed daily close |
| `v` | sum of the known daily volumes so far (missing when none is known; never guessed) |
| `is_complete` | `false`; also `label: "INCOMPLETE"`, `sessions`, `first_session`, `last_session`, `period_end` |

It is built from **completed daily candles**, so its close and time are the latest daily close and session: the latest price is the same on 1D, 1W and the multi-month charts (`tests/test_partial_candles.py::test_chart_shows_running_candle_consistent_with_daily`, UI check "latest price / time consistent across 1D / 1W / 1M"). When the period has closed at the cutoff there is no running candle; the period is an ordinary complete candle. The weekly running candle needs the daily cache; a symbol without daily data shows none.

* **Chart**: drawn grey with reduced opacity and an "INCOMPLETE" marker; in line mode it is the last, grey point of the line. The chart legend reads "INCOMPLETE: N sessions to <last session>"; the chart preview lists the running period in its details.
* **Detection never sees it**: patterns, S/R zones, Marubozu, breakouts, Stage Analysis and every scanner run on completed candles only (`tests/test_partial_candles.py::test_detectors_never_see_the_partial_candle`, `test_weekly_chart_detections_use_completed_candles_only`).
* **Indicators**: by default EMA / RSI / ATR use completed candles only. The chart switch **Ind. incl. incomplete** (`include_partial=true` on `/api/technical`) also computes them on the running candle; those last values are provisional and marked (`partial_included`, `partial_t`).

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

`tests/test_partial_candles.py` (16): running week OHLCV aggregation (open = first day's open, high / low so far, close = latest, volume summed), missing volume never guessed and a holiday Monday, running 1 / 3 / 6 / 12-month periods, detectors never see the partial candle, chart running candle consistent with the daily chart, weekly detections on completed candles only, no partial candle once the period closed, indicators optionally including the partial candle, index chart running week and month.
