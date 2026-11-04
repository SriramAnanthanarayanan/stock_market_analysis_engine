"""Shared types for every detector: catalogue, statuses, lines, candidates.

Every detector implements ``PatternDetector.detect(ctx) -> list[Candidate]``.
The engine then deduplicates candidates, runs the lifecycle state machine and
builds the versioned chart geometry. Detectors never touch the provider, the
database or the clock.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from ..config import DetectionConfig
from ..indicators import SwingPoints, atr_wilder, swing_points

# ----------------------------------------------------------------- catalogue

CAT_CHANNELS = "channels_ranges"
CAT_TRIANGLES = "triangles_wedges"
CAT_CONTINUATION = "continuation"
CAT_REVERSAL = "reversal"

CATEGORY_LABELS = {
    CAT_CHANNELS: "Channels & ranges",
    CAT_TRIANGLES: "Triangles & wedges",
    CAT_CONTINUATION: "Continuation",
    CAT_REVERSAL: "Reversal",
}


@dataclass(frozen=True)
class PatternSpec:
    slug: str
    name: str
    category: str
    family: str
    implemented: bool
    phase: str


CATALOGUE: tuple[PatternSpec, ...] = (
    PatternSpec("falling_channel", "Falling Channel", CAT_CHANNELS, "parallel", True, "P1"),
    PatternSpec("rising_channel", "Rising Channel", CAT_CHANNELS, "parallel", True, "P1"),
    PatternSpec("horizontal_channel", "Horizontal Channel", CAT_CHANNELS, "parallel", True, "P1"),
    PatternSpec("rectangle", "Rectangle", CAT_CHANNELS, "parallel", True, "P1"),
    PatternSpec("ascending_triangle", "Ascending Triangle", CAT_TRIANGLES, "converging", True, "P1"),
    PatternSpec("descending_triangle", "Descending Triangle", CAT_TRIANGLES, "converging", True, "P1"),
    PatternSpec("symmetrical_triangle", "Symmetrical Triangle", CAT_TRIANGLES, "converging", True, "P1"),
    PatternSpec("falling_wedge", "Falling Wedge", CAT_TRIANGLES, "converging", True, "P1"),
    PatternSpec("rising_wedge", "Rising Wedge", CAT_TRIANGLES, "converging", True, "P1"),
    PatternSpec("bullish_flag", "Bullish Flag", CAT_CONTINUATION, "continuation", True, "P2"),
    PatternSpec("bearish_flag", "Bearish Flag", CAT_CONTINUATION, "continuation", True, "P2"),
    PatternSpec("bullish_pennant", "Bullish Pennant", CAT_CONTINUATION, "continuation", True, "P2"),
    PatternSpec("bearish_pennant", "Bearish Pennant", CAT_CONTINUATION, "continuation", True, "P2"),
    PatternSpec("double_top", "Double Top", CAT_REVERSAL, "reversal", True, "P2"),
    PatternSpec("double_bottom", "Double Bottom", CAT_REVERSAL, "reversal", True, "P2"),
    PatternSpec("head_shoulders", "Head & Shoulders", CAT_REVERSAL, "reversal", True, "P2"),
    PatternSpec("inverse_head_shoulders", "Inverse Head & Shoulders", CAT_REVERSAL, "reversal", True, "P2"),
)
SPEC_BY_SLUG: dict[str, PatternSpec] = {p.slug: p for p in CATALOGUE}


class Status(str, Enum):
    DEVELOPING = "developing"
    FORMING = "forming"
    VALIDATED = "validated"
    NEAR_UPPER = "near_upper"
    NEAR_LOWER = "near_lower"
    BROKE_ABOVE = "broke_above"
    BROKE_BELOW = "broke_below"
    INVALIDATED = "invalidated"


STATUS_LABELS = {
    Status.DEVELOPING: "Developing",
    Status.FORMING: "Forming",
    Status.VALIDATED: "Validated",
    Status.NEAR_UPPER: "Near Upper Boundary",
    Status.NEAR_LOWER: "Near Lower Boundary",
    Status.BROKE_ABOVE: "Broke Above",
    Status.BROKE_BELOW: "Broke Below",
    Status.INVALIDATED: "Invalidated",
}

# Lower number wins when several states could apply (spec section 10.5).
STATUS_PRECEDENCE = {
    Status.INVALIDATED: 1,
    Status.BROKE_ABOVE: 2,
    Status.BROKE_BELOW: 2,
    Status.NEAR_UPPER: 3,
    Status.NEAR_LOWER: 3,
    Status.VALIDATED: 4,
    Status.FORMING: 5,
    Status.DEVELOPING: 6,
}

# ------------------------------------------------------------------- geometry


@dataclass(frozen=True)
class Line:
    """Straight line in (absolute bar index, price) space: p(i) = intercept + slope * (i - x0)."""

    x0: int
    intercept: float
    slope: float

    def at(self, i):
        """Price at absolute bar index ``i`` (scalar or array)."""
        if isinstance(i, np.ndarray):
            return self.intercept + self.slope * (i.astype(float) - self.x0)
        return self.intercept + self.slope * (float(i) - self.x0)


@dataclass(frozen=True)
class Touch:
    index: int
    price: float
    boundary: str  # "upper" | "lower"
    residual: float  # |line - price|
    tolerance: float


@dataclass
class Gate:
    name: str
    passed: bool
    value: Any
    threshold: str
    group: str = "structure"  # "touch" gates may be relaxed for Developing

    def as_dict(self) -> dict[str, Any]:
        v = self.value
        if isinstance(v, (np.floating, float)):
            v = round(float(v), 6)
        elif isinstance(v, np.integer):
            v = int(v)
        return {"name": self.name, "passed": bool(self.passed), "value": v, "threshold": self.threshold, "group": self.group}


@dataclass
class QualityComponent:
    name: str
    weight: float
    raw: float
    score: float  # clamped to [0, 1]
    formula: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "raw": round(float(self.raw), 6) if np.isfinite(self.raw) else None,
            "score": round(float(self.score), 6),
            "formula": self.formula,
        }


def sdiv(num: float, den: float) -> float:
    """Division for quality formulas whose denominator comes from configuration.
    A degenerate variant (e.g. a minimum equal to its full-score level) gives an
    infinite raw value, which ``clamp01`` turns into a 0 score - never a crash."""
    if den == 0:
        return 0.0 if num == 0 else float("inf") * (1 if num > 0 else -1)
    return num / den


def clamp01(v: float) -> float:
    if not np.isfinite(v):
        return 0.0
    return max(0.0, min(1.0, float(v)))


def quality_score(components: list[QualityComponent]) -> float:
    total_w = sum(c.weight for c in components)
    if total_w <= 0:
        return 0.0
    return 100.0 * sum((c.weight / total_w) * clamp01(c.score) for c in components)


@dataclass
class Candidate:
    """A pattern candidate produced by a detector (before lifecycle/geometry)."""

    pattern: str
    family: str
    start: int
    end: int
    upper: Line | None
    lower: Line | None
    touches: list[Touch]
    swing_highs: list[int]
    swing_lows: list[int]
    fit_highs: list[int]
    fit_lows: list[int]
    metrics: dict[str, float]
    gates: list[Gate]
    components: list[QualityComponent]
    quality: float
    developing: bool = False
    rejected: bool = False  # only produced when SeriesContext.collect_rejections is set
    apex_index: float | None = None
    first_touch: int | None = None
    notes: list[str] = field(default_factory=list)
    # --- P2 extensions -------------------------------------------------------
    provisional: bool = False  # Forming: provisional pivot / 3-4 bar consolidation
    group: str | None = None  # dedup group (defaults to family)
    lifecycle_kind: str = "band"  # band | neckline_down | neckline_up
    extreme: float | None = None  # reversal invalidation level
    pivots: list[tuple[str, int, float]] = field(default_factory=list)  # (label, bar index, price)
    pole: tuple[int, float, int, float] | None = None  # (start idx, start price, end idx, end price)
    region_start: int | None = None  # first bar of the drawn boundaries (consolidation start for flags)

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def dedup_group(self) -> str:
        return self.group or self.family

    @property
    def upper_touches(self) -> int:
        return sum(1 for t in self.touches if t.boundary == "upper")

    @property
    def lower_touches(self) -> int:
        return sum(1 for t in self.touches if t.boundary == "lower")


# -------------------------------------------------------------------- context


class SeriesContext:
    """Completed candles for one symbol/timeframe plus cached indicators."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        frame: pd.DataFrame,
        cfg: DetectionConfig,
        collect_rejections: bool = False,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.cfg = cfg
        # Diagnostics only (self-test / near-miss tests): keep classified but
        # rejected candidates so the failing gate can be inspected.
        self.collect_rejections = collect_rejections
        self.times = pd.DatetimeIndex(frame.index)
        self.open = frame["open"].to_numpy(dtype=float)
        self.high = frame["high"].to_numpy(dtype=float)
        self.low = frame["low"].to_numpy(dtype=float)
        self.close = frame["close"].to_numpy(dtype=float)
        self.volume = frame["volume"].to_numpy(dtype=float)
        # Dividend-adjusted close (E1): only for return calculations, never for levels.
        self.adj_close = frame["adj_close"].to_numpy(dtype=float) if "adj_close" in frame.columns else None
        self.n = len(frame)
        self.atr = atr_wilder(self.high, self.low, self.close, cfg.atr_period)
        self.fit_end = self.n - 1 - cfg.fit_exclude_last_bars
        self._swings: dict[int, SwingPoints] = {}

    def swings(self, k: int) -> SwingPoints:
        if k not in self._swings:
            self._swings[k] = swing_points(self.high, self.low, k)
        return self._swings[k]

    @property
    def last(self) -> int:
        return self.n - 1

    def first_valid_atr(self) -> int:
        valid = np.flatnonzero(np.isfinite(self.atr))
        return int(valid[0]) if len(valid) else self.n


class PatternDetector(ABC):
    family: str = ""
    patterns: tuple[str, ...] = ()

    @abstractmethod
    def detect(self, ctx: SeriesContext) -> list[Candidate]:
        """Return valid and Developing candidates (all candidate lengths)."""
