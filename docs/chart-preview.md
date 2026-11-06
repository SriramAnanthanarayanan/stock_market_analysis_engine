# Chart preview and fullscreen chart

Code: `frontend/src/components/StockChartPreview.tsx` (`ChartPreviewHost`, preview drawer, fullscreen layer), `frontend/src/lib/openChart.tsx` (`useOpenChart`, `SymbolButton`), `frontend/src/pages/Explorer.tsx` (`ExplorerView`, the full terminal used by both the Explorer page and the fullscreen layer).

## Behaviour

* In every results table the **symbol is clickable** (dotted underline); clicking it - or the row - opens the chart in a **right-side drawer** over the current page. The page is not navigated away from and stays mounted underneath, so its filters (URL), pagination, sorting and scroll position are kept. Clicking another symbol while the drawer is open swaps the chart.
* Tables wired to the preview: Pattern Scanner, Candlestick Scanner, Breakout Scanner (E1), Fresh Breakouts, About to Break Out, 52-Week Breakouts, Volume Shocker, VCP Scanner, IPO Base Breakout, Indicator Scanner, Stage Analysis (symbol column and the detail panel's chart button), Index Analysis (indices), RRG (index points and list), Pattern History (track detail), and the Confluence Dashboard. Future tables use the same two lines: `const openChart = useOpenChart()` and `<SymbolButton ... onOpen={() => openChart({...})} />`.
* The drawer shows: symbol, company, F&O badge, latest close and change, timeframe switch (1H, 4H, 1D, 1W, 1 / 3 / 6 / 12 Months), candles and volume, EMA chips from the user's preferences, S/R zones, log scale, reset / latest, a **Candles / Line** chip (same overlays and drawings in both modes), the running (incomplete) 1W / multi-month candle drawn grey, the overlays of the signal that opened it (pattern geometry for a pattern row, breakout level and marker, the 10-day level of an about-to-break-out setup, volume-shock marker, VCP T1 / T2 / ... contraction structure with swing points, depths, pivot and volume dry-up, IPO listing date / discovery period / base / pivot / breakout volume, Stage overlay), a "signal details" panel with the row's key numbers, performance horizons and notes. "Tools" opens the full indicator and drawing toolbar; drawings are the same persistent drawings as everywhere else.
* **Expand** opens the full charting terminal for the stock in a fullscreen layer - the same `ExplorerView` component, chart component and APIs as the Explorer page, so values are identical. **Back to the list** or **Esc** closes it and returns to the drawer; Esc (or ✕) closes the drawer. Esc is ignored while it is used by a drawing tool (cancelling a half-drawn shape or deselecting). For indices Expand opens the Index Explorer page (browser Back returns to the list with its filters).
* **Open page** navigates to the stock's chart page (own URL, bookmarkable); "← Scanner" on that page returns to the list URL.
* Overlays requested by a row (`show=...`) are applied to that chart only; they never change the saved chart preferences.

## Data

The drawer loads `/api/chart?max_candles=600` (stocks; the selected formation is always included even if older) or `/api/index/chart` (indices) plus the same `/api/indicators`, `/api/technical`, `/api/vcp/symbol`, `/api/ipo/symbol` and `/api/drawings` calls as the full chart. Nothing is computed in the browser.

## Verification

`frontend/scripts/verify-advanced.mjs`: symbol click opens the drawer from every table, signal overlays are drawn, Expand / Esc / Esc, and the page's filter, URL and scroll position are unchanged afterwards. `frontend/scripts/verify-improvements.mjs`: the symbol opens the same preview from the Pattern Scanner, VCP, IPO Base, Fresh Breakouts, About to Break Out, Volume Shocker, Candlestick (Marubozu) and Confluence pages without navigating; VCP, IPO and setup overlays are drawn.
