# Chart drawing persistence

Code: `backend/app/drawings.py`, `backend/app/api/drawings.py` (tables `chart_drawings`, `chart_drawing_history`, `drawing_templates`, migration 8); `frontend/src/lib/drawingStore.ts` (autosave, conflicts, tabs), `frontend/src/charts/useChartTerminal.ts`.

Drawings are stored in the application database - not in React state, not only in the browser. They appear wherever that symbol's chart is opened: chart preview, fullscreen chart, Explorer / Index Explorer pages, VCP and breakout previews, the Confluence Dashboard preview.

## Drawing entity

`drawing_id` (unique), instrument (stock / index) and symbol, drawing type (h-line, v-line, trend, ray, zone / rectangle, parallel channel, Fibonacci retracement, Fibonacci extension), anchors (candle start time in epoch seconds + price, 1-3 per type), colour, line style, width, style (opacity, label, extend, fill), settings (complete Fibonacci configuration: levels with visibility / colour / text, linear / log, reverse, extend left / right, label options, template), visibility, timeframe visibility (`null` = every timeframe), created / updated time, version, origin (ui / import / legacy_browser).

## Symbol and timeframe

A drawing belongs to the **symbol** and by default shows on every timeframe. Its original (time, price) anchors are mapped onto the active timeframe's candles: a time between two candles lands proportionally between them (a daily anchor on a weekly chart sits inside its week), times after the last candle extrapolate with the bar spacing. Changing the number of candles (more history, another timeframe) never moves a drawing. Timeframe visibility can be set per drawing (only this timeframe, or any subset).

## Saving

* **Autosave**: every completed change (create, move, resize, style, settings, visibility, delete) is diffed against the saved copy and sent 0.7 s later; **Save** sends pending changes immediately. The toolbar shows "All drawings saved" / "Unsaved changes" / "Saving..." / "Save failed - retrying".
* Pending changes are flushed (keep-alive requests) when the page is hidden or closed and when the chart switches symbol; a failed save stays pending and is retried.
* Nothing is lost on refresh, navigation, another symbol, another timeframe or an application restart (verified: `scripts/verify-advanced.mjs` including `--after-restart`, `tests/test_drawings_api.py::test_drawings_survive_application_restart`).

## Several tabs

Every update / delete sends the version it was based on. If another tab saved a newer version first, the server rejects the stale change (HTTP 409) and returns the current copy; the tab keeps the **newer** copy and says so ("changed in another window ... the newer version was kept") - it never silently overwrites it. After each save the other tabs showing that symbol reload (BroadcastChannel), and every tab reloads when it regains focus with no pending edits. A drawing deleted elsewhere disappears with a note.

## Delete, history, backup

* A drawing is removed from the database only by the user's delete action (Delete button / key, "Clear all" after a confirmation). Every create / update / delete / import is kept in `chart_drawing_history` (`GET /api/drawings/{id}/history`).
* **Export drawings** downloads every drawing of every symbol plus the templates (`GET /api/drawings/export`, format `pas-drawings-v2`). **Import** restores a backup: unknown drawings are added; an existing drawing is replaced only when the backup copy is newer; nothing newer is overwritten. Browser-only backups of the previous version (`pas-drawings-v1`) import too.
* **Migration**: drawings that older versions kept only in this browser's localStorage (`pas.drawings.v1:<chart>:<SYMBOL>:<timeframe>`) are imported once into the database, each visible only on its original timeframe (the old per-timeframe behaviour). The localStorage keys are left untouched.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/drawings?symbol=&instrument=&timeframe=` | drawings of a symbol (optionally only those visible on a timeframe) |
| `POST /api/drawings` | create (validated: anchor count per type, colour, timeframes, Fibonacci settings, log mode needs positive prices) |
| `PUT /api/drawings/{id}` | update with `base_version`; 409 + current copy when stale; 404 when deleted elsewhere |
| `DELETE /api/drawings/{id}?version=` | delete; 409 when stale |
| `GET /api/drawings/{id}/history` | version history |
| `GET /api/drawings/export`, `POST /api/drawings/import` | backup / restore |
| `GET/POST /api/drawing-templates`, `DELETE /api/drawing-templates/{id}` | Fibonacci (and other) templates, global or per symbol |

## Tests

`tests/test_drawings_api.py` (14): create / reopen / edit / stale edit 409 / delete with versions and history, timeframe visibility and symbol scope, invalid payloads, three-anchor tools, restart persistence, export and restore (older backup never overwrites newer data), legacy browser backup import, templates. UI: `scripts/verify-advanced.mjs` (create, settings, refresh, timeframe visibility, second tab, stale edit, export, import, delete, restart) and the updated drawing section of `scripts/verify-e1.mjs`.
