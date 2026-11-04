"""Detector evaluation against human labels (P3, spec 18.3-19).

Evaluation unit: a *labelled chart* = (symbol, timeframe, as-of date). The
detector runs on that chart exactly as a historical as-of scan would (cached
candles, only bars completed by the end of the as-of IST day) with the chosen
configuration (default or a variant).

Matching (documented in docs/validation.md): a detection matches a label when
symbol, timeframe and pattern type are equal and the formation windows (bar
indices) overlap with IoU >= ``iou_threshold`` (default 0.5). Pairs are
assigned greedily by IoU (ties to higher quality); each label and each
detection is used at most once.

* TP: a ``valid`` label matched by a detection.
* FN: a ``valid`` label without a match.
* FP: a detection matched to an ``invalid`` / ``near_miss`` /
  ``false_detection`` label, a duplicate of an already matched valid label, or
  an unmatched detection of a pattern type labelled on that chart
  (``fp_scope="labelled_patterns"``, default) / of any type (``fp_scope="all"``).

Developing detections are never counted (hidden by default); Forming
detections only with ``include_forming``. Metrics are reported per dataset
(tuning / validation), pattern, timeframe, pattern x timeframe and F&O segment.
Nothing here selects or deploys a configuration.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Any

import pandas as pd

from .. import database as db
from ..config import TIMEFRAME_SOURCE_INTERVAL, DetectionConfig, config_hash
from ..data.ohlcv import prepare_series
from ..lifecycle.tracking import epoch_index
from ..patterns.base import SeriesContext
from ..patterns.engine import detect_series
from ..patterns.shared import iou
from ..timeutil import asof_cutoff

NEGATIVE_VERDICTS = ("invalid", "near_miss", "false_detection")
TARGETS = {"precision": 0.80, "recall": 0.60}


@dataclass(frozen=True)
class EvalOptions:
    iou_threshold: float = 0.5
    include_forming: bool = False
    fp_scope: str = "labelled_patterns"  # or "all"


class ConfigOverrideError(ValueError):
    pass


def make_variant(base: DetectionConfig, overrides: dict[str, Any] | None) -> DetectionConfig:
    """A configuration variant for evaluation only (never deployed automatically).

    Only scalar numeric / boolean parameters may be overridden; types are kept."""
    if not overrides:
        return base
    known = {f.name for f in fields(DetectionConfig)}
    clean: dict[str, Any] = {}
    for k, v in overrides.items():
        if k == "version" or k not in known:
            raise ConfigOverrideError(f"unknown parameter {k!r}")
        cur = getattr(base, k)
        try:
            if isinstance(cur, bool):
                if not isinstance(v, bool):
                    raise TypeError
                clean[k] = v
            elif isinstance(cur, int):
                if isinstance(v, bool) or float(v) != int(float(v)):
                    raise TypeError
                clean[k] = int(float(v))
            elif isinstance(cur, float):
                if isinstance(v, bool):
                    raise TypeError
                clean[k] = float(v)
            else:
                raise ConfigOverrideError(f"{k} is not a scalar parameter; only numeric/boolean parameters can be overridden")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ConfigOverrideError):
                raise
            raise ConfigOverrideError(f"invalid value {v!r} for {k} ({type(cur).__name__})") from None
    variant = replace(base, **clean)
    return replace(variant, version=f"{base.version}+variant.{config_hash(variant)[:6]}")


def _metric(tp: int, fp: int, fn: int, dup: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "duplicates": dup,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "support": tp + fn,
    }


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics per dataset (tuning / validation / all) and grouping."""
    out: dict[str, Any] = {}
    for ds in ("tuning", "validation", "all"):
        sel = [c for c in cases if ds == "all" or c["dataset"] == ds]
        groups: dict[str, dict[str, list[dict[str, Any]]]] = {
            "overall": {"all": sel},
            "by_pattern": defaultdict(list),
            "by_timeframe": defaultdict(list),
            "by_pattern_timeframe": defaultdict(list),
            "by_segment": defaultdict(list),
        }
        for c in sel:
            groups["by_pattern"][c["pattern"]].append(c)
            groups["by_timeframe"][c["timeframe"]].append(c)
            groups["by_pattern_timeframe"][f"{c['pattern']}|{c['timeframe']}"].append(c)
            groups["by_segment"][c.get("fno_status") or "UNKNOWN"].append(c)
        block: dict[str, Any] = {}
        for gname, g in groups.items():
            block[gname] = {
                key: _metric(
                    sum(c["type"] == "TP" for c in cs),
                    sum(c["type"] == "FP" for c in cs),
                    sum(c["type"] == "FN" for c in cs),
                    sum(c.get("reason") == "duplicate" for c in cs),
                )
                for key, cs in sorted(g.items())
            }
        out[ds] = block
    return out


def notes_for(metrics: dict[str, Any]) -> list[str]:
    notes = []
    for ds in ("tuning", "validation"):
        for key, m in metrics[ds]["by_pattern_timeframe"].items():
            p, r = m["precision"], m["recall"]
            if p is not None and r is not None and p >= TARGETS["precision"] and r < TARGETS["recall"]:
                notes.append(f"{ds}: {key} has high precision ({p:.0%}) but low recall ({r:.0%}) - missed patterns are listed as FN cases.")
            if m["support"] and m["support"] < 10:
                notes.append(f"{ds}: {key} has only {m['support']} valid labels - too few for a reliable estimate.")
    return notes


class Evaluator:
    def __init__(self, services):
        self.s = services

    def _chart_ctx(self, symbol: str, timeframe: str, asof: date, cfg: DetectionConfig) -> tuple[SeriesContext | None, str | None]:
        raw = self.s.store.load_current(symbol, TIMEFRAME_SOURCE_INTERVAL[timeframe])
        if raw is None or raw.empty:
            return None, "no cached market data for this symbol/timeframe"
        ps = prepare_series(raw, symbol, timeframe, asof_cutoff(asof), self.s.calendar)
        if not len(ps.frame):
            return None, "no completed candles at the label's as-of date"
        return SeriesContext(symbol, timeframe, ps.frame, cfg), None

    def evaluate(self, labels: list[dict[str, Any]], cfg: DetectionConfig, opts: EvalOptions = EvalOptions()) -> dict[str, Any]:
        charts: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for lab in labels:
            charts[(lab["symbol"], lab["timeframe"], lab["asof"])].append(lab)
        cases: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for (symbol, tf, asof_s), labs in sorted(charts.items()):
            asof = date.fromisoformat(asof_s)
            ctx, why = self._chart_ctx(symbol, tf, asof, cfg)
            if ctx is None:
                skipped += [{"label_id": lab["label_id"], "reason": why} for lab in labs]
                continue
            pos = epoch_index(ctx)
            fno = self.s.universe.fno_status(symbol, asof)
            usable = []
            for lab in labs:
                s = pos.get(int(pd.Timestamp(lab["formation_start"]).timestamp()))
                e = pos.get(int(pd.Timestamp(lab["formation_end"]).timestamp()))
                if s is None or e is None or e < s:
                    skipped.append({"label_id": lab["label_id"], "reason": "formation candles not found in the chart as of its date"})
                    continue
                usable.append((lab, (s, e)))
            dets = [
                d
                for d in detect_series(ctx, self.s.calendar)
                if d.status != "developing" and (opts.include_forming or d.status != "forming")
            ]
            pairs = []
            for d in dets:
                dw = (d.formation_start_index, d.formation_end_index)
                for lab, lw in usable:
                    if lab["pattern"] == d.pattern:
                        ov = iou(dw, lw)
                        if ov >= opts.iou_threshold:
                            pairs.append((ov, d.quality, d, lab))
            pairs.sort(key=lambda x: (-x[0], -x[1]))
            used_d: set[str] = set()
            used_l: set[str] = set()
            matched: dict[str, tuple[Any, float]] = {}
            for ov, _q, d, lab in pairs:
                if d.detection_id in used_d or lab["label_id"] in used_l:
                    continue
                used_d.add(d.detection_id)
                used_l.add(lab["label_id"])
                matched[lab["label_id"]] = (d, ov)
            chart = {"symbol": symbol, "timeframe": tf, "asof": asof_s, "fno_status": fno}

            def case(kind: str, lab: dict | None, d=None, ov: float | None = None, reason: str | None = None) -> dict[str, Any]:
                return {
                    "type": kind,
                    **chart,
                    "pattern": (lab or {}).get("pattern") or d.pattern,
                    "dataset": (lab or {}).get("dataset") or self._dataset_for(symbol),
                    "label_id": (lab or {}).get("label_id"),
                    "verdict": (lab or {}).get("verdict"),
                    "label_window": [lab["formation_start"], lab["formation_end"]] if lab else None,
                    "detection_id": d.detection_id if d is not None else None,
                    "detection_window": [d.formation_start, d.formation_end] if d is not None else None,
                    "status": d.status if d is not None else None,
                    "quality": d.quality if d is not None else None,
                    "iou": round(ov, 4) if ov is not None else None,
                    "reason": reason,
                }

            for lab, _lw in usable:
                hit = matched.get(lab["label_id"])
                if lab["verdict"] == "valid":
                    cases.append(case("TP", lab, hit[0], hit[1], "matched") if hit else case("FN", lab, reason="no detection matched"))
                elif hit:
                    cases.append(case("FP", lab, hit[0], hit[1], f"matched_{lab['verdict']}"))
            labelled_types = {lab["pattern"] for lab, _ in usable}
            for d in dets:
                if d.detection_id in used_d:
                    continue
                dw = (d.formation_start_index, d.formation_end_index)
                dup = next((lab for lab, lw in usable if lab["verdict"] == "valid" and lab["pattern"] == d.pattern and lab["label_id"] in used_l and iou(dw, lw) >= opts.iou_threshold), None)
                if dup is not None:
                    cases.append(case("FP", dup, d, None, "duplicate"))
                elif opts.fp_scope == "all" or d.pattern in labelled_types:
                    cases.append(case("FP", None, d, None, "unlabelled_detection"))
        metrics = summarize(cases)
        return {
            "config": {"version": cfg.version, "hash": config_hash(cfg)},
            "options": opts.__dict__,
            "label_count": len(labels),
            "charts_evaluated": len({(c["symbol"], c["timeframe"], c["asof"]) for c in cases}) if cases else 0,
            "skipped": skipped,
            "metrics": metrics,
            "targets": TARGETS,
            "notes": notes_for(metrics),
            "cases": cases,
        }

    def _dataset_for(self, symbol: str) -> str:
        from .labels import default_dataset

        return default_dataset(symbol, self.s.settings.validation_modulus)

    # ------------------------------------------------------------ compare
    @staticmethod
    def _keys(cases: list[dict[str, Any]], kind: str) -> dict[tuple, dict[str, Any]]:
        out = {}
        for c in cases:
            if c["type"] != kind:
                continue
            if kind == "FN":
                out[(c["label_id"],)] = c
            else:
                out[(c["symbol"], c["timeframe"], c["asof"], c["pattern"], tuple(c["detection_window"] or ()))] = c
        return out

    def compare(self, labels: list[dict[str, Any]], cfg_a: DetectionConfig, cfg_b: DetectionConfig, opts: EvalOptions = EvalOptions()) -> dict[str, Any]:
        a = self.evaluate(labels, cfg_a, opts)
        b = self.evaluate(labels, cfg_b, opts)
        diff: dict[str, Any] = {}
        for ds in ("tuning", "validation", "all"):
            rows = {}
            keys = set(a["metrics"][ds]["by_pattern_timeframe"]) | set(b["metrics"][ds]["by_pattern_timeframe"])
            for k in sorted(keys) + ["overall"]:
                ma = a["metrics"][ds]["overall"]["all"] if k == "overall" else a["metrics"][ds]["by_pattern_timeframe"].get(k)
                mb = b["metrics"][ds]["overall"]["all"] if k == "overall" else b["metrics"][ds]["by_pattern_timeframe"].get(k)
                rows[k] = {"a": ma, "b": mb, "precision_delta": _delta(ma, mb, "precision"), "recall_delta": _delta(ma, mb, "recall")}
            diff[ds] = rows
        fp_a, fp_b = self._keys(a["cases"], "FP"), self._keys(b["cases"], "FP")
        fn_a, fn_b = self._keys(a["cases"], "FN"), self._keys(b["cases"], "FN")
        return {
            "a": {k: a[k] for k in ("config", "metrics", "label_count", "skipped")},
            "b": {k: b[k] for k in ("config", "metrics", "label_count", "skipped")},
            "diff": diff,
            "fp_added": [fp_b[k] for k in sorted(set(fp_b) - set(fp_a))],
            "fp_removed": [fp_a[k] for k in sorted(set(fp_a) - set(fp_b))],
            "fn_added": [fn_b[k] for k in sorted(set(fn_b) - set(fn_a))],
            "fn_removed": [fn_a[k] for k in sorted(set(fn_a) - set(fn_b))],
            "note": "Comparison only - no configuration is changed or deployed. Judge a change on the validation rows, which must not be used for tuning.",
        }

    # -------------------------------------------------------------- sweep
    def sweep(self, labels: list[dict[str, Any]], base: DetectionConfig, param: str, values: list[Any], opts: EvalOptions = EvalOptions()) -> dict[str, Any]:
        rows = []
        for v in values:
            cfg = make_variant(base, {param: v})
            res = self.evaluate(labels, cfg, opts)
            rows.append(
                {
                    "value": getattr(cfg, param),
                    "is_current": getattr(cfg, param) == getattr(base, param),
                    "config_version": cfg.version,
                    "config_hash": config_hash(cfg),
                    "tuning": res["metrics"]["tuning"]["overall"]["all"],
                    "validation": res["metrics"]["validation"]["overall"]["all"],
                    "by_pattern_timeframe_tuning": res["metrics"]["tuning"]["by_pattern_timeframe"],
                }
            )
        return {
            "param": param,
            "base_value": getattr(base, param),
            "rows": rows,
            "note": "The sweep reports how each value affects precision and recall; it does not select a value. Never pick a value for producing the most detections.",
        }

    # ------------------------------------------------------------ storage
    def save_run(self, kind: str, cfg: DetectionConfig, overrides: dict | None, label_count: int, summary: dict, details: dict | None) -> str:
        run_id = f"{kind}-{uuid.uuid4().hex[:10]}"
        with self.s.engine.begin() as conn:
            conn.execute(
                db.evaluation_runs.insert().values(
                    run_id=run_id,
                    created_at=db.now_ist_iso(),
                    kind=kind,
                    config_hash=config_hash(cfg),
                    config_version=cfg.version,
                    overrides_json=db.dumps(overrides or {}),
                    label_count=label_count,
                    summary_json=db.dumps(summary),
                    details_json=db.dumps(details) if details is not None else None,
                )
            )
        return run_id


def _delta(a: dict | None, b: dict | None, key: str) -> float | None:
    if not a or not b or a.get(key) is None or b.get(key) is None:
        return None
    return round(b[key] - a[key], 4)


__all__ = ["EvalOptions", "Evaluator", "ConfigOverrideError", "make_variant", "summarize"]
