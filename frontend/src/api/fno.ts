// F&O OI activity (backend/app/api/fno.py, docs/fno-activity.md). The backend
// runs the user's futures price / OI buildup rules on NSE bhavcopy data; the UI
// only displays the stored results.

import { request } from "./client";

export interface FnoContext {
  spot?: number;
  basis?: number;
  basis_pct?: number;
  volume_ratio?: number;
  delivery_pct?: number;
  delivery_surge_pct?: number;
  volume_rank_pct?: number;
  high_10d?: number;
  low_10d?: number;
  dist_high_10d_pct?: number;
  dist_low_10d_pct?: number;
  high_20d?: number;
  low_20d?: number;
  dist_high_20d_pct?: number;
  dist_low_20d_pct?: number;
  pcr?: number;
  max_call_strike?: number;
  max_call_oi?: number;
  max_put_strike?: number;
  max_put_oi?: number;
  option_expiry?: string;
  oi_all_expiries?: number;
  oi_change_5d_pct?: number;
  oi_change_20d_pct?: number;
  oi_build_streak?: number;
  basis_avg?: number;
  basis_trend?: "Expanding" | "Contracting" | "Neutral";
  atm_iv?: number;
  iv_expiry?: string;
  iv_rank?: number;
  iv_percentile?: number;
  history_sessions?: number;
}

export interface FnoFlag {
  code: string;
  text: string;
}

export interface FnoRow {
  rank: number;
  symbol: string;
  company: string | null;
  instrument: "FUTSTK" | "FUTIDX";
  expiry: string;
  signal: string;
  priority: number | null;
  side: "long" | "short" | "neutral";
  close_today: number | null;
  oi_today: number | null;
  close_prev: number | null;
  oi_prev: number | null;
  price_change_pct: number | null;
  oi_change_pct: number | null;
  oi_change_infinite: boolean;
  context: FnoContext;
  flags: FnoFlag[];
  chart: { instrument: "stock" | "index"; symbol: string } | null;
  t: number;
}

export interface FnoNotice {
  level: "warn" | "info";
  code: string;
  text: string;
}

export interface FnoRule {
  signal: string;
  oi: string;
  stock: string;
  index: string;
}

export interface FnoDate {
  trade_date: string;
  prev_date: string | null;
  signals: number;
  notices: number;
}

export interface FnoActivity {
  trade_date: string | null;
  prev_date: string | null;
  front_expiry?: { latest: string | null; prev: string | null };
  computed_at?: string;
  data?: { fo_source: string | null; equity: string | null; index: string | null };
  config: { version: string; hash: string };
  rules: FnoRule[];
  notices: FnoNotice[];
  signals: FnoRow[];
  watchlist: FnoRow[];
  watchlist_symbols: string[];
  watchlist_missing?: string[];
  dates: FnoDate[];
}

export interface FnoJob {
  state: "idle" | "running" | "done" | "error";
  trigger?: string;
  phase?: string;
  progress?: string;
  done?: number;
  total?: number;
  errors?: string[];
  error?: string;
  started_at?: string;
  finished_at?: string;
  ingested?: number;
  runs_computed?: number;
}

export interface FnoStatus {
  job: FnoJob;
  sessions: {
    stored: number;
    first: string | null;
    last: string | null;
    missing: string[];
    errors: { trade_date: string; error: string }[];
    partial: { trade_date: string; equity: string; index: string }[];
  };
  latest_completed_session: string;
  backfill_sessions: number;
  network: boolean;
  config: { version: string; hash: string };
  sources: { name: string; url: string; use: string; from: string }[];
}

export const fnoApi = {
  status: () => request<FnoStatus>("/api/fno/status"),
  activity: (date?: string | null) => request<FnoActivity>(`/api/fno/activity${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  refresh: (body: { sessions?: number; recompute?: boolean } = {}) => request<FnoJob>("/api/fno/refresh", { method: "POST", body: JSON.stringify(body) }),
  setWatchlist: (symbols: string[]) => request<{ symbols: string[]; known: string[] }>("/api/fno/watchlist", { method: "PUT", body: JSON.stringify({ symbols }) }),
  watchlist: () => request<{ symbols: string[]; known: string[] }>("/api/fno/watchlist"),
};
