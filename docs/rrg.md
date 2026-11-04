# Relative Rotation Graph (E2)

**This is an internally implemented, configurable RRG-style relative rotation calculation. It does not reproduce the proprietary JdK RS-Ratio / RS-Momentum and makes no claim of matching published RRG charts.**

Code: `backend/app/rrg.py`, `backend/app/indices/analysis.py` (`IndexAnalysis.rrg`), API `GET /api/rrg`, page `/rrg` (`frontend/src/pages/RRG.tsx`, `components/RRGChart.tsx`). Parameters: `IndexConfig` (version 1.0.0).

## Method

1. Weekly closes of **completed weeks** only (official daily index closes aggregated into Monday-labelled NSE weeks; the running week is excluded). As-of dates use only weeks completed by the end of that day.
2. Index and benchmark are aligned on **identical weeks** (inner join on the week label). Weeks missing for the index are not forward-filled: they are skipped, and more than `rrg_max_gap_weeks` (1) consecutive missing weeks after the index's first week -> status `data_gap` (not plotted).
3. Formulas (EMA = SMA-seeded EMA, `docs/indicators.md`):

```
RS          = index_close / benchmark_close
RS_Ratio    = 100 x RS / EMA(RS, 14)
RS_Momentum = 100 x RS_Ratio / EMA(RS_Ratio, 14)
```

4. Trail = the last 12 weeks (4 / 8 / 12 selectable), oldest to newest; the arrow head is the latest completed week. Direction = heading of the latest weekly move in the (RS-Ratio, RS-Momentum) plane (right, up-right, ...).
5. Quadrants around 100 / 100 (a value of exactly 100 counts as the weak side):

| Quadrant | RS-Ratio | RS-Momentum | Meaning |
|---|---|---|---|
| Leading | > 100 | > 100 | relative strength and momentum positive |
| Weakening | > 100 | <= 100 | relative strength positive, momentum fading |
| Lagging | <= 100 | <= 100 | relative strength and momentum weak |
| Improving | <= 100 | > 100 | relative strength weak, momentum improving |

The labels describe relative rotation, not future performance.

## Parameters (`IndexConfig`)

| Parameter | Default |
|---|---|
| `rrg_default_benchmark` / `rrg_benchmarks` | NIFTY 500 / (NIFTY 500, NIFTY 50) |
| `rrg_rs_period`, `rrg_momentum_period` | 14 weeks each |
| `rrg_trail_weeks`, `rrg_trail_options` | 12; 4, 8, 12 |
| `rrg_center` | 100 |
| `rrg_max_gap_weeks` | 1 |
| `rrg_lookback_weeks` | 156 |
| `rrg_max_visible` | 20 (UI default) |

The benchmark is a parameter everywhere (never hard-coded in the calculation); it is excluded from its own graph.

## Data basis

Official NSE **price-return** index levels for every index and the benchmark - one basis per graph. Total-return index data is not available from the implemented providers; the page and API state the basis (`data_basis`). Leveraged, inverse, dividend-points, currency, volatility and fixed-income indices are excluded.

## Output and persistence

`GET /api/rrg?benchmark&asof&trail&codes&category` returns per index: status (`ok`, `data_gap`, `insufficient_history`, `data_unavailable`), aligned/valid week counts, the trail, latest RS-Ratio, RS-Momentum, quadrant, previous quadrant, heading and direction; plus benchmark, its latest completed week, parameters, method text, configuration version and hash. Every distinct calculation (benchmark, as-of, latest week, parameters) is stored in `rrg_runs` with its metadata and result.

## Page

Benchmark selector, trail 4/8/12 weeks, as-of date, category filters (broad, sector, thematic, factor/strategy), search, show/hide per index, maximum visible items, quadrant backgrounds, hover tooltip (week, values, quadrant, direction), click a point to open the Index Explorer. Indices that cannot be plotted are listed with the reason.

## Tests

`tests/test_indices_e2.py`: formulas vs a direct computation, warm-up length, quadrant classification incl. boundaries, heading, weekly alignment (different week labels never align), missing benchmark data, data gaps (3 weeks -> `data_gap`, 1 week skipped not filled), insufficient history, trail ordering and length, as-of correctness (earlier values unchanged), benchmark switching, category filter, persistence, benchmark validation. UI: `frontend/scripts/verify-e2.mjs`.

## Limitations

* RS-Ratio / RS-Momentum here are normalisations by an EMA ratio; their scale differs from proprietary RRG values.
* Price-return basis: dividend differences between indices affect relative strength slightly.
* Index history starts at each index's launch; young indices show short trails or `insufficient_history`.
