# Validation and threshold governance

## What is verified today

| Evidence | What it shows | What it does **not** show |
|---|---|---|
| `python -m app.selftest` (96 checks) | Engine formulas and, for each of the 17 patterns, a positive, a negative, a near-miss (rejected for the expected gate) and an invalidated example; Forming states, pennant precedence, S/R role flips; Falling Channel regressions | Real-chart accuracy |
| `pytest` (235 tests) | Data, calendar, universe, every detector, dedup/precedence, geometry, lifecycle (incl. neckline mode), cross-scan tracking, outcomes, S/R, labels, evaluation/compare/sweep, all API endpoints, migrations, restart recovery | Real-chart accuracy |
| `frontend/scripts/verify-ui.mjs` (25 checks), `verify-p3.mjs` (12 checks) | Rendering and interaction against the running app with live data, including the labelling and evaluation workflow | Pattern correctness |

Synthetic examples prove that the code follows the configured formulas. **No claim is made that the detectors are validated against real charts.** That requires the labels you create.

## Governance rules (enforced)

1. Every threshold lives in `DetectionConfig` with `PARAM_META` metadata; no numeric literals in detector code.
2. Changing a value requires bumping `DetectionConfig.version` and registering the new hash in `KNOWN_CONFIG_HASHES` (enforced by `tests/test_config.py`). History so far: 1.0.0 (P1), 1.1.0 (P2), 1.1.1 (S/R merge cap after live QA), 1.2.0 (P3 tracking IoU).
3. Scans, detections, tracks and evaluation runs persist `config_version` and `config_hash`; `config_versions` stores every parameter set used. Old results are never rewritten.
4. Detection thresholds, lifecycle thresholds and quality weights are separate parameter groups and are tuned independently.
5. Evaluation, comparison and sweeps never change or deploy a configuration. A change is made by editing `DetectionConfig` explicitly (reversible by reverting the version).

## Labels

Create labels in the Pattern Explorer (**Label chart**). A label records symbol, timeframe, pattern type, formation start/end (candle timestamps, validated against the chart as of the label date), optional upper/lower lines and swing points, a verdict, notes and the dataset:

* `valid` - a pattern the detector should report;
* `invalid` / `near_miss` - structures it should not report;
* `false_detection` - a detector output judged wrong ("Start from detection" copies the selected detection's window and lines as a starting point; the verdict is yours).

Ground truth is never inferred from detector output. Labels can be edited, deleted and exported (`/api/labels/export?format=csv|json`).

**Dataset split**: default = deterministic by stock (`int(sha1(symbol)) % 5 == 0` → validation, ~1 stock in 5), so tuning and validation use different stocks. Override per label (e.g. put later time periods into validation). Keep the validation set untouched while tuning.

## Evaluation method (implemented: `app/evaluation/engine.py`)

* **Unit**: a labelled chart = (symbol, timeframe, as-of date). The detector runs on it exactly like a historical as-of scan (cached candles completed by the end of that IST day) with the current configuration or a variant.
* **Matching**: same symbol, timeframe and pattern type; formation-window IoU (bar indices) ≥ 0.5 (configurable per run). Pairs are assigned greedily by IoU, ties to higher quality; each label and each detection is used at most once.
* **TP**: matched `valid` label. **FN**: unmatched `valid` label. **FP**: detection matched to an `invalid` / `near_miss` / `false_detection` label; an extra detection overlapping an already matched valid label (**duplicate**); or an unmatched detection of a pattern type labelled on that chart (`fp_scope = labelled_patterns`, default) / of any type (`fp_scope = all`).
* Developing detections are never counted; Forming only with `include_forming`.
* **Metrics**: `precision = TP / (TP + FP)`, `recall = TP / (TP + FN)`, duplicates, support - reported for the tuning set, the validation set and all labels, by pattern, timeframe, pattern × timeframe and F&O segment, with the configuration version and hash. Never only one aggregate.
* Notes flag groups with high precision but low recall (missed patterns stay visible as FN cases) and groups with fewer than 10 valid labels.
* Labels whose candles are not in the cached data are **skipped** and listed, never guessed.

Targets (engineering targets, not current claims): precision ≥ 80 %, recall ≥ 60 %, duplicates near zero.

## Comparing configurations and sweeping parameters

* **Compare** (`POST /api/evaluation/compare`, Evaluation page): A = current configuration (+ optional overrides), B = current + overrides. Reports per-group precision/recall for A and B with deltas, and the FP/FN cases added and removed. Only numeric/boolean parameters can be overridden.
* **Sweep** (`POST /api/evaluation/sweep`): evaluates each candidate value (e.g. `converging_cr_max` = 0.50, 0.55, 0.60, 0.65, 0.70) and reports tuning and validation precision/recall per value. It does not pick a value.
* Every run is stored in `evaluation_runs` and can be exported with its cases (`/api/evaluation/runs/{id}/export?format=csv|json&cases=all|tp|fp|fn`).

## Required report for every threshold change

| Field | Content |
|---|---|
| Parameter(s) | name, previous value, new value |
| Reason | the observed failure, with label IDs |
| Scope | affected patterns and timeframes |
| Tuning set | precision and recall before/after; FP added/removed; FN added/removed (from the compare run) |
| Validation set | the same metrics on the untouched held-out set |
| Decision | adopt / reject; then bump the version, register the hash, document it here |

## Workflow

1. Label charts across patterns, timeframes, sectors and F&O / non-F&O stocks; let the default split assign the dataset (or decide the split up front).
2. Run an evaluation on the current configuration; read FN and FP cases (each links to its chart as of the label date).
3. Form a hypothesis for one parameter; run a sweep or a compare on the **tuning** set.
4. Only when the change is decided, look at the validation rows. A threshold is not "optimal" because it does well on the tuning set.
5. Apply the change explicitly in `DetectionConfig` (new version + hash) and record the report above.

## Outcome analytics (separate from evaluation)

Post-breakout movement of tracked live patterns (returns, MFE, MAE at 5/10/20 bars from the close of the confirming bar) is shown on the History page and `/api/outcomes/summary`. It never influences detection. Groups below 30 complete observations are flagged as insufficient; these are descriptive statistics, not success rates.
