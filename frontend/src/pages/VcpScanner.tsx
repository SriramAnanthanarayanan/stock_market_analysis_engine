import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { vcpApi, type VcpRow, type VcpScanResult } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge, QualityCell } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { StageChip, VcpPanel, VcpStatus } from "../components/VcpPanel";
import { fmtBarTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const PRESETS: { key: string; label: string; status: string }[] = [
  { key: "", label: "Active VCPs", status: "" },
  { key: "near", label: "Near Pivot", status: "near_pivot" },
  { key: "tight", label: "Final Contraction", status: "final_contraction,near_pivot" },
  { key: "bo", label: "Breakouts", status: "breakout" },
  { key: "forming", label: "Forming", status: "forming" },
  { key: "failed", label: "Failed / invalidated", status: "failed_breakout,invalidated" },
];

export function VcpScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const preset = params.get("preset") ?? "";
  const stage = params.get("stage") ?? "all";
  const minQ = params.get("minq") ?? "";
  const dev = params.get("dev") === "1";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<VcpScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<VcpRow | null>(null);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const status = PRESETS.find((p) => p.key === preset)?.status ?? "";
  const load = useCallback(() => {
    vcpApi
      .scan({ universe, asof: asof || null, status: status || null, stage, min_quality: minQ || null, include_developing: dev || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, status, stage, minQ, dev, fno]);
  useEffect(load, [load, tick]);

  const open = (r: VcpRow) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      show: ["vcp"],
      context: {
        title: `VCP - ${r.status_label}`,
        vcp: true,
        lines: [
          { k: "Quality", v: `${r.quality.toFixed(0)} / 100` },
          { k: "Contractions", v: r.depths_pct.map((d, i) => `T${i + 1} ${d.toFixed(1)}%`).join(" > ") },
          { k: "Pivot", v: fmtNum(r.pivot) },
          { k: "Distance to pivot", v: r.pivot_distance_pct === null ? "-" : `${r.pivot_distance_pct.toFixed(2)}%` },
          { k: "Base", v: `${r.base_sessions} sessions` },
          { k: "Stage (context)", v: r.stage?.stage_label ?? "no weekly stage" },
        ],
      },
    });

  const cols: Column<VcpRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "status", label: "VCP status", render: (r) => <VcpStatus v={r} />, sort: (r) => r.status },
    { key: "quality", label: "Quality", render: (r) => <QualityCell score={r.quality} label={r.qualified ? "qualified" : "not qualified"} />, sort: (r) => r.quality },
    { key: "t", label: "Contractions", render: (r) => <span className="num text-ink">{r.depths_pct.map((d) => `${d.toFixed(1)}%`).join(" > ")}</span>, sort: (r) => r.contractions, title: "Depth of T1 > T2 > ... (% from each contraction high)" },
    { key: "final", label: "Final", align: "right", render: (r) => `${fmtNum(r.final_depth_pct, 1)}%`, sort: (r) => r.final_depth_pct },
    { key: "vol", label: "Final/T1 vol", align: "right", render: (r) => fmtNum((r.metrics?.volume_trend_final_vs_first as number | null) ?? null, 2), sort: (r) => (r.metrics?.volume_trend_final_vs_first as number | null) ?? null, title: "Mean volume of the final contraction / T1 (supply drying up < 1)" },
    { key: "pivot", label: "Pivot", align: "right", render: (r) => fmtNum(r.pivot), sort: (r) => r.pivot },
    { key: "dist", label: "To pivot", align: "right", render: (r) => (r.pivot_distance_pct === null ? "-" : `${r.pivot_distance_pct.toFixed(2)}%`), sort: (r) => r.pivot_distance_pct, title: "(pivot - close) / pivot; negative = above the pivot" },
    { key: "base", label: "Base", align: "right", render: (r) => `${r.base_sessions}d`, sort: (r) => r.base_sessions },
    { key: "bo", label: "Breakout", render: (r) => (r.breakout_time ? fmtBarTime(r.breakout_time, "1d") : "-"), sort: (r) => r.breakout_time ?? "" },
    { key: "stage", label: "Stage (context)", render: (r) => <StageChip st={r.stage} />, sort: (r) => r.stage?.stage ?? "" },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="vcp-scanner">
      <TechScanHeader
        title="VCP Scanner"
        subtitle="Volatility Contraction Patterns on completed daily candles (Minervini principles, configurable engineering thresholds). Stage Analysis is shown as separate trend context."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <div className="flex flex-col gap-1">
          <span className="label">Status</span>
          <div className="flex flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button key={p.key} className={`chip h-8 ${preset === p.key ? "chip-on" : ""}`} onClick={() => set({ preset: p.key })} data-testid={`vcp-preset-${p.key || "active"}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <Field label="Trend context (Stage)">
          <select className="ctl" value={stage} onChange={(e) => set({ stage: e.target.value === "all" ? "" : e.target.value })} data-testid="stage-filter">
            <option value="all">All stages</option>
            <option value="stage2">Stage 2 only (Early + Confirmed)</option>
            <option value="stage2_or_stage1">Stage 1 or 2</option>
            <option value="exclude_other">Exclude Other / Unqualified</option>
          </select>
        </Field>
        <Field label="Min quality">
          <input className="ctl w-20" type="number" min={0} max={100} step={5} value={minQ} onChange={(e) => set({ minq: e.target.value })} data-testid="min-quality" />
        </Field>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={dev} onChange={(e) => set({ dev: e.target.checked ? "1" : "" })} /> Include developing
        </label>
        <Field label="F&O">
          <select className="ctl" value={fno} onChange={(e) => set({ fno: e.target.value })}>
            <option value="">All</option>
            <option value="YES">F&amp;O only</option>
            <option value="NO">Non-F&amp;O</option>
          </select>
        </Field>
        <span className="text-xs text-muted" data-testid="result-count">
          {res?.count ?? 0} results
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <div className={`grid gap-3 ${selected ? "xl:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
        <DataTable
          rows={res?.items ?? []}
          columns={cols}
          testid="vcp-table"
          onRow={(r) => {
            setSelected(r);
            vcpApi
              .symbol(r.symbol, asof || null)
              .then((full) => full.vcp && full.vcp.vcp_id === r.vcp_id && setSelected({ ...r, contraction_list: full.vcp.contraction_list, geometry: full.vcp.geometry }))
              .catch(() => undefined);
          }}
          empty="No VCP matches these filters on the latest daily scan."
          initialSort={{ key: "quality", desc: true }}
        />
        {selected && (
          <div className="flex flex-col gap-2" data-testid="vcp-detail">
            <div className="flex items-center justify-between">
              <SymbolButton symbol={selected.symbol} onOpen={() => open(selected)} />
              <button className="chip h-6" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <VcpPanel v={selected} stage={selected.stage} />
          </div>
        )}
      </div>
    </div>
  );
}
