"""Application settings and the versioned detection configuration.

Two kinds of configuration live here:

* ``Settings``: paths, ports, URLs and runtime switches. Changing these never
  changes what a detector reports.
* ``DetectionConfig``: every numeric threshold used by the detectors, the
  lifecycle state machine and the quality score. All values are *initial
  engineering defaults*, not validated technical-analysis constants.

Governance rules (see docs/validation.md):

* Every detection parameter has an entry in ``PARAM_META`` (description, unit,
  applicable patterns and timeframes). A test enforces this.
* ``config_hash()`` is a reproducible SHA-256 over all parameter values. It is
  persisted with every scan and every detection, so a parameter change never
  silently rewrites earlier results.
* ``KNOWN_CONFIG_HASHES`` pins the hash of each released ``version``. Changing a
  value without bumping ``version`` and registering the new hash fails the
  test-suite.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent

TIMEFRAMES: tuple[str, ...] = ("1h", "4h", "1d", "1w")
UNIVERSES: tuple[str, ...] = ("nifty500", "fno", "non_fno", "all")

# Provider interval that backs each analysis timeframe. 4H is resampled from 1H.
TIMEFRAME_SOURCE_INTERVAL: dict[str, str] = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Override with ``PAS_*`` environment variables."""

    backend_dir: Path
    data_dir: Path
    cache_dir: Path
    ssl_cache_dir: Path
    db_path: Path
    scheduler_enabled: bool
    startup_scans: bool
    network_enabled: bool
    host: str = "127.0.0.1"
    port: int = 8031

    nifty500_url: str = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    fno_url: str = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
    holidays_url: str = "https://www.nseindia.com/api/holiday-master?type=trading"
    # E2: official NSE index catalogue and index history (NSE Indices data on nseindia.com).
    index_catalogue_url: str = "https://www.nseindia.com/api/allIndices"
    index_history_url: str = "https://www.nseindia.com/api/historicalOR/indicesHistory"
    index_history_page: str = "https://www.nseindia.com/reports-indices-historical-index-data"
    # A new index cache downloads the last ``index_history_years`` first, then
    # keeps going back one year per request until ``index_backfill_empty_stop``
    # consecutive years come back empty (before the index existed) or the
    # floor year is reached. NIFTY 50 history on NSE starts in 1990.
    index_history_years: int = 10
    index_history_floor_year: int = 1990
    index_backfill_empty_stop: int = 2
    index_request_delay_s: float = 0.25

    # Market data download behaviour.
    yahoo_batch_size: int = 100
    download_max_attempts: int = 3
    download_backoff_base_s: float = 2.0
    incremental_overlap_bars: int = 5
    # Relative close difference on overlapping candles that indicates the
    # provider re-adjusted history (split/dividend) -> full re-download.
    adjustment_mismatch_tolerance: float = 0.005
    # Minutes after a bar's nominal end before the provider value is treated as
    # settled for scheduling/freshness purposes (NSE publishes the official
    # close after 15:30 IST).
    intraday_settle_minutes: int = 3
    daily_settle_minutes: int = 30
    # Symbols for which the provider returned no data are retried after this.
    failed_symbol_retry_minutes: int = 360

    # Universe file sanity bounds (a download outside them is rejected).
    nifty500_min_rows: int = 400
    nifty500_max_rows: int = 600
    fno_min_active: int = 100
    fno_max_active: int = 400

    # Chart payload sizing. chart_max_candles is high enough to send the whole
    # cached history (daily since 1996 is ~7,800 bars); the UI still opens on
    # the latest bars / the selected formation and the user scrolls back.
    chart_min_candles: int = 160
    chart_context_bars: int = 60
    chart_max_candles: int = 20000

    # Scan execution.
    scan_workers: int = 2

    # P3 outcome analytics (post-detection measurement; never used by detection).
    outcome_horizons: tuple[int, ...] = (5, 10, 20)
    outcome_min_sample: int = 30
    # Labels: deterministic default split - a symbol is in the validation set when
    # int(sha1(symbol), 16) % validation_modulus == 0 (about 1 in 5 stocks).
    validation_modulus: int = 5


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("PAS_DATA_DIR", BACKEND_DIR / "data"))
    cache_dir = Path(os.environ.get("PAS_CACHE_DIR", BACKEND_DIR / "cache"))
    ssl_cache_dir = Path(os.environ.get("PAS_SSL_CACHE_DIR", BACKEND_DIR / ".cache"))
    db_path = Path(os.environ.get("PAS_DB_PATH", data_dir / "screener.db"))
    return Settings(
        backend_dir=BACKEND_DIR,
        data_dir=data_dir,
        cache_dir=cache_dir,
        ssl_cache_dir=ssl_cache_dir,
        db_path=db_path,
        scheduler_enabled=_env_bool("PAS_SCHEDULER", True),
        startup_scans=_env_bool("PAS_STARTUP_SCANS", True),
        network_enabled=_env_bool("PAS_NETWORK", True),
        port=int(os.environ.get("PAS_PORT", "8031")),
    )


# ---------------------------------------------------------------------------
# Detection configuration
# ---------------------------------------------------------------------------


def _tf_min_move() -> dict[str, float]:
    return {"1h": 2.5, "4h": 4.0, "1d": 6.0, "1w": 10.0}


def _tf_width_band() -> dict[str, tuple[float, float]]:
    return {"1h": (1.0, 15.0), "4h": (1.5, 22.0), "1d": (3.0, 30.0), "1w": (5.0, 45.0)}


def _w_parallel() -> dict[str, float]:
    return {"parallelism": 25, "touches": 20, "containment": 20, "boundary_fit": 20, "trend_or_flatness": 15}


def _w_converging() -> dict[str, float]:
    return {"convergence": 20, "touches": 25, "containment": 20, "boundary_fit": 20, "touch_spread": 15}


def _w_continuation() -> dict[str, float]:
    return {"pole_strength": 30, "retracement": 25, "tightness": 20, "boundary_fit": 15, "volume_contraction": 10}


def _w_double() -> dict[str, float]:
    return {"similarity": 30, "depth": 25, "separation": 15, "prior_trend": 15, "volume": 15}


def _w_hs() -> dict[str, float]:
    return {"shoulder_similarity": 25, "time_symmetry": 20, "head_prominence": 20, "neckline_flatness": 15, "size": 20}


def _quality_labels() -> dict[str, float]:
    return {"Excellent": 85.0, "Strong": 70.0, "Moderate": 50.0}


@dataclass(frozen=True)
class DetectionConfig:
    version: str = "1.3.0"

    # --- shared definitions -------------------------------------------------
    atr_period: int = 14
    swing_k_structure: int = 3
    fit_exclude_last_bars: int = 3
    fit_refine_tolerance_mult: float = 2.0
    touch_tol_width_frac: float = 0.15
    touch_tol_atr_frac: float = 0.25
    touch_spread_min_frac: float = 0.40
    containment_slack_width_frac: float = 0.05
    containment_slack_atr_frac: float = 0.10
    close_trend_min_r2: float = 0.50
    break_buffer_atr_frac: float = 0.10
    line_state_move_frac: float = 0.15
    tf_min_move_pct: dict[str, float] = field(default_factory=_tf_min_move)
    tf_width_band_pct: dict[str, tuple[float, float]] = field(default_factory=_tf_width_band)
    dedup_iou_threshold: float = 0.60
    dedup_geometry_tolerance_frac: float = 0.15
    max_lead_before_first_touch_frac: float = 0.33
    suppress_collapsed_subwindows: bool = True

    # --- parallel family: channels and ranges ------------------------------
    parallel_lengths: tuple[int, ...] = (40, 60, 90, 120)
    parallel_min_upper_touches: int = 2
    parallel_min_lower_touches: int = 2
    parallel_min_total_touches: int = 5
    parallel_min_total_touches_flat: int = 6
    parallel_max_error: float = 0.25
    parallel_min_containment: float = 0.88
    channel_move_frac: float = 0.60
    rectangle_move_frac: float = 0.20
    rectangle_max_close_r2: float = 0.30

    # --- converging family: triangles and wedges ---------------------------
    converging_lengths: tuple[int, ...] = (30, 45, 60, 90, 120)
    converging_min_upper_touches: int = 2
    converging_min_lower_touches: int = 2
    converging_min_total_touches: int = 5
    converging_cr_min: float = 0.15
    converging_cr_max: float = 0.50
    converging_min_containment: float = 0.85
    apex_invalidation_bars: int = 3

    # --- continuation: flags and pennants ----------------------------------
    pole_lookback_bars: int = 15
    pole_min_bars: int = 3
    pole_max_bars: int = 15
    pole_min_atr_mult: float = 3.0
    pole_min_directional_frac: float = 0.60
    pole_min_atr_per_bar: float = 0.50
    flag_min_bars: int = 5
    flag_max_bars: int = 25
    pennant_max_bars: int = 20
    consolidation_max_pole_mult: float = 3.0
    flag_max_cr_deviation: float = 0.25
    flag_move_against_max_frac: float = 0.50
    flag_move_with_max_frac: float = 0.03
    flag_max_retracement: float = 0.50
    pennant_max_retracement: float = 0.38
    pennant_cr_min: float = 0.15
    pennant_cr_max: float = 0.60
    consolidation_max_width_pole_frac: float = 0.50
    continuation_min_upper_touches: int = 2
    continuation_min_lower_touches: int = 2
    continuation_touch_min_gap_bars: int = 2
    continuation_min_containment: float = 0.85
    forming_min_bars: int = 3
    forming_max_bars: int = 4
    volume_contraction_full: float = 0.70
    volume_contraction_zero: float = 1.00

    # --- reversals: double tops/bottoms, head & shoulders --------------------
    swing_k_reversal: int = 5
    reversal_recency_bars: int = 30
    double_min_separation: int = 10
    double_max_separation: int = 60
    reversal_min_height_atr: float = 3.0
    double_min_depth_move_frac: float = 1.0
    double_peak_diff_depth_frac: float = 0.25
    double_peak_diff_price_frac: float = 0.03
    hs_min_span: int = 20
    hs_max_span: int = 120
    hs_prominence_height_frac: float = 0.20
    hs_prominence_price_frac: float = 0.03
    hs_shoulder_diff_height_frac: float = 0.20
    hs_shoulder_diff_price_frac: float = 0.04
    hs_shoulder_min_height_frac: float = 0.40
    hs_time_symmetry_min: float = 0.5
    hs_time_symmetry_max: float = 2.0
    hs_neckline_tilt_frac: float = 0.25
    hs_neckline_tilt_price_frac: float = 0.08
    hs_shoulder_max_avg_height_frac: float = 0.70
    hs_leg_balance_max: float = 2.2
    hs_head_region_swing_frac: float = 0.20
    reversal_counter_swing_frac: float = 0.33
    reversal_peak_wick_max_frac: float = 0.25
    reversal_neckline_wick_max_frac: float = 0.15
    reversal_near_neckline_atr: float = 1.0
    extreme_exceeded_atr: float = 0.5
    provisional_min_confirm_bars: int = 1
    double_separation_ramp: tuple[int, int, int, int] = (10, 15, 40, 60)
    double_volume_full_ratio: float = 0.5

    # --- support / resistance zones ------------------------------------------
    sr_swing_k: int = 3
    sr_lookback_bars: int = 250
    sr_cluster_atr_frac: float = 0.5
    sr_pad_atr_frac: float = 0.1
    sr_min_touches: int = 2
    sr_touch_min_gap_bars: int = 5
    sr_merge_atr_frac: float = 0.25
    sr_max_zone_width_atr: float = 2.0
    sr_display_per_side: int = 4

    # --- lifecycle ----------------------------------------------------------
    breakout_confirm_closes: int = 2
    near_boundary_width_frac: float = 0.15
    failed_breakout_closes: int = 3
    post_breakout_track_bars: int = 20
    track_match_iou: float = 0.50

    # --- quality score ------------------------------------------------------
    quality_touch_target: int = 8
    quality_trend_r2_full: float = 0.90
    quality_weights_parallel: dict[str, float] = field(default_factory=_w_parallel)
    quality_weights_converging: dict[str, float] = field(default_factory=_w_converging)
    quality_weights_continuation: dict[str, float] = field(default_factory=_w_continuation)
    quality_weights_double: dict[str, float] = field(default_factory=_w_double)
    quality_weights_hs: dict[str, float] = field(default_factory=_w_hs)
    quality_size_atr_full: float = 6.0
    quality_labels: dict[str, float] = field(default_factory=_quality_labels)

    def min_move_pct(self, timeframe: str) -> float:
        return float(self.tf_min_move_pct[timeframe])

    def width_band_pct(self, timeframe: str) -> tuple[float, float]:
        lo, hi = self.tf_width_band_pct[timeframe]
        return float(lo), float(hi)

    def quality_label(self, score: float) -> str:
        for label, floor in sorted(self.quality_labels.items(), key=lambda kv: -kv[1]):
            if score >= floor:
                return label
        return "Weak"


@dataclass(frozen=True)
class ParamMeta:
    description: str
    unit: str
    patterns: tuple[str, ...]
    timeframes: tuple[str, ...] = ("all",)


_PAR = ("Falling Channel", "Rising Channel", "Horizontal Channel", "Rectangle")
_CONV = ("Ascending Triangle", "Descending Triangle", "Symmetrical Triangle", "Falling Wedge", "Rising Wedge")
_LINE = _PAR + _CONV
_FLAG = ("Bullish Flag", "Bearish Flag")
_PEN = ("Bullish Pennant", "Bearish Pennant")
_CONT = _FLAG + _PEN
_DBL = ("Double Top", "Double Bottom")
_HS = ("Head & Shoulders", "Inverse Head & Shoulders")
_REV = _DBL + _HS
_SR = ("Support/Resistance zones",)
_ALL = ("all",)

PARAM_META: dict[str, ParamMeta] = {
    "version": ParamMeta("Human-readable configuration version. Bump on any parameter change.", "semver", _ALL),
    "atr_period": ParamMeta("ATR lookback, Wilder smoothing, completed candles only.", "bars", _ALL),
    "swing_k_structure": ParamMeta("Swing lookback k for channels, ranges, triangles and wedges. A swing is confirmed only after k later completed bars.", "bars", _LINE),
    "fit_exclude_last_bars": ParamMeta("Newest completed bars excluded from geometry fitting (still used for status).", "bars", _LINE),
    "fit_refine_tolerance_mult": ParamMeta("Boundary refinement: after an initial anchored OLS fit, swing points farther than this multiple of the touch tolerance are dropped and the slope is re-estimated (needs >= 2 points).", "x touch tolerance", _LINE),
    "touch_tol_width_frac": ParamMeta("Touch tolerance width component: tolerance = max(frac x W(x), atr_frac x ATR).", "x W(x)", _LINE),
    "touch_tol_atr_frac": ParamMeta("Touch tolerance ATR component.", "x ATR", _LINE),
    "touch_spread_min_frac": ParamMeta("Minimum distance between first and last touch on each boundary.", "x L", _LINE),
    "containment_slack_width_frac": ParamMeta("Containment slack width component: s = max(frac x W(x), atr_frac x ATR).", "x W(x)", _LINE),
    "containment_slack_atr_frac": ParamMeta("Containment slack ATR component.", "x ATR", _LINE),
    "close_trend_min_r2": ParamMeta("Minimum R^2 of the close-price OLS for rising/falling channels.", "R^2", ("Falling Channel", "Rising Channel")),
    "break_buffer_atr_frac": ParamMeta("A close is beyond a boundary only if it exceeds it by at least this much.", "x ATR", _ALL),
    "line_state_move_frac": ParamMeta("Converging line state: Rising if M > +frac x W0, Falling if M < -frac x W0, else Flat.", "x W0", _CONV),
    "tf_min_move_pct": ParamMeta("Minimum move as % of start price, by timeframe.", "% of price", ("Falling Channel", "Rising Channel", "Falling Wedge", "Rising Wedge"), TIMEFRAMES),
    "tf_width_band_pct": ParamMeta("Allowed pattern width band as % of price, by timeframe.", "% of price", _LINE, TIMEFRAMES),
    "dedup_iou_threshold": ParamMeta("Same-type detections whose formation windows overlap with IoU >= this are deduplicated (higher quality kept, ties to longer).", "IoU", _ALL),
    "max_lead_before_first_touch_frac": ParamMeta("The fit window may start at most this fraction of L before the first touch; otherwise W0, CR and M would be measured on extrapolated lines where price never interacted with them. With candidate lengths spaced <= 1.5x apart every formation length remains coverable.", "x L", _LINE),
    "suppress_collapsed_subwindows": ParamMeta("Drop a converging candidate when a longer candidate with the same boundaries fails the CR minimum (a collapsed formation is not revived by viewing only its tail).", "flag", _CONV),
    "dedup_geometry_tolerance_frac": ParamMeta("Nested same-type detections are also duplicates when both boundaries coincide within frac x mean width over the overlapping bars (same geometry = same formation; genuinely different nested geometries are kept).", "x W_mean", _LINE),
    "parallel_lengths": ParamMeta("Candidate formation lengths for the parallel family.", "bars", _PAR),
    "parallel_min_upper_touches": ParamMeta("Minimum upper-boundary touches.", "touches", _PAR),
    "parallel_min_lower_touches": ParamMeta("Minimum lower-boundary touches.", "touches", _PAR),
    "parallel_min_total_touches": ParamMeta("Minimum total touches for channels.", "touches", _PAR),
    "parallel_min_total_touches_flat": ParamMeta("Minimum total touches for Horizontal Channel and Rectangle.", "touches", ("Horizontal Channel", "Rectangle")),
    "parallel_max_error": ParamMeta("Maximum parallelism error |b_upper - b_lower| x (L-1) / W_mean (independent fits).", "ratio", _PAR),
    "parallel_min_containment": ParamMeta("Minimum share of bars inside the boundaries (+ slack) from first touch to last fitted bar.", "fraction", _PAR),
    "channel_move_frac": ParamMeta("Rising/Falling Channel requires |M| >= frac x W_mean; Horizontal Channel requires |M| < frac x W_mean.", "x W_mean", _PAR),
    "rectangle_move_frac": ParamMeta("Rectangle requires |M| <= frac x W_mean; Horizontal Channel requires |M| > frac x W_mean.", "x W_mean", ("Horizontal Channel", "Rectangle")),
    "rectangle_max_close_r2": ParamMeta("Rectangle requires close-price OLS R^2 below this.", "R^2", ("Rectangle",)),
    "converging_lengths": ParamMeta("Candidate formation lengths for the converging family.", "bars", _CONV),
    "converging_min_upper_touches": ParamMeta("Minimum upper-boundary touches.", "touches", _CONV),
    "converging_min_lower_touches": ParamMeta("Minimum lower-boundary touches.", "touches", _CONV),
    "converging_min_total_touches": ParamMeta("Minimum total touches.", "touches", _CONV),
    "converging_cr_min": ParamMeta("Minimum convergence ratio CR = W1/W0 (rejects collapsed formations).", "ratio", _CONV),
    "converging_cr_max": ParamMeta("Maximum convergence ratio CR = W1/W0 (requires >= 50% narrowing; at CR 0.5-0.6 the lines look parallel and the formation is a channel, not a wedge/triangle - visual review 1.3.0).", "ratio", _CONV),
    "converging_min_containment": ParamMeta("Minimum containment share.", "fraction", _CONV),
    "apex_invalidation_bars": ParamMeta("Invalidate (apex_passed) when the latest completed bar reaches apex_index - this, with no breakout.", "bars", _CONV),
    "breakout_confirm_closes": ParamMeta("Consecutive completed closes beyond boundary + buffer that confirm a break.", "closes", _ALL),
    "near_boundary_width_frac": ParamMeta("Near Upper/Lower when the close is inside and within frac x W(now) of the boundary.", "x W(now)", _LINE),
    "failed_breakout_closes": ParamMeta("Consecutive closes back inside frozen boundaries after a break -> Invalidated (failed_breakout).", "closes", _ALL),
    "track_match_iou": ParamMeta("A fresh detection continues an open track of the same pattern (and symbol/timeframe) when their formation windows overlap with IoU >= this; it is suppressed as a refit when the track is frozen after a breakout.", "IoU", _ALL),
    "post_breakout_track_bars": ParamMeta("Completed bars a broken pattern is tracked with frozen geometry before archival.", "bars", _ALL),
    "quality_touch_target": ParamMeta("Touch component = min(total_touches / target, 1).", "touches", _LINE),
    "quality_trend_r2_full": ParamMeta("Channel trend-fit component reaches 1 at this R^2.", "R^2", ("Falling Channel", "Rising Channel")),
    "quality_weights_parallel": ParamMeta("Quality weights for the parallel family (placeholders, not validated).", "weight", _PAR),
    "quality_weights_converging": ParamMeta("Quality weights for the converging family (placeholders, not validated).", "weight", _CONV),
    "pole_lookback_bars": ParamMeta("Pole start = lowest low (bullish) / highest high (bearish) within this many bars before P_end.", "bars", _CONT),
    "pole_min_bars": ParamMeta("Minimum pole length (P_end - pole start).", "bars", _CONT),
    "pole_max_bars": ParamMeta("Maximum pole length.", "bars", _CONT),
    "pole_min_atr_mult": ParamMeta("Pole height H >= this x ATR measured at the pole start.", "x ATR", _CONT),
    "pole_min_directional_frac": ParamMeta("Share of pole bars closing beyond the previous close in the pole direction.", "fraction", _CONT),
    "pole_min_atr_per_bar": ParamMeta("Steep pole (Bulkowski: the flagpole is a steep, quick move): pole height / ATR / pole bars >= this. A slow drift is not a flagpole. Number: assumption.", "x ATR per bar", _CONT),
    "flag_min_bars": ParamMeta("Minimum consolidation length for a validated flag/pennant.", "bars", _CONT),
    "flag_max_bars": ParamMeta("Maximum flag consolidation length.", "bars", _FLAG),
    "pennant_max_bars": ParamMeta("Maximum pennant consolidation length.", "bars", _PEN),
    "consolidation_max_pole_mult": ParamMeta("Consolidation length <= this x pole length.", "x pole bars", _CONT),
    "flag_max_cr_deviation": ParamMeta("Flag shape: |1 - CR| <= this (independent raw-bar fit).", "ratio", _FLAG),
    "flag_move_against_max_frac": ParamMeta("Flag shared-slope movement may run against the pole by up to this x H.", "x H", _FLAG),
    "flag_move_with_max_frac": ParamMeta("Flag shared-slope movement may run with the pole by up to this x H (Bulkowski: the flag is flat or slopes AGAINST the pole; only fit noise is tolerated).", "x H", _FLAG),
    "flag_max_retracement": ParamMeta("Maximum flag retracement of the pole (also the provisional Forming limit).", "fraction of H", _FLAG),
    "pennant_max_retracement": ParamMeta("Maximum pennant retracement of the pole.", "fraction of H", _PEN),
    "pennant_cr_min": ParamMeta("Minimum pennant convergence ratio W1/W0.", "ratio", _PEN),
    "pennant_cr_max": ParamMeta("Maximum pennant convergence ratio W1/W0.", "ratio", _PEN),
    "consolidation_max_width_pole_frac": ParamMeta("Consolidation initial width W0 <= this x H.", "x H", _CONT),
    "continuation_min_upper_touches": ParamMeta("Minimum raw-bar upper touches.", "touches", _CONT),
    "continuation_min_lower_touches": ParamMeta("Minimum raw-bar lower touches.", "touches", _CONT),
    "continuation_touch_min_gap_bars": ParamMeta("Counted raw-bar touches on one boundary must be at least this many bars apart.", "bars", _CONT),
    "continuation_min_containment": ParamMeta("Minimum containment share of the consolidation.", "fraction", _CONT),
    "forming_min_bars": ParamMeta("Shortest provisional consolidation reported as Forming.", "bars", _CONT),
    "forming_max_bars": ParamMeta("Longest provisional consolidation reported as Forming (below flag_min_bars).", "bars", _CONT),
    "volume_contraction_full": ParamMeta("Volume component = 1 when consolidation/pole mean volume <= this.", "ratio", _CONT),
    "volume_contraction_zero": ParamMeta("Volume component = 0 when consolidation/pole mean volume >= this.", "ratio", _CONT),
    "swing_k_reversal": ParamMeta("Swing lookback k for double tops/bottoms and H&S (confirmed after k later bars).", "bars", _REV),
    "reversal_recency_bars": ParamMeta("The latest pivot (P2 / RS) must be within this many completed bars.", "bars", _REV),
    "double_min_separation": ParamMeta("P1 is at least this many bars before P2.", "bars", _DBL),
    "double_max_separation": ParamMeta("P1 is at most this many bars before P2.", "bars", _DBL),
    "reversal_min_height_atr": ParamMeta("Pattern height (D for doubles, HH for H&S) >= this x ATR (at P2 / at the head).", "x ATR", _REV),
    "double_min_depth_move_frac": ParamMeta("D / average peak price >= this x timeframe minimum move (1.0: 6% daily, 10% weekly = Bulkowski's >= 10% valley guideline for Eve & Eve double tops).", "x tf min move", _DBL),
    "double_peak_diff_depth_frac": ParamMeta("|P1 - P2| <= this x D (and the price-fraction limit).", "x D", _DBL),
    "double_peak_diff_price_frac": ParamMeta("|P1 - P2| <= this x average peak price (and the depth limit).", "fraction", _DBL),
    "hs_min_span": ParamMeta("Minimum LS-to-RS span.", "bars", _HS),
    "hs_max_span": ParamMeta("Maximum LS-to-RS span.", "bars", _HS),
    "hs_prominence_height_frac": ParamMeta("Head - max(shoulders) >= this x HH.", "x HH", _HS),
    "hs_prominence_price_frac": ParamMeta("Head - max(shoulders) >= this x head price.", "fraction", _HS),
    "hs_shoulder_diff_height_frac": ParamMeta("|LS - RS| <= this x HH (and the price limit).", "x HH", _HS),
    "hs_shoulder_diff_price_frac": ParamMeta("|LS - RS| <= this x average shoulder price (and the HH limit).", "fraction", _HS),
    "hs_shoulder_min_height_frac": ParamMeta("Each shoulder >= this x HH beyond the neckline at its index.", "x HH", _HS),
    "hs_time_symmetry_min": ParamMeta("Minimum bars(LS->H) / bars(H->RS).", "ratio", _HS),
    "hs_time_symmetry_max": ParamMeta("Maximum bars(LS->H) / bars(H->RS).", "ratio", _HS),
    "hs_neckline_tilt_frac": ParamMeta("|T1 - T2| <= this x HH (and the price limit).", "x HH", _HS),
    "hs_neckline_tilt_price_frac": ParamMeta("|T1 - T2| <= this x average armpit price: the two troughs within 4% of their average (Savin, Weller & Zvingelis 2007, restriction R5a, calibrated on Bulkowski's examples).", "fraction", _HS),
    "hs_shoulder_max_avg_height_frac": ParamMeta("Average shoulder height above the armpits <= this x HH so the head stands clear of the shoulders (SWZ 2007 R6).", "x HH", _HS),
    "hs_leg_balance_max": ParamMeta("Each of the four legs LS->T1->H->T2->RS lasts at most this x the average leg (SWZ 2007 R9: |X(i+1) - X(i) - mean| <= 1.2 mean).", "x mean leg", _HS),
    "hs_head_region_swing_frac": ParamMeta("Single head: while price trades above the higher shoulder, no close-to-close dip >= this x HH (a second peak above the shoulders = a complex / double head, not a textbook H&S). Assumption.", "x HH", _HS),
    "reversal_counter_swing_frac": ParamMeta("CONSECUTIVE extrema (Lo, Mamaysky & Wang 2000): no close-to-close counter-swing >= this x pattern height (D or HH) inside any leg (P1-T-P2 / LS-T1-H-T2-RS), i.e. no extra peak or trough between the pattern points. Fraction: assumption (they smooth prices with a kernel instead).", "x height", _REV),
    "reversal_peak_wick_max_frac": ParamMeta("Close confirmation of the peaks (P1/P2, LS/H/RS): the best close within 2 bars of each is within this x pattern height of it - no lone-wick peaks. Assumption.", "x height", _REV),
    "reversal_neckline_wick_max_frac": ParamMeta("Close confirmation of the neckline points (T / T1, T2): the best close within 2 bars is within this x pattern height, so the breakout line is not drawn through a lone spike. Assumption.", "x height", _REV),
    "reversal_near_neckline_atr": ParamMeta("Near the neckline when the close is within this x ATR on the pattern side.", "x ATR", _REV),
    "extreme_exceeded_atr": ParamMeta("Invalidated (extreme_exceeded) when a close goes beyond the pattern extreme by this x ATR before the neckline break.", "x ATR", _REV),
    "provisional_min_confirm_bars": ParamMeta("A provisional (unconfirmed) latest pivot needs at least this many later bars; it can only produce Forming.", "bars", _REV),
    "double_separation_ramp": ParamMeta("Separation quality: 0 at a, ramps to 1 at b, 1 until c, back to 0 at d.", "bars", _DBL),
    "double_volume_full_ratio": ParamMeta("Volume component = 1 when P2 volume <= this x P1 volume; 0 at >= P1 volume.", "ratio", _DBL),
    "sr_swing_k": ParamMeta("Swing lookback for support/resistance pivots.", "bars", _SR),
    "sr_lookback_bars": ParamMeta("Completed bars examined for support/resistance pivots.", "bars", _SR),
    "sr_cluster_atr_frac": ParamMeta("A pivot joins the current cluster when within this x its own ATR of the cluster mean.", "x ATR", _SR),
    "sr_pad_atr_frac": ParamMeta("Zone bounds padded by this x median cluster ATR on each side.", "x ATR", _SR),
    "sr_min_touches": ParamMeta("Minimum spaced touches for a zone.", "touches", _SR),
    "sr_touch_min_gap_bars": ParamMeta("Counted zone touches must be at least this many bars apart.", "bars", _SR),
    "sr_merge_atr_frac": ParamMeta("Zones overlapping or closer than this x median ATR are merged.", "x ATR", _SR),
    "sr_max_zone_width_atr": ParamMeta("Zones are not merged when the merged zone would be wider than this x median ATR (sequential merging otherwise chains a congested price area into one meaningless band).", "x ATR", _SR),
    "sr_display_per_side": ParamMeta("Nearest zones shown above and below the latest close.", "zones", _SR),
    "quality_weights_continuation": ParamMeta("Quality weights for flags and pennants (placeholders, not validated).", "weight", _CONT),
    "quality_weights_double": ParamMeta("Quality weights for double tops/bottoms (placeholders, not validated).", "weight", _DBL),
    "quality_weights_hs": ParamMeta("Quality weights for H&S / inverse H&S (placeholders, not validated).", "weight", _HS),
    "quality_size_atr_full": ParamMeta("Pole-strength / depth / size components reach 1 at this many ATRs.", "x ATR", _CONT + _REV),
    "quality_labels": ParamMeta("Score floors for presentation labels. Not probabilities.", "score", _ALL),
}

# Hash of every released configuration version. Register a new entry whenever a
# parameter value changes (and bump DetectionConfig.version).
KNOWN_CONFIG_HASHES: dict[str, str] = {
    "1.0.0": "a8f1c94573f3374a",  # P1: 9 line-based patterns
    "1.1.0": "c4ed712cfaaa05e3",  # P2: + flags, pennants, reversals, S/R zones (P1 values unchanged)
    "1.1.1": "28b66327205a3d05",  # P2: S/R merged-zone width cap (live QA: chained merges produced 5-ATR zones)
    "1.2.0": "e224c19b7f658e1b",  # P3: track_match_iou for cross-scan lifecycle tracking (detection values unchanged)
    "1.3.0": "9c33f4bf86016487",  # chart-quality review: H&S / doubles structure rules (Lo-Mamaysky-Wang, Savin-Weller-Zvingelis, Bulkowski), steep flag poles, >= 50% wedge narrowing
}


def config_to_dict(cfg: DetectionConfig) -> dict[str, Any]:
    out = asdict(cfg)
    # Normalise tuples -> lists so the JSON form is stable.
    return json.loads(json.dumps(out, sort_keys=True))


def config_hash(cfg: DetectionConfig) -> str:
    """Reproducible hash of all parameter values (the version label excluded)."""
    payload = config_to_dict(cfg)
    payload.pop("version", None)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def describe_config(cfg: Any, meta_table: dict[str, "ParamMeta"] | None = None) -> list[dict[str, Any]]:
    values = config_to_dict(cfg)
    table = PARAM_META if meta_table is None else meta_table
    rows = []
    for f in fields(cfg):
        meta = table.get(f.name)
        rows.append(
            {
                "name": f.name,
                "value": values[f.name],
                "description": meta.description if meta else "",
                "unit": meta.unit if meta else "",
                "patterns": list(meta.patterns) if meta else [],
                "timeframes": list(meta.timeframes) if meta else [],
            }
        )
    return rows


DEFAULT_CONFIG = DetectionConfig()


# ====================================================================== E1
# Technical-analysis configuration: candlesticks, N-day breakouts, EMA/RSI/ATR
# and RSI divergence. Versioned and hashed separately from DetectionConfig so a
# change here never alters price-action pattern detections or their IDs.
# Same governance: no numeric literals in the analysis code; change a value ->
# bump ``version`` and register the hash in KNOWN_TECH_CONFIG_HASHES.


@dataclass(frozen=True)
class TechnicalConfig:
    version: str = "1.0.0"

    # ---- ATR (one ATR for every module: indicators.core.atr_wilder)
    atr_period: int = 14  # must equal DetectionConfig.atr_period (enforced by tests)

    # ---- Marubozu (spec 4.1)
    marubozu_min_body_ratio: float = 0.90
    marubozu_max_upper_wick_ratio: float = 0.05
    marubozu_max_lower_wick_ratio: float = 0.05
    marubozu_min_range: float = 0.0  # candle range must be strictly greater
    candle_volume_lookback: int = 20
    candle_scan_recent_bars: int = 5  # scans store candlestick signals of the last N completed bars

    # ---- N-day breakouts (spec 5.1, always daily candles)
    breakout_lookback_days: tuple[int, ...] = (10,)  # 20/50 can be added without code changes
    breakout_buffer_atr: float = 0.10
    breakout_confirm_closes: int = 1  # default mode; scans store both 1- and 2-close results
    breakout_volume_lookback: int = 20
    breakout_min_volume_ratio: float = 0.0  # 0 = volume is supporting information only
    breakout_scan_recent_bars: int = 5

    # ---- EMA (spec 6.1)
    ema_periods: tuple[int, ...] = (10, 20, 30, 50, 100, 200)
    ema_seed: str = "sma"  # first value = SMA of the first n closes (TradingView ta.ema)
    ema_cross_pairs: tuple[tuple[int, int], ...] = ((20, 50), (50, 200))
    ema_partial_alignment: tuple[int, ...] = (20, 50, 200)
    ema_cross_recent_bars: int = 5

    # ---- RSI (spec 6.2, Wilder smoothing)
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_reference_levels: tuple[float, ...] = (40.0, 50.0, 60.0)

    # ---- RSI regular divergence (spec 6.3)
    div_swing_k: int = 5
    div_min_pivot_separation: int = 5
    div_max_pivot_separation: int = 60
    div_min_price_diff_atr: float = 0.10
    div_min_rsi_diff: float = 2.0
    div_invalidation_buffer_atr: float = 0.0
    div_provisional_min_confirm_bars: int = 1
    div_scan_max_age_bars: int = 20


TECH_PARAM_META: dict[str, ParamMeta] = {
    "atr_period": ParamMeta("ATR period, Wilder smoothing (TradingView ta.atr). Shared with pattern detection.", "bars", ("ATR",)),
    "marubozu_min_body_ratio": ParamMeta("Minimum body / (high - low).", "ratio", ("Marubozu",)),
    "marubozu_max_upper_wick_ratio": ParamMeta("Maximum (high - max(open, close)) / (high - low).", "ratio", ("Marubozu",)),
    "marubozu_max_lower_wick_ratio": ParamMeta("Maximum (min(open, close) - low) / (high - low).", "ratio", ("Marubozu",)),
    "marubozu_min_range": ParamMeta("Candle range must be strictly greater than this (zero-range candles excluded).", "price", ("Marubozu",)),
    "candle_volume_lookback": ParamMeta("Volume ratio = candle volume / mean volume of the previous N completed candles (supporting information only).", "bars", ("Marubozu",)),
    "candle_scan_recent_bars": ParamMeta("Candlestick signals stored per scan: the last N completed candles.", "bars", ("Marubozu",)),
    "breakout_lookback_days": ParamMeta("Reference windows: previous N completed daily candles (the breakout candle excluded).", "daily bars", ("Breakouts",), ("1d",)),
    "breakout_buffer_atr": ParamMeta("Close must exceed the previous N-day high (or fall below the low) by this x ATR(14).", "x ATR", ("Breakouts",), ("1d",)),
    "breakout_confirm_closes": ParamMeta("Default confirmation mode: 1 = one completed close beyond the level, 2 = two consecutive completed closes.", "closes", ("Breakouts",), ("1d",)),
    "breakout_volume_lookback": ParamMeta("Volume ratio against the previous N completed daily candles.", "bars", ("Breakouts",), ("1d",)),
    "breakout_min_volume_ratio": ParamMeta("Optional volume requirement (0 = off; volume is supporting information).", "ratio", ("Breakouts",), ("1d",)),
    "breakout_scan_recent_bars": ParamMeta("Breakout events stored per scan: confirmations within the last N daily candles.", "daily bars", ("Breakouts",), ("1d",)),
    "ema_periods": ParamMeta("EMA periods calculated for charts and alignment.", "bars", ("EMA",)),
    "ema_seed": ParamMeta("EMA initialisation: 'sma' = first value is the SMA of the first n closes, then alpha = 2/(n+1) (TradingView ta.ema). Values before n bars are not available.", "-", ("EMA",)),
    "ema_cross_pairs": ParamMeta("Fast/slow EMA pairs whose crossovers are reported (fast crosses above/below slow on a completed close).", "bars", ("EMA",)),
    "ema_partial_alignment": ParamMeta("Partial alignment: these EMAs in strict order (e.g. 20 > 50 > 200) without full six-EMA alignment.", "bars", ("EMA",)),
    "ema_cross_recent_bars": ParamMeta("EMA crossovers stored per scan: within the last N completed bars.", "bars", ("EMA",)),
    "rsi_period": ParamMeta("RSI period, Wilder smoothing (TradingView ta.rsi).", "bars", ("RSI",)),
    "rsi_overbought": ParamMeta("Overbought reference level.", "RSI", ("RSI",)),
    "rsi_oversold": ParamMeta("Oversold reference level.", "RSI", ("RSI",)),
    "rsi_reference_levels": ParamMeta("Additional reference levels drawn on the RSI pane.", "RSI", ("RSI",)),
    "div_swing_k": ParamMeta("Swing lookback for divergence pivots (confirmed after k later completed bars).", "bars", ("RSI divergence",)),
    "div_min_pivot_separation": ParamMeta("Minimum bars between the two compared pivots.", "bars", ("RSI divergence",)),
    "div_max_pivot_separation": ParamMeta("Maximum bars between the two compared pivots (initial engineering default, not from the spec).", "bars", ("RSI divergence",)),
    "div_min_price_diff_atr": ParamMeta("Minimum price difference between the two pivots, x ATR(14) at the second pivot.", "x ATR", ("RSI divergence",)),
    "div_min_rsi_diff": ParamMeta("Minimum RSI difference between the two pivots (RSI read at the price pivot bars).", "RSI points", ("RSI divergence",)),
    "div_invalidation_buffer_atr": ParamMeta("Invalidation: a completed close beyond the second pivot's extreme (low for bullish, high for bearish) by this x ATR.", "x ATR", ("RSI divergence",)),
    "div_provisional_min_confirm_bars": ParamMeta("Developing divergence: the latest pivot has at least this many (but fewer than k) later bars.", "bars", ("RSI divergence",)),
    "div_scan_max_age_bars": ParamMeta("Divergences stored per scan: confirmed or developing within the last N bars.", "bars", ("RSI divergence",)),
}

KNOWN_TECH_CONFIG_HASHES: dict[str, str] = {
    "1.0.0": "c555a7506d34d674",  # E1: Marubozu, 10-day breakouts, EMA/RSI/ATR, RSI divergence (initial defaults)
}

DEFAULT_TECH_CONFIG = TechnicalConfig()


# ====================================================================== E2
# NSE index analysis and the Relative Rotation Graph. Versioned like the other
# configurations; RRG output persists this version and hash.


@dataclass(frozen=True)
class IndexConfig:
    version: str = "1.0.0"
    # RRG (internally implemented RRG-style calculation, docs/rrg.md)
    rrg_default_benchmark: str = "NIFTY 500"
    rrg_benchmarks: tuple[str, ...] = ("NIFTY 500", "NIFTY 50")
    rrg_rs_period: int = 14  # weeks: RS_Ratio = 100 x RS / EMA(RS, n)
    rrg_momentum_period: int = 14  # weeks: RS_Momentum = 100 x RS_Ratio / EMA(RS_Ratio, n)
    rrg_trail_weeks: int = 12
    rrg_trail_options: tuple[int, ...] = (4, 8, 12)
    rrg_center: float = 100.0
    rrg_max_gap_weeks: int = 1  # more consecutive missing weeks inside the window -> data_gap
    rrg_lookback_weeks: int = 156  # weekly history used for the calculation
    rrg_max_visible: int = 20
    # Index Analysis dashboard
    return_horizons: tuple[tuple[str, str], ...] = (("1W", "7D"), ("1M", "1M"), ("3M", "3M"), ("6M", "6M"), ("1Y", "12M"))
    rs_horizon: str = "3M"  # relative performance vs benchmark over this horizon


INDEX_PARAM_META: dict[str, ParamMeta] = {
    "rrg_default_benchmark": ParamMeta("Default RRG / relative-performance benchmark (official index symbol).", "-", ("RRG",)),
    "rrg_benchmarks": ParamMeta("Selectable benchmarks.", "-", ("RRG",)),
    "rrg_rs_period": ParamMeta("EMA period (weeks) of the RS line for RS-Ratio = 100 x RS / EMA(RS).", "weeks", ("RRG",), ("1w",)),
    "rrg_momentum_period": ParamMeta("EMA period (weeks) for RS-Momentum = 100 x RS-Ratio / EMA(RS-Ratio).", "weeks", ("RRG",), ("1w",)),
    "rrg_trail_weeks": ParamMeta("Default trail length.", "weeks", ("RRG",), ("1w",)),
    "rrg_trail_options": ParamMeta("Selectable trail lengths.", "weeks", ("RRG",), ("1w",)),
    "rrg_center": ParamMeta("Centre of both axes (quadrant boundary).", "-", ("RRG",)),
    "rrg_max_gap_weeks": ParamMeta("Maximum consecutive benchmark weeks missing for an index inside the calculation window; more -> data_gap (never forward-filled).", "weeks", ("RRG",), ("1w",)),
    "rrg_lookback_weeks": ParamMeta("Completed weekly candles used for the RRG calculation.", "weeks", ("RRG",), ("1w",)),
    "rrg_max_visible": ParamMeta("Default maximum number of indices drawn at once (clutter control).", "items", ("RRG",)),
    "return_horizons": ParamMeta("Index Analysis return horizons: label and calendar offset; return = close / close on the last trading day on or before (latest date - offset) - 1.", "-", ("Index Analysis",)),
    "rs_horizon": ParamMeta("Horizon of the relative performance vs the benchmark: (1 + r_index) / (1 + r_benchmark) - 1.", "-", ("Index Analysis",)),
}

KNOWN_INDEX_CONFIG_HASHES: dict[str, str] = {
    "1.0.0": "a00ce2b234cd470b",  # E2: RRG 14/14/12 weeks vs NIFTY 500, index analysis horizons
}

DEFAULT_INDEX_CONFIG = IndexConfig()


# ====================================================================== E3
# Stage Analysis + RS Line, translated from the user's ``stage_analysis.txt``
# (logic specification) and ``rs_line.txt`` (TradingView Pine v6 "Relative
# Strength" indicator). Values marked SOURCE come from those files verbatim;
# values marked ASSUMPTION are not specified there and are configurable
# placeholders shown as such in the UI and docs (docs/stage-analysis-implementation.md).


@dataclass(frozen=True)
class StageConfig:
    version: str = "1.3.0"
    # ---- SOURCE: stage_analysis.txt section 1 / 4 / 10
    benchmark: str = "NIFTY 50"  # "Benchmark: Nifty 50 (^NSEI)"; rs_line.txt default 'NSE:NIFTY'
    # ---- SOURCE: section 2 (weekly base formation)
    base_lookback_weeks: int = 52
    max_base_range_pct: float = 30.0
    base_high_proximity_pct: float = 5.0
    # ---- SOURCE: section 3 (30-week EMA, pandas ewm(span=30, adjust=False))
    ema_period: int = 30
    ema_testing_band_pct: float = 5.0
    ema_direction_lookback: int = 4
    # ---- SOURCE: section 4 (RS configuration) / rs_line.txt
    rs_length_weekly: int = 52
    rs_length_daily: int = 63
    rs_length_pine_default: int = 123  # rs_line.txt input default (used where the setup defines no period: 1H/4H)
    rs_trend_base: int = 5
    rs_ma_length: int = 50
    rs_ma_rising_bars: int = 3
    price_sma_length: int = 50
    price_sma_rising_bars: int = 3
    # ---- SOURCE: section 5 (volume)
    volume_base_weeks: int = 26
    volume_recent_weeks: int = 4
    volume_expansion_ratio: float = 1.5
    # ---- ASSUMPTION (not quantified in stage_analysis.txt)
    ema_flat_tolerance_pct: float = 0.5  # "approximately flat": |EMA / EMA[4] - 1| below this
    prior_decline_min_pct: float = 20.0  # "meaningful prior decline" before the base
    prior_decline_lookback_weeks: int = 52  # weeks before the base window searched for the prior high
    higher_lows_swing_k: int = 3  # 1.1.0: k=3 separates breakdown risk best (event study, docs/stage-analysis-implementation.md)
    stage_rank_order: tuple[str, ...] = ("early_stage2", "stage2_confirmed", "stage1", "other")  # 1.1.0: Early Stage 2 first
    benchmark_max_ffill_bars: int = 2  # Pine request.security gaps_off carries the benchmark forward; capped here
    # ---- USER: entry setup stated by the user (confluence, 2026-11-04): after a major decline, a
    # long weekly base, close above the 30W EMA, RS > 0, at / near the base high, and volume HIGH in
    # the last weeks compared with the sideways base - ALL must hold. Longest base ranks first.
    setup_rank_by_base_length: bool = True
    setup_volume_pickup_ratio: float = 1.5  # last 4 weeks avg / avg of the 52 sideways weeks before them (1.5x = s.5 multiple)


STAGE_SOURCE_PARAMS = (
    "benchmark", "base_lookback_weeks", "max_base_range_pct", "base_high_proximity_pct", "ema_period", "ema_testing_band_pct",
    "ema_direction_lookback", "rs_length_weekly", "rs_length_daily", "rs_length_pine_default", "rs_trend_base", "rs_ma_length",
    "rs_ma_rising_bars", "price_sma_length", "price_sma_rising_bars", "volume_base_weeks", "volume_recent_weeks", "volume_expansion_ratio",
)
STAGE_ASSUMPTION_PARAMS = (
    "ema_flat_tolerance_pct", "prior_decline_min_pct", "prior_decline_lookback_weeks", "higher_lows_swing_k", "stage_rank_order", "benchmark_max_ffill_bars",
)
STAGE_USER_PARAMS = ("setup_rank_by_base_length", "setup_volume_pickup_ratio")

STAGE_PARAM_META: dict[str, ParamMeta] = {
    "benchmark": ParamMeta("SOURCE stage_analysis.txt s.1/4/10: benchmark Nifty 50 (^NSEI); rs_line.txt comparative symbol default NSE:NIFTY.", "-", ("Stage Analysis", "RS Line")),
    "base_lookback_weeks": ParamMeta("SOURCE s.2: base lookback 52 weeks; base high = highest weekly high, base low = lowest weekly low.", "weeks", ("Stage Analysis",), ("1w",)),
    "max_base_range_pct": ParamMeta("SOURCE s.2: maximum base range 30 % ((BaseHigh - BaseLow) / BaseLow x 100).", "%", ("Stage Analysis",), ("1w",)),
    "base_high_proximity_pct": ParamMeta("SOURCE s.2: price proximity to base high within 5 %.", "%", ("Stage Analysis",), ("1w",)),
    "ema_period": ParamMeta("SOURCE s.3: ema30 = weekly_close.ewm(span=30, adjust=False).mean().", "weeks", ("Stage Analysis",), ("1w",)),
    "ema_testing_band_pct": ParamMeta("SOURCE s.3: price within +/-5 % of the EMA = testing / transition.", "%", ("Stage Analysis",), ("1w",)),
    "ema_direction_lookback": ParamMeta("SOURCE s.3: EMA direction = current EMA vs the EMA four weeks ago.", "weeks", ("Stage Analysis",), ("1w",)),
    "rs_length_weekly": ParamMeta("SOURCE s.4: RS lookback 52 bars (weekly; the Stage Analysis screener uses the weekly configuration).", "bars", ("RS Line", "Stage Analysis"), ("1w",)),
    "rs_length_daily": ParamMeta("SOURCE s.4: RS lookback 63 bars (daily).", "bars", ("RS Line",), ("1d",)),
    "rs_length_pine_default": ParamMeta("SOURCE rs_line.txt: input 'Period' default 123; used on timeframes the setup does not configure (1H/4H).", "bars", ("RS Line",), ("1h", "4h")),
    "rs_trend_base": ParamMeta("SOURCE s.4B / rs_line.txt 'Range' 5: y0 = res - res[base]; angle0 = atan(y0 / base).", "bars", ("RS Line",)),
    "rs_ma_length": ParamMeta("SOURCE s.4C / rs_line.txt 'RS Mean' 50: sma_res = ta.sma(res, 50).", "bars", ("RS Line",)),
    "rs_ma_rising_bars": ParamMeta("SOURCE rs_line.txt: ma_rising = ta.rising(sma_res, 3), ma_falling = ta.falling(sma_res, 3).", "bars", ("RS Line",)),
    "price_sma_length": ParamMeta("SOURCE s.4D / rs_line.txt 'Price Confirmation' 50: sma_symb = ta.sma(close, 50).", "bars", ("RS Line",)),
    "price_sma_rising_bars": ParamMeta("SOURCE rs_line.txt: pos_div = ta.rising(sma_symb, 3) and close >= sma_symb; neg_div = ta.falling(sma_symb, 3) and close < sma_symb.", "bars", ("RS Line",)),
    "volume_base_weeks": ParamMeta("SOURCE s.5: previous average volume = 26 weeks before the recent base; base average volume = recent 26 weeks.", "weeks", ("Stage Analysis",), ("1w",)),
    "volume_recent_weeks": ParamMeta("SOURCE s.5: recent volume = last 4 weeks.", "weeks", ("Stage Analysis",), ("1w",)),
    "volume_expansion_ratio": ParamMeta("SOURCE s.5: volume expansion = last 4-week average >= 1.5 x recent 26-week average.", "ratio", ("Stage Analysis",), ("1w",)),
    "ema_flat_tolerance_pct": ParamMeta("ASSUMPTION (s.3 says 'approximately flat' without a number): |EMA / EMA[4] - 1| x 100 below this = flat.", "%", ("Stage Analysis",), ("1w",)),
    "prior_decline_min_pct": ParamMeta("ASSUMPTION (s.2 'consolidation after a meaningful prior decline', no number): decline from the highest weekly high in the weeks before the base window to the base low.", "%", ("Stage Analysis",), ("1w",)),
    "prior_decline_lookback_weeks": ParamMeta("ASSUMPTION: how many weeks before the base window are searched for that prior high.", "weeks", ("Stage Analysis",), ("1w",)),
    "higher_lows_swing_k": ParamMeta("ASSUMPTION (s.2 'whether the stock is forming higher lows', no rule): last two confirmed weekly swing lows (k bars each side) inside the base rising. Reported only, never a gate.", "bars", ("Stage Analysis",), ("1w",)),
    "stage_rank_order": ParamMeta("ASSUMPTION (s.8 ranks by 'stage classification' without an order): order of stages in the ranking.", "-", ("Stage Analysis",)),
    "setup_rank_by_base_length": ParamMeta("USER (stated setup, 2026-11-04): entry setups are ranked by base length, longest first ('the longer the base, the bigger the target').", "-", ("Stage Analysis",), ("1w",)),
    "setup_volume_pickup_ratio": ParamMeta("USER (stated setup, 2026-11-04: 'volume low in the sideways base, very high when about to break out'): average volume of the last 4 weeks / average volume of the 52 sideways weeks before them must be >= this. 1.5x = the volume-expansion multiple of stage_analysis.txt s.5; event study 2023-26 favoured >= 1.5x.", "ratio", ("Stage Analysis",), ("1w",)),
    "benchmark_max_ffill_bars": ParamMeta("ASSUMPTION: Pine request.security (gaps_off) carries the last benchmark value onto stock bars; more than this many consecutive carried bars -> RS missing (data gap).", "bars", ("RS Line",)),
}

KNOWN_STAGE_CONFIG_HASHES: dict[str, str] = {
    "1.0.0": "c35e7f222bbd4d33",  # E3: stage_analysis.txt + rs_line.txt values; flagged assumptions at initial defaults
    "1.1.0": "0d4849126e3192f0",  # higher lows k 2->3, rank Early Stage 2 first (event study 2018-2026, tuning/validation split)
    "1.2.0": "9561423e1323042f",  # user entry setup (confluence): quiet base 1.0, volume pickup 1.0, rank setups by base length
    "1.3.0": "86f711e9b6b7301b",  # user simplification: no decline-volume check; last 4w volume >= 1.5x the 52 sideways weeks before them
}

DEFAULT_STAGE_CONFIG = StageConfig()

