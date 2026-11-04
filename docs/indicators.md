# Technical indicators (E1)

EMA, RSI, ATR, EMA alignment/crossovers and regular RSI divergence. Backend code: `backend/app/indicators/technical.py`, `backend/app/indicators/divergence.py`, `backend/app/indicators/core.py` (ATR, swing points), orchestration in `backend/app/technical.py`. Configuration: `TechnicalConfig` in `backend/app/config.py` (version 1.0.0, hash `c555a7506d34d674`), separate from the pattern `DetectionConfig` so indicator settings never change pattern detection IDs.

**The backend is the only place indicators are computed.** The chart draws the arrays returned by `/api/indicators`; scanners read snapshots computed by the same functions during a scan. Every value at bar *i* depends only on bars 0..*i* (tested), uses completed candles only, raw OHLC (`docs/data-sources.md`), and missing values are `null` - never zero.

## Formulas (TradingView-compatible)

| Indicator | Formula | Warm-up |
|---|---|---|
| EMA(n) | alpha = 2/(n+1); first value = SMA of the first n closes (`ta.ema`); then `EMA[i] = alpha*close[i] + (1-alpha)*EMA[i-1]` | NaN for the first n-1 bars |
| RMA(n) (Wilder) | alpha = 1/n, seeded with the SMA of the first n values (`ta.rma`) | n-1 bars |
| RSI(n) | `100 - 100/(1 + RMA(gains)/RMA(losses))` on close-to-close changes (`ta.rsi`). No losses -> 100, no gains -> 0, neither (flat) -> **50** (the formula is undefined; documented choice) | first value at bar n |
| ATR(n) | `RMA(TR)`, TR[0] = high-low, TR[i] = max(H-L, |H-C[i-1]|, |L-C[i-1]|) (`ta.atr`). **One ATR for every module** (patterns, breakouts, Marubozu, divergence) | first value at bar n-1 |
| SMA(n) | mean of the last n values; missing if any value in the window is missing (`ta.sma`) | n-1 bars |

A missing input value makes the EMA/RMA missing and the recursion restarts from the next SMA of n valid values (Pine's `na(sum[1])` behaviour). The pandas form `ewm(span=n, adjust=False)` (first value = first input) is available as `ema_pandas` for sources that prescribe it (Stage Analysis, E3).

EMA memory is infinite, so values depend on the history start: daily history is 5 years and weekly history is the provider's full history to keep the warm-up effect negligible for EMA 200 (TradingView uses the full listing history; small differences can remain for recently listed stocks).

Reference-data tests (`tests/test_indicators_e1.py`, self-test): RSI(14) on the StockCharts 33-close sample matches all 19 published values exactly; EMA(10) on the StockCharts sample matches all 21 published values to the rounding (0.01); ATR on a hand-computed example.

## EMA alignment and crossovers

* Periods: 10, 20, 30, 50, 100, 200 (configurable `ema_periods`).
* **Bullish full**: EMA10 > EMA20 > EMA30 > EMA50 > EMA100 > EMA200 (strict). **Bearish full**: the reverse.
* **Partial** (engineering definition, configurable `ema_partial_alignment`): EMA20 > EMA50 > EMA200 (bullish) / < (bearish) without full alignment. **Mixed** otherwise. **Not available** when an EMA lacks history (never inferred from fewer EMAs).
* Price vs EMA 20 / 50 / 200: above / below / at on the latest completed close.
* Crossovers (`ema_cross_pairs` 20/50 and 50/200): bullish when `fast[i] > slow[i]` and `fast[i-1] <= slow[i-1]` (`ta.crossover`), bearish mirrored, on completed bars; scans keep crossovers of the last 5 bars.

These describe the current order of moving averages. They are not signals and carry no guarantee.

## RSI divergence (regular)

1. Pivots: confirmed swing points with k = 5 (`div_swing_k`): a swing low is lower than the previous 5 lows and not higher than the next 5; it is confirmed only after 5 later completed bars.
2. Compare the **latest two** pivots of one side (lows for bullish, highs for bearish); RSI is read at those **price pivot bars**.
3. Bullish: `low[P1] - low[P2] >= 0.1 x ATR[P2]` (lower low) and `RSI[P2] - RSI[P1] >= 2` (higher low). Bearish mirrors with highs.
4. Separation between pivots: 5 to 60 bars (`div_min_pivot_separation`, `div_max_pivot_separation`; the 60-bar maximum is an initial engineering default).

| Status | Rule |
|---|---|
| Developing | P2 is a provisional pivot (1 to 4 later bars; left-side swing rule and not exceeded since). Never shown as confirmed. |
| Confirmed | Both pivots confirmed; confirmation time = bar P2 + k. |
| Invalidated | After P2, a completed close below low[P2] (bullish) / above high[P2] (bearish) by `div_invalidation_buffer_atr` x ATR (default 0) while the pair is still the latest one. |
| Superseded | A newer pivot of the same side confirmed (kept as history only). |

Every divergence lists its gates (separation, values available, price and RSI differences) with values. Hidden divergence is not implemented (by design, spec 6.3).

## API

| Endpoint | Returns |
|---|---|
| `GET /api/indicators?symbol&timeframe&asof&scan_id&start_t&ema=10,20&rsi_period&atr_period` | EMA/RSI/ATR/ATR% arrays aligned with the chart candles from `start_t`, latest values, per-indicator status (`ok`, `insufficient_history`, `not_ready`), parameters, config version/hash, price basis, data cutoff |
| `GET /api/technical?symbol&timeframe&asof&scan_id&start_t&confirm=1|2` | snapshot (alignment, RSI state, ATR%), Marubozu candles, divergences, EMA crossovers, daily N-day levels and (1D) breakouts |
| `GET /api/scan/indicators?universe&timeframe&asof&alignment&price_vs_ema200&cross&rsi_state&divergence&divergence_status&max_age` | Indicator Scanner rows from the latest scan |
| `GET /api/config/technical` | every technical parameter with description, unit, scope |

`scan_id` makes the indicator request use exactly the candles of the chart's scan (same data cutoff). Results are cached in-process by symbol, timeframe, basis, parameters, candle count, first/last candle time and closes, so a new or revised candle or another parameter never returns a stale value.

## Chart

EMA lines on the price pane (per-EMA visibility, period, colour, width, restore defaults; legend with values at the crosshair); RSI pane with 70/30 and 40/50/60 reference lines; ATR pane; divergence segments on both the price and RSI panes plus a marker at the confirmation bar. Nothing is enabled by default except candles and volume. See `docs/charting-tools.md`.

## Limitations

* Initial engineering defaults, not tuned (`div_max_pivot_separation`, partial-alignment set, flat-RSI = 50).
* Values can differ slightly from TradingView where the history start differs (EMA memory) or the provider's candles differ.
* Divergence uses the latest two pivots only; earlier pivot pairs are history.
