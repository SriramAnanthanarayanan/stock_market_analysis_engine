# Volume Shocker

Code: `backend/app/signals/volume.py`; run by every scan (1H, 4H, 1D, 1W) on the scan's completed candles; API `GET /api/scan/volume-shocks`; page **Volume Shocker**. Parameters: `SignalConfig` (`vs_*`).

Unusually high volume relative to the stock's **own** history. Volume expansion can be bullish, bearish or neutral; the scanner never infers accumulation or distribution from volume alone.

## Calculation (completed candles; the current candle is always excluded from its own reference)

| Metric | Definition |
|---|---|
| reference (1D / 1W) | the previous 20 completed candles of the timeframe |
| reference (1H / 4H) | the **same time-of-day candle** of the previous 20 sessions (today's 10:15-11:15 hour vs the previous 10:15-11:15 hours) - a partial session is never compared with full-day volume |
| RVOL | volume / mean(reference) |
| RVOL (50) | volume / mean(previous 50 references) |
| median, max | of the 20 references |
| 60-candle percentile | share of the previous 60 references below the current volume (ties count half) |
| P90 condition (optional) | volume >= 90th percentile of the previous 60 references |
| z-score | (volume - mean) / sample standard deviation of the 20 references |
| traded value | close x volume (rupees); average traded value over the 20 references |

Default shock: **RVOL >= 2.0**; selectable 1.5x / 2x / 3x / 5x (candles >= 1.5x are stored, status `shock` at the default threshold, `elevated` below it). These are screening thresholds, not validated predictions.

Missing volume is never guessed: a candle without volume is skipped and a reference needs at least 75 % known volumes (`vs_min_valid_frac`). Yahoo reports the 09:15 hourly bar without volume (stored as missing), so that hour never gets an intraday RVOL. A zero-volume candle is never a shock; zeros in a reference are real no-trade sessions and lower the average.

## Direction (ASSUMPTION thresholds, configurable)

| Class | Rule |
|---|---|
| Bullish | close > open, body >= 30 % of the range, close location `(close - low) / range` >= 0.5 |
| Bearish | close < open, body >= 30 %, close location <= 0.5 |
| Neutral | anything else: high volume without a meaningful directional close |

Context per shock: close location, body %, range / ATR, change vs the previous close, close vs EMA 20 / EMA 50 (the chart's EMAs), distance from the previous 20 candles' high / low.

## Liquidity filter (optional, on by default in the page)

Average traded value of the reference candles >= `vs_min_avg_traded_value` (ASSUMPTION, rupees per candle): 1D ₹1 Cr, 1W ₹5 Cr, 4H ₹40 L, 1H ₹15 L. It removes stocks where a small absolute volume makes RVOL misleading.

## Output and filters

Symbol, company, direction, volume, 20-candle average, RVOL, 60-candle percentile, traded value, range / ATR, price change, close location, date, F&O. Filters: direction, RVOL threshold, P90 condition, liquidity, timeframe, age (latest / last 3 / last 5 candles), F&O.

## Tests

`tests/test_volume_shocker.py` (13): 1.5x / 2x / 3x / 5x, current candle excluded, below-threshold not stored, bullish / bearish / neutral, median / max / percentile / z-score / RVOL(50) / traded value against numpy, optional percentile condition, missing volume, zero volume, liquidity metric, same-time-of-day intraday reference, incomplete daily candle excluded. API filters: `tests/test_api_signals.py`.
