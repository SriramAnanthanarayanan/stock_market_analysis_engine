# Shared level engine - tests of a level and "about to break out" states

Code: `backend/app/signals/levels.py`; used by the 10-day base (`signals/breakouts.py`), the 52-week state (`week52_state`), the multi-year approach (`signals/multiyear.py::multiyear_approach`) and the Darvas box (`signals/darvas.py`). Parameters: `SignalConfig` **1.4.0** (`level_tests_score`, `w52_*`, `multiyear_approach_pct`, `multiyear_very_near_pct`, `fresh_base_test_separation`; `GET /api/config/signals`).

Phase 2 of the plan agreed on 2026-11-15. It answers your points:
- 7: "resistance touched at least 3 times shows buyers' strength; catch it before the breakout".
- 9: "multi-year: a stock trading in a range for many years and about to break out; for 52-week, score stocks that touched the resistance at least 3 times".

## Separate tests

A candle tests a resistance when its high is inside the level's zone (support: its low). A zone candle at least `separation` sessions after the previous zone candle starts a new test. Consecutive zone candles are one test.

| Level | Zone | Separation |
|---|---|---|
| 10-day base top | within `fresh_base_touch_zone_atr` (0.5) x ATR | `fresh_base_test_separation` 2 |
| 52-week high | within `w52_zone_pct` (3 %) | `w52_test_separation_sessions` 10 |
| Multi-year resistance | within `multiyear_zone_pct` (3 %) | `multiyear_test_separation_sessions` 20 |
| Darvas box top | within 3 % | `darvas_confirm_sessions` 3 |

## Test score (ranking aid, ASSUMPTION)

`level_tests_score` = (0, 0.2, 0.5, 0.8, 1.0) for 0, 1, 2, 3 and 4+ tests. 3+ tests rank higher, as you asked.

Trading literature is split. Some say more tests weaken a level, others that they show supply being absorbed. This is a ranking aid to be validated with the labelled outcomes, **not a probability**.

Where the score is used:
- About to Break Out setup quality: new component `level_tests`, weight 10.
- 52-week and multi-year approach rows, and Darvas rows: shown as Tests, with 3+ highlighted.

"Lows rising" (the lowest low of the second half above the first half) is reported next to it.

## Approach states (before the breakout)

| State | Rule |
|---|---|
| Touched | the candle reached the level intraday; the close stayed at or below it |
| Very Near | close within 2 % below it (`w52_very_near_pct`, `multiyear_very_near_pct`) |
| Approaching | close within 5 % below it (`w52_approach_pct`, `multiyear_approach_pct`) |

A close above the level is a breakout. It belongs to each scanner's own event logic (52-week crossing, multi-year breakout), so the same move is never reported twice.

### Multi-year: "trading in a range for years and about to break out"

The candidate levels:
- Overhead levels are the suffix maxima of the highs before the latest candle (the same structure as the breakout walk).
- For each level within 5 % above the close, the range starts at the first candle, after the stock was last above it, whose high came within 3 % of it.
- Levels with a range of at least `multiyear_min_years` (1) qualify. The one with the longest range is reported.

Reported with each row: level, years, tests, test score, range depth, lows rising, and all-time-high or "above it last on ...".

Where to see it:
- Page **Multi-Year Breakouts**, tab **About to break out** (`GET /api/scan/multiyear-approach?min_years=&state=&min_tests=&fno=`).
- Evidence: source `multiyear`, `signal_type multiyear_approach`, kind `state`. The confluence multi-year trigger reads events only, so it never counts these rows.

An MTNL-type stock (capped at about 40 from 2012 to 2024) is exactly this case. MTNL itself is outside the NIFTY 500 / F&O universe, so it is not scanned until the universe grows.

### 52-week

Every 52-week state row now carries:
- `high_tests`
- `tests_score`
- `lows_rising`
- `approach_state`
- `distance_below_high_pct`

Page **52-Week Breakouts**, view **About to break the 52-week high** (`GET /api/scan/52week?view=approach&min_tests=`).

## Live check (cached data of 2026-11-13, 500 symbols)

- 26 stocks were within 5 % of a multi-year resistance. Examples:
  - MAHABANK: 18.9-year range, 2 tests
  - UNIONBANK: 11.2 years, 4 tests, Very Near
  - PNB: 8.7 years, 3 tests, Touched
  - DRREDDY: 2.3 years, 5 tests
- 56 stocks were approaching their 52-week high: 32 Approaching, 10 Very Near, 14 Touched. RADICO and SUNTV had 4 tests each.

Not validated on labelled outcomes.
