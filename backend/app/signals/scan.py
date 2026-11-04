"""Per-symbol run of the advanced event scanners inside a scan (shared candles and ATR)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import SignalConfig, config_hash
from .breakouts import crossing_events, fresh_kind, fresh_params, touch_records, w52_kind, w52_params, week52_state
from .volume import volume_shocks


@dataclass
class SignalOutputs:
    fresh_events: list[dict[str, Any]] = field(default_factory=list)
    fresh_touches: list[dict[str, Any]] = field(default_factory=list)
    w52_events: list[dict[str, Any]] = field(default_factory=list)
    w52_touches: list[dict[str, Any]] = field(default_factory=list)
    w52_state: dict[str, Any] = field(default_factory=dict)
    shocks: list[dict[str, Any]] = field(default_factory=list)


def run_signals(frame: pd.DataFrame, timeframe: str, atr: np.ndarray, ema20: np.ndarray | None, ema50: np.ndarray | None, cfg: SignalConfig, symbol: str) -> SignalOutputs:
    """Fresh breakouts and 52-week events run on daily candles only; the Volume
    Shocker runs on every scan timeframe (same-time-of-day reference intraday)."""
    out = SignalOutputs()
    n = len(frame)
    if n == 0:
        return out
    chash = config_hash(cfg)
    if timeframe == "1d":
        nd = cfg.fresh_lookback_days
        out.fresh_events, walks = crossing_events(
            frame, atr, fresh_params(cfg), symbol=symbol, kind_of=lambda d: fresh_kind(d, nd), config_hash=chash,
            since=n - cfg.fresh_store_sessions, volume_lookback=cfg.fresh_volume_lookback, first_lookback=cfg.fresh_first_lookback_sessions,
        )
        out.fresh_touches = touch_records(frame, walks, lambda d: fresh_kind(d, nd), since=n - cfg.touch_store_sessions)
        out.w52_events, w52walks = crossing_events(
            frame, atr, w52_params(cfg), symbol=symbol, kind_of=w52_kind, config_hash=chash,
            since=n - cfg.w52_store_sessions, volume_lookback=cfg.w52_volume_lookback, first_lookback=cfg.fresh_first_lookback_sessions,
        )
        out.w52_touches = touch_records(frame, w52walks, w52_kind, since=n - cfg.touch_store_sessions)
        out.w52_state = week52_state(frame, atr, cfg, w52walks)
    out.shocks = volume_shocks(frame, timeframe, atr, ema20, ema50, cfg, since=n - cfg.vs_store_bars)
    return out
