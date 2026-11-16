import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { darvasApi, type DarvasResult, type DarvasRow } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge, KV, Panel } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const PRESETS: { key: string; label: string; state: string }[] = [
  { key: "", label: "Active boxes", state: "" },
  { key: "bo", label: "Breakouts", state: "breakout" },
  { key: "near", label: "Near top", state: "near_top" },
  { key: "box", label: "In box", state: "in_box,near_top" },
  { key: "forming", label: "Forming", state: "forming_top,forming_bottom" },
  { key: "down", label: "Breakdowns", state: "breakdown" },
];
const STATE_CLS: Record<string, string> = { breakout: "text-up", near_top: "text-accent", in_box: "text-validated", forming_bottom: "text-forming", forming_top: "text-developing", breakdown: "text-invalid" };

/** Darvas box scanner (Nicolas Darvas, 1960): boxes built by the backend detector on
 *  completed daily candles; the page only lists and draws them. */
export function DarvasScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const preset = params.get("preset") ?? "";
  const minTests = params.get("tests") ?? "";
  const volOk = params.get("vol") === "1";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<DarvasResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<DarvasRow | null>(null);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const state = PRESETS.find((p) => p.key === preset)?.state ?? "";
  const load = useCallback(() => {
    darvasApi
      .scan({ universe, asof: asof || null, state: state || null, min_tests: minTests || null, volume_confirmed: volOk || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, state, minTests, volOk, fno]);
  useEffect(load, [load, tick]);

  const open = (r: DarvasRow) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      context: {
        title: `Darvas box - ${r.state_label}`,
        lines: [
          { k: "Box", v: `${fmtNum(r.box_top)} / ${r.box_bottom === null ? "bottom not confirmed" : fmtNum(r.box_bottom)}${r.box_depth_pct === null ? "" : ` · ${r.box_depth_pct.toFixed(1)}% deep`}` },
          { k: "Since", v: `${fmtBarTime(r.box_start_time, "1d")} (${r.box_sessions} sessions)` },
          { k: "Top tests / boxes stacked", v: `${r.top_tests} · ${r.boxes_stacked}` },
          ...(r.event ? [{ k: r.state === "breakdown" ? "Breakdown" : "Breakout", v: `${fmtBarTime(r.event.time, "1d")} · close ${fmtNum(r.event.close)} · volume ${fmtNum(r.event.volume_ratio, 2)}x${r.event.volume_ok ? " (confirmed)" : ""}` }] : []),
        ],
        levels: [
          { price: r.box_top, label: `box top ${fmtNum(r.box_top)}`, color: "#95EB45", t0: r.box_start_t },
          ...(r.box_bottom !== null ? [{ price: r.box_bottom, label: `box bottom ${fmtNum(r.box_bottom)}`, color: "#F2643C", t0: r.box_start_t }] : []),
        ],
        markers: [
          { t: r.box_start_t, label: "box top", color: "#95EB45", position: "above" as const },
          ...(r.event ? [{ t: r.event.t, label: r.state === "breakdown" ? "Darvas breakdown" : "Darvas breakout", color: r.state === "breakdown" ? "#F2643C" : "#95EB45", position: r.state === "breakdown" ? ("above" as const) : ("below" as const) }] : []),
        ],
        focusT: r.box_start_t,
      },
    });

  const cols: Column<DarvasRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="inline-block max-w-[180px] truncate align-bottom text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "state", label: "State", render: (r) => <span className={`font-medium ${r.back_below_top ? "text-forming" : STATE_CLS[r.state] ?? ""}`} data-darvas-state={r.state} title={r.back_below_top ? "The close is back at or below the broken box top: the breakout has not held (Darvas's stop stays at the old box bottom)" : undefined}>{r.state_label}</span>, sort: (r) => ({ breakout: 0, near_top: 1, in_box: 2, forming_bottom: 3, forming_top: 4, breakdown: 5 })[r.state] ?? 9 },
    { key: "top", label: "Box top", align: "right", render: (r) => fmtNum(r.box_top), sort: (r) => r.box_top },
    { key: "bottom", label: "Box bottom", align: "right", render: (r) => (r.box_bottom === null ? <span className="text-muted">-</span> : fmtNum(r.box_bottom)), sort: (r) => r.box_bottom },
    { key: "depth", label: "Depth", align: "right", render: (r) => (r.box_depth_pct === null ? "-" : `${r.box_depth_pct.toFixed(1)}%`), sort: (r) => r.box_depth_pct },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "dist", label: "To top", align: "right", render: (r) => `${r.distance_to_top_pct.toFixed(2)}%`, sort: (r) => r.distance_to_top_pct, title: "(box top - close) / box top; negative = above the top" },
    { key: "sessions", label: "Box days", align: "right", render: (r) => r.box_sessions, sort: (r) => r.box_sessions },
    { key: "tests", label: "Top tests", align: "right", render: (r) => <span className={r.top_tests >= 3 ? "font-semibold text-validated" : ""}>{r.top_tests}</span>, sort: (r) => r.top_tests, title: "Separate tests of the box top (3+ highlighted)" },
    { key: "stack", label: "Boxes", align: "right", render: (r) => r.boxes_stacked, sort: (r) => r.boxes_stacked, title: "Boxes broken upward in the current advance (boxes stack)" },
    { key: "event", label: "Event", render: (r) => (r.event ? `${fmtBarTime(r.event.time, "1d")} (${r.event.sessions_ago}d)` : "-"), sort: (r) => r.event?.t ?? null },
    { key: "vol", label: "Vol x", align: "right", render: (r) => (r.event ? <span className={r.event.volume_ok ? "text-up" : "text-muted"}>{fmtNum(r.event.volume_ratio, 2)}</span> : "-"), sort: (r) => r.event?.volume_ratio ?? null, title: "Breakout volume / mean of the previous 50 sessions (volume-confirmed at 1.5x)" },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="darvas-scanner">
      <TechScanHeader
        title="Darvas Box"
        subtitle="Nicolas Darvas (1960): a new 52-week high the next 3 sessions fail to exceed is the box top; the lowest low after it that the next 3 sessions do not undercut is the bottom. Breakout = close above the top (boxes stack in an advance); breakdown = close below the bottom. Completed daily candles."
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
              <button key={p.key} className={`chip h-8 ${preset === p.key ? "chip-on" : ""}`} onClick={() => set({ preset: p.key })} data-testid={`darvas-preset-${p.key || "active"}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <Field label="Min top tests">
          <select className="ctl" value={minTests} onChange={(e) => set({ tests: e.target.value })} data-testid="darvas-tests">
            <option value="">Any</option>
            <option value="2">2+</option>
            <option value="3">3+</option>
          </select>
        </Field>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={volOk} onChange={(e) => set({ vol: e.target.checked ? "1" : "" })} data-testid="darvas-vol" /> Volume-confirmed breakouts
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
      <div className={`grid gap-3 ${selected ? "xl:grid-cols-[minmax(0,1fr)_360px]" : ""}`}>
        <DataTable rows={res?.items ?? []} columns={cols} testid="darvas-table" onRow={(r) => setSelected(r)} empty="No Darvas box matches these filters on the latest daily scan." initialSort={{ key: "state", desc: false }} />
        {selected && (
          <Panel title={`${selected.symbol}: ${selected.state_label}`} right={<button className="btn h-6 text-[11px]" onClick={() => setSelected(null)}>Close</button>}>
            <div className="text-xs" data-testid="darvas-panel">
              <KV k="Box top / bottom" v={`${fmtNum(selected.box_top)} / ${selected.box_bottom === null ? "-" : fmtNum(selected.box_bottom)}`} />
              <KV k="Depth / days" v={`${selected.box_depth_pct === null ? "-" : `${selected.box_depth_pct.toFixed(1)}%`} / ${selected.box_sessions}`} />
              <KV k="Top tests (score)" v={`${selected.top_tests} (${selected.tests_score.toFixed(1)})`} />
              <KV k="Lows rising" v={selected.lows_rising === null ? "-" : selected.lows_rising ? "yes" : "no"} />
              <KV k="Boxes stacked" v={String(selected.boxes_stacked)} />
              <ul className="mt-2 space-y-0.5 text-[11px] text-muted">
                {selected.explanation.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
              <button className="btn mt-2 h-7" onClick={() => open(selected)} data-testid="darvas-open-chart">
                Open chart with the box
              </button>
              {selected.partial_history && <p className="mt-1 text-[10px] text-forming">Less than 52 weeks of history: the new-high check used the available candles.</p>}
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
