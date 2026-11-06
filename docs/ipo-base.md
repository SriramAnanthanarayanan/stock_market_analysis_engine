# IPO Base Breakout

Code: `backend/app/ipo/detector.py`; run by every **daily** scan (`ScanManager._store_ipo`) and published as `signal_evidence` rows (source `ipo_base`); API `GET /api/scan/ipo-base`, `GET /api/ipo/symbol`; page **IPO Base Breakout** (`frontend/src/pages/IpoBaseScanner.tsx`); chart overlay chip **IPO base** (daily charts) in the chart terminal and the chart preview. Parameters: `IPOConfig` **1.0.0**, hash `a2f7e09abbcbc9c9` (`GET /api/config/ipo`, `IPO_PARAM_META`). Synthetic fixtures: `backend/app/synthetic_ipo.py`.

## What it is (and is not)

The scanner follows **recent listings** through the sequence described for IPO bases in the William O'Neil / IBD tradition:

```
listing -> initial price discovery -> volatility settles -> first post-listing base -> resistance / pivot -> breakout on volume
```

It is **not** an IPO-launch or hype scanner: nothing is reported for the listing itself, the first sessions are only the discovery period, and a breakout counts only above a validated base and on volume. An IPO base is **not** classified as a VCP: a VCP needs successive shrinking contractions inside an established uptrend (`docs/vcp-methodology.md`); the VCP detector also needs at least 50 completed sessions before its base (`VCPConfig.min_prior_bars`), so a fresh listing's first base is this scanner's job.

## Listing date

* **Official source**: NSE's equity list (`EQUITY_L.csv`, column `DATE OF LISTING`; `Settings.equity_list_url`), stored in `stocks.listing_date` / `listing_date_source` (migration 9). It is refreshed together with the universe (`UniverseService.refresh_listing_dates`), and fetched on start-up when no date is stored yet. A failed refresh keeps the stored dates.
* The listing candle is the first daily candle on or after the official date. If the provider's history starts more than 5 sessions after the official date (`listing_gap_tolerance_sessions`), the discovery period is missing and the stock is **not evaluated**.
* **No date, no evaluation** (default): `first_candle_fallback` is off, because a renamed or re-listed ticker can restart its provider history and would look like an IPO. When switched on, the first candle of a short history is used and flagged "first candle - fallback" everywhere.
* Only listings of the last `max_age_bars` (365) completed sessions are candidates.

## Algorithm (completed daily candles, no look-ahead)

Walk over the candles since listing; candle `j` is judged on candles before it:

* **Resistance** before candle `j` = the highest high since listing up to `j-1`. The **base** runs from that high to `j-1`: base high (= **pivot**), base low, `depth % = (high - low) / high x 100`, length in sessions, **resistance touches** (separate highs within 3 % of the base high, at least 5 sessions apart, the first high included).
* The base is **valid** when all gates pass:

| Gate | Rule (initial default) | Basis |
|---|---|---|
| age | >= 25 completed sessions since listing (`min_age_bars`) | task default |
| base_length | 20-120 sessions from the base high (`min_base_bars`, `max_base_bars`) | task default |
| base_depth | 8-35 % below the base high (`min_base_depth_pct`, `max_base_depth_pct`) | task default |
| resistance_touches | >= 2 touches of the base high (`min_resistance_touches`; zone 3 %, `resistance_zone_pct` ASSUMPTION) | task default + ASSUMPTION zone |
| volatility_settled | mean daily range % of the last 10 base sessions <= 0.80 x that of the 20-session discovery period (`settle_window_bars`, `settle_range_ratio`) | ASSUMPTION |

* A completed close above a valid base's pivot (`breakout_buffer_pct` 0) is a **breakout** when its volume is >= 1.5 x the average of the previous sessions since listing (up to 50; `breakout_volume_ratio`, `volume_avg_bars`). A close back below the pivot within 5 sessions (`failed_breakout_bars`) is a **Failed Breakout**; a close below the base low after the breakout **Invalidates** it. A breakout / failure is reported for 10 sessions (`breakout_track_bars`), then the current base is evaluated again.
* A close above the resistance without a valid base just makes a new post-listing high: the next base starts from it. A close above a valid pivot **without** breakout volume is not a breakout: the state stays Near Pivot with the reason.

## States

| State | Meaning |
|---|---|
| Too Young | fewer than 25 completed sessions since listing |
| Discovery Phase | inside the first 20 sessions, a new post-listing high in the last 5 sessions (`discovery_new_high_bars`), or volatility has not settled yet |
| Base Forming | a base exists, but a gate is still open (listed in the reason) |
| Base Validated | all gates pass; close more than 3 % below the pivot |
| Near Pivot | valid base and the close within 3 % below the pivot (`near_pivot_pct`), or a close above it without breakout volume (flagged) |
| Breakout | close above the pivot of a valid base on >= 1.5x volume |
| Failed Breakout | back below the pivot within 5 sessions of the breakout |
| Invalidated | base deeper than 35 %, longer than 120 sessions without a breakout, or a close below the base low after a breakout |

Too Young and Invalidated are hidden by default in the scanner ("Too young" and "Failed / invalidated" presets show them).

## Quality (explainable, not a probability)

`Quality = 100 x weighted mean of six 0-1 components` (ASSUMPTION weights, placeholders until labelled charts exist):

| Component | Weight | Formula |
|---|---:|---|
| depth | 20 | 1 at 15-30 % deep; 0.5-1 from 8 to 15 %; down to 0.6 at 35 % |
| length | 15 | 1 at 25-60 sessions; 0.7 when shorter; down to 0.5 at the maximum |
| resistance | 20 | touches of the base high: 2 -> 0.7, 3+ -> 1 |
| volume_dry_up | 20 | volume of the last base sessions / discovery-period volume: 1 at <= 0.5, 0 at >= 1 |
| tightness | 15 | daily range of the last base sessions / discovery range: 1 at <= 0.5, 0 at >= 1 |
| proximity | 10 | close within 3 % below the pivot -> 1, 10 % or more below -> 0 |

## Scanner page and chart

Columns: symbol, company, F&O, state, listing date (official / fallback), age, quality, base depth, base length, touches, pivot, distance to pivot, breakout volume. Presets: all bases, near pivot / breakout, base validated, discovery / forming, failed / invalidated, too young; F&O filter. A row opens the detail panel (listing, age, discovery range, base, pivot, touches, right side vs discovery range / volume, breakout, quality breakdown, every gate as PASS / FAIL); the symbol opens the chart preview.

The chart (`ipoShapes`, `ipoMarkers`, `ipoVolume` in `frontend/src/charts/vcpOverlay.ts`, from the stored geometry `ipo-base-1.0`) draws: the **listing date** (vertical line + "Listed <date>" marker), the **discovery period** (shaded zone, labelled), the **base** (zone from the base high to the base low), the **resistance touches**, the **pivot line**, the **breakout candle** marker with its volume ratio and the breakout volume bar highlighted, and the failure marker.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/scan/ipo-base?universe=&asof=&status=&include_too_young=&include_invalidated=&min_quality=&fno=` | rows of the latest daily scan (stored evidence; nothing recomputed), `listing_dates_loaded`, thresholds, note |
| `GET /api/ipo/symbol?symbol=&asof=` | one stock with geometry: the stored row of the latest daily scan, else computed on demand (not stored) |
| `GET /api/config/ipo` | parameters with descriptions |

## Confluence

The IPO base is setup evidence (family `ipo_base`: quality x state factor - Base Validated 0.8, Near Pivot / Breakout 1.0, Base Forming 0.5); a fresh IPO breakout is a trigger (family `ipo_breakout`, fresh for 5 sessions); its volume dry-up joins the volume category (`dry_up` family). Screen "IPO Base Near Pivot / Breakout". Details: `docs/confluence-engine.md`.

## Tests

`tests/test_ipo_base.py` (13): too young, discovery phase, base forming, valid base and pivot, near pivot, breakout with volume confirmation (and a high-close without volume is not a breakout), failed breakout and invalidation, listing-date rules (official date, history gap, no date / fallback switch), no future leakage, an IPO base is not a VCP, state labels, EQUITY_L parsing, scanner API. Self-test: "IPO base breakout". UI: `scripts/verify-improvements.mjs` (scanner, detail panel, chart overlay).

## Limitations

* Engineering defaults; **not validated on labelled charts**. No accuracy is claimed; the quality is not a probability.
* Needs the official listing dates: they come from NSE (`nsearchives.nseindia.com`), blocked in the cloud session where this was built - the UI checks ran on synthetic listings (`IPONEW`, `IPOBASE` in the demo server).
* A demerger, a re-listing or a change of series can appear as a fresh listing in the NSE list; check the chart.
* Only NIFTY 500 / F&O stocks of the app's universe are scanned, so most fresh IPOs appear only once they join it.

## Sources

Secondary descriptions of the IPO base (search results; no primary IBD / O'Neil text was available here, and they do not agree on numbers - hence every number is configurable):

* [LuxAlgo - IPO base](https://www.luxalgo.com/library/concept/ipo-base/) and [O'Neil base analysis](https://www.luxalgo.com/library/concept/oneil-base-analysis/)
* [TraderLion - IPO Bases, a comprehensive study](https://traderlion.com/profile/chhirag-kedia/master-ipo-base-trading/)
* [Sakatas - How to trade an IPO base breakout](https://sakatas.substack.com/p/how-to-trade-an-ipo-base-breakout)
* [All About IPO Bases (study notes)](https://dssrajput.notion.site/IPO-studies-270b09aec0eb466ab333c02bb46aeda6)
* [MarketSmith India blog](https://blog.marketsmithindia.com/?p=5089)
