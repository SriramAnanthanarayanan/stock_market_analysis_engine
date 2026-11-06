# Fresh 10-day breakouts ("first-time" breakouts)

Code: `backend/app/signals/crossing.py` (crossing engine, shared with the 52-week scanner), `backend/app/signals/breakouts.py` (event records); run by every daily scan (`ScanManager._store_signals`); API `GET /api/scan/fresh-breakouts`; page **Fresh Breakouts**. Parameters: `SignalConfig` (`fresh_*`, `GET /api/config/signals`). The E1 Breakout Scanner (every close beyond the level) is unchanged.

Goal: report a stock once, when it **crosses** its previous 10-session high / low, not every day it stays above it.

The state **before** the crossing (Approaching / Very Near / Touched, then Breakout / Confirmed Breakout / Rejected / Expired) is documented in `docs/about-to-breakout.md`; it uses the same level and crossing engine and does not change this rule.

## Definitions (completed daily candles; trading sessions, not calendar days)

```
ref_high[t] = max(high[t-10 .. t-1])       ref_low[t] = min(low[t-10 .. t-1])     (candle t excluded)
buffer[t]   = 0.1 x ATR(14)[t]
breakout state at t : close[t] > ref_high[t] + buffer[t]          (breakdown: close[t] < ref_low[t] - buffer[t])
intraday touch at t : high[t] > ref_high[t] + 0.0 x ATR[t]        (provisional, whatever the close)
```

**Fresh crossing** at t (all must hold):

1. breakout state at t;
2. the previous candle was **not** in breakout state, judged on **its own** window: `close[t-1] <= ref_high[t-1] + buffer[t-1]`;
3. *genuine crossing* (`fresh_require_prior_level_clear`, default on): `close[t] > ref_high[t-1] + buffer[t]`. When the highest high of the window has just rolled out, `ref_high` drops; a close above that lowered level which did not clear yesterday's level did not cross the resistance that was in force (test `test_level_that_dropped_when_an_old_high_rolled_out_is_not_a_crossing`).

"The previous close was not already above the same level" is implied: the previous candle's own high is inside `ref_high[t]`'s window, so `close[t-1] <= ref_high[t]` always.

## Events, episodes and re-arming

A deterministic walk over all completed candles (recomputed every scan, no stored state, so an as-of scan reproduces what was known then):

| State | What happens |
|---|---|
| armed | a fresh crossing creates an **event** (event ID = sha1(symbol, kind, candle time, config hash)) and opens an **episode** at the broken level |
| episode | further crossings are **suppressed** (counted as `suppressed_crossings`) |
| re-arm (default) | `fresh_rearm_closes` = **3 consecutive completed closes back inside the prior reference range** (at or below the broken high / at or above the broken low) -> armed (`episode_state = reset`) |
| new range | `fresh_rearm_on_new_range`: a fresh crossing of a reference window that starts **after** the event candle (a new 10-session range formed above the old level, e.g. a base-on-base) creates a new event (`trigger = new_range`, the old episode becomes `superseded`) |
| blocked start | if the first computable candle is already in breakout state no crossing was seen; the walk arms after 3 closes inside |

## Filters

* **Filter A - Fresh Crossing** (default): the event candle is the latest completed candle (`max_age = 0`). "Sessions since breakout" widens it (up to 60; `fresh_store_sessions`).
* **Filter B - First Breakout in Lookback** (`first_only`, N = `fresh_first_lookback_sessions` = 20, changeable per request): no close in breakout state of the same type (on its own window) in the preceding N completed sessions. When the history before the event is shorter than N the answer is *n/a* (`None`), never "yes". It is **not** "first ever": the app does not claim a first breakout in the stock's whole history.
* Active episodes only (hide events whose episode reset), direction, minimum volume ratio, minimum strength (ATR), F&O.

## Modes

| Mode | Rule | Status |
|---|---|---|
| Fresh daily close breakout (default) | the fresh crossing close | confirmed |
| One-close confirmation | same as above | confirmed |
| Two-consecutive-close confirmation | level + buffer fixed at the event candle; the next close must also be beyond | confirmed / **failed** (back inside) / **pending** (event is the latest candle) |
| Intraday touch | high above (low below) the level on a completed candle | **provisional touch**, with the close outcome (closed beyond / closed inside); never a confirmed breakout |

Live touches of the running session are not computed (daily candles are used only when complete).

## Output

Symbol, company, F&O, breakout type, previous 10-day high/low (the level), current (latest) close, breakout % = `(close / level - 1) x 100`, strength = `(close - level) / ATR(14)`, volume ratio (vs the previous 20 daily candles), first crossing date, sessions since, First in N, confirmation status, episode state, event ID, reference window dates, the candle that set the level. Chart preview: the level and a "Fresh BO" marker.

## Tests

`tests/test_fresh_breakout.py` (19): fresh crossing above / below, current candle excluded, previous bar on its own window, rolled-out high, no repeat events while above the level, re-arm after 3 closes, 2 closes do not re-arm, configurable re-arming, new-range re-arm, first in last 20 / custom N / insufficient history, intraday touch vs close, 2-close confirmed / failed / pending, insufficient history, blocked start, as-of unchanged by later candles, longer history does not change recent events, incomplete candles excluded. API: `tests/test_api_signals.py`.

## Limitations

* Thresholds are initial engineering defaults; precision of the "fresh" definition is not validated with labelled charts.
* A gap through the level counts like any close beyond it.
