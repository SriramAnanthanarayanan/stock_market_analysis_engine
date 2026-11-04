# Charting terminal (E1)

The Pattern Explorer chart (`frontend/src/charts/PatternChart.tsx`) is the common chart for every page. It uses Lightweight Charts v5 only (`createChart`, `addSeries(CandlestickSeries | LineSeries | HistogramSeries, options, paneIndex)`, `createSeriesMarkers`, series primitives); no v4 APIs.

## Layout

| Pane | Content |
|---|---|
| Price (0) | candles, EMA overlays, pattern geometry, S/R zones, daily 10-day levels, Marubozu / breakout / divergence markers, divergence segments, user drawings |
| Volume | histogram (toggle) |
| RSI | RSI line, 70/30 (OB/OS) and 40/50/60 reference lines, divergence segments |
| ATR | ATR line |
| Extra | page-supplied panes (the Stage Analysis RS Line, E3) |

Sub-panes are rebuilt in a fixed order whenever their visibility changes; pane heights are resizable (drag the separator) and saved with the layout. Clean defaults: candles + volume only.

## Controls

* Indicators menu: per-EMA visibility, period (1-400), colour, width, restore EMA defaults; RSI pane + period + OB/OS levels; ATR pane + period; breakout markers and confirmation mode; restore all chart defaults. Quick chips for EMA 10-200, RSI, ATR, RSI divergence, Marubozu, 10D levels, Breakouts.
* Timeframe 1H / 4H / 1D / 1W; symbol switch via the header search (NSE stocks; indices from E2).
* Fit (zoom out to the whole cached history, e.g. daily since 1996), Log (logarithmic price scale, saved with the chart preferences; equal % moves look equal across decades), Reset zoom (focus the selected formation), Latest candle, mouse-wheel zoom, drag to pan, crosshair. Charts carry the full history; they open on the latest bars or the selected formation.
* Save layout / Restore layout (indicator settings + pane heights), PNG export (`chart.takeScreenshot()`).

Changing an EMA period, the RSI period or the ATR period re-requests the series from the backend - nothing is computed in the browser.

## Drawing tools

| Tool | Creation |
|---|---|
| H-line | one click (price) |
| V-line | one click (time) |
| Trend | click start, click end |
| Ray | click start, click a second point; extends to the right edge |
| Zone (rectangle / price zone) | click two corners |
| Fib | click start, click end; levels 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1 (1 = start point, 0 = end point) with prices |
| Inspect | crosshair readout: price under the cursor, bar time, % from the last close |

* **Select** a drawing by clicking it in Pan mode (handles appear); **move** by dragging its body; **resize** by dragging a handle; **style**: colour, width, dashed apply to the selected drawing (or to new drawings when none is selected); **delete**: button or Delete/Backspace; **Esc** cancels a half-drawn shape and deselects; **hide/show** all; **clear all** asks for confirmation.
* Anchors are (bar start time, price) - never pixels - so drawings stay in place across zoom, pan, resize, pane changes and reloads. Times between bars interpolate; beyond the last candle they extrapolate with the median bar spacing.
* A press that hits a drawing is captured before Lightweight Charts sees it, so dragging a drawing never pans the chart; creation tools disable drag-panning while active (wheel zoom stays).
* Drawings are a separate primitive and separate storage: they never touch backend geometry, detector calculations or S/R zones.

### Persistence and backup

Drawings are stored in this browser's `localStorage` per chart identity, symbol and timeframe: key `pas.drawings.v1:<chart>:<SYMBOL>:<timeframe>` (chart = `stock` for the stock explorer). Preferences: `pas.chartPrefs.v1`; saved layout: `pas.chartLayout.v1`. Storage failures (private windows, blocked storage) fall back to defaults silently.

**Backup**: *Export drawings* downloads every stored drawing set (all symbols and timeframes) as `chart-drawings.json` (`{"format": "pas-drawings-v1", "drawings": {key: [...]}}`); *Import* merges a backup (same ids are replaced). Clearing browser data deletes drawings that were not exported.

## Verification

`frontend/scripts/verify-e1.mjs` (38 checks against the running app) covers navigation and scanners, symbol search, multiple EMA overlays and backend = chart values, RSI/ATR panes, EMA period change and reset, divergence lines/markers, Marubozu markers and metrics, breakout markers and daily levels (1D and daily-derived on 1H), creating all six drawing types, selecting, moving, resizing, restyling, deleting, persistence per symbol/timeframe, export/import, hide, clear with confirmation, crosshair readout, layout save/restore, PNG export, resize and disposal, no console or server errors. `verify-ui.mjs` and `verify-p3.mjs` cover the pre-E1 chart behaviour.

## Limitations

* Drawings live in one browser; use Export/Import to move or back them up.
* Extrapolated future times for intraday drawings use the median bar spacing, so a drawing beyond the last candle may shift by a bar when new candles arrive.
* PNG export captures the chart canvas (panes and series), not the HTML legend.
