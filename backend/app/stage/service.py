"""Stage Analysis / RS Line service for stocks and indices (E3).

Stocks: weekly candles from the market-data cache (raw basis), benchmark = the
official NIFTY 50 weekly series (StageConfig.benchmark). Indices: official weekly
series of the index. Both are aligned per rs_line.align_benchmark.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from ..config import TIMEFRAME_SOURCE_INTERVAL, config_hash
from ..data.ohlcv import prepare_series
from ..indices.data import IndexDataError
from .analysis import STAGE_LABELS, evaluate


class StageError(RuntimeError):
    pass


class StageService:
    def __init__(self, services):
        self.s = services
        self._memo: dict[tuple, Any] = {}

    @property
    def cfg(self):
        return self.s.stage_cfg

    def benchmark(self, timeframe: str, cutoff: datetime, benchmark: str | None = None) -> pd.Series:
        code = benchmark or self.cfg.benchmark
        try:
            return self.s.indices.frame(code, timeframe, cutoff)["close"]
        except IndexDataError as exc:
            raise StageError(f"benchmark {code} unavailable on {timeframe}: {exc}")

    def frame(self, symbol: str, instrument: str, timeframe: str, cutoff: datetime) -> pd.DataFrame:
        if instrument == "index":
            try:
                return self.s.indices.frame(symbol, timeframe, cutoff)
            except IndexDataError as exc:
                raise StageError(str(exc))
        interval = TIMEFRAME_SOURCE_INTERVAL[timeframe]
        raw = self.s.store.load_current(symbol, interval)
        if raw is None:
            self.s.market_data.ensure([symbol], interval, cutoff=cutoff)  # same on-demand download as /api/chart
            raw = self.s.store.load_current(symbol, interval)
        if raw is None or raw.empty:
            raise StageError(f"no cached {timeframe} data for {symbol} on the current price basis")
        return prepare_series(raw, symbol, timeframe, cutoff, self.s.calendar).frame

    def evaluate(self, symbol: str, instrument: str, cutoff: datetime) -> dict[str, Any]:
        weekly = self.frame(symbol, instrument, "1w", cutoff)
        return evaluate(weekly, self.benchmark("1w", cutoff), self.cfg)

    def index_table(self, cutoff: datetime) -> list[dict[str, Any]]:
        """Stage of every analysable index (memoised per latest completed week + data version)."""
        week_end = self.s.calendar.latest_completed_bar_end("1w", cutoff).isoformat()
        key = (week_end, self.s.indices.data_version(), config_hash(self.cfg))
        if key in self._memo:
            return self._memo[key]
        try:
            bench = self.benchmark("1w", cutoff)
        except StageError as exc:
            return [{"symbol": None, "stage": "data_unavailable", "reasons": [str(exc)]}]
        rows = []
        for it in self.s.indices.registry.all(active_only=True):
            if not it["analysis_enabled"]:
                continue
            base = {"symbol": it["code"], "company": it["name"], "instrument": "index", "category": it["category"], "subcategory": it["subcategory"], "fno_status": None, "in_nifty500": None}
            try:
                r = evaluate(self.s.indices.frame(it["code"], "1w", cutoff), bench, self.cfg)
            except IndexDataError as exc:
                rows.append({**base, "stage": "data_unavailable", "stage_label": STAGE_LABELS["data_unavailable"], "metrics": {}, "gates": [], "reasons": [str(exc)], "rank_key": None, "stage_since": None, "previous_stage": None})
                continue
            r.pop("series", None)
            rows.append({**base, **r})
        if len(self._memo) > 16:
            self._memo.clear()
        self._memo[key] = rows
        return rows

    def index_stage_lookup(self, cutoff: datetime) -> dict[str, str]:
        return {r["symbol"]: r["stage"] for r in self.index_table(cutoff) if r.get("symbol")}
