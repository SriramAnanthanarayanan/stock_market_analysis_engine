import type {
  RSLinePayload,
  StageDetail,
  StageList,
  IndexChart,
  IndexInfo,
  IndexOverview,
  RRGResult,
  BreakoutSignal,
  CandleSignal,
  ChartResponse,
  ChartTf,
  IndicatorPayload,
  IndicatorRow,
  TechnicalPayload,
  TechScanResult,
  CompareResult,
  ConfigParam,
  EvalOptions,
  EvalResult,
  HealthResponse,
  Label,
  LabelIn,
  Match,
  OutcomeGroup,
  OverviewResponse,
  PatternInfo,
  RefreshResponse,
  ScanResponse,
  ScanSummary,
  SweepResult,
  Timeframe,
  Track,
  TrackDetail,
  Universe,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json", "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

function clean(o: Record<string, string | undefined>): Record<string, string> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v)) as Record<string, string>;
}

export interface ScanQuery {
  universe: Universe;
  timeframe: Timeframe;
  asof?: string | null;
  pattern?: string | null;
  category?: string | null;
  status?: string | null;
  min_quality?: number;
  include_developing?: boolean;
}

type LabelFilter = Record<string, string | undefined>;
type Overrides = Record<string, unknown>;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const exportUrl = {
  labels: (format: "csv" | "json") => `/api/labels/export?format=${format}`,
  detections: (universe: Universe, timeframe: Timeframe, asof: string | null, format: "csv" | "json") =>
    `/api/export/detections${qs({ universe, timeframe, asof, format })}`,
  run: (runId: string, format: "csv" | "json", cases: "all" | "tp" | "fp" | "fn") => `/api/evaluation/runs/${runId}/export${qs({ format, cases })}`,
};

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  patterns: () => request<PatternInfo[]>("/api/patterns"),
  config: () => request<{ version: string; hash: string; parameters: ConfigParam[] }>("/api/config"),
  scan: (q: ScanQuery) => request<ScanResponse>(`/api/scan${qs({ ...q })}`),
  scanStatus: (id: string) => request<ScanSummary>(`/api/scan/status/${encodeURIComponent(id)}`),
  refresh: (body: { universe: Universe; timeframe: Timeframe; asof?: string | null; patterns?: string[] | null; full_refresh?: boolean }) =>
    request<RefreshResponse>("/api/scan/refresh", post(body)),
  overview: (universe: Universe, asof?: string | null) => request<OverviewResponse>(`/api/overview${qs({ universe, asof })}`),
  chart: (symbol: string, timeframe: ChartTf, patternId?: string | null, asof?: string | null, maxCandles?: number | null) =>
    request<ChartResponse>(`/api/chart${qs({ symbol, timeframe, pattern_id: patternId, asof, max_candles: maxCandles })}`),
  history: (symbol: string, limit = 50) => request<{ count: number; items: Match[]; note: string }>(`/api/history${qs({ symbol, limit })}`),
  // P3 ---------------------------------------------------------------
  labels: (q: { symbol?: string; timeframe?: Timeframe } = {}) => request<Label[]>(`/api/labels${qs(q)}`),
  createLabel: (body: LabelIn) => request<Label>("/api/labels", post(body)),
  updateLabel: (id: string, body: LabelIn) => request<Label>(`/api/labels/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteLabel: (id: string) => request<void>(`/api/labels/${id}`, { method: "DELETE" }),
  tracks: (q: Record<string, string | number | boolean | undefined> = {}) => request<Track[]>(`/api/tracks${qs(q)}`),
  track: (id: string) => request<TrackDetail>(`/api/tracks/${id}`),
  outcomes: () => request<{ horizons: number[]; min_sample: number; groups: OutcomeGroup[]; note: string }>("/api/outcomes/summary"),
  evaluate: (options: EvalOptions, labels: LabelFilter = {}, overrides: Overrides = {}) =>
    request<EvalResult>("/api/evaluation/run", post({ options, labels: clean(labels), overrides })),
  compare: (options: EvalOptions, a: Overrides, b: Overrides, labels: LabelFilter = {}) =>
    request<CompareResult>("/api/evaluation/compare", post({ options, a_overrides: a, b_overrides: b, labels: clean(labels) })),
  sweep: (options: EvalOptions, param: string, values: (number | boolean)[], labels: LabelFilter = {}) =>
    request<SweepResult>("/api/evaluation/sweep", post({ options, param, values, labels: clean(labels) })),
  runs: () => request<{ run_id: string; created_at: string; kind: string; config_version: string; label_count: number; overrides: unknown }[]>("/api/evaluation/runs"),
  // E1 ---------------------------------------------------------------
  indicators: (q: { symbol: string; timeframe: ChartTf; asof?: string | null; scan_id?: string | null; start_t?: number; ema?: string; rsi_period?: number; atr_period?: number; instrument?: string }) =>
    request<IndicatorPayload>(`/api/indicators${qs(q)}`),
  technical: (q: { symbol: string; timeframe: ChartTf; asof?: string | null; scan_id?: string | null; start_t?: number; confirm?: number; instrument?: string }) =>
    request<TechnicalPayload>(`/api/technical${qs(q)}`),
  scanCandlesticks: (q: Record<string, string | number | boolean | null | undefined>) => request<TechScanResult<CandleSignal>>(`/api/scan/candlesticks${qs(q)}`),
  scanBreakouts: (q: Record<string, string | number | boolean | null | undefined>) => request<TechScanResult<BreakoutSignal>>(`/api/scan/breakouts${qs(q)}`),
  scanIndicators: (q: Record<string, string | number | boolean | null | undefined>) => request<TechScanResult<IndicatorRow>>(`/api/scan/indicators${qs(q)}`),
  // E2 ---------------------------------------------------------------
  indices: (q: Record<string, string | undefined> = {}) =>
    request<{ count: number; items: IndexInfo[]; counts: Record<string, { total: number; ok: number; intraday: number }>; source: string; data_provider: string; job: Record<string, unknown> }>(`/api/indices${qs(q)}`),
  index: (code: string) => request<IndexInfo>(`/api/indices/${encodeURIComponent(code)}`),
  indexStatus: () => request<Record<string, unknown>>("/api/indices/status"),
  refreshIndices: () => request<Record<string, unknown>>("/api/indices/refresh", post({ force: false, intraday: true })),
  indexOverview: (q: { timeframe: "1d" | "1w"; benchmark?: string; asof?: string | null; codes?: string }) => request<IndexOverview>(`/api/indices/overview${qs(q)}`),
  indexChart: (code: string, timeframe: ChartTf, asof?: string | null) => request<IndexChart>(`/api/index/chart${qs({ code, timeframe, asof })}`),
  rrg: (q: { benchmark?: string; asof?: string | null; trail?: number; codes?: string; category?: string }) => request<RRGResult>(`/api/rrg${qs(q)}`),
  // E3 ---------------------------------------------------------------
  stage: (q: Record<string, string | number | null | undefined>) => request<StageList>(`/api/stage${qs(q)}`),
  stageSymbol: (q: { symbol: string; instrument: "stock" | "index"; asof?: string | null; start_t?: number }) => request<StageDetail>(`/api/stage/symbol${qs(q)}`),
  rsline: (q: { symbol: string; instrument: "stock" | "index"; timeframe: Timeframe; asof?: string | null; scan_id?: string | null; start_t?: number }) => request<RSLinePayload>(`/api/rsline${qs(q)}`),
  symbols: (q: string, limit = 20) => request<{ count: number; items: { symbol: string; name: string | null; kind: "stock" | "index"; fno_status?: string; category?: string }[] }>(`/api/symbols${qs({ q, limit })}`),
};
