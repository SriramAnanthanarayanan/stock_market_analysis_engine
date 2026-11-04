"""Pydantic request/response models, including the versioned geometry contract."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

Universe = Literal["nifty500", "fno", "non_fno", "all"]
Timeframe = Literal["1h", "4h", "1d", "1w"]
StatusSlug = Literal["developing", "forming", "validated", "near_upper", "near_lower", "broke_above", "broke_below", "invalidated"]
FnoStatus = Literal["YES", "NO", "UNKNOWN"]

# ------------------------------------------------------------- geometry v1.0


class GeoPoint(BaseModel):
    """A chart point. ``t`` is the candle start (Unix seconds), ``i`` the bar
    index in the detector's series, ``p`` the price on the detector's basis."""

    t: int
    i: int
    p: float
    projected: bool = False


class LineElement(BaseModel):
    type: Literal["line"]
    id: str
    role: str
    style: Literal["solid", "dashed"]
    points: list[GeoPoint] = Field(min_length=2)


class BandElement(BaseModel):
    type: Literal["band"]
    id: str
    role: str
    upper: list[GeoPoint] = Field(min_length=2)
    lower: list[GeoPoint] = Field(min_length=2)


class ZoneElement(BaseModel):
    type: Literal["zone"]
    id: str
    role: str
    t_start: int
    t_end: int
    p_low: float
    p_high: float
    label: str = ""


class MarkerElement(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["marker"]
    id: str
    role: str
    t: int
    i: int
    p: float
    projected: bool = False
    label: str = ""


GeometryElement = Annotated[Union[LineElement, BandElement, ZoneElement, MarkerElement], Field(discriminator="type")]


class FormationRef(BaseModel):
    start: GeoPoint
    end: GeoPoint
    length: int


class LifecycleModel(BaseModel):
    """Frozen-geometry evaluation model (schema 1.1): how status is derived."""

    kind: Literal["band", "neckline_down", "neckline_up"]
    extreme: float | None = None
    apex_index: float | None = None


class Geometry(BaseModel):
    """Geometry contract. 1.0 = P1 line patterns; 1.1 adds ``lifecycle_model``
    and the roles pole / pole_start / pole_end / neckline / invalidation_level /
    peak / trough / provisional_pivot. 1.0 payloads remain valid."""

    schema_version: Literal["1.0", "1.1"]
    detection_id: str
    pattern: str
    timeframe: Timeframe
    price_basis: str
    time_basis: str
    formation: FormationRef
    last_candle: GeoPoint
    lifecycle_model: LifecycleModel | None = None
    elements: list[GeometryElement]


# ------------------------------------------------------------------ results


class QualityComponentOut(BaseModel):
    name: str
    weight: float
    raw: float | None
    score: float
    formula: str


class GateOut(BaseModel):
    name: str
    passed: bool
    value: float | int | str | None
    threshold: str
    group: str


class LifecycleEventOut(BaseModel):
    time: str
    t: int | None = None
    index: int | None = None
    event: str
    status: str | None = None
    detail: dict[str, Any] = {}


class Match(BaseModel):
    pattern_id: str
    scan_id: str | None
    symbol: str
    company: str | None
    fno_status: FnoStatus
    in_nifty500: bool
    pattern: str
    pattern_name: str
    category: str
    family: str | None
    timeframe: Timeframe
    status: StatusSlug
    status_label: str
    status_reason: str | None
    quality: float
    quality_label: str
    formation_start: str
    formation_end: str
    formation_length: int
    upper_touches: int | None
    lower_touches: int | None
    width_pct: float | None
    move_pct: float | None
    volume_ratio: float | None
    last_close: float | None
    last_candle_time: str | None
    metrics: dict[str, Any]
    config_version: str
    config_hash: str


class ScanSummary(BaseModel):
    scan_id: str
    state: str
    phase: str | None
    trigger: str
    universe: str
    timeframe: Timeframe
    asof: str | None
    patterns: list[str] | None
    config_version: str
    config_hash: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    data_cutoff: str | None
    total_symbols: int | None
    processed_symbols: int | None
    successful_symbols: int | None
    failed_symbols: int | None
    detection_count: int | None
    message: str | None
    warnings: list[str] = []


class ScanResponse(BaseModel):
    requested: dict[str, Any]
    scan: ScanSummary | None
    active_scan: ScanSummary | None
    detection_counts: dict[str, Any]
    symbol_counts: dict[str, int]
    total_matches: int
    matches: list[Match]
    note: str | None = None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: Universe = "all"
    timeframe: Timeframe = "1d"
    patterns: list[str] | None = None
    category: str | None = None
    asof: date | None = None
    full_refresh: bool = False


class RefreshResponse(BaseModel):
    scan_id: str
    created: bool
    state: str


class Candle(BaseModel):
    t: int
    i: int
    o: float
    h: float
    l: float
    c: float
    v: float | None


class FutureBar(BaseModel):
    """Projected start of a bar after the last candle (NSE trading calendar).
    Rendered as chart whitespace so projected geometry keeps its bar spacing."""

    i: int
    t: int


class SwingOut(BaseModel):
    t: int
    i: int
    p: float
    kind: Literal["high", "low"]


class SRZoneOut(BaseModel):
    zone_id: str
    p_low: float
    p_high: float
    width: float
    role: Literal["support", "resistance"]
    status: Literal["intact", "broken", "retested"]
    side: Literal["above", "below"]
    touches: int
    touch_times: list[str]
    first_touch_time: str | None
    last_touch_time: str | None
    broken_time: str | None
    retest_time: str | None
    flips: int
    distance_pct: float
    distance_atr: float | None
    median_atr: float
    t_start: int
    t_end: int
    events: list[dict[str, Any]] = []


class ChartDetection(Match):
    quality_components: list[QualityComponentOut]
    gates: list[GateOut]
    explanation: list[str]
    lifecycle: list[LifecycleEventOut]
    geometry: Geometry


class DataSourceInfo(BaseModel):
    """Which prices the chart shows (spec 3.1): provider, price basis, and the basis of the scan it came from."""

    provider: str
    price_basis: str
    price_basis_label: str
    scan_price_basis: str | None = None
    adjusted_close_available: bool
    note: str


class ChartResponse(BaseModel):
    symbol: str
    company: str | None
    fno_status: FnoStatus
    in_nifty500: bool
    timeframe: Timeframe
    asof: str | None
    source: Literal["scan", "adhoc"]
    scan_id: str | None
    data_cutoff: str | None
    candles: list[Candle]
    future_bars: list[FutureBar] = []
    swing_points: list[SwingOut]
    swing_k: int
    detections: list[ChartDetection]
    selected_detection_id: str | None
    sr_zones: list[SRZoneOut] = []
    sr_zones_note: str
    newer_data_available: bool
    notes: list[str] = []
    data_source: DataSourceInfo | None = None


class PatternInfo(BaseModel):
    slug: str
    name: str
    category: str
    category_label: str
    family: str
    implemented: bool
    phase: str


# ------------------------------------------------------------------- P3


class LinePointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int
    p: float = Field(gt=0)


class SwingPointIn(LinePointIn):
    kind: Literal["high", "low"]


class LabelIn(BaseModel):
    """A human label. Times are candle start timestamps (Unix seconds) of the
    labelled timeframe; they must exist in the chart as of ``asof``."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&\-_.]+$")
    timeframe: Timeframe
    pattern: str
    verdict: Literal["valid", "invalid", "near_miss", "false_detection"]
    asof: date
    formation_start_t: int
    formation_end_t: int
    upper_line: list[LinePointIn] | None = Field(default=None, min_length=2, max_length=2)
    lower_line: list[LinePointIn] | None = Field(default=None, min_length=2, max_length=2)
    swing_points: list[SwingPointIn] | None = Field(default=None, max_length=50)
    dataset: Literal["tuning", "validation"] | None = None
    detection_id: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)


class LabelOut(BaseModel):
    label_id: str
    symbol: str
    timeframe: Timeframe
    pattern: str
    verdict: str
    asof: str
    formation_start: str
    formation_end: str
    upper_line: list[dict[str, Any]] | None
    lower_line: list[dict[str, Any]] | None
    swing_points: list[dict[str, Any]] | None
    dataset: str
    detection_id: str | None
    notes: str | None
    created_at: str
    updated_at: str


class EvalOptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iou_threshold: float = Field(0.5, gt=0, le=1)
    include_forming: bool = False
    fp_scope: Literal["labelled_patterns", "all"] = "labelled_patterns"


class LabelFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str | None = None
    timeframe: Timeframe | None = None
    pattern: str | None = None
    dataset: Literal["tuning", "validation"] | None = None


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overrides: dict[str, Any] = {}
    options: EvalOptionsIn = EvalOptionsIn()
    labels: LabelFilter = LabelFilter()


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a_overrides: dict[str, Any] = {}
    b_overrides: dict[str, Any]
    options: EvalOptionsIn = EvalOptionsIn()
    labels: LabelFilter = LabelFilter()


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    param: str
    values: list[float | int | bool] = Field(min_length=1, max_length=20)
    options: EvalOptionsIn = EvalOptionsIn()
    labels: LabelFilter = LabelFilter()


class TrackOut(BaseModel):
    track_id: str
    symbol: str
    timeframe: Timeframe
    pattern: str
    pattern_name: str
    family: str
    state: str
    status: str
    status_label: str
    status_reason: str | None
    closed_reason: str | None
    first_detected_at: str
    last_detected_at: str
    formation_start: str
    formation_end: str
    quality_first: float | None
    quality_last: float | None
    quality_max: float | None
    breakout_time: str | None
    breakout_direction: str | None
    breakout_close: float | None
    invalidated_time: str | None
    archived_time: str | None
    closed_time: str | None
    config_version: str
    current_detection_id: str
    last_scan_id: str
    outcome: dict[str, Any] | None
