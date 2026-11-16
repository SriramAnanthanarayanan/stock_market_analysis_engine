# F&O OI Buildup (NSE bhavcopy)

Page: **Markets > F&O OI Buildup** (`/fno`). Backend: `backend/app/fno/`, API `backend/app/api/fno.py`, tables from migration 10.

This feature is the user's standalone "Bhav copy" F&O screener (`C:\Users\Admin\Desktop\MicroSaas-Repo\Bhav copy`, commit `eeedf71`) moved into the screener. The Telegram report is not ported; the page replaces it.

## Source data

These are free public NSE archive files. NSE publishes them on the evening of each session.

| File | URL pattern | Used for |
|---|---|---|
| F&O bhavcopy, UDiFF (July 2024 onwards) | `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip` | Futures close and open interest (the selection input); options OI and settlement prices (PCR, OI walls, ATM IV); lot size |
| F&O bhavcopy, legacy (before July 2024) | `https://nsearchives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip` | Same data for older sessions |
| Full equity bhavcopy | `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv` | Spot close, high/low, traded quantity, delivery quantity and % |
| Index closes | `https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv` | Spot price for index futures (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) |

- All prices are NSE's raw end-of-day values. They are **not** adjusted for corporate actions.
- This is a separate dataset. It is never mixed with the Yahoo candles the scanners and charts use (hard rule 4). The chart preview still shows the usual Yahoo chart, with a marker on the signal date and lines at the call and put walls.
- TLS certificates are always verified. The original project's Samco fallback (an undocumented internal server path fetched with certificate checks off) is not used.

## Selection rule (the user's own; unchanged)

`app/fno/selection.py` ports `process_bhavcopy` and `process_watchlist` from the original `src/processor.py`. Only two mechanical things changed: the functions take frames instead of file paths, and the thresholds come from `FnoConfig`. The default threshold values are the original literals.

- Both sessions are filtered to futures (FUTSTK, FUTIDX). Each is then cut to its own nearest expiry, and the two are merged on the same contract.
- Price change % and OI change % are computed versus the previous stored session.

| Signal | OI change | Stock futures price | Index futures price |
|---|---|---|---|
| Strong Long Buildup | > 10 % | > +2 % | > +1 % |
| Long Buildup | > 7 % | > +1.5 % | > +1 % |
| Strong Short Buildup | > 10 % | < -2 % | < -1 % |
| Short Buildup | > 7 % | < -1.5 % | < -1 % |

- Sort order: Strong signals first, then by OI change, highest first.
- Existing positions (the watchlist) use the same comparison and are labelled by quadrant: Long Buildup, Short Buildup, Long Unwinding, Short Covering, or Neutral.

**Parity:**
- `tests/test_fno.py` runs the verbatim original (`tests/fno_original_processor.py`) and the port on the same files and requires identical rows, values and order. It covers both file formats, the database round trip, ties, infinite OI change and an expiry rollover.
- A one-off check on real files gave identical results for 7 session pairs. These were the original project's own downloads (including the report day 2026-05-29 vs 2026-05-27, 13 signals), UDiFF sessions from November 2026 and legacy sessions from July 2024.

The rule has these properties. They were **kept unchanged**; the page reports them instead:

- **The first session after an expiry has no signals.** The two days' front months differ, so no contract matches. The page shows a `rollover` notice.
- **Corporate actions are not adjusted.** A bonus or split shows up as a large price fall with an OI jump. Example: LICI on 2026-05-29, lot 700 -> 1400, -50.9 % price and +111 % OI, reported as "Strong Short Buildup". The row is flagged **CORP. ACTION?** when the lot size changed (UDiFF files only), but it is still shown.
- **A contract with 0 OI on the previous day has an infinite OI change.** It is shown as "∞ (prev OI 0)" and flagged.
- **Gaps:** if a session's F&O file is missing, the comparison spans more than one session. The page shows a `gap` notice.

## Context metrics (informational; never select, rank or hide)

`app/fno/enrich.py` computes the same metrics and formulas as the original `enrich_signals`, with these fixes.

| Original issue | Fix |
|---|---|
| The equity file was read with the wrong column names (`CLOSE`, `TOTTRDQTY`) and unstripped values (`" EQ"`), so every stock-level metric (spot, basis, volume, delivery, ranges, stock IV) was always empty | The real `sec_bhavdata_full` columns are mapped and the values are stripped; `-` is read as missing, never 0 |
| FINNIFTY was mapped to "Nifty Fin Service", but the file says "Nifty Financial Services", so FINNIFTY never had a spot price | Corrected name (old name still accepted) |
| History CSVs were appended after enrichment, so a same-day re-run counted today twice (every "OI streak" read 0) | History comes from the database and is strictly the previous sessions (`trade_date < D`). Re-runs are idempotent |
| Windows used whatever rows existed | 10/20-session ranges, the 20-session volume and delivery averages and the 5/20-session OI change need their full window; otherwise the value is empty |
| 5/20-day OI change and the build-up streak used front-month OI, which collapses at each rollover | They use the combined OI of all expiries |
| The 5-day basis average mixed the expiring and the next contract | Same contract only (its history while it was the next month counts) |
| ATM IV returned nothing in the last 0-1 days before an expiry | The nearest expiry with at least `iv_min_days_to_expiry` (2) days left is used |
| Legacy files mark futures with `STRIKE_PR 0 / OPTION_TYP XX`, UDiFF leaves them empty, so a legacy-to-UDiFF day pair would not merge | Futures get NaN in both formats (no effect within one format) |

Metrics:
- Spot and basis (futures minus spot, in rupees and %).
- Basis versus the same contract's 5-session average (Expanding, Contracting or Neutral; the band is 5 % of |average|).
- Volume ratio versus the previous 20 sessions.
- Delivery % and delivery surge versus 20 sessions.
- Volume rank over 1 year.
- 10- and 20-session high/low and today's distance from them.
- PCR, call wall and put wall of the nearest option expiry.
- Combined OI, its 5- and 20-session change, and the build-up streak.
- ATM IV, IV rank and IV percentile over 1 year.

ATM IV is the Brenner-Subrahmanyam approximation from settlement prices: spot, not forward, and NSE theoretical prices for illiquid strikes. Treat it as approximate.

## Data flow and schedule

1. **Startup:**
   - The app downloads every missing session in the last `PAS_FNO_BACKFILL_SESSIONS` (default 260, about one year), from the raw cache or NSE.
   - On the first run this is about 260 x 1.8 MB and took 18 minutes on 2026-11-16 (up to ~40 on a slow connection) in the background.
   - Later starts fetch only the new sessions.
2. **Evening job** (`fno_bhavcopy`, weekdays 18:05-23:35 IST every 30 min): stores the latest session once NSE has published its files. It stops as soon as the session, its equity and index files and its run are stored.
3. **Update now** on the page does the same on demand (`POST /api/fno/refresh`, optional `{"sessions": n, "recompute": true}`).
4. **Per session:**
   - `fno_sessions`: status of the three files.
   - `fno_futures`: every futures row, in file order.
   - `fno_underlying`: equity rows for F&O underlyings, plus index rows.
   - `fno_option_summary`: PCR, walls and ATM IV per underlying.
   - Then one `fno_runs` / `fno_signals` run per session, keyed by `FnoConfig` hash.
5. **Missing files:**
   - A session without an F&O file older than 5 days is recorded as `missing` (a holiday) and not retried.
   - Recent ones are retried, because NSE may simply not have published yet.
6. **Raw files** are cached in `backend/cache/bhavcopy/` and deleted after `PAS_FNO_RAW_RETENTION_DAYS` (45). Everything the feature needs is in the database.
7. **Watchlist** ("existing positions"): table `fno_watchlist`, edited on the page (`PUT /api/fno/watchlist`). It is evaluated when the page asks, so editing it never needs a re-run.

## Configuration governance

- `FnoConfig` 1.0.0 lives in `app/fno/config.py`, with hash `9a1275c4ebc50526`, `KNOWN_FNO_CONFIG_HASHES` and `FNO_PARAM_META`.
- The six selection thresholds are the **user's own rules. Treat them like StageConfig** and change them only on the user's explicit request (then bump `version`, register the hash and document it).
- The enrichment parameters are engineering defaults.
- A config change creates new runs under the new hash on the next update with `recompute`; old runs stay.

## Not ported

- The Telegram notifier and the GitHub Action.
- The CSV history files.
- The Samco download fallback.

## Validation status

The original project calls its rule "validated and live", but no backtest, labels or outcome statistics exist. Like every other screen here, it is a screening rule, not a validated signal; no accuracy may be claimed.
