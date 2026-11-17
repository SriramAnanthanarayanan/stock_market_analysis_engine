# Chart preview and fullscreen chart

Code: `frontend/src/components/StockChartPreview.tsx` (`ChartPreviewHost`, preview drawer, fullscreen layer), `frontend/src/lib/openChart.tsx` (`useOpenChart`, `SymbolButton`), `frontend/src/pages/Explorer.tsx` (`ExplorerView`, the full terminal used by both the Explorer page and the fullscreen layer).

## Behaviour

* In every results table the **symbol is clickable** (dotted underline); clicking it - or the row - opens the chart in a **right-side drawer** over the current page. The page is not navigated away from and stays mounted underneath, so its filters (URL), pagination, sorting and scroll position are kept. Clicking another symbol while the drawer is open swaps the chart.
* Tables wired to the preview: Pattern Scanner, Candlestick Scanner, Breakout Scanner (E1), Fresh Breakouts, About to Break Out, 52-Week Breakouts, Multi-Year Breakouts (opens on the weekly or monthly chart so the whole base is visible), Volume Shocker, VCP Scanner, IPO Base Breakout, Indicator Scanner, Stage Analysis (symbol column and the detail panel's chart button), Index Analysis (indices), RRG (index points and list), Pattern History (track detail), and the Confluence Dashboard. Future tables use the same two lines: `const openChart = useOpenChart()` and `<SymbolButton ... onOpen={() => openChart({...})} />`.
* The drawer shows: symbol, company, F&O badge, latest close and change, timeframe switch (1H, 4H, 1D, 1W, 1 / 3 / 6 / 12 Months), candles and volume, EMA chips from the user's preferences, S/R zones, log scale, reset / latest, a **Candles / Line** chip (same overlays and drawings in both modes), the running (incomplete) 1W / multi-month candle drawn grey, the overlays of the signal that opened it (pattern geometry for a pattern row, breakout level and marker, the 10-day level of an about-to-break-out setup, volume-shock marker, VCP T1 / T2 / ... contraction structure with swing points, depths, pivot and volume dry-up, IPO listing date / discovery period / base / pivot / breakout volume, Stage overlay), a "signal details" panel with the row's key numbers, performance horizons and notes. "Tools" opens the full indicator and drawing toolbar; drawings are the same persistent drawings as everywhere else.
* **Expand** opens the full charting terminal for the stock in a fullscreen layer - the same `ExplorerView` component, chart component and APIs as the Explorer page, so values are identical. **Back to the list** or **Esc** closes it and returns to the drawer; Esc (or ✕) closes the drawer. Esc is ignored while it is used by a drawing tool (cancelling a half-drawn shape or deselecting). For indices Expand opens the Index Explorer page (browser Back returns to the list with its filters).
* **Open page** navigates to the stock's chart page (own URL, bookmarkable); "← Scanner" on that page returns to the list URL.
* Overlays requested by a row (`show=...`) are applied to that chart only; they never change the saved chart preferences. A layer the user switched off is not switched back on by a row (see Layers).

## Keyboard: step through the list

Code: `frontend/src/lib/chartNav.ts`.

* **Space** opens the next row's chart, **Shift+Space** the previous one; the ◀ / ▶ buttons in the drawer header do the same and show the row (`12 / 768`). The order is the table as shown: its sort, filters and page. At the end of a page the table's own pager moves on (row 50 -> row 51 on page 2), and back.
* Each step is a click on the neighbouring row's symbol, so the row opens exactly as with the mouse (its pattern, scanner levels and markers, signal details). A timeframe picked in the drawer (e.g. 1W) is kept for the next stocks until the drawer closes.
* The open row is highlighted in the table and scrolled into view, so after Esc you are where you stopped.
* Works in the drawer and in the fullscreen layer (Expand; ◀ / ▶ at the top). Off while typing in an input or a drawing's text, and with Ctrl / Alt / Meta. Holding Space does not auto-repeat. A chart opened outside a table (RRG, Pattern History) has no list: Space shows a note.
* Tables opt in through `data-chart-nav-table` (+ page offset and row total) on `DataTable` / `ResultsTable`, `data-chart-nav` on `SymbolButton` and `data-chart-nav-page` on the pager buttons.

## Layers: hide what clutters the chart

Code: `frontend/src/lib/chartLayers.ts`, `frontend/src/components/LayersMenu.tsx`.

* **Layers ▾** (drawer) lists every overlay with a checkbox: chart (detected pattern, scanner signal levels / markers, text labels, volume, my drawings), signals and patterns (10D breakouts, 10D levels, Marubozu, RSI divergence, VCP, IPO base, multi-year, Stage), panes (RSI, ATR, RS Line). Layers the row switched on are tagged "from this list"; layers with nothing to draw on this chart are greyed.
* **Labels** removes the text of overlays and markers (10D high, T1 32.8 %, Pivot, Broke Above, INCOMPLETE, ...); lines, arrows and zones stay. The user's own drawings keep their text.
* **Hide all (H)** switches every overlay off at once (pattern, scanner signal, EMAs, indicators, signal overlays, panes): candles, volume and the user's drawings stay. **Show all (H)** brings back exactly what was on. It stays on while stepping through the list. Also on the Explorer and Index Explorer pages (terminal bar + H).
* Everything is saved in this browser (`pas.chartPrefs.v1`, `localStorage`), like the other chart preferences; nothing goes to the backend.
* Scanner rows and saved choices: a row's overlays (`show=breakouts,...`) are drawn for that chart only and never saved, also when another chip is toggled on that chart (before 2026-11-17 toggling any chip saved the row's overlays as defaults). When the user switches such a layer off, it is remembered (`hiddenByUser`) and later rows do not switch it on again; switching it on removes it from that list. "Restore all chart defaults" clears the list.

## Data

The drawer loads `/api/chart?max_candles=600` (stocks; the selected formation is always included even if older) or `/api/index/chart` (indices) plus the same `/api/indicators`, `/api/technical`, `/api/vcp/symbol`, `/api/ipo/symbol` and `/api/drawings` calls as the full chart. Nothing is computed in the browser.

## Verification

`frontend/scripts/verify-advanced.mjs`: symbol click opens the drawer from every table, signal overlays are drawn, Expand / Esc / Esc, and the page's filter, URL and scroll position are unchanged afterwards. `frontend/scripts/verify-improvements.mjs`: the symbol opens the same preview from the Pattern Scanner, VCP, IPO Base, Fresh Breakouts, About to Break Out, Volume Shocker, Candlestick (Marubozu) and Confluence pages without navigating; VCP, IPO and setup overlays are drawn. `frontend/scripts/verify-chart-nav.mjs` (read-only, live or demo data; `NAV_PAGE=/vcp` for another table): Space / Shift+Space / ◀ ▶, position and row highlight, kept timeframe, page boundary both ways, a hidden scanner layer stays hidden on the next stock, Labels, Hide all (H) round trip, Esc. Unit tests: `frontend/tests/layers.test.mjs`.
