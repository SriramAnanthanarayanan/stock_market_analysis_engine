"""Index Analysis (E2, spec 9.3): returns, indicators, relative performance and
RRG quadrant per index, all on completed candles of the official NSE index data.

Returns are calendar based: ``close_latest / close_base - 1`` where the base is
the close of the last trading day on or before (latest date - offset). The
benchmark uses the same dates. Nothing is computed when the base date precedes
the index history (``None``, never extrapolated).
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import database as db
from ..config import IndexConfig, TechnicalConfig, config_hash
from ..indicators.core import atr_wilder
from ..indicators.technical import ema, rsi
from ..rrg import rrg_entry
from ..timeutil import iso
from .data import IndexDataError, IndexDataService
from .registry import CATEGORY_LABELS


def _offset(spec: str):
    m = re.fullmatch(r"(\d+)([DM])", spec)
    if not m:
        raise ValueError(f"bad horizon {spec!r}")
    n, unit = int(m.group(1)), m.group(2)
    return pd.Timedelta(days=n) if unit == "D" else pd.DateOffset(months=n)


def base_close(daily: pd.DataFrame, spec: str) -> tuple[float | None, str | None]:
    if daily.empty:
        return None, None
    target = daily.index[-1] - _offset(spec)
    prior = daily[daily.index <= target]
    if prior.empty:
        return None, None
    return float(prior["close"].iloc[-1]), iso(prior.index[-1])


def calendar_return(daily: pd.DataFrame, spec: str) -> float | None:
    b, _ = base_close(daily, spec)
    if b is None or b == 0:
        return None
    return round((float(daily["close"].iloc[-1]) / b - 1.0) * 100.0, 3)


def relative_performance(daily: pd.DataFrame, bench: pd.DataFrame, spec: str) -> float | None:
    """(1 + r_index) / (1 + r_benchmark) - 1 over the same calendar window, in %."""
    ri = calendar_return(daily, spec)
    if ri is None or bench.empty:
        return None
    # Benchmark measured over the index's dates (same end, same base rule).
    b_end = bench[bench.index <= daily.index[-1]]
    rb = calendar_return(b_end, spec) if not b_end.empty else None
    if rb is None:
        return None
    return round(((1 + ri / 100.0) / (1 + rb / 100.0) - 1.0) * 100.0, 3)


def _f(x: Any, nd: int = 4) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


class IndexAnalysis:
    def __init__(self, data: IndexDataService, cfg: IndexConfig, tcfg: TechnicalConfig):
        self.data = data
        self.cfg = cfg
        self.tcfg = tcfg
        self._memo: dict[tuple, Any] = {}
        self.stage_lookup = None  # E3: callable(cutoff) -> {code: stage}

    def _key(self, kind: str, cutoff: datetime, *parts: Any) -> tuple:
        """Results depend on the completed candles only: key on the latest completed
        daily bar at the cutoff (not the cutoff itself) plus the data version."""
        bar = self.data.calendar.latest_completed_bar_end("1d", cutoff).isoformat()
        return (kind, bar, self.data.data_version(), config_hash(self.cfg), config_hash(self.tcfg), *parts)

    def _remember(self, key: tuple, value: Any) -> Any:
        if len(self._memo) > 64:
            self._memo.clear()
        self._memo[key] = value
        return value

    def _frames(self, items: list[dict[str, Any]], timeframe: str, cutoff: datetime) -> dict[str, pd.DataFrame]:
        out = {}
        for it in items:
            try:
                out[it["code"]] = self.data.frame(it["code"], timeframe, cutoff)
            except IndexDataError:
                continue
        return out

    def rrg(self, benchmark: str, cutoff: datetime, trail: int, codes: list[str] | None = None) -> dict[str, Any]:
        key = self._key("rrg", cutoff, benchmark, trail, tuple(sorted(codes)) if codes else None)
        if key in self._memo:
            return copy.deepcopy(self._memo[key])
        return copy.deepcopy(self._remember(key, self._rrg(benchmark, cutoff, trail, codes)))

    def _rrg(self, benchmark: str, cutoff: datetime, trail: int, codes: list[str] | None = None) -> dict[str, Any]:
        bench_item = self.data.registry.get(benchmark)
        if bench_item is None:
            raise IndexDataError(f"unknown benchmark {benchmark!r}")
        bench_w = self.data.frame(benchmark, "1w", cutoff)
        items = [r for r in self.data.registry.all(active_only=True) if r["analysis_enabled"] and r["code"] != benchmark]
        if codes:
            items = [r for r in items if r["code"] in set(codes)]
        entries = []
        for it in items:
            try:
                w = self.data.frame(it["code"], "1w", cutoff)
            except IndexDataError as exc:
                entries.append({"code": it["code"], "status": "data_unavailable", "reason": str(exc), "trail": [], "latest": None})
                continue
            e = rrg_entry(it["code"], w["close"], bench_w["close"], self.cfg, trail)
            entries.append(e)
        meta = {r["code"]: r for r in items}
        for e in entries:
            r = meta.get(e["code"], {})
            e.update(name=r.get("name"), category=r.get("category"), category_label=CATEGORY_LABELS.get(r.get("category", ""), r.get("category")), subcategory=r.get("subcategory"))
        return {
            "benchmark": benchmark,
            "benchmark_name": bench_item["name"],
            "benchmark_last_week": iso(bench_w.index[-1]) if len(bench_w) else None,
            "data_basis": "Official NSE price-return index levels (PR); weekly closes of completed weeks. Total-return index data is not available from the implemented providers.",
            "params": {"rs_period": self.cfg.rrg_rs_period, "momentum_period": self.cfg.rrg_momentum_period, "trail_weeks": trail, "center": self.cfg.rrg_center, "max_gap_weeks": self.cfg.rrg_max_gap_weeks, "lookback_weeks": self.cfg.rrg_lookback_weeks},
            "config_version": self.cfg.version,
            "config_hash": config_hash(self.cfg),
            "method": "Internally implemented RRG-style calculation (RS_Ratio = 100 x RS / EMA(RS); RS_Momentum = 100 x RS_Ratio / EMA(RS_Ratio)); not the proprietary JdK RRG.",
            "items": entries,
        }

    def overview(self, cutoff: datetime, timeframe: str, benchmark: str, codes: list[str] | None = None) -> dict[str, Any]:
        key = self._key("overview", cutoff, timeframe, benchmark, tuple(sorted(codes)) if codes else None)
        if key in self._memo:
            return copy.deepcopy(self._memo[key])
        return copy.deepcopy(self._remember(key, self._overview(cutoff, timeframe, benchmark, codes)))

    def _overview(self, cutoff: datetime, timeframe: str, benchmark: str, codes: list[str] | None) -> dict[str, Any]:
        items = [r for r in self.data.registry.all(active_only=True) if not codes or r["code"] in set(codes)]
        try:
            bench_d = self.data.frame(benchmark, "1d", cutoff)
        except IndexDataError:
            bench_d = pd.DataFrame()
        rrg = {e["code"]: e for e in self.rrg(benchmark, cutoff, self.cfg.rrg_trail_weeks, codes)["items"]} if not bench_d.empty else {}
        stages: dict[str, str] = {}
        if self.stage_lookup is not None:
            try:
                stages = self.stage_lookup(cutoff)
            except Exception:  # stage data problems never break the overview
                stages = {}
        rows = []
        for it in items:
            row: dict[str, Any] = {
                "code": it["code"],
                "name": it["name"],
                "category": it["category"],
                "category_label": CATEGORY_LABELS.get(it["category"], it["category"]),
                "subcategory": it["subcategory"],
                "index_type": it["index_type"],
                "analysis_enabled": bool(it["analysis_enabled"]),
                "data_status": it["data_status"],
                "data_status_reason": it["data_status_reason"],
                "intraday": it.get("intraday_status") == "verified",
                "stage": stages.get(it["code"]),  # Stage Analysis (E3)
            }
            if not it["analysis_enabled"]:
                rows.append(row)
                continue
            try:
                daily = self.data.frame(it["code"], "1d", cutoff)
                tf_frame = daily if timeframe == "1d" else self.data.frame(it["code"], "1w", cutoff)
            except IndexDataError as exc:
                row["data_status_reason"] = str(exc)
                rows.append(row)
                continue
            if daily.empty or tf_frame.empty:
                rows.append(row)
                continue
            c = tf_frame["close"].to_numpy(dtype=float)
            h, l = tf_frame["high"].to_numpy(dtype=float), tf_frame["low"].to_numpy(dtype=float)
            r = rsi(c, self.tcfg.rsi_period)
            a = atr_wilder(h, l, c, self.tcfg.atr_period)
            e50, e200 = ema(c, 50, self.tcfg.ema_seed), ema(c, 200, self.tcfg.ema_seed)
            close = float(c[-1])

            def pos(v: float) -> str | None:
                return None if not np.isfinite(v) else ("above" if close > v else "below" if close < v else "at")

            row.update(
                latest_close=_f(daily["close"].iloc[-1], 2),
                latest_date=iso(daily.index[-1]),
                returns={lab: calendar_return(daily, spec) for lab, spec in self.cfg.return_horizons},
                rsi=_f(r[-1], 2),
                atr_pct=_f(a[-1] / close * 100.0, 3) if np.isfinite(a[-1]) else None,
                ema50=pos(e50[-1]),
                ema200=pos(e200[-1]),
                rs_vs_benchmark=relative_performance(daily, bench_d, self.cfg.rs_horizon) if it["code"] != benchmark else 0.0,
                rrg_quadrant=(rrg.get(it["code"], {}).get("latest") or {}).get("quadrant"),
                rrg_status=rrg.get(it["code"], {}).get("status") if it["code"] != benchmark else "benchmark",
            )
            rows.append(row)
        return {
            "benchmark": benchmark,
            "timeframe": timeframe,
            "rs_horizon": self.cfg.rs_horizon,
            "return_horizons": [lab for lab, _ in self.cfg.return_horizons],
            "config_version": self.cfg.version,
            "config_hash": config_hash(self.cfg),
            "count": len(rows),
            "items": rows,
            "notes": [
                "Returns use completed daily closes of the official NSE index history (calendar windows: close on the last trading day on or before the start date).",
                f"RSI({self.tcfg.rsi_period}), ATR({self.tcfg.atr_period}) % and EMA 50/200 position on the {timeframe.upper()} timeframe.",
                f"RS vs benchmark = (1 + index return) / (1 + benchmark return) - 1 over {self.cfg.rs_horizon}.",
            ],
        }
