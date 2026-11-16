# Darvas box

Code: `backend/app/signals/darvas.py`. It runs in every daily scan and is published as `signal_evidence` (source `darvas`, kind `state`, one row per stock).

- API: `GET /api/scan/darvas` and `GET /api/darvas/symbol`
- Page: **Darvas Box** (`/darvas`)
- Parameters: `SignalConfig` **1.4.0** (`darvas_*`)

This was your suggestion 1 of 2026-11-15.

## Rules (Nicolas Darvas, *How I Made $2,000,000 in the Stock Market*, 1960)

| Rule | Implementation | Basis |
|---|---|---|
| Trade stocks making new highs | the box top must be a new high of the last `darvas_new_high_lookback` (252) sessions; with a shorter history the available candles are used (flagged) | SOURCE |
| Box top | the next `darvas_confirm_sessions` (3) sessions do not exceed the new high. The top is known only on the 3rd of them, and a higher high before the box is complete restarts it | SOURCE |
| Box bottom | after the top, the lowest low that the next 3 sessions do not undercut, confirmed on the 3rd | SOURCE |
| Breakout | a completed close above the top of a complete box. The breakout candle starts the next box (boxes stack in an advance) | SOURCE |
| Breakdown | a completed close below the bottom. The advance ends, and a new 52-week high restarts the walk | SOURCE |
| Breakout volume | breakout volume / mean of the previous 50 sessions; volume-confirmed at `darvas_breakout_volume_ratio` (1.5x). Reported, not a gate | ASSUMPTION number (Darvas wanted rising volume) |
| Maximum box depth | a box whose bottom falls more than `darvas_max_depth_pct` (20 %) below its top is a decline, not a pause, so tracking stops | ASSUMPTION |

The depth limit comes from the live check of 2026-11-16. Without it, falling stocks built boxes 20 to 38 % deep (THANGAMAYL 38 %, STLTECH 28 %). Darvas's own examples were mostly 5 to 15 %.

## States of the latest candle

| State | Meaning |
|---|---|
| Forming top | a new high, not yet held for 3 sessions |
| Forming bottom | top confirmed, bottom not yet |
| In box | box complete, close inside it |
| Near top | box complete, close within `darvas_near_pct` (2 %) below the top |
| Breakout | close above the top, reported for `darvas_track_sessions` (10) sessions |
| Breakdown | close below the bottom, reported for the same period |

Each row also has:
- box top and bottom, depth and days
- separate tests of the top (shared level engine; 3+ highlighted)
- lows rising
- boxes stacked in the current advance
- the breakout or breakdown candle with its volume ratio

## Page and chart

The page shows status presets:
- Active boxes (breakout, near top, in box, forming bottom)
- Breakouts
- Near top
- In box
- Forming
- Breakdowns

Filters: minimum top tests and volume-confirmed breakouts.

A row opens the detail panel. The symbol opens the chart preview with the box top and bottom drawn from the top candle, plus markers at the top and at the breakout or breakdown.

The confluence engine does not score Darvas rows, so a Darvas breakout and the 52-week breakout of the same move are not counted twice. The rows are listed in the evidence only.

## Tests

`tests/test_levels_darvas.py` covers:
- box top, bottom and Near top
- breakout with and without volume, and stacking
- breakdown
- no look-ahead (the top is unknown until 3 sessions after the high)
- a deep fall is not a box
- a pause far below the 52-week high is not a box
- the API

Self-test: "Darvas box: close above a confirmed box top on volume".

## Live check (cached data of 2026-11-13, with the 20 % depth limit)

See the implementation notes for the scan of 2026-11-16. Engineering choices are flagged; nothing here is validated on labelled outcomes.
