import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { StageList, StageRow } from "../api/types";
import { ErrorNote, FnoBadge, Panel } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { GateList, STAGE_COLOR } from "../components/StagePanel";
import { Field } from "../components/TechScanHeader";
import { fmtDate, fmtNum, humanize } from "../lib/format";
import { UNIVERSES } from "../lib/status";

const yesNo = (v: boolean | null | undefined) => (v === null || v === undefined ? <span className="text-muted">n/a</span> : v ? <span className="text-validated">yes</span> : <span className="text-muted">no</span>);

export function StageAnalysis() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const instrument = (params.get("inst") as "stock" | "index") || "stock";
  const universe = params.get("u") || "nifty500";
  const stage = params.get("stage") ?? "";
  const rs = params.get("rs") ?? "";
  const asof = params.get("asof") ?? "";
  const setupOnly = params.get("setup") === "1";
  const [data, setData] = useState<StageList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<StageRow | null>(null);
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    setLoading(true);
    api
      .stage({ instrument, universe, stage: stage || null, rs: rs || null, asof: asof || null, setup: setupOnly ? "true" : null })
      .then((r) => (setData(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [instrument, universe, stage, rs, asof, setupOnly]);
  const open = (r: StageRow) => navigate(r.instrument === "index" ? `/index/${encodeURIComponent(r.symbol)}?tf=1w&show=stage,rs` : `/explorer/${r.symbol}?tf=1w&show=stage,rs${asof ? `&asof=${asof}` : ""}`);
  const cols: Column<StageRow>[] = [
    { key: "rank", label: "#", align: "right", render: (r) => r.rank, sort: (r) => r.rank },
    { key: "setup", label: "Setup", render: (r) => (r.metrics.setup_met ? <span className="font-semibold text-validated" title="All entry-setup checks pass">&#10003; entry</span> : <span className="text-muted" title={(r.metrics.setup_failed ?? []).join(", ")}>{(r.metrics.setup_checks ?? 6) - (r.metrics.setup_failed?.length ?? 6)}/{r.metrics.setup_checks ?? 6}</span>), sort: (r) => (r.metrics.setup_met ? 0 : 1) * 10 + (r.metrics.setup_failed?.length ?? 7) },
    { key: "baselen", label: "Base wks", align: "right", render: (r) => r.metrics.base_duration_weeks ?? "-", sort: (r) => r.metrics.base_duration_weeks ?? null },
    { key: "symbol", label: instrument === "index" ? "Index" : "Symbol", render: (r) => <span className="font-medium text-ink">{r.symbol}</span>, sort: (r) => r.symbol },
    { key: "company", label: instrument === "index" ? "Name" : "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    ...(instrument === "stock" ? [{ key: "fno", label: "F&O", render: (r: StageRow) => (r.fno_status ? <FnoBadge value={r.fno_status} /> : null) }] : []),
    { key: "price", label: "Price", align: "right", render: (r) => fmtNum(r.metrics.close), sort: (r) => r.metrics.close ?? null },
    { key: "stage", label: "Stage", render: (r) => <span className={STAGE_COLOR[r.stage] ?? ""}>{r.stage_label}</span>, sort: (r) => r.rank },
    { key: "since", label: "Since", render: (r) => <span className="text-muted">{fmtDate(r.stage_since)}</span>, sort: (r) => r.stage_since ?? "" },
    { key: "ema", label: "30W EMA", align: "right", render: (r) => fmtNum(r.metrics.ema30), sort: (r) => r.metrics.ema30 ?? null },
    { key: "emadir", label: "EMA dir", render: (r) => r.metrics.ema30_direction ?? "-" },
    { key: "range", label: "Base range %", align: "right", render: (r) => fmtNum(r.metrics.base_range_pct, 1), sort: (r) => r.metrics.base_range_pct ?? null },
    { key: "res", label: "Base resistance", align: "right", render: (r) => fmtNum(r.metrics.base_high), sort: (r) => r.metrics.base_high ?? null },
    { key: "dist", label: "To breakout %", align: "right", render: (r) => fmtNum(r.metrics.distance_to_breakout_pct, 2), sort: (r) => r.metrics.distance_to_breakout_pct ?? null },
    { key: "rs", label: "RS 52W", align: "right", render: (r) => <span className={(r.metrics.rs ?? 0) > 0 ? "text-validated" : "text-down"}>{fmtNum(r.metrics.rs, 3)}</span>, sort: (r) => r.metrics.rs ?? null },
    { key: "rsdir", label: "RS 5W", render: (r) => r.metrics.rs_trend ?? "-" },
    { key: "rsma", label: "RS 50 SMA", render: (r) => r.metrics.rs_sma_direction ?? "-" },
    { key: "pc", label: "Price conf.", render: (r) => r.metrics.price_confirmation ?? "-" },
    { key: "vc", label: "Vol contraction", render: (r) => yesNo(r.metrics.volume_contraction) },
    { key: "ve", label: "Vol expansion", render: (r) => yesNo(r.metrics.volume_expansion) },
  ];
  const sm = data?.summary ?? {};
  const card = (key: string, label: string) => (
    <button key={key} className={`panel flex-1 px-3 py-2 text-left ${stage === key ? "ring-1 ring-accent" : ""}`} onClick={() => set({ stage: stage === key ? "" : key })} data-testid={`stage-card-${key}`}>
      <div className={`text-xl font-semibold ${STAGE_COLOR[key] ?? "text-ink"}`}>{sm[key] ?? 0}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </button>
  );
  const assumptions = (data?.config.parameters ?? []).filter((p) => p.basis === "assumption");
  return (
    <div className="mx-auto flex max-w-[1700px] flex-col gap-3 px-4 py-3" data-testid="stage-page">
      <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5">
        <div className="mr-2">
          <h1 className="text-base font-semibold text-ink">Stage Analysis</h1>
          <div className="text-[11px] text-muted">Your stage_analysis.txt rules with the rs_line.txt RS Line (weekly, RS 52 vs NIFTY 50, 30W EMA, 52-week base). Entry setup = major decline, long base (52+ weeks), above 30W EMA, RS &gt; 0, at/near the base high, last 4 weeks' volume &gt;= 1.5x the sideways base - all at once. Completed weekly candles only.</div>
        </div>
        <Field label="Instruments">
          <div className="flex gap-1">
            {(["stock", "index"] as const).map((x) => (
              <button key={x} className={`chip h-8 ${instrument === x ? "chip-on" : ""}`} onClick={() => set({ inst: x, stage: "" })} data-testid={`stage-inst-${x}`}>
                {x === "stock" ? "Stocks" : "Indices"}
              </button>
            ))}
          </div>
        </Field>
        {instrument === "stock" && (
          <Field label="Universe">
            <select className="ctl" value={universe} onChange={(e) => set({ u: e.target.value })} data-testid="stage-universe">
              {UNIVERSES.map((u) => (
                <option key={u.value} value={u.value}>
                  {u.label}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="RS condition">
          <select className="ctl" value={rs} onChange={(e) => set({ rs: e.target.value })} data-testid="stage-rs">
            <option value="">Any</option>
            <option value="above_zero">RS above zero</option>
            <option value="below_zero">RS below zero</option>
            <option value="improving">RS improving (5W)</option>
            <option value="deteriorating">RS deteriorating (5W)</option>
            <option value="ma_rising">RS 50 SMA rising</option>
            <option value="ma_falling">RS 50 SMA falling</option>
            <option value="crossed_up">RS crossed above zero (latest week)</option>
          </select>
        </Field>
        <Field label="As of">
          <input type="date" className="ctl" max={today} value={asof} onChange={(e) => set({ asof: e.target.value })} data-testid="stage-asof" />
        </Field>
        <div className="ml-auto text-right text-[11px] text-muted">
          {data?.scan ? `Weekly scan ${data.scan.scan_id} · ${fmtDate(data.scan.finished_at)}` : instrument === "index" ? "Indices: official NSE weekly series" : ""}
          <div>Stage config v{data?.config.version} ({data?.config.config_hash})</div>
        </div>
      </div>
      <div className="flex flex-wrap gap-2" data-testid="stage-summary">
        <button className={`panel flex-1 px-3 py-2 text-left ${setupOnly ? "ring-1 ring-validated" : ""}`} onClick={() => set({ setup: setupOnly ? "" : "1" })} data-testid="stage-card-setup">
          <div className="text-xl font-semibold text-validated">{sm.entry_setup ?? 0}</div>
          <div className="text-[11px] text-muted">Entry setups (all checks)</div>
        </button>
        {card("stage1", "Stage 1 - Watchlist")}
        {card("early_stage2", "Early Stage 2 - Candidates")}
        {card("stage2_confirmed", "Stage 2 - Confirmed")}
        {card("other", "Other / Unqualified")}
        {card("insufficient_history", "Insufficient history")}
      </div>
      {error && <ErrorNote message={error} />}
      {data?.note && <div className="text-[11px] text-muted">{data.note}</div>}
      {loading && <div className="text-xs text-muted">Loading...</div>}
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <DataTable rows={data?.items ?? []} columns={cols} testid="stage-table" onRow={(r) => setDetail(r)} empty="No instrument matches these filters." initialSort={{ key: "rank", desc: false }} />
        <div className="flex flex-col gap-3">
          {detail ? (
            <Panel title={`${detail.symbol}: ${detail.stage_label}`}>
              <div className="text-xs" data-testid="stage-detail">
                <ul className="mb-2 space-y-0.5 text-[11px] text-muted">
                  {detail.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
                <GateList gates={detail.gates} />
                <button className="btn mt-2 h-7" onClick={() => open(detail)} data-testid="stage-open-chart">
                  Open weekly chart with Stage overlays
                </button>
              </div>
            </Panel>
          ) : (
            <Panel title="Details">
              <div className="text-xs text-muted">Select a row to see why it received its classification.</div>
            </Panel>
          )}
          <Panel title="Assumptions (not in the source files)">
            <ul className="space-y-1 text-[11px]" data-testid="stage-assumptions">
              {assumptions.map((p) => (
                <li key={p.name}>
                  <span className="text-forming">{humanize(p.name)} = {JSON.stringify(p.value)}</span>
                  <div className="text-muted">{p.description}</div>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
