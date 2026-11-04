import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { advApi, type VolumeResult, type VolumeShock } from "../api/advanced";
import type { Timeframe, Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum, fmtPct, fmtVolume } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const DIR_CLS: Record<string, string> = { bullish: "text-validated", bearish: "text-down", neutral: "text-forming" };

export function fmtRupees(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`;
  return `₹${v.toLocaleString("en-IN")}`;
}

export function VolumeShocker() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const timeframe = (params.get("tf") as Timeframe) || "1d";
  const asof = params.get("asof") ?? "";
  const direction = params.get("dir") ?? "all";
  const minRvol = params.get("rvol") ?? "2";
  const percentile = params.get("pct") === "1";
  const liquidity = params.get("liq") !== "0";
  const maxAge = params.get("age") ?? "0";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<VolumeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    advApi
      .volumeShocks({ universe, timeframe, asof: asof || null, direction, min_rvol: minRvol, percentile: percentile || null, liquidity, max_age: maxAge, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, timeframe, asof, direction, minRvol, percentile, liquidity, maxAge, fno]);
  useEffect(load, [load, tick]);

  const open = (r: VolumeShock) =>
    openChart({
      symbol: r.symbol,
      timeframe,
      asof: asof || null,
      context: {
        title: `${r.direction[0].toUpperCase()}${r.direction.slice(1)} volume shock (${fmtNum(r.rvol, 2)}x)`,
        lines: [
          { k: "Candle", v: fmtBarTime(r.time, timeframe) },
          { k: "Volume / average", v: `${fmtVolume(r.volume)} / ${fmtVolume(r.avg_volume)}` },
          { k: "RVOL (20) / (50)", v: `${fmtNum(r.rvol, 2)} / ${fmtNum(r.rvol_50, 2)}` },
          { k: "60-candle percentile", v: fmtNum(r.percentile_60, 1) },
          { k: "Traded value", v: fmtRupees(r.traded_value) },
          { k: "Change", v: fmtPct(r.change_pct, 2) },
          { k: "Range / ATR", v: fmtNum(r.range_atr, 2) },
          { k: "Reference", v: r.reference },
        ],
        markers: [{ t: r.t, label: `Vol ${fmtNum(r.rvol, 1)}x`, color: r.direction === "bearish" ? "#F2643C" : r.direction === "bullish" ? "#95EB45" : "#E3A33B", position: r.direction === "bearish" ? "above" : "below" }],
      },
    });

  const cols: Column<VolumeShock>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "dir", label: "Direction", render: (r) => <span className={DIR_CLS[r.direction]}>{r.direction}</span>, sort: (r) => r.direction },
    { key: "vol", label: "Volume", align: "right", render: (r) => fmtVolume(r.volume), sort: (r) => r.volume },
    { key: "avg", label: `Avg (${timeframe === "1d" ? "20D" : "20"})`, align: "right", render: (r) => fmtVolume(r.avg_volume), sort: (r) => r.avg_volume, title: "Mean volume of the previous 20 completed candles (current excluded); intraday: same time-of-day candle of the previous sessions" },
    { key: "rvol", label: "RVOL", align: "right", render: (r) => <span className={r.status === "shock" ? "text-ink" : "text-muted"}>{fmtNum(r.rvol, 2)}x</span>, sort: (r) => r.rvol },
    { key: "pct", label: "60-candle pct", align: "right", render: (r) => fmtNum(r.percentile_60, 0), sort: (r) => r.percentile_60, title: "Share of the previous 60 candles with lower volume" },
    { key: "tv", label: "Traded value", align: "right", render: (r) => fmtRupees(r.traded_value), sort: (r) => r.traded_value },
    { key: "ratr", label: "Range / ATR", align: "right", render: (r) => fmtNum(r.range_atr, 2), sort: (r) => r.range_atr },
    { key: "chg", label: "Price change", align: "right", render: (r) => <span className={(r.change_pct ?? 0) >= 0 ? "text-validated" : "text-down"}>{fmtPct(r.change_pct, 2)}</span>, sort: (r) => r.change_pct },
    { key: "clv", label: "Close loc.", align: "right", render: (r) => fmtNum(r.close_location, 2), sort: (r) => r.close_location, title: "(close - low) / (high - low)" },
    { key: "time", label: "Date", render: (r) => fmtBarTime(r.time, timeframe), sort: (r) => r.t },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="volume-shocker">
      <TechScanHeader
        title="Volume Shocker"
        subtitle="Unusual volume vs the stock's own previous 20 completed candles (intraday: same time of day). High volume can be bullish, bearish or neutral; screening thresholds, not predictions."
        universe={universe}
        timeframe={timeframe}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, tf: p.timeframe ?? timeframe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="Direction">
          <select className="ctl" value={direction} onChange={(e) => set({ dir: e.target.value === "all" ? "" : e.target.value })} data-testid="dir-select">
            <option value="all">All</option>
            <option value="bullish">Bullish</option>
            <option value="bearish">Bearish</option>
            <option value="neutral">Neutral</option>
          </select>
        </Field>
        <Field label="RVOL at least">
          <select className="ctl" value={minRvol} onChange={(e) => set({ rvol: e.target.value === "2" ? "" : e.target.value })} data-testid="rvol-select">
            {(res?.rvol_options ?? [1.5, 2, 3, 5]).map((v) => (
              <option key={v} value={String(v)}>
                {v}x
              </option>
            ))}
          </select>
        </Field>
        <label className="flex items-center gap-1 text-xs text-muted" title="Current volume >= the 90th percentile of the previous 60 completed candles">
          <input type="checkbox" checked={percentile} onChange={(e) => set({ pct: e.target.checked ? "1" : "" })} data-testid="pct-toggle" /> &gt;= P90 of 60
        </label>
        <label className="flex items-center gap-1 text-xs text-muted" title="Minimum average traded value per candle (configurable, by timeframe)">
          <input type="checkbox" checked={liquidity} onChange={(e) => set({ liq: e.target.checked ? "" : "0" })} data-testid="liq-toggle" /> Liquidity filter{res?.liquidity_floor ? ` (${fmtRupees(res.liquidity_floor)})` : ""}
        </label>
        <Field label="Age">
          <select className="ctl" value={maxAge} onChange={(e) => set({ age: e.target.value === "0" ? "" : e.target.value })} data-testid="age-select">
            <option value="0">Latest candle</option>
            <option value="2">Last 3 candles</option>
            <option value="4">Last 5 candles</option>
          </select>
        </Field>
        <Field label="F&O">
          <select className="ctl" value={fno} onChange={(e) => set({ fno: e.target.value })} data-testid="fno-select">
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
      <DataTable rows={res?.items ?? []} columns={cols} testid="volume-table" onRow={open} empty="No volume shocks match these filters on the latest scan." initialSort={{ key: "rvol", desc: true }} />
    </div>
  );
}
