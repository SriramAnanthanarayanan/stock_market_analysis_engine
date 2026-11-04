// Mirrors backend/app/models.py. The backend is the source of truth for every
// detection and every coordinate; the frontend only renders.

export type Universe = "nifty500" | "fno" | "non_fno" | "all";
export type Timeframe = "1h" | "4h" | "1d" | "1w";
export type StatusSlug =
  | "developing"
  | "forming"
  | "validated"
  | "near_upper"
  | "near_lower"
  | "broke_above"
  | "broke_below"
  | "invalidated";
export type FnoStatus = "YES" | "NO" | "UNKNOWN";

export interface GeoPoint {
  t: number; // candle start, Unix seconds (UTC instant)
  i: number; // bar index in the detector's series
  p: number;
  projected: boolean;
}

export interface LineElement {
  type: "line";
  id: string;
  role: string;
  style: "solid" | "dashed";
  points: GeoPoint[];
}
export interface BandElement {
  type: "band";
  id: string;
  role: string;
  upper: GeoPoint[];
  lower: GeoPoint[];
}
export interface ZoneElement {
  type: "zone";
  id: string;
  role: string;
  t_start: number;
  t_end: number;
  p_low: number;
  p_high: number;
  label: string;
}
export interface MarkerElement extends GeoPoint {
  type: "marker";
  id: string;
  role: string;
  label: string;
  residual?: number;
  tolerance?: number;
  index_exact?: number;
}
export type GeometryElement = LineElement | BandElement | ZoneElement | MarkerElement;

export interface LifecycleModel {
  kind: "band" | "neckline_down" | "neckline_up";
  extreme: number | null;
  apex_index: number | null;
}

export interface Geometry {
  schema_version: "1.0" | "1.1";
  lifecycle_model?: LifecycleModel | null;
  detection_id: string;
  pattern: string;
  timeframe: Timeframe;
  price_basis: string;
  time_basis: string;
  formation: { start: GeoPoint; end: GeoPoint; length: number };
  last_candle: GeoPoint;
  elements: GeometryElement[];
}

export interface Match {
  pattern_id: string;
  scan_id: string | null;
  symbol: string;
  company: string | null;
  fno_status: FnoStatus;
  in_nifty500: boolean;
  pattern: string;
  pattern_name: string;
  category: string;
  family: string | null;
  timeframe: Timeframe;
  status: StatusSlug;
  status_label: string;
  status_reason: string | null;
  quality: number;
  quality_label: string;
  formation_start: string;
  formation_end: string;
  formation_length: number;
  upper_touches: number | null;
  lower_touches: number | null;
  width_pct: number | null;
  move_pct: number | null;
  volume_ratio: number | null;
  last_close: number | null;
  last_candle_time: string | null;
  metrics: Record<string, number | string | boolean | null>;
  config_version: string;
  config_hash: string;
}

export interface ScanSummary {
  scan_id: string;
  state: string;
  phase: string | null;
  trigger: string;
  universe: string;
  timeframe: Timeframe;
  asof: string | null;
  patterns: string[] | null;
  config_version: string;
  config_hash: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  data_cutoff: string | null;
  total_symbols: number | null;
  processed_symbols: number | null;
  successful_symbols: number | null;
  failed_symbols: number | null;
  detection_count: number | null;
  message: string | null;
  warnings: string[];
}

export interface DetectionCounts {
  by_pattern?: Record<string, number>;
  by_status?: Record<string, number>;
  by_fno_status?: Record<string, number>;
  by_category?: Record<string, number>;
}

export interface ScanResponse {
  requested: Record<string, unknown>;
  scan: ScanSummary | null;
  active_scan: ScanSummary | null;
  detection_counts: DetectionCounts;
  symbol_counts: Record<string, number>;
  total_matches: number;
  matches: Match[];
  note: string | null;
}

export interface QualityComponent {
  name: string;
  weight: number;
  raw: number | null;
  score: number;
  formula: string;
}
export interface Gate {
  name: string;
  passed: boolean;
  value: number | string | null;
  threshold: string;
  group: string;
}
export interface LifecycleEvent {
  time: string;
  t: number | null;
  index: number | null;
  event: string;
  status: string | null;
  detail: Record<string, unknown>;
}

export interface ChartDetection extends Match {
  quality_components: QualityComponent[];
  gates: Gate[];
  explanation: string[];
  lifecycle: LifecycleEvent[];
  geometry: Geometry;
}

export interface Candle {
  t: number;
  i: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number | null;
}
export interface Swing {
  t: number;
  i: number;
  p: number;
  kind: "high" | "low";
}

export interface SRZone {
  zone_id: string;
  p_low: number;
  p_high: number;
  width: number;
  role: "support" | "resistance";
  status: "intact" | "broken" | "retested";
  side: "above" | "below";
  touches: number;
  touch_times: string[];
  first_touch_time: string | null;
  last_touch_time: string | null;
  broken_time: string | null;
  retest_time: string | null;
  flips: number;
  distance_pct: number;
  distance_atr: number | null;
  median_atr: number;
  t_start: number;
  t_end: number;
}

export interface ChartResponse {
  symbol: string;
  company: string | null;
  fno_status: FnoStatus;
  in_nifty500: boolean;
  timeframe: Timeframe;
  asof: string | null;
  source: "scan" | "adhoc";
  scan_id: string | null;
  data_cutoff: string | null;
  candles: Candle[];
  future_bars: { i: number; t: number }[]; // projected bar starts (whitespace on the chart)
  swing_points: Swing[];
  swing_k: number;
  detections: ChartDetection[];
  selected_detection_id: string | null;
  sr_zones: SRZone[];
  sr_zones_note: string;
  newer_data_available: boolean;
  notes: string[];
  data_source?: DataSourceInfo | null;
}

export interface DataSourceInfo {
  provider: string;
  price_basis: string;
  price_basis_label: string;
  scan_price_basis: string | null;
  adjusted_close_available: boolean;
  note: string;
}

export interface PatternInfo {
  slug: string;
  name: string;
  category: string;
  category_label: string;
  family: string;
  implemented: boolean;
  phase: string;
}

export interface OverviewResponse {
  universe: Universe;
  asof: string | null;
  timeframes: Record<Timeframe, { scan: ScanSummary | null; counts: DetectionCounts }>;
  data: Record<string, unknown>;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  degraded_components: string[];
  time: string;
  app: { name: string; version: string; phase: string; config_version: string; config_hash: string; uptime_s: number };
  database: { status: string; schema_version?: number };
  universe: Record<string, any>;
  market_data: Record<string, any>;
  last_scan: Record<Timeframe, ScanSummary | null>;
  scheduler: { enabled: boolean; running: boolean; jobs?: { id: string; next_run: string | null }[] };
  active_scans: ScanSummary[];
}

export interface RefreshResponse {
  scan_id: string;
  created: boolean;
  state: string;
}

// ---------------------------------------------------------------- P3

export interface LinePoint {
  t: number;
  p: number;
}
export interface LabelPoint extends LinePoint {
  kind: "high" | "low";
}
export type Verdict = "valid" | "invalid" | "near_miss" | "false_detection";

export interface LabelIn {
  symbol: string;
  timeframe: Timeframe;
  pattern: string;
  verdict: Verdict;
  asof: string;
  formation_start_t: number;
  formation_end_t: number;
  upper_line?: LinePoint[] | null;
  lower_line?: LinePoint[] | null;
  swing_points?: LabelPoint[] | null;
  dataset?: "tuning" | "validation" | null;
  detection_id?: string | null;
  notes?: string | null;
}

export interface Label {
  label_id: string;
  symbol: string;
  timeframe: Timeframe;
  pattern: string;
  verdict: Verdict;
  asof: string;
  formation_start: string;
  formation_end: string;
  upper_line: LinePoint[] | null;
  lower_line: LinePoint[] | null;
  swing_points: LabelPoint[] | null;
  dataset: "tuning" | "validation";
  detection_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface OutcomeHorizon {
  status: "complete" | "pending";
  return_pct?: number;
  mfe_pct?: number;
  mae_pct?: number;
  end_time?: string;
  bars_available?: number;
}
export interface Outcome {
  breakout_time: string;
  breakout_close: number;
  direction: string;
  horizons: Record<string, OutcomeHorizon>;
}

export interface Track {
  track_id: string;
  symbol: string;
  timeframe: Timeframe;
  pattern: string;
  pattern_name: string;
  family: string;
  state: "active" | "frozen" | "closed";
  status: StatusSlug;
  status_label: string;
  status_reason: string | null;
  closed_reason: string | null;
  first_detected_at: string;
  last_detected_at: string;
  formation_start: string;
  formation_end: string;
  quality_first: number | null;
  quality_last: number | null;
  quality_max: number | null;
  breakout_time: string | null;
  breakout_direction: string | null;
  breakout_close: number | null;
  invalidated_time: string | null;
  archived_time: string | null;
  closed_time: string | null;
  config_version: string;
  current_detection_id: string;
  last_scan_id: string;
  outcome: Outcome | null;
}

export interface TrackDetail {
  track: Track;
  events: { id: number; event: string; event_time: string; status: string | null; quality: number | null; scan_id: string; detection_id: string | null; detail: Record<string, unknown> }[];
  snapshots: { scan_id: string; detection_id: string; status: string; quality: number; created_at: string }[];
  note: string;
}

export interface OutcomeGroup {
  pattern: string;
  pattern_name: string;
  timeframe: Timeframe;
  direction: string;
  tracks: number;
  horizons: Record<string, { n: number; insufficient_sample: boolean; mean_return_pct?: number; median_return_pct?: number; share_positive?: number; mean_mfe_pct?: number; mean_mae_pct?: number }>;
}

export interface Metric {
  tp: number;
  fp: number;
  fn: number;
  duplicates: number;
  precision: number | null;
  recall: number | null;
  support: number;
}
export type MetricBlock = Record<"overall" | "by_pattern" | "by_timeframe" | "by_pattern_timeframe" | "by_segment", Record<string, Metric>>;
export interface EvalCase {
  type: "TP" | "FP" | "FN";
  reason: string | null;
  symbol: string;
  timeframe: Timeframe;
  asof: string;
  fno_status: string;
  pattern: string;
  dataset: string;
  label_id: string | null;
  verdict: string | null;
  label_window: string[] | null;
  detection_id: string | null;
  detection_window: string[] | null;
  status: string | null;
  quality: number | null;
  iou: number | null;
}
export interface EvalOptions {
  iou_threshold: number;
  include_forming: boolean;
  fp_scope: "labelled_patterns" | "all";
}
export interface EvalResult {
  run_id: string;
  config: { version: string; hash: string };
  label_count: number;
  charts_evaluated: number;
  skipped: { label_id: string; reason: string }[];
  metrics: Record<"tuning" | "validation" | "all", MetricBlock>;
  targets: { precision: number; recall: number };
  notes: string[];
  cases: EvalCase[];
}
export interface CompareRow {
  a: Metric | null;
  b: Metric | null;
  precision_delta: number | null;
  recall_delta: number | null;
}
export interface CompareResult {
  run_id: string;
  a: { config: { version: string; hash: string } };
  b: { config: { version: string; hash: string } };
  diff: Record<"tuning" | "validation" | "all", Record<string, CompareRow>>;
  fp_added: EvalCase[];
  fp_removed: EvalCase[];
  fn_added: EvalCase[];
  fn_removed: EvalCase[];
  note: string;
}
export interface SweepResult {
  run_id: string;
  param: string;
  base_value: number | boolean;
  rows: { value: number | boolean; is_current: boolean; config_version: string; tuning: Metric; validation: Metric }[];
  note: string;
}
export interface ConfigParam {
  name: string;
  value: unknown;
  description: string;
  unit: string;
  patterns: string[];
}

// ---------------------------------------------------------------- E1: technical analysis
export type IndicatorStatus = "ok" | "insufficient_history" | "not_ready";

export interface TechMeta {
  symbol: string;
  timeframe: Timeframe;
  data_cutoff: string;
  scan_id: string | null;
  bars: number;
  last_candle_time: string;
  price_basis: string;
  config_version: string;
  config_hash: string;
}

export interface IndicatorPayload extends TechMeta {
  times: number[];
  ema: Record<string, (number | null)[]>;
  rsi: (number | null)[];
  atr: (number | null)[];
  atr_pct: (number | null)[];
  latest: { close: number | null; ema: Record<string, number | null>; rsi: number | null; atr: number | null; atr_pct: number | null };
  status: { ema: Record<string, IndicatorStatus>; rsi: IndicatorStatus; atr: IndicatorStatus };
  params: {
    ema_periods: number[];
    ema_seed: string;
    rsi_period: number;
    atr_period: number;
    rsi_overbought: number;
    rsi_oversold: number;
    rsi_reference_levels: number[];
  };
}

export interface MarubozuEvent {
  kind: "bullish_marubozu" | "bearish_marubozu";
  direction: "bullish" | "bearish";
  index: number;
  time: string;
  t: number;
  timeframe: Timeframe;
  age_bars: number;
  open: number;
  high: number;
  low: number;
  close: number;
  body_pct: number;
  upper_wick_pct: number;
  lower_wick_pct: number;
  range: number;
  atr: number | null;
  range_atr: number | null;
  volume: number | null;
  volume_ratio: number | null;
  volume_lookback: number;
}

export interface BreakoutEvent {
  kind: string; // breakout_high_10d | breakdown_low_10d
  direction: "bullish" | "bearish";
  status: "confirmed" | "failed" | "pending";
  confirm_closes: 1 | 2;
  lookback_days: number;
  first_close_time: string;
  index: number;
  time: string;
  t: number;
  age_bars: number;
  reference_start: string;
  reference_end: string;
  prev_high: number;
  prev_low: number;
  level: number;
  buffer: number;
  close: number;
  distance: number;
  breakout_pct: number;
  distance_atr: number | null;
  range_atr: number | null;
  atr: number | null;
  volume: number | null;
  volume_ratio: number | null;
  first_of_run?: boolean;
  volume_ok?: boolean;
  prior_level?: { low: number; high: number; role: string; touches: number; status_before: string } | null;
}

export interface DivPivot {
  i: number;
  t: number;
  time: string;
  price: number;
  rsi: number;
}

export interface DivergenceEvent {
  kind: "rsi_divergence";
  direction: "bullish" | "bearish";
  status: "developing" | "confirmed" | "invalidated" | "superseded";
  p1: DivPivot;
  p2: DivPivot;
  confirmed_time: string | null;
  confirmed_t: number | null;
  invalidated_time: string | null;
  invalidated_t: number | null;
  price_diff_atr: number;
  rsi_diff: number;
  separation: number;
  gates: { name: string; value: unknown; rule: string; passed: boolean }[];
  time: string;
  t: number;
  age_bars: number;
}

export interface EmaCross {
  kind: string;
  direction: "bullish" | "bearish";
  fast: number;
  slow: number;
  time: string;
  t: number;
  age_bars: number;
}

export interface DailyLevel {
  lookback_days: number;
  high: number;
  low: number;
  reference_start: string;
  reference_end: string;
  t_start: number;
  t_end: number;
  basis: "daily";
}

export interface TechSnapshot {
  bars: number;
  last_candle_time: string;
  close: number | null;
  ema: Record<string, number | null>;
  ema_distance_pct: Record<string, number | null>;
  alignment: string;
  ema_missing: number[];
  price_vs_ema: Record<string, string | null>;
  rsi: number | null;
  rsi_state: string | null;
  atr: number | null;
  atr_pct: number | null;
  range_atr: number | null;
}

export interface TechnicalPayload extends TechMeta {
  snapshot: TechSnapshot;
  snapshot_status: string;
  marubozu: MarubozuEvent[];
  divergences: DivergenceEvent[];
  ema_crosses: EmaCross[];
  daily: {
    status: string;
    levels: Record<string, DailyLevel | null>;
    breakouts: BreakoutEvent[];
    confirm_closes: number;
    latest_daily_candle?: string;
  };
  notes: string[];
}

export interface TechScanSummary {
  scan_id: string;
  universe: Universe;
  timeframe: Timeframe;
  asof: string | null;
  state: string;
  finished_at: string | null;
  data_cutoff: string | null;
  successful_symbols: number;
  failed_symbols: number;
  price_basis: string | null;
  tech_config_version: string | null;
  tech_config_hash: string | null;
}

interface StockFields {
  symbol: string;
  company: string | null;
  fno_status: FnoStatus;
  in_nifty500: boolean;
}

export type CandleSignal = MarubozuEvent & StockFields;
export type BreakoutSignal = BreakoutEvent & StockFields;

export interface IndicatorRow extends StockFields {
  timeframe: Timeframe;
  status: string;
  last_candle_time: string;
  close: number | null;
  alignment: string;
  ema: Record<string, number | null>;
  price_vs_ema: Record<string, string | null>;
  rsi: number | null;
  rsi_state: string | null;
  atr: number | null;
  atr_pct: number | null;
  crosses: { kind: string; direction: string; age_bars: number; time: string }[];
  divergences: { direction: string; status: string; age_bars: number; time: string; p1: DivPivot; p2: DivPivot }[];
}

export interface TechScanResult<T> {
  scan: TechScanSummary | null;
  count: number;
  items: T[];
  note: string;
  confirm_closes?: number;
}


// ---------------------------------------------------------------- E2: indices and RRG
export interface IndexInfo {
  code: string;
  name: string;
  short_name: string;
  category: string;
  category_label: string;
  subcategory: string | null;
  nse_group: string;
  index_type: string;
  return_basis: string;
  active: boolean;
  analysis_enabled: boolean;
  source_url: string;
  data_provider: string | null;
  data_status: string;
  data_status_reason: string | null;
  data_verified: { status: string; matched?: number; compared?: number; checks: { ref: string; date: string; official: number; cached: number | null; ok: boolean | null }[] } | null;
  last_refresh_at: string | null;
  first_date: string | null;
  last_date: string | null;
  rows: number | null;
  intraday_provider: string | null;
  intraday_ticker: string | null;
  intraday_status: string | null;
  intraday_checked_at: string | null;
  reference: Record<string, { date: string; close: number } | null>;
}

export interface IndexOverviewRow {
  code: string;
  name: string;
  category: string;
  category_label: string;
  subcategory: string | null;
  index_type: string;
  analysis_enabled: boolean;
  data_status: string;
  data_status_reason: string | null;
  intraday: boolean;
  stage: string | null;
  latest_close?: number | null;
  latest_date?: string;
  returns?: Record<string, number | null>;
  rsi?: number | null;
  atr_pct?: number | null;
  ema50?: string | null;
  ema200?: string | null;
  rs_vs_benchmark?: number | null;
  rrg_quadrant?: string | null;
  rrg_status?: string | null;
}

export interface IndexOverview {
  benchmark: string;
  timeframe: "1d" | "1w";
  rs_horizon: string;
  return_horizons: string[];
  config_version: string;
  count: number;
  items: IndexOverviewRow[];
  notes: string[];
}

export interface RRGPoint {
  week: string;
  t: number;
  ratio: number;
  momentum: number;
}

export interface RRGItem {
  code: string;
  name: string;
  category: string;
  category_label: string;
  subcategory: string | null;
  status: string;
  reason?: string;
  note?: string;
  trail: RRGPoint[];
  latest: { week: string; t: number; ratio: number; momentum: number; quadrant: string; heading_deg: number | null; direction: string; previous_quadrant: string | null } | null;
  weeks_aligned?: number;
  max_gap_weeks?: number;
}

export interface RRGResult {
  benchmark: string;
  benchmark_name: string;
  benchmark_last_week: string | null;
  data_basis: string;
  method: string;
  params: { rs_period: number; momentum_period: number; trail_weeks: number; center: number; max_gap_weeks: number; lookback_weeks: number };
  config_version: string;
  config_hash: string;
  items: RRGItem[];
  asof: string | null;
  trail_options: number[];
  benchmarks: string[];
  run_id?: string;
}

export interface IndexChart {
  symbol: string;
  code: string;
  name: string;
  category: string;
  category_label: string;
  subcategory: string | null;
  timeframe: Timeframe;
  asof: string | null;
  data_cutoff: string;
  candles: Candle[];
  future_bars: { i: number; t: number }[];
  sr_zones: SRZone[];
  sr_zones_note: string;
  intraday_available: boolean;
  data_source: DataSourceInfo;
  registry: IndexInfo;
  notes: string[];
}

// ---------------------------------------------------------------- E3: Stage Analysis + RS Line
export type StageKey = "stage2_confirmed" | "early_stage2" | "stage1" | "other" | "insufficient_history" | "data_unavailable";

export interface StageGate {
  stage: string;
  name: string;
  value: unknown;
  rule: string;
  passed: boolean | null;
  basis: "source" | "assumption" | "user";
}

export interface StageMetrics {
  close?: number | null;
  week?: string;
  base_high?: number | null;
  base_low?: number | null;
  base_range_pct?: number | null;
  base_duration_weeks?: number | null;
  distance_to_breakout_pct?: number | null;
  higher_lows?: boolean | null;
  prior_decline_pct?: number | null;
  ema30?: number | null;
  ema30_distance_pct?: number | null;
  ema30_position?: string | null;
  ema30_direction?: string | null;
  rs?: number | null;
  rs_zero_line?: string | null;
  rs_trend?: string | null;
  rs_trend_angle?: number | null;
  rs_sma?: number | null;
  rs_sma_direction?: string | null;
  price_confirmation?: string | null;
  volume_contraction?: boolean | null;
  volume_contraction_ratio?: number | null;
  volume_expansion?: boolean | null;
  volume_expansion_ratio?: number | null;
  rs_length?: number;
  setup_met?: boolean;
  setup_failed?: string[];
  setup_checks?: number;
  base_volume_trend?: number | null;
  base_volume_drying_up?: boolean | null;
  volume_pickup_ratio?: number | null;
  [k: string]: unknown;
}

export interface StageRow {
  symbol: string;
  company: string | null;
  instrument: "stock" | "index";
  fno_status: FnoStatus | null;
  category?: string;
  stage: StageKey;
  stage_label: string;
  stage_since: string | null;
  previous_stage: string | null;
  rank: number;
  metrics: StageMetrics;
  gates: StageGate[];
  reasons: string[];
}

export interface StageConfigInfo {
  version: string;
  config_hash: string;
  parameters: (ConfigParam & { basis: "source" | "assumption" | "user" })[];
  sources: string[];
}

export interface StageList {
  instrument: "stock" | "index";
  scan: { scan_id: string; finished_at: string | null; price_basis: string | null; stage_config_version: string | null } | null;
  benchmark?: string;
  summary: Record<string, number>;
  count: number;
  items: StageRow[];
  config: StageConfigInfo;
  note: string;
}

export interface StageDetail extends StageRow {
  data_cutoff: string;
  benchmark: string;
  config: StageConfigInfo;
  overlay?: { times: number[]; ema30: (number | null)[]; base: { high: number | null; low: number | null; t_start: number; t_end: number } | null };
  history?: { week: string; t: number; from: string; to: string }[];
}

export interface RSLinePayload {
  symbol: string;
  instrument: "stock" | "index";
  timeframe: Timeframe;
  benchmark: string;
  length: number;
  status: string;
  times: number[];
  rs: (number | null)[];
  rs_sma: (number | null)[];
  positive: (boolean | null)[];
  bubbles: { t: number; kind: "bullish" | "bearish" }[];
  latest: Record<string, unknown>;
  reference: { date: string; label: string } | null;
  notes: string[];
}

