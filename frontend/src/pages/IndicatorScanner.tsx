import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { SymbolButton, useOpenChart } from "../lib/openChart";
import { api } from "../api/client";
import type { IndicatorRow, TechScanResult, Timeframe, Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtNum, humanize } from "../lib/format";

const ALIGN: Record<string, string> = {
  bullish_full: "Bullish full",
  bearish_full: "Bearish full",
  bullish_partial: "Bullish partial",
  bearish_partial: "Bearish partial",
  mixed: "Mixed",
  not_available: "n/a",
};

export function IndicatorScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const open = (r: IndicatorRow) =>
    openChart({
      symbol: r.symbol,
      timeframe: r.timeframe,
      asof: asof || null,
      show: ["ema", "rsi", ...(r.divergences.length ? ["divergence"] : [])],
      context: {
        title: `EMA alignment: ${ALIGN[r.alignment ?? "not_available"] ?? r.alignment}`,
        lines: [
          { k: "Close", v: fmtNum(r.close) },
          { k: "RSI", v: fmtNum(r.rsi, 1) },
          { k: "ATR %", v: fmtNum(r.atr_pct, 2) },
          { k: "Crossovers", v: r.crosses.map((c) => `${c.kind} ${c.direction}`).join(", ") || "-" },
          { k: "Divergences", v: r.divergences.map((d) => `${d.direction} (${d.status})`).join(", ") || "-" },
        ],
      },
    });
  const universe = (params.get("u") as Universe) || "all";
  const timeframe = (params.get("tf") as Timeframe) || "1d";
  const asof = params.get("asof") ?? "";
  const alignment = params.get("align") ?? "";
  const ema200 = params.get("ema200") ?? "";
  const cross = params.get("cross") ?? "";
  const rsiState = params.get("rsi") ?? "";
  const div = params.get("div") ?? "";
  const divStatus = params.get("div_status") ?? "";
  const maxAge = params.get("age") ?? "5";
  const [res, setRes] = useState<TechScanResult<IndicatorRow> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    api
      .scanIndicators({ universe, timeframe, asof: asof || null, alignment: alignment || null, price_vs_ema200: ema200 || null, cross: cross || null, rsi_state: rsiState || null, divergence: div || null, divergence_status: divStatus || null, max_age: maxAge })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, timeframe, asof, alignment, ema200, cross, rsiState, div, divStatus, maxAge]);
  useEffect(load, [load, tick]);

  const pos = (r: IndicatorRow, p: string) => {
    const v = r.price_vs_ema[p];
    return <span className={v === "above" ? "text-validated" : v === "below" ? "text-down" : "text-muted"}>{v ?? "n/a"}</span>;
  };
  const cols: Column<IndicatorRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "align", label: "EMA alignment", render: (r) => <span className={r.alignment.startsWith("bullish") ? "text-validated" : r.alignment.startsWith("bearish") ? "text-down" : "text-muted"}>{ALIGN[r.alignment] ?? r.alignment}</span>, sort: (r) => r.alignment },
    { key: "e20", label: "vs EMA20", render: (r) => pos(r, "20") },
    { key: "e50", label: "vs EMA50", render: (r) => pos(r, "50") },
    { key: "e200", label: "vs EMA200", render: (r) => pos(r, "200") },
    { key: "rsi", label: "RSI(14)", align: "right", render: (r) => <span className={r.rsi_state === "overbought" ? "text-down" : r.rsi_state === "oversold" ? "text-validated" : ""}>{fmtNum(r.rsi, 1)}</span>, sort: (r) => r.rsi },
    { key: "atr", label: "ATR %", align: "right", render: (r) => fmtNum(r.atr_pct, 2), sort: (r) => r.atr_pct },
    { key: "cross", label: "EMA crossovers", render: (r) => r.crosses.map((c) => `${humanize(c.kind.replace("ema_cross_", "EMA "))} ${c.direction} (${c.age_bars}b)`).join(", ") || "-" },
    { key: "div", label: "RSI divergence", render: (r) => r.divergences.map((d) => `${d.direction} ${d.status} (${d.age_bars}b)`).join(", ") || "-" },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="indicator-scanner">
      <TechScanHeader
        title="Indicator Scanner"
        subtitle="EMA 10/20/30/50/100/200 alignment and crossovers, RSI(14) and regular RSI divergence on completed candles. Descriptive states, not signals."
        universe={universe}
        timeframe={timeframe}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, tf: p.timeframe ?? timeframe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="EMA alignment">
          <select className="ctl" value={alignment} onChange={(e) => set({ align: e.target.value })} data-testid="align-select">
            <option value="">Any</option>
            <option value="bullish_full">Bullish full (10&gt;20&gt;30&gt;50&gt;100&gt;200)</option>
            <option value="bearish_full">Bearish full</option>
            <option value="bullish_partial">Bullish partial (20&gt;50&gt;200)</option>
            <option value="bearish_partial">Bearish partial</option>
            <option value="mixed">Mixed</option>
          </select>
        </Field>
        <Field label="Close vs EMA 200">
          <select className="ctl" value={ema200} onChange={(e) => set({ ema200: e.target.value })} data-testid="ema200-select">
            <option value="">Any</option>
            <option value="above">Above</option>
            <option value="below">Below</option>
          </select>
        </Field>
        <Field label="EMA crossover">
          <select className="ctl" value={cross} onChange={(e) => set({ cross: e.target.value })} data-testid="cross-select">
            <option value="">Any</option>
            <option value="ema_cross_20_50_bullish">EMA 20/50 bullish</option>
            <option value="ema_cross_20_50_bearish">EMA 20/50 bearish</option>
            <option value="ema_cross_50_200_bullish">EMA 50/200 bullish</option>
            <option value="ema_cross_50_200_bearish">EMA 50/200 bearish</option>
          </select>
        </Field>
        <Field label="RSI">
          <select className="ctl" value={rsiState} onChange={(e) => set({ rsi: e.target.value })} data-testid="rsi-select">
            <option value="">Any</option>
            <option value="overbought">&gt;= 70</option>
            <option value="oversold">&lt;= 30</option>
            <option value="neutral">Between</option>
          </select>
        </Field>
        <Field label="RSI divergence">
          <select className="ctl" value={div} onChange={(e) => set({ div: e.target.value })} data-testid="div-select">
            <option value="">Any</option>
            <option value="bullish">Bullish</option>
            <option value="bearish">Bearish</option>
          </select>
        </Field>
        <Field label="Divergence status">
          <select className="ctl" value={divStatus} onChange={(e) => set({ div_status: e.target.value })}>
            <option value="">Any</option>
            <option value="confirmed">Confirmed</option>
            <option value="developing">Developing</option>
            <option value="invalidated">Invalidated</option>
          </select>
        </Field>
        <Field label="Event age (bars)">
          <input className="ctl w-20" type="number" min={0} max={20} value={maxAge} onChange={(e) => set({ age: e.target.value })} />
        </Field>
        <span className="text-xs text-muted" data-testid="result-count">
          {res?.count ?? 0} symbols
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <DataTable
        rows={res?.items ?? []}
        columns={cols}
        testid="indicator-table"
        onRow={open}
        empty="No symbols match these filters on this scan."
        initialSort={{ key: "rsi", desc: true }}
      />
    </div>
  );
}
