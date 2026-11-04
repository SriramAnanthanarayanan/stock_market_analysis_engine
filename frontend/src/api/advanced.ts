// Advanced scanners (fresh breakouts, Volume Shocker, 52-week, VCP), normalized
// evidence, confluence and chart drawings. Mirrors backend/app/api/signals.py,
// vcp.py, confluence.py and drawings.py. The backend computes everything.

import { request, qs } from "./client";
import type { FnoStatus, Timeframe, Universe } from "./types";

export type Direction = "bullish" | "bearish" | "neutral";
type Q = Record<string, string | number | boolean | null | undefined>;

export interface AdvScanSummary {
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
  signal_config_version: string | null;
  signal_config_hash: string | null;
  vcp_config_version: string | null;
  vcp_config_hash: string | null;
  tech_config_version: string | null;
}

export interface StockRef {
  symbol: string;
  company: string | null;
  fno_status: FnoStatus;
  in_nifty500: boolean;
}

export interface AdvResult<T> {
  scan: AdvScanSummary | null;
  count: number;
  items: T[];
  note: string;
}

export interface FreshBreakout extends StockRef {
  event_id: string;
  kind: string;
  direction: Direction;
  lookback: number;
  index: number;
  time: string;
  t: number;
  confirmed_at: string;
  age_bars: number;
  level: number;
  level_time: string;
  previous_level: number | null;
  buffer: number;
  reference_high: number;
  reference_low: number;
  reference_start: string;
  reference_end: string;
  close: number;
  high: number;
  low: number;
  breakout_pct: number;
  distance_atr: number | null;
  range_atr: number | null;
  volume: number | null;
  volume_ratio: number | null;
  trigger: "armed" | "new_range" | "blocked_rearmed";
  confirm_2: "confirmed" | "failed" | "pending";
  episode_state: "active" | "reset" | "superseded";
  episode_change_time: string | null;
  latest_close: number;
  latest_vs_level_pct: number;
  still_beyond: boolean;
  sessions_since_last_beyond: number | null;
  history_sessions_before: number;
  first_in_lookback: boolean | null;
  first_in_lookback_n?: boolean | null;
  previous_event_time: string | null;
  sessions_since_previous_event: number | null;
  confirmation_status?: string;
  // touch mode
  close_status?: string;
  extreme?: number;
  state?: { status: string; high_52w: number | null; low_52w: number | null; distance_from_high_pct: number | null; distance_from_low_pct: number | null };
  official?: OfficialCheck;
  adjustment_basis?: string;
}

export interface FreshResult extends AdvResult<FreshBreakout> {
  lookback_n: number;
  mode: string;
  confirm_closes: number;
}

export interface VolumeShock extends StockRef {
  kind: string;
  direction: Direction;
  status: "shock" | "elevated";
  time: string;
  t: number;
  age_bars: number;
  timeframe: Timeframe;
  reference: string;
  volume: number;
  avg_volume: number;
  median_volume: number;
  max_volume: number;
  rvol: number;
  rvol_50: number | null;
  percentile_60: number | null;
  above_percentile: boolean;
  zscore: number | null;
  traded_value: number;
  avg_traded_value: number | null;
  close: number;
  change_pct: number | null;
  body_pct: number | null;
  close_location: number | null;
  range_atr: number | null;
  close_vs_ema20_pct: number | null;
  close_vs_ema50_pct: number | null;
  distance_from_recent_high_pct: number | null;
  distance_from_recent_low_pct: number | null;
}

export interface VolumeResult extends AdvResult<VolumeShock> {
  min_rvol: number;
  percentile_required: boolean;
  liquidity_floor: number | null;
  rvol_options: number[];
}

export interface OfficialCheck {
  status: "match" | "mismatch" | "unavailable";
  note?: string;
  report_date?: string;
  nse_adjusted_high?: number | null;
  nse_adjusted_low?: number | null;
  high_rel_diff?: number | null;
  low_rel_diff?: number | null;
}

export interface Week52State extends StockRef {
  status: "ok" | "insufficient_history";
  time: string;
  close: number;
  high_52w?: number;
  high_52w_time?: string;
  low_52w?: number;
  low_52w_time?: string;
  distance_from_high_pct?: number;
  distance_from_low_pct?: number;
  close_above_high?: boolean;
  close_below_low?: boolean;
  touch_high?: boolean;
  touch_low?: boolean;
  available_sessions: number;
  available_high?: number;
  available_low?: number;
  note?: string;
  adjustment_basis: string;
  official: OfficialCheck;
}

export interface Week52Result extends AdvResult<FreshBreakout | Week52State> {
  view: "events" | "touch" | "state";
  insufficient_history: number;
  official_report_date: string | null;
}

export interface Evidence extends StockRef {
  evidence_id: string;
  scan_id: string;
  timeframe: Timeframe;
  source: string;
  signal_type: string;
  category: string;
  kind: "event" | "state" | "formation";
  direction: Direction;
  signal_time: string | null;
  age_bars: number | null;
  status: string;
  lifecycle: "active" | "invalidated" | "expired";
  valid_until: string | null;
  expiry_rule: string | null;
  quality: number | null;
  strength: number | null;
  underlying_id: string | null;
  metrics: Record<string, unknown>;
  explanation: string[];
  config_version: string;
  config_hash: string;
}

export interface ConfigParamRow {
  name: string;
  value: unknown;
  description: string;
  unit: string;
  basis?: string;
}

export const advApi = {
  freshBreakouts: (q: Q) => request<FreshResult>(`/api/scan/fresh-breakouts${qs(q)}`),
  volumeShocks: (q: Q) => request<VolumeResult>(`/api/scan/volume-shocks${qs(q)}`),
  week52: (q: Q) => request<Week52Result>(`/api/scan/52week${qs(q)}`),
  evidence: (q: Q) => request<AdvResult<Evidence>>(`/api/evidence${qs(q)}`),
  config: (which: "signals" | "vcp" | "confluence") => request<{ version: string; config_hash: string; parameters: ConfigParamRow[]; detector_timeframes?: Record<string, string[]> }>(`/api/config/${which}`),
};

// ---------------------------------------------------------------- VCP (P2)
export type VcpStatus = "developing" | "forming" | "tightening" | "near_pivot" | "breakout" | "breakout_pending" | "pivot_cross_low_volume" | "failed_breakout" | "invalidated";

export interface VcpContraction {
  seq: number;
  label: string;
  start_time: string;
  end_time: string;
  start_price: number;
  end_price: number;
  depth: number;
  depth_pct: number;
  bars: number;
  avg_volume: number | null;
  min_volume: number | null;
  volume_vs_avg50: number | null;
  provisional: boolean;
}

export interface VcpGeometry {
  schema_version: string;
  vcp_id: string;
  base: { i_start: number; t_start: number; i_end: number; t_end: number; p_high: number; p_low: number };
  contractions: { seq: number; label: string; t_high: number; p_high: number; t_low: number; p_low: number; depth_pct: number; volume_vs_avg50: number | null; provisional: boolean }[];
  pivot: { i: number; t: number; p: number; t_end: number };
  breakout: { i: number; t: number; time: string; close: number; volume_ratio: number | null; volume_ok: boolean } | null;
  event: { i: number; t: number; status: string } | null;
  last_candle: { i: number; t: number; p: number };
}

export interface StageContext {
  stage: string;
  stage_label: string;
  stage_since: string | null;
  rs: number | null;
  ema30_direction: string | null;
  week: string | null;
}

export interface QualityComp {
  name: string;
  weight: number;
  raw: number | null;
  score: number;
  formula: string;
}

export interface VcpGate {
  name: string;
  passed: boolean | null;
  value: unknown;
  rule: string;
  basis: string;
}

export interface VcpRow extends StockRef {
  vcp_id: string;
  status: VcpStatus;
  status_label: string;
  status_reason: string | null;
  qualified: boolean;
  quality: number;
  quality_components: QualityComp[];
  gates: VcpGate[];
  explanation: string[];
  contractions: number;
  depths_pct: number[];
  base_start: string;
  base_end: string;
  base_sessions: number;
  pivot: number | null;
  pivot_time: string | null;
  final_depth_pct: number | null;
  last_close: number | null;
  pivot_distance_pct: number | null;
  breakout_time: string | null;
  last_candle_time: string | null;
  metrics: Record<string, unknown> & { volume_trend_final_vs_first?: number | null; final_volume_vs_avg50?: number | null; dry_up_days_final?: number | null; prior_advance_pct?: number | null; contraction_volumes?: (number | null)[] };
  stage?: StageContext | null;
  trend_context_ok?: boolean | null;
  geometry?: VcpGeometry;
  contraction_list?: VcpContraction[];
  config_version: string;
}

export interface VcpScanResult extends AdvResult<VcpRow> {
  stage_scan: { scan_id: string; finished_at: string | null } | null;
  status_labels: Record<string, string>;
}

export interface VcpSymbol {
  symbol: string;
  source: "scan" | "adhoc";
  vcp: VcpRow | null;
  stage: StageContext | null;
  note?: string;
}

export const vcpApi = {
  scan: (q: Q) => request<VcpScanResult>(`/api/scan/vcp${qs(q)}`),
  symbol: (symbol: string, asof?: string | null) => request<VcpSymbol>(`/api/vcp/symbol${qs({ symbol, asof })}`),
};

// ---------------------------------------------------------------- chart drawings (P3)
import type { Drawing, DrawingType } from "../charts/drawings";

export interface DrawingTemplate {
  template_id: string;
  name: string;
  drawing_type: DrawingType;
  scope: "global" | "symbol";
  symbol: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export class ConflictError extends Error {
  current: Drawing | null;
  constructor(message: string, current: Drawing | null) {
    super(message);
    this.current = current;
  }
}

async function drawingRequest<T>(path: string, init?: RequestInit & { keepalive?: boolean }): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json", "Content-Type": "application/json" }, ...init });
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    throw new ConflictError(body?.detail?.message ?? "conflict", body?.detail?.current ?? null);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep */
    }
    const err = new Error(msg) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

type DrawingBody = Omit<Drawing, "version" | "created_at" | "updated_at" | "origin">;

export const drawingsApi = {
  list: (instrument: string, symbol: string) => drawingRequest<{ items: Drawing[]; server_time: string }>(`/api/drawings${qs({ instrument, symbol })}`),
  create: (d: DrawingBody, keepalive = false) => drawingRequest<Drawing>("/api/drawings", { method: "POST", body: JSON.stringify(d), keepalive }),
  update: (d: DrawingBody, baseVersion: number, keepalive = false) => drawingRequest<Drawing>(`/api/drawings/${encodeURIComponent(d.drawing_id)}`, { method: "PUT", body: JSON.stringify({ ...d, base_version: baseVersion }), keepalive }),
  remove: (id: string, version: number, keepalive = false) => drawingRequest<void>(`/api/drawings/${encodeURIComponent(id)}${qs({ version })}`, { method: "DELETE", keepalive }),
  exportUrl: "/api/drawings/export",
  import: (payload: unknown) => drawingRequest<{ inserted: number; updated: number; skipped: number; invalid: number; errors: string[] }>("/api/drawings/import", { method: "POST", body: JSON.stringify(payload) }),
  templates: (drawingType?: string, symbol?: string) => drawingRequest<{ items: DrawingTemplate[] }>(`/api/drawing-templates${qs({ drawing_type: drawingType, symbol })}`),
  saveTemplate: (t: Omit<DrawingTemplate, "created_at" | "updated_at">) => drawingRequest<DrawingTemplate>("/api/drawing-templates", { method: "POST", body: JSON.stringify(t) }),
  deleteTemplate: (id: string) => drawingRequest<void>(`/api/drawing-templates/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
