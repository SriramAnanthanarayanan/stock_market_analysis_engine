# Fibonacci retracement and trend-based extension

Code: `frontend/src/charts/fib.ts` (pure level math), `frontend/src/charts/drawings.ts` (rendering, hit testing), `frontend/src/charts/useDrawingTools.ts` (clicks, moves), `frontend/src/components/DrawingSettingsPanel.tsx` (settings, templates). Storage: the database (`docs/chart-drawing-persistence.md`). This reproduces the user-facing behaviour of TradingView's documented tools on Lightweight Charts; no TradingView code or library is used.

## Formulas (prices, never pixels)

Retracement, two anchors P1 (start of the move) -> P2 (end of the move):

```
Level(r) = P2 + (P1 - P2) x r          r = 0 at P2, r = 1 at P1, r > 1 beyond P1
```

Trend-based extension, three anchors P1, P2 (the move) and P3 (end of the retracement):

```
Extension(r) = P3 + (P2 - P1) x r      r = 1 projects a move equal to P1 -> P2 from P3
```

Logarithmic mode (only when every anchor price is positive; otherwise linear is used):

```
log Level(r)     = log P2 + (log P1 - log P2) x r
log Extension(r) = log P3 + (log P2 - log P1) x r          level = exp(...)
```

Reverse: the retracement swaps the anchor interpretation (0 at P1, 1 at P2); the extension projects the P1 -> P2 move in the opposite direction from P3.

Reference examples (unit tests, `frontend/tests/fib.test.mjs`): up move 100 -> 200: 0.382 = 161.8, 0.618 = 138.2, 1.618 = 38.2; down move 200 -> 100: 0.618 = 161.8; extension 100 -> 200, P3 150: 1.0 = 250, 1.618 = 311.8; log 100 -> 400: 0.5 = 200; log extension 100 -> 200, P3 150: 1.0 = 300.

## Levels

* Retracement defaults: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236.
* Extension defaults: 0, 0.382, 0.618, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236.
* Add, remove, edit (ratio, colour, custom text), show / hide and reorder levels; "Default levels" restores the set. Up to 64 levels are stored; the first 24 visible ones (sorted by ratio) are drawn.

## Settings (per drawing)

Trend line, prices, ratios, percentage labels (61.8 %) or decimals, custom text per level, background fill and its transparency, one-colour mode, per-level colours, line style (solid / dashed / dotted), width (1-4), opacity, extend left / right, reverse, label position (left / right), font size, linear / logarithmic calculation, anchor editing (date-time in IST and price per anchor), visibility (hide / show) and visibility by timeframe (all, only this timeframe, or any set of the eight chart timeframes).

**Templates**: "Save template" stores the current Fibonacci settings and line style (all symbols or this symbol only); a template is applied with one click and deleted with ✕. Templates are included in the drawings backup.

## Interaction

Draw: choose *Fib* (two clicks) or *Fib ext* (three clicks); a live preview follows the cursor. Edit: click a level or the trend line to select it, drag the body to move all anchors, drag a handle to move one anchor; Settings / Duplicate / Hide / Delete act on the selection; Delete or Backspace deletes; Esc cancels or deselects. Anchors are (candle time, price); drawings stay in place while zooming, panning, resizing and across timeframes. Only the drawing layer repaints when a drawing changes (`DrawingPrimitive.update`), the chart's series are not rebuilt. Drawings never mix with backend pattern geometry.

## Tests

Unit (`npm test`, Node test runner, 10 tests): uptrend / downtrend retracement, uptrend / downtrend extension, 0 / 1 / 1.0 / 1.618 levels, reverse, custom and maximum levels, linear and log modes, time-axis anchors that survive prepended history and other timeframes, price-axis moves. UI (`scripts/verify-advanced.mjs`): draw both tools, log mode, custom level, percent labels, template, timeframe visibility, persistence.
