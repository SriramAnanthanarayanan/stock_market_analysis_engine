import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { IndexOverview, IndexOverviewRow } from "../api/types";
import { ErrorNote } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field } from "../components/TechScanHeader";
import { fmtDate, fmtNum, humanize } from "../lib/format";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? <span className="text-muted">-</span> : <span className={v >= 0 ? "text-validated" : "text-down"}>{v.toFixed(2)}%</span>);
const QCOL: Record<string, string> = { leading: "text-validated", weakening: "text-forming", lagging: "text-down", improving: "text-accent" };

export function IndexAnalysis() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tf = (params.get("tf") as "1d" | "1w") || "1d";
  const benchmark = params.get("bench") ?? "NIFTY 500";
  const category = params.get("cat") ?? "";
  const sub = params.get("sub") ?? "";
  const avail = params.get("avail") ?? "ok";
  const rs = params.get("rs") ?? "";
  const trend = params.get("trend") ?? "";
  const stage = params.get("stage") ?? "";
  const q = params.get("q") ?? "";
  const [data, setData] = useState<IndexOverview | null>(null);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = () => {
    setLoading(true);
    api
      .indexOverview({ timeframe: tf, benchmark })
      .then((r) => (setData(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
    api.indexStatus().then(setStatus).catch(() => setStatus(null));
  };
  useEffect(load, [tf, benchmark]);
  const job = (status?.job ?? {}) as { state?: string; phase?: string; progress?: string };
  useEffect(() => {
    if (job.state !== "running") return;
    const id = window.setTimeout(() => api.indexStatus().then(setStatus).catch(() => null), 3000);
    return () => window.clearTimeout(id);
  }, [status]);

  const subs = useMemo(() => [...new Set((data?.items ?? []).filter((r) => !category || r.category === category).map((r) => r.subcategory).filter(Boolean) as string[])].sort(), [data, category]);
  const rows = (data?.items ?? []).filter((r) => {
    if (category && r.category !== category) return false;
    if (sub && r.subcategory !== sub) return false;
    if (avail === "ok" && !(r.analysis_enabled && r.latest_close !== undefined && r.latest_close !== null)) return false;
    if (avail === "unavailable" && r.analysis_enabled && r.latest_close !== undefined) return false;
    if (q && !r.code.toLowerCase().includes(q.toLowerCase()) && !r.name.toLowerCase().includes(q.toLowerCase())) return false;
    if (rs === "out" && !((r.rs_vs_benchmark ?? -Infinity) > 0)) return false;
    if (rs === "under" && !((r.rs_vs_benchmark ?? Infinity) < 0)) return false;
    if (trend && r.ema200 !== trend) return false;
    if (stage && r.stage !== stage) return false;
    return true;
  });
  const cols: Column<IndexOverviewRow>[] = [
    { key: "name", label: "Index", render: (r) => <span className="font-medium text-ink" title={r.code}>{r.name}</span>, sort: (r) => r.name },
    { key: "cat", label: "Category", render: (r) => <span className="text-muted">{r.category_label}{r.subcategory ? ` · ${humanize(r.subcategory)}` : ""}</span>, sort: (r) => `${r.category}${r.subcategory}` },
    { key: "close", label: "Latest close", align: "right", render: (r) => (r.latest_close !== undefined ? fmtNum(r.latest_close) : <span className="text-muted" title={r.data_status_reason ?? ""}>{r.analysis_enabled ? r.data_status : humanize(r.index_type)}</span>), sort: (r) => r.latest_close ?? null },
    ...(data?.return_horizons ?? ["1W", "1M", "3M", "6M", "1Y"]).map(
      (h): Column<IndexOverviewRow> => ({ key: `r${h}`, label: `${h} return`, align: "right", render: (r) => pct(r.returns?.[h]), sort: (r) => r.returns?.[h] ?? null }),
    ),
    { key: "rsi", label: "RSI", align: "right", render: (r) => fmtNum(r.rsi, 1), sort: (r) => r.rsi ?? null },
    { key: "atr", label: "ATR %", align: "right", render: (r) => fmtNum(r.atr_pct, 2), sort: (r) => r.atr_pct ?? null },
    { key: "e50", label: "EMA 50", render: (r) => <span className={r.ema50 === "above" ? "text-validated" : r.ema50 === "below" ? "text-down" : "text-muted"}>{r.ema50 ?? "-"}</span>, sort: (r) => r.ema50 ?? "" },
    { key: "e200", label: "EMA 200", render: (r) => <span className={r.ema200 === "above" ? "text-validated" : r.ema200 === "below" ? "text-down" : "text-muted"}>{r.ema200 ?? "-"}</span>, sort: (r) => r.ema200 ?? "" },
    { key: "rs", label: `RS vs ${benchmark} (${data?.rs_horizon ?? "3M"})`, align: "right", render: (r) => pct(r.rs_vs_benchmark), sort: (r) => r.rs_vs_benchmark ?? null },
    { key: "stage", label: "Stage", render: (r) => <span className="text-muted">{r.stage ?? "-"}</span>, sort: (r) => r.stage ?? "" },
    { key: "rrg", label: "RRG quadrant", render: (r) => <span className={QCOL[r.rrg_quadrant ?? ""] ?? "text-muted"}>{r.rrg_quadrant ?? (r.rrg_status && r.rrg_status !== "ok" ? humanize(r.rrg_status) : "-")}</span>, sort: (r) => r.rrg_quadrant ?? "" },
  ];
  const counts = status as { registry?: number; equity?: number; data_ok?: number; data_error?: number; pending?: number; intraday_verified?: number } | null;
  return (
    <div className="mx-auto flex max-w-[1700px] flex-col gap-3 px-4 py-3" data-testid="index-analysis">
      <div className="panel flex flex-col gap-2 px-3 py-2.5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="mr-2">
            <h1 className="text-base font-semibold text-ink">Index Analysis</h1>
            <div className="text-[11px] text-muted">Official NSE index catalogue and official index history (price-return levels). Completed candles only.</div>
          </div>
          <Field label="Timeframe">
            <div className="flex gap-1">
              {(["1d", "1w"] as const).map((t) => (
                <button key={t} className={`chip h-8 ${t === tf ? "chip-on" : ""}`} onClick={() => set({ tf: t })} data-testid={`ia-tf-${t}`}>
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Benchmark">
            <select className="ctl" value={benchmark} onChange={(e) => set({ bench: e.target.value })} data-testid="ia-benchmark">
              <option value="NIFTY 500">NIFTY 500</option>
              <option value="NIFTY 50">NIFTY 50</option>
            </select>
          </Field>
          <Field label="Category">
            <select className="ctl" value={category} onChange={(e) => set({ cat: e.target.value, sub: "" })} data-testid="ia-category">
              <option value="">All</option>
              <option value="broad">Broad market</option>
              <option value="sector">Sectoral</option>
              <option value="thematic">Thematic</option>
              <option value="strategy">Factor / strategy</option>
              <option value="fixed_income">Fixed income</option>
            </select>
          </Field>
          <Field label="Subcategory">
            <select className="ctl" value={sub} onChange={(e) => set({ sub: e.target.value })} data-testid="ia-subcategory">
              <option value="">All</option>
              {subs.map((x) => (
                <option key={x} value={x}>
                  {humanize(x)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Availability">
            <select className="ctl" value={avail} onChange={(e) => set({ avail: e.target.value })} data-testid="ia-availability">
              <option value="ok">Data available</option>
              <option value="unavailable">Unavailable / not applicable</option>
              <option value="all">All</option>
            </select>
          </Field>
          <Field label="Relative strength">
            <select className="ctl" value={rs} onChange={(e) => set({ rs: e.target.value })} data-testid="ia-rs">
              <option value="">Any</option>
              <option value="out">Outperforming benchmark</option>
              <option value="under">Underperforming</option>
            </select>
          </Field>
          <Field label="Trend (EMA 200)">
            <select className="ctl" value={trend} onChange={(e) => set({ trend: e.target.value })} data-testid="ia-trend">
              <option value="">Any</option>
              <option value="above">Above</option>
              <option value="below">Below</option>
            </select>
          </Field>
          <Field label="Stage">
            <select className="ctl" value={stage} onChange={(e) => set({ stage: e.target.value })} data-testid="ia-stage">
              <option value="">Any</option>
              {[...new Set((data?.items ?? []).map((r) => r.stage).filter(Boolean) as string[])].map((s) => (
                <option key={s} value={s}>
                  {humanize(s)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Search">
            <input className="ctl w-40" value={q} onChange={(e) => set({ q: e.target.value })} data-testid="ia-search" />
          </Field>
          <button className="btn h-8" onClick={() => api.refreshIndices().then(() => api.indexStatus().then(setStatus))} disabled={job.state === "running"} data-testid="ia-refresh">
            {job.state === "running" ? `Refreshing (${job.phase} ${job.progress ?? ""})` : "Refresh index data"}
          </button>
        </div>
        <div className="flex flex-wrap gap-4 text-[11px] text-muted" data-testid="ia-status">
          <span>{counts?.registry ?? 0} indices in the official catalogue</span>
          <span>{counts?.equity ?? 0} equity price indices</span>
          <span className="text-validated">{counts?.data_ok ?? 0} with verified data</span>
          {(counts?.data_error ?? 0) > 0 && <span className="text-down">{counts?.data_error} data errors</span>}
          {(counts?.pending ?? 0) > 0 && <span className="text-forming">{counts?.pending} pending download</span>}
          <span>{counts?.intraday_verified ?? 0} with verified intraday data</span>
          <span>{rows.length} shown</span>
          {loading && <span>Loading...</span>}
        </div>
      </div>
      {error && <ErrorNote message={error} />}
      <DataTable rows={rows} columns={cols} testid="index-table" onRow={(r) => r.analysis_enabled && navigate(`/index/${encodeURIComponent(r.code)}?tf=1d`)} empty="No index matches these filters." initialSort={{ key: "rs", desc: true }} pageSize={60} />
      <ul className="text-[11px] text-muted">
        {data?.notes.map((n) => <li key={n}>{n}</li>)}
        <li>Stage Analysis per index arrives with E3. Latest data: {fmtDate(data?.items.find((r) => r.latest_date)?.latest_date)}.</li>
      </ul>
    </div>
  );
}
