# Marubozu candles and N-day breakouts (E1)

Code: `backend/app/candlesticks/marubozu.py`, `backend/app/breakouts/nday.py`; scanning in `backend/app/technical.py` (run by every scan); API in `backend/app/api/technical.py`. Raw OHLC, completed candles only. Parameters live in `TechnicalConfig` (`docs/indicators.md`).

## Marubozu (Candlestick Scanner)

```
range = high - low                  body = |close - open|
upper_wick = high - max(open, close) lower_wick = min(open, close) - low
Bullish: close > open, body/range >= 0.90, upper_wick/range <= 0.05, lower_wick/range <= 0.05
Bearish: close < open, same ratios
```

* Zero-range candles and invalid OHLC (missing values, high below max(open, close), low above min(open, close)) are never classified.
* Metrics per candle: body %, upper and lower wick %, range, range / ATR(14), volume, volume ratio = volume / mean volume of the previous 20 completed candles (missing if more than half of them lack volume), candle time, timeframe.
* Volume is supporting information only, never a validity condition.
* Scans store Marubozu candles of the last 5 completed candles (`candle_scan_recent_bars`); the scanner defaults to the latest completed candle.

| Parameter | Default |
|---|---|
| `marubozu_min_body_ratio` | 0.90 |
| `marubozu_max_upper_wick_ratio` / `marubozu_max_lower_wick_ratio` | 0.05 |
| `marubozu_min_range` | 0 (strictly greater) |
| `candle_volume_lookback` | 20 |

Note: with both wick limits at 5 % the body is at least 90 % automatically; the body threshold matters when it is raised above 0.90 (tested).

## 10-day high breakout / low breakdown (Breakout Scanner)

Always on **completed daily candles**; the lookback counts trading candles, not calendar days.

```
ref_high[i] = max(high[i-10 .. i-1])     ref_low[i] = min(low[i-10 .. i-1])     (candle i excluded)
buffer      = 0.1 x ATR(14)[i]
Bullish breakout : close[i] > ref_high[i] + buffer
Bearish breakdown: close[i] < ref_low[i]  - buffer
```

Confirmation modes (both computed by every 1D scan; default 1):

| Mode | Rule | Statuses |
|---|---|---|
| 1 close | the first completed close beyond the level confirms it | confirmed (`first_of_run` marks the first close of a run) |
| 2 closes | level and buffer fixed at the first close beyond (bar f); confirmed if close[f+1] is also beyond | confirmed; **failed** if close[f+1] is back inside (false breakout); **pending** if f is the latest candle |

Metrics: previous 10-day high and low, level, buffer, close, distance, breakout % (`(close/level - 1) x 100`), distance / ATR (breakout strength), candle range / ATR, volume ratio vs the previous 20 daily candles, reference window dates, the S/R zone the move attacked (computed on candles before the breakout), confirmation status and mode, breakout candle time. `breakout_min_volume_ratio` (default 0 = off) flags events with `volume_ok`. Lookbacks are a list (`breakout_lookback_days = (10,)`): adding 20 or 50 days needs no code change.

**Intraday charts never compute a "10-bar" breakout.** On 1H/4H the chart overlays the daily levels from the last 10 completed daily candles as of the chart's cutoff (labelled *daily-derived*, with the reference date range); breakout markers are drawn on the daily chart.

Scans store breakout events confirmed (or failed/pending) within the last 5 daily candles; the scanner defaults to the latest completed daily candle. Results are rule-based levels, not trade recommendations.

## API

| Endpoint | Filters |
|---|---|
| `GET /api/scan/candlesticks` | `universe`, `timeframe`, `asof`, `kind` (bullish_marubozu / bearish_marubozu), `min_range_atr`, `min_volume_ratio`, `max_age` |
| `GET /api/scan/breakouts` | `universe` (nifty500 / fno / non_fno / all), `asof`, `kind` (breakout_high_10d / breakdown_low_10d), `confirm` (1 / 2), `status` (confirmed / failed / pending / all), `min_volume_ratio`, `min_atr` (strength), `max_age`, `first_of_run` |
| `GET /api/technical` | per-symbol Marubozu candles, daily levels, breakouts (`confirm`) for the chart |

Both scanners read the latest completed scan of the universe/timeframe/as-of (the same scan as the pattern scanner, so candles and cutoff are identical); "Run scan" on the page queues one. Scans from before E1 have no technical results and say so.

## Tests

`tests/test_candles_breakouts.py`: bullish/bearish Marubozu, large body with excessive wick, body threshold, zero range, missing and invalid OHLC, incomplete candle exclusion, metrics, volume never guessed; true 10-day breakout and breakdown, current candle excluded, buffer, one- and two-close confirmation, false breakout, pending, insufficient history, trading candles vs calendar days, as-of results unchanged by later candles, daily reference levels. `tests/test_api_e1.py`: scanner filters, universe filters, confirmation modes, 1H chart levels = daily levels (not 10 hourly bars). UI: `frontend/scripts/verify-e1.mjs`.

## Limitations

* Initial defaults, not tuned. A gap through the level counts like any close beyond it.
* Volume ratios inherit Yahoo's volume quirks (`docs/data-sources.md`).
* Historical F&O membership before the first snapshot is unknown; historical NIFTY 500 membership is not reconstructed.
