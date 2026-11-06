# 52-week high breakouts and low breakdowns

Breakouts above a resistance that held for **years** (5, 10, 15, 20 or any number) are a separate scanner with their own rule (the age of the resistance, not a fixed window): `docs/multi-year-breakouts.md`.

Code: `backend/app/signals/breakouts.py` (`week52_state`, events from the shared crossing engine in `crossing.py`), official reference `backend/app/signals/nse52w.py`; run by every daily scan; API `GET /api/scan/52week`, `GET /api/52week/reference`, `POST /api/52week/reference/refresh`; page **52-Week Breakouts**. Parameters: `SignalConfig` (`w52_*`).

## Definitions (completed daily candles)

```
previous_52w_high = max(high[t-252 .. t-1])     previous_52w_low = min(low[t-252 .. t-1])     (current candle excluded)
buffer            = 0.1 x ATR(14)[t]
completed-close breakout : close[t] > previous_52w_high + buffer
completed-close breakdown: close[t] < previous_52w_low  - buffer
intraday touch           : high[t] > previous_52w_high (low[t] < previous_52w_low), whatever the close - provisional
```

* Fewer than 252 completed sessions before the latest candle: status **INSUFFICIENT_HISTORY**; no 52-week level and no 52-week event is reported. The available-period high / low are shown separately in brackets.
* Events use the same fresh-crossing and de-duplication rules as the fresh 10-day breakout (`docs/first-time-breakout.md`): the previous candle judged on its own 252-session window, genuine crossing of the previous level, one event per episode, re-armed after 3 closes back inside. The *range view* additionally lists every stock's current 52-week high / low, the distance of the close from each and today's flags (close above / below, touched), so stocks sitting at a new high without a fresh crossing are still visible.
* One-close and two-consecutive-close confirmation like the 10-day scanner; the confirmation timestamp is the session close (15:30 IST) of the confirming candle.

## Price basis and corporate actions

| Level | Available here | Basis |
|---|---|---|
| App 52-week high / low | yes | provider OHLC **adjusted for splits and bonus issues** (`raw_split_adjusted`, the chart basis) |
| Close compared with it | yes | the same basis (the latest candle is unaffected by later adjustments) |
| NSE official adjusted 52-week high / low | when the daily report was downloaded | NSE, adjusted for corporate actions (bonus, splits, rights) |
| Actual unadjusted traded 52-week prices | **no** | not provided by Yahoo; NSE publishes them only in bhavcopies, which this app does not download |

An adjusted level is never compared with an unadjusted close, and the official values are never substituted into the calculation. When the official report for the candle's date is stored, each row shows `match` (within 0.5 %) or `mismatch` (both values in the tooltip - e.g. a rights issue the provider did not adjust) or `n/a`.

The official report (`CM_52_wk_High_low_DDMMYYYY.csv`) is downloaded by the existing weekday 08:30 universe job for the last completed session, or on demand (`POST /api/52week/reference/refresh`). The parser is tolerant of column order and quoting but rejects a file without SYMBOL and 52-week columns. **Not verified live in the build environment** (network policy blocked nseindia.com); the parser is tested against a sample in NSE's documented layout. If NSE changes the file, the status says `error` and the scanner shows `n/a` - nothing is invented.

## Output

Symbol, company, F&O, type, 52-week high / low, close, distance from the level, breakout %, ATR multiple, volume ratio, adjustment basis, official NSE check, confirmation timestamp, sessions since.

## Tests

`tests/test_week52.py` (9): 252-session high breakout and low breakdown, current candle excluded (a high 253 sessions back is outside, 252 inside), intraday touch vs confirmed close, INSUFFICIENT_HISTORY, basis reporting, as-of unchanged by later candles, NSE report parser (tolerant / strict), store / lookup / match / mismatch / unavailable / error. API views: `tests/test_api_signals.py`.
