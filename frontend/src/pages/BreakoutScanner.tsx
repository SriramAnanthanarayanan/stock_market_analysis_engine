import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { SymbolButton, useOpenChart } from "../lib/openChart";
import { api } from "../api/client";
import type { BreakoutSignal, TechScanResult, Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum } from "../lib/format";

export function BreakoutScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const kind = params.get("kind") ?? "";
  const confirm = params.get("confirm") ?? "1";
  const status = params.get("status") ?? "confirmed";
  const minVol = params.get("min_vol") ?? "";
  const minAtr = params.get("min_atr") ?? "";
  const maxAge = params.get("age") ?? "0";
  const firstRun = params.get("first") === "1";
  const [res, setRes] = useState<TechScanResult<BreakoutSignal> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    api
      .scanBreakouts({ universe, asof: asof || null, kind: kind || null, confirm, status, min_volume_ratio: minVol || null, min_atr: minAtr || null, max_age: maxAge, first_of_run: firstRun || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, kind, confirm, status, minVol, minAtr, maxAge, firstRun]);
  useEffect(load, [load, tick]);

  const open = (r: BreakoutSignal) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      show: ["levels", "breakouts"],
      context: {
        title: r.direction === "bullish" ? `${r.lookback_days}-day high breakout` : `${r.lookback_days}-day low breakdown`,
        lines: [
          { k: "Breakout candle", v: fmtBarTime(r.time, "1d") },
          { k: "Level", v: fmtNum(r.level) },
          { k: "Close", v: fmtNum(r.close) },
          { k: "Strength (ATR)", v: fmtNum(r.distance_atr, 2) },
          { k: "Status", v: `${r.status} (${r.confirm_closes}-close)` },
        ],
        levels: [{ price: r.level, label: `${r.lookback_days}D level ${fmtNum(r.level)}`, color: r.direction === "bullish" ? "#95EB45" : "#F2643C" }],
      },
    });
  const cols: Column<BreakoutSignal>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    {
      key: "kind",
      label: "Type",
      render: (r) => <span className={r.direction === "bullish" ? "text-validated" : "text-down"}>{r.direction === "bullish" ? `${r.lookback_days}-day high breakout` : `${r.lookback_days}-day low breakdown`}</span>,
      sort: (r) => r.kind,
    },
    { key: "level", label: "Previous high / low", align: "right", render: (r) => fmtNum(r.level), sort: (r) => r.level, title: "Max high / min low of the previous N completed daily candles (breakout candle excluded)" },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "pct", label: "Breakout %", align: "right", render: (r) => `${r.breakout_pct.toFixed(2)}%`, sort: (r) => Math.abs(r.breakout_pct) },
    { key: "atr", label: "ATR multiple", align: "right", render: (r) => fmtNum(r.distance_atr, 2), sort: (r) => (r.distance_atr === null ? null : Math.abs(r.distance_atr)), title: "(close - level) / ATR(14)" },
    { key: "ratr", label: "Range / ATR", align: "right", render: (r) => fmtNum(r.range_atr, 2), sort: (r) => r.range_atr },
    { key: "vr", label: "Vol ratio (20D)", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio },
    { key: "time", label: "Breakout candle", render: (r) => fmtBarTime(r.time, "1d"), sort: (r) => r.t },
    { key: "status", label: "Status", render: (r) => <span className={r.status === "confirmed" ? "text-ink" : "text-muted"}>{r.status} ({r.confirm_closes}-close)</span>, sort: (r) => r.status },
    { key: "prior", label: "Prior S/R zone", render: (r) => (r.prior_level ? `${fmtNum(r.prior_level.low)}-${fmtNum(r.prior_level.high)} ${r.prior_level.role}` : "-") },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="breakout-scanner">
      <TechScanHeader
        title="Breakout Scanner"
        subtitle="Close beyond the previous 10 completed daily candles' high / low by 0.1 x ATR(14). Always daily candles. Rule-based levels, not trade recommendations."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="Type">
          <select className="ctl" value={kind} onChange={(e) => set({ kind: e.target.value })} data-testid="kind-select">
            <option value="">Breakouts + breakdowns</option>
            <option value="breakout_high_10d">10-day high breakout</option>
            <option value="breakdown_low_10d">10-day low breakdown</option>
          </select>
        </Field>
        <Field label="Confirmation">
          <select className="ctl" value={confirm} onChange={(e) => set({ confirm: e.target.value })} data-testid="confirm-select">
            <option value="1">One completed close</option>
            <option value="2">Two consecutive closes</option>
          </select>
        </Field>
        <Field label="Status">
          <select className="ctl" value={status} onChange={(e) => set({ status: e.target.value })} data-testid="status-select">
            <option value="confirmed">Confirmed</option>
            <option value="pending">Pending (2-close: first close only)</option>
            <option value="failed">Failed (false breakout)</option>
            <option value="all">All</option>
          </select>
        </Field>
        <Field label="Min volume ratio">
          <input className="ctl w-24" type="number" min={0} step={0.1} value={minVol} onChange={(e) => set({ min_vol: e.target.value })} data-testid="min-vol" />
        </Field>
        <Field label="Min strength (x ATR)">
          <input className="ctl w-24" type="number" min={0} step={0.1} value={minAtr} onChange={(e) => set({ min_atr: e.target.value })} data-testid="min-atr" />
        </Field>
        <Field label="Age">
          <select className="ctl" value={maxAge} onChange={(e) => set({ age: e.target.value })} data-testid="age-select">
            <option value="0">Latest daily candle</option>
            {[1, 2, 4].map((n) => (
              <option key={n} value={n}>
                Within last {n + 1} days
              </option>
            ))}
          </select>
        </Field>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={firstRun} onChange={(e) => set({ first: e.target.checked ? "1" : "" })} data-testid="first-run" /> first close of a run only
        </label>
        <span className="text-xs text-muted" data-testid="result-count">
          {res?.count ?? 0} results
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <DataTable
        rows={res?.items ?? []}
        columns={cols}
        testid="breakout-table"
        onRow={open}
        empty="No breakouts match these filters on the latest daily scan."
        initialSort={{ key: "atr", desc: true }}
      />
    </div>
  );
}
