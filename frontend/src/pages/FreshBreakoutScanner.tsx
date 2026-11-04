import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { advApi, type FreshBreakout, type FreshResult } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const TYPE_LABEL: Record<string, string> = { bullish: "10-day high breakout", bearish: "10-day low breakdown" };

export function FreshBreakoutScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const direction = params.get("dir") ?? "both";
  const mode = params.get("mode") ?? "close";
  const confirm = params.get("confirm") ?? "1";
  const status = params.get("status") ?? "confirmed";
  const maxAge = params.get("age") ?? "0";
  const firstOnly = params.get("first") === "1";
  const lookbackN = params.get("n") ?? "";
  const activeOnly = params.get("active") === "1";
  const minVol = params.get("min_vol") ?? "";
  const minAtr = params.get("min_atr") ?? "";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<FreshResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    advApi
      .freshBreakouts({ universe, asof: asof || null, direction, mode, confirm, status, max_age: maxAge, first_only: firstOnly || null, lookback_n: lookbackN || null, active_only: activeOnly || null, min_volume_ratio: minVol || null, min_atr: minAtr || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, direction, mode, confirm, status, maxAge, firstOnly, lookbackN, activeOnly, minVol, minAtr, fno]);
  useEffect(load, [load, tick]);

  const open = (r: FreshBreakout) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      show: ["levels", "breakouts"],
      context: {
        title: `${TYPE_LABEL[r.direction] ?? r.kind}${r.confirmation_status === "provisional touch" ? " (provisional touch)" : ""}`,
        lines: [
          { k: "Breakout candle", v: fmtBarTime(r.time, "1d") },
          { k: r.direction === "bullish" ? "Previous 10-day high" : "Previous 10-day low", v: fmtNum(r.level) },
          { k: "Close", v: fmtNum(r.close) },
          { k: "Breakout %", v: `${fmtNum(r.breakout_pct, 2)}%` },
          { k: "Strength (ATR)", v: fmtNum(r.distance_atr, 2) },
          { k: "Volume ratio (20D)", v: fmtNum(r.volume_ratio, 2) },
          { k: "First in lookback", v: r.first_in_lookback_n === null || r.first_in_lookback_n === undefined ? "insufficient history" : r.first_in_lookback_n ? "yes" : "no" },
          { k: "Episode", v: r.episode_state ?? "-" },
        ],
        levels: [{ price: r.level, label: `${r.direction === "bullish" ? "10D high" : "10D low"} ${fmtNum(r.level)}`, color: r.direction === "bullish" ? "#95EB45" : "#F2643C", t0: Math.floor(new Date(r.reference_start ?? r.time).getTime() / 1000) }],
        markers: [{ t: r.t, label: r.direction === "bullish" ? "Fresh BO" : "Fresh BD", color: r.direction === "bullish" ? "#95EB45" : "#F2643C", position: r.direction === "bullish" ? "below" : "above" }],
      },
    });

  const touch = mode === "touch";
  const cols: Column<FreshBreakout>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "type", label: "Breakout type", render: (r) => <span className={r.direction === "bullish" ? "text-validated" : "text-down"}>{TYPE_LABEL[r.direction] ?? r.kind}</span>, sort: (r) => r.direction },
    { key: "level", label: "Prev 10D high / low", align: "right", render: (r) => fmtNum(r.level), sort: (r) => r.level, title: "Max high / min low of the previous 10 completed daily candles (breakout candle excluded)" },
    ...(touch
      ? ([
          { key: "extreme", label: "Candle high / low", align: "right", render: (r) => fmtNum(r.extreme ?? null), sort: (r) => r.extreme ?? null },
          { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
          { key: "cs", label: "Close outcome", render: (r) => <span className="text-forming">{(r.close_status ?? "").replace("_", " ")} (provisional)</span> },
        ] as Column<FreshBreakout>[])
      : ([
          { key: "latest", label: "Current close", align: "right", render: (r) => fmtNum(r.latest_close), sort: (r) => r.latest_close, title: "Latest completed daily close" },
          { key: "pct", label: "Breakout %", align: "right", render: (r) => `${r.breakout_pct.toFixed(2)}%`, sort: (r) => Math.abs(r.breakout_pct), title: "(breakout close / level - 1) x 100" },
          { key: "atr", label: "Strength (ATR)", align: "right", render: (r) => fmtNum(r.distance_atr, 2), sort: (r) => (r.distance_atr === null ? null : Math.abs(r.distance_atr)), title: "(close - level) / ATR(14)" },
          { key: "vr", label: "Vol ratio", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio, title: "Breakout candle volume / mean of the previous 20 daily candles" },
        ] as Column<FreshBreakout>[])),
    { key: "time", label: "First crossing", render: (r) => fmtBarTime(r.time, "1d"), sort: (r) => r.t },
    { key: "age", label: "Sessions since", align: "right", render: (r) => r.age_bars, sort: (r) => r.age_bars },
    ...(touch
      ? []
      : ([
          { key: "first", label: `First in ${res?.lookback_n ?? 20}`, render: (r) => (r.first_in_lookback_n === null || r.first_in_lookback_n === undefined ? <span className="text-muted" title="History before the event is shorter than the lookback">n/a</span> : r.first_in_lookback_n ? <span className="text-validated">yes</span> : <span className="text-muted">no</span>), sort: (r) => (r.first_in_lookback_n ? 1 : 0), title: "First Breakout in Lookback: no close beyond its own 10-day level in the preceding N sessions (not 'first ever')" },
          { key: "conf", label: "Confirmation", render: (r) => <span className={r.confirmation_status === "confirmed" ? "text-ink" : "text-muted"}>{r.confirmation_status} ({confirm}-close)</span>, sort: (r) => r.confirmation_status ?? "" },
          { key: "episode", label: "Episode", render: (r) => <span className={r.episode_state === "active" ? "text-ink" : "text-muted"} title={r.episode_change_time ? `changed ${fmtBarTime(r.episode_change_time, "1d")}` : "still open"}>{r.episode_state}{r.trigger === "new_range" ? " (new range)" : ""}</span>, sort: (r) => r.episode_state },
          { key: "id", label: "Event ID", render: (r) => <span className="font-mono text-[10px] text-muted">{r.event_id}</span> },
        ] as Column<FreshBreakout>[])),
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="fresh-breakout-scanner">
      <TechScanHeader
        title="Fresh Breakouts"
        subtitle="Fresh crossings of the previous 10 completed daily candles' high / low (current candle excluded), one event per episode. Completed daily candles; not trade recommendations."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="Type">
          <select className="ctl" value={direction} onChange={(e) => set({ dir: e.target.value === "both" ? "" : e.target.value })} data-testid="dir-select">
            <option value="both">Breakouts + breakdowns</option>
            <option value="bullish">10-day high breakouts</option>
            <option value="bearish">10-day low breakdowns</option>
          </select>
        </Field>
        <Field label="Mode">
          <select className="ctl" value={mode} onChange={(e) => set({ mode: e.target.value === "close" ? "" : e.target.value })} data-testid="mode-select">
            <option value="close">Fresh daily close breakout</option>
            <option value="touch">Intraday touch (provisional)</option>
          </select>
        </Field>
        {!touch && (
          <>
            <Field label="Confirmation">
              <select className="ctl" value={confirm} onChange={(e) => set({ confirm: e.target.value === "1" ? "" : e.target.value })} data-testid="confirm-select">
                <option value="1">One completed close</option>
                <option value="2">Two consecutive closes</option>
              </select>
            </Field>
            {confirm === "2" && (
              <Field label="Status">
                <select className="ctl" value={status} onChange={(e) => set({ status: e.target.value === "confirmed" ? "" : e.target.value })} data-testid="status-select">
                  <option value="confirmed">Confirmed</option>
                  <option value="pending">Pending (first close only)</option>
                  <option value="failed">Failed (back inside)</option>
                  <option value="all">All</option>
                </select>
              </Field>
            )}
          </>
        )}
        <Field label="Sessions since breakout">
          <select className="ctl" value={maxAge} onChange={(e) => set({ age: e.target.value === "0" ? "" : e.target.value })} data-testid="age-select">
            <option value="0">Today (fresh crossing)</option>
            {[2, 5, 10, 20, 60].map((n) => (
              <option key={n} value={n}>
                Within {n} sessions
              </option>
            ))}
          </select>
        </Field>
        {!touch && (
          <>
            <label className="flex items-center gap-1 text-xs text-muted" title="No close beyond its own 10-day level in the preceding N sessions">
              <input type="checkbox" checked={firstOnly} onChange={(e) => set({ first: e.target.checked ? "1" : "" })} data-testid="first-only" /> First Breakout in Lookback
            </label>
            <Field label="Lookback N">
              <input className="ctl w-16" type="number" min={1} max={250} placeholder={String(res?.lookback_n ?? 20)} value={lookbackN} onChange={(e) => set({ n: e.target.value })} data-testid="lookback-n" />
            </Field>
            <label className="flex items-center gap-1 text-xs text-muted" title="Hide events whose episode reset (closed back inside the prior range)">
              <input type="checkbox" checked={activeOnly} onChange={(e) => set({ active: e.target.checked ? "1" : "" })} data-testid="active-only" /> Active episodes
            </label>
            <Field label="Min vol ratio">
              <input className="ctl w-20" type="number" min={0} step={0.1} value={minVol} onChange={(e) => set({ min_vol: e.target.value })} data-testid="min-vol" />
            </Field>
            <Field label="Min ATR">
              <input className="ctl w-20" type="number" min={0} step={0.1} value={minAtr} onChange={(e) => set({ min_atr: e.target.value })} data-testid="min-atr" />
            </Field>
          </>
        )}
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
      <DataTable rows={res?.items ?? []} columns={cols} testid="fresh-table" onRow={open} empty="No fresh breakouts match these filters on the latest daily scan." initialSort={{ key: "atr", desc: true }} />
    </div>
  );
}
