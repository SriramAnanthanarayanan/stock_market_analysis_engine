# About to Break Out (10-day high)

Code: `backend/app/signals/breakouts.py` (`breakout_setups`, `setup_quality`; the same crossing engine as the fresh 10-day breakouts in `signals/crossing.py`), run by every daily scan and published as `signal_evidence` rows (source `fresh_breakout`, kind `state`); API `GET /api/scan/breakout-setups`; page **About to Break Out** (`frontend/src/pages/AboutToBreakout.tsx`). Parameters: `SignalConfig` **1.1.0**, hash `bb601be383bb6721` (`GET /api/config/signals`).

Goal: list stocks **before** the 10-day breakout - closing just below the previous 10-day high - and follow what happens next. The existing 10-day breakout rule (`docs/first-time-breakout.md`) is unchanged; this is a separate state of the latest completed daily candle against the same level.

## Distances (completed daily candles; current candle excluded from the level)

```
10D_high        = max(high of the previous 10 completed candles)
distance_pct    = (10D_high - close) / 10D_high x 100
distance_atr    = (10D_high - close) / ATR(14)
```

The bearish mirror uses the 10-day low (`(close - 10D_low) / 10D_low x 100`); the page shows bullish setups by default (direction: toward the 10D high / toward the 10D low / both).

## States

| State | Rule (initial defaults) |
|---|---|
| Approaching | close within **2.0 %** and **0.5 ATR** below the 10D high (`about_to_breakout_pct`, `about_to_breakout_atr`; `about_to_breakout_rule` "both" = both distances, "either" = one of them) |
| Very Near | within **1.0 %** and **0.25 ATR** (`very_near_pct`, `very_near_atr`, ASSUMPTION) |
| Touched | the candle's high went above the level (intraday) but the close stayed at or below it, inside the Very Near distance (an intraday touch is never a breakout) |
| Breakout | the latest candle is a fresh 10-day breakout (the existing rule: close > level + 0.1 ATR, crossing from inside) |
| Confirmed Breakout | a recent breakout (<= 5 sessions) whose next close also held above the level (two-close confirmation) and the close is still above it |
| Rejected | an intraday push through the level that closed outside the Very Near distance, or a breakout that closed back below its level within 3 sessions (`setup_rejection_sessions`) |
| Expired | the stock was in a setup during the last 5 sessions (`setup_expiry_sessions`), is not any more, and did not break out since |

Stocks further away than the Approaching distance have no state and are not listed. "Days in setup" counts consecutive sessions in Approaching / Very Near / Touched up to the latest one. A gap through the level counts like any close beyond it.

## Setup quality (explainable, not a probability)

`0-100 = weighted mean of the available parts` (ASSUMPTION weights): proximity 30 (1 at the level, 0 at 2 % below), trend 25 (share of close > EMA 20, close > EMA 50, EMA 20 > EMA 50), tightness 15 (10-day range 5 % -> 1, 15 % -> 0), volume 10 (volume / 20-day average 0.5 -> 0, 1.2 -> 1), relative strength 10 (RS Line of the latest weekly scan on the setup's side of zero), Stage 10 (your Stage Analysis of the latest weekly scan: Stage 2 -> 1, Stage 1 -> 0.5). Parts without data are left out, not scored 0.

## Page

Columns: symbol, company, F&O, state, 10D high, close, distance %, distance ATR, EMA 20 / 50, volume ratio, RS, Stage, setup quality, days in setup. Presets: before the breakout (default: Approaching, Very Near, Touched), very near / touched, breakout / confirmed, all live states, rejected / expired. Filters: direction, minimum quality, F&O. The thresholds in force are printed under the header. A symbol or row opens the chart preview with the **10-day level drawn across its 10-session reference window** (and the date of the candle that set it in the details) and, after an event, the breakout / rejected-breakout marker.

## Confluence

An Approaching / Very Near / Touched state is trigger evidence in the **same family as the 10-day breakout** (`breakout_10d`; 0.35 / 0.5 / 0.55): a setup and a breakout of the same level never count twice. Screen "10D About to Break Out + Bullish Trend". Details: `docs/confluence-engine.md`.

## API

A daily scan run with SignalConfig 1.0.0 (before this feature) has no setup states: the page then says so and asks for a new 1D scan (after an upgrade the scheduler starts one automatically because the signal configuration hash changed).


`GET /api/scan/breakout-setups?universe=&asof=&direction=bullish|bearish|both&state=&include_rejected=&include_expired=&min_quality=&fno=` - rows of the latest daily scan (stored evidence), with Stage context from the latest weekly scan, the setup quality breakdown and the thresholds.

## Tests

`tests/test_breakout_setups.py` (13): Approaching and the two distances, Very Near and Touched, rejected intraday push, Breakout and Confirmed Breakout, a breakout that closes back inside is Rejected, Expired, far from the level is no setup, "either" rule and configurable thresholds, bearish mirror, no look-ahead, explainable setup quality, scanner API, a scan from before the feature is explained instead of shown as an empty market. Self-test: "10-day Very Near". UI: `scripts/verify-improvements.mjs`.

## Limitations

* Thresholds are initial engineering defaults (the Very Near, expiry and rejection numbers are ASSUMPTIONs); not validated on labelled outcomes. A setup is not a prediction that the breakout will happen.
* Built and UI-checked on synthetic data only (cloud session without Yahoo / NSE access).
