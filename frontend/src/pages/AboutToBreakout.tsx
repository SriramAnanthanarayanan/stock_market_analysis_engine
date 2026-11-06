import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { advApi, type SetupResult, type SetupRow, type SetupState } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge, QualityCell } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { StageChip } from "../components/VcpPanel";
import { fmtBarTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const STATE_CLS: Record<SetupState, string> = {
  approaching: "text-accent",
  very_near: "text-validated",
  touched: "text-forming",
  breakout: "text-up",
  confirmed_breakout: "text-up",
  rejected: "text-down",
  expired: "text-invalid",
};
const PRESETS: { key: string; label: string; state: string }[] = [
  { key: "", label: "Before the breakout", state: "approaching,very_near,touched" },
  { key: "near", label: "Very near / touched", state: "very_near,touched" },
  { key: "bo", label: "Breakout / confirmed", state: "breakout,confirmed_breakout" },
  { key: "all", label: "All live states", state: "approaching,very_near,touched,breakout,confirmed_breakout" },
  { key: "failed", label: "Rejected / expired", state: "rejected,expired" },
];

/** 10-day level setups: stocks approaching their previous 10-day high before the
 *  breakout (and what happened to recent ones). Backend states; nothing computed here. */
export function AboutToBreakout() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const preset = params.get("preset") ?? "";
  const direction = params.get("dir") ?? "bullish";
  const minQ = params.get("minq") ?? "";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<SetupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const state = PRESETS.find((p) => p.key === preset)?.state ?? PRESETS[0].state;
  const load = useCallback(() => {
    advApi
      .breakoutSetups({ universe, asof: asof || null, state, direction, min_quality: minQ || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, state, direction, minQ, fno]);
  useEffect(load, [load, tick]);

  const open = (r: SetupRow) => {
    const bull = r.direction === "bullish";
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      show: ["levels", "breakouts"],
      context: {
        title: `${r.state_label} - ${r.level_kind} ${fmtNum(r.level)}`,
        lines: [
          { k: r.level_kind, v: `${fmtNum(r.level)} (set ${fmtBarTime(r.level_time, "1d")})` },
          { k: "Close", v: fmtNum(r.close) },
          { k: "Distance", v: `${fmtNum(r.distance_pct, 2)}% · ${fmtNum(r.distance_atr, 2)} ATR` },
          { k: "EMA 20 / 50", v: `${fmtNum(r.ema20)} / ${fmtNum(r.ema50)}` },
          { k: "Volume / 20-day avg", v: fmtNum(r.volume_ratio, 2) },
          { k: "RS Line (weekly)", v: r.rs === null ? "-" : r.rs.toFixed(3) },
          { k: "Stage (weekly)", v: r.stage?.stage_label ?? "no weekly scan" },
          { k: "Setup quality (not a probability)", v: `${r.setup_quality.toFixed(0)} / 100` },
        ],
        levels: [{ price: r.level, label: `${r.level_kind} ${fmtNum(r.level)}`, color: bull ? "#95EB45" : "#F2643C", t0: Math.floor(new Date(r.reference_start).getTime() / 1000) }],
        markers: r.event_time ? [{ t: Math.floor(new Date(r.event_time).getTime() / 1000), label: r.state === "rejected" ? "Rejected BO" : "10D BO", color: r.state === "rejected" ? "#8B9BA5" : "#95EB45", position: bull ? "below" : "above" }] : [],
      },
    });
  };

  const cols: Column<SetupRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="inline-block max-w-[170px] truncate align-bottom text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "state", label: "State", render: (r) => <span className={`font-medium ${STATE_CLS[r.state]}`} data-state={r.state}>{r.state_label}</span>, sort: (r) => r.state },
    { key: "level", label: "10D high", align: "right", render: (r) => fmtNum(r.level), sort: (r) => r.level, title: "Highest high of the previous 10 completed daily candles (current excluded)" },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "dpct", label: "Distance %", align: "right", render: (r) => `${fmtNum(r.distance_pct, 2)}%`, sort: (r) => r.distance_pct, title: "(10D high - close) / 10D high x 100" },
    { key: "datr", label: "Distance ATR", align: "right", render: (r) => fmtNum(r.distance_atr, 2), sort: (r) => r.distance_atr, title: "(10D high - close) / ATR(14)" },
    { key: "ema", label: "EMA20 / 50", align: "right", render: (r) => <span className={r.ema20_vs_ema50 === "above" ? "text-validated" : "text-muted"}>{fmtNum(r.ema20)} / {fmtNum(r.ema50)}</span>, sort: (r) => r.ema20 },
    { key: "vol", label: "Vol ratio", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio, title: "Volume / mean of the previous 20 sessions" },
    { key: "rs", label: "RS", align: "right", render: (r) => (r.rs === null ? "-" : <span className={r.rs > 0 ? "text-validated" : "text-down"}>{r.rs >= 0 ? "+" : ""}{r.rs.toFixed(3)}</span>), sort: (r) => r.rs, title: "RS Line of the latest weekly scan (rs_line.txt)" },
    { key: "stage", label: "Stage", render: (r) => <StageChip st={r.stage} />, sort: (r) => r.stage?.stage ?? "" },
    { key: "q", label: "Setup quality", render: (r) => <QualityCell score={r.setup_quality} label="" />, sort: (r) => r.setup_quality, title: "Explainable screening score (proximity, trend, tightness, volume, RS, stage) - not a probability" },
    { key: "days", label: "Days in setup", align: "right", render: (r) => r.sessions_in_setup, sort: (r) => r.sessions_in_setup },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="about-to-breakout">
      <TechScanHeader
        title="About to Break Out (10-day high)"
        subtitle="Stocks approaching their previous 10-day high before the breakout, and the outcome of recent ones (touched, breakout, confirmed, rejected, expired). Completed daily candles; the existing 10-day breakout rule is unchanged."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <div className="flex flex-col gap-1">
          <span className="label">State</span>
          <div className="flex flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button key={p.key} className={`chip h-8 ${preset === p.key ? "chip-on" : ""}`} onClick={() => set({ preset: p.key })} data-testid={`setup-preset-${p.key || "pre"}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <Field label="Direction">
          <select className="ctl" value={direction} onChange={(e) => set({ dir: e.target.value === "bullish" ? "" : e.target.value })} data-testid="setup-direction">
            <option value="bullish">Toward the 10D high</option>
            <option value="bearish">Toward the 10D low</option>
            <option value="both">Both</option>
          </select>
        </Field>
        <Field label="Min quality">
          <input className="ctl w-20" type="number" min={0} max={100} step={5} value={minQ} onChange={(e) => set({ minq: e.target.value })} data-testid="setup-min-quality" />
        </Field>
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
      {res?.thresholds && (
        <div className="text-[11px] text-muted" data-testid="setup-thresholds">
          Approaching: within {res.thresholds.approach_pct}% {res.thresholds.rule === "both" ? "and" : "or"} {res.thresholds.approach_atr} ATR · Very near: {res.thresholds.very_near_pct}% / {res.thresholds.very_near_atr} ATR · Expired after {res.thresholds.expiry_sessions} sessions
        </div>
      )}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <DataTable rows={res?.items ?? []} columns={cols} testid="setup-table" onRow={open} empty="No stock in these states on the latest daily scan." />
    </div>
  );
}
