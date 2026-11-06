import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { confluenceApi, type ConfluenceResult, type ConfluenceRow, type ConfluenceScreen } from "../api/advanced";
import type { Timeframe, Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { BandChip, ConfluenceBreakdown, ScoreCell } from "../components/ConfluencePanel";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtDateTime, fmtNum, humanize } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const TFS: { value: Timeframe; label: string }[] = [
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
];
const BANDS = ["Very High Confluence", "High Confluence", "Moderate Confluence", "Low Confluence"];
const STAGE_OPTS = [
  { v: "", label: "All stages" },
  { v: "stage2_confirmed,early_stage2", label: "Stage 2 (Early + Confirmed)" },
  { v: "stage2_confirmed", label: "Stage 2 Confirmed" },
  { v: "early_stage2", label: "Early Stage 2" },
  { v: "stage1", label: "Stage 1" },
  { v: "other", label: "Other / Unqualified" },
];
const VCP_OPTS = [
  { v: "", label: "Any" },
  { v: "near_pivot,final_contraction,forming,breakout", label: "Any active VCP" },
  { v: "near_pivot", label: "Near pivot" },
  { v: "final_contraction", label: "Final Contraction" },
  { v: "breakout", label: "Breakout" },
  { v: "forming", label: "Forming" },
];

const ago = (age: number, tf: string) => (age === 0 ? "latest" : `${age}${tf === "1w" ? "w" : "d"} ago`);

/** Stocks supported by several independent kinds of evidence (docs/confluence-engine.md).
 *  Everything comes from the stored scanner evidence; nothing is detected here. */
export function ConfluenceDashboard() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const tf = (params.get("tf") as "1d" | "1w") || "1d";
  const asof = params.get("asof") ?? "";
  const screen = params.get("screen") ?? "";
  const dir = (params.get("dir") as "bullish" | "bearish") || "bullish";
  const minScore = params.get("min") ?? "";
  const band = params.get("band") ?? "";
  const unranked = params.get("unranked") === "1";
  const fno = params.get("fno") ?? "";
  const stage = params.get("stage") ?? "";
  const vcp = params.get("vcp") ?? "";
  const fb = params.get("fb") === "1";
  const vs = params.get("vs") === "1";
  const q = params.get("q") ?? "";
  const [res, setRes] = useState<ConfluenceResult | null>(null);
  const [screens, setScreens] = useState<ConfluenceScreen[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<ConfluenceRow | null>(null);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };

  useEffect(() => {
    confluenceApi
      .screens()
      .then((r) => setScreens(r.items))
      .catch(() => setScreens([]));
  }, []);
  const load = useCallback(() => {
    confluenceApi
      .list({
        universe,
        timeframe: tf,
        asof: asof || null,
        direction: dir,
        screen: screen || null,
        min_score: minScore || null,
        band: band || null,
        include_unranked: unranked || null,
        fno: fno || null,
        stage: stage || null,
        vcp: vcp || null,
        fresh_breakout: fb || null,
        volume_shock: vs || null,
      })
      .then((r) => {
        setRes(r);
        setError(null);
        setSelected((cur) => (cur ? r.items.find((x) => x.symbol === cur.symbol && x.direction === cur.direction) ?? null : null));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, tf, asof, dir, screen, minScore, band, unranked, fno, stage, vcp, fb, vs]);
  useEffect(load, [load, tick]);

  const recompute = () => {
    setBusy(true);
    confluenceApi
      .refresh({ universe, timeframe: tf, asof: asof || null })
      .then(() => setTick((x) => x + 1))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  const needle = q.trim().toUpperCase();
  const rows = useMemo(() => (res?.items ?? []).filter((r) => !needle || r.symbol.includes(needle) || (r.company ?? "").toUpperCase().includes(needle)), [res, needle]);
  const activeDir = res?.direction ?? dir;
  const screenDef = screens.find((s) => s.key === screen);

  const open = (r: ConfluenceRow) => {
    const sm = r.summary;
    const bull = r.direction === "bullish";
    const levels: { price: number; label: string; color?: string }[] = [];
    if (sm.fresh_breakout?.level != null) levels.push({ price: sm.fresh_breakout.level, label: `${bull ? "10D high" : "10D low"} ${fmtNum(sm.fresh_breakout.level)}`, color: bull ? "#95EB45" : "#F2643C" });
    if (sm.week52?.level != null) levels.push({ price: sm.week52.level, label: `52W ${bull ? "high" : "low"} ${fmtNum(sm.week52.level)}`, color: "#19C3DA" });
    const markers = r.combo?.session ? [{ t: Math.floor(Date.parse(r.combo.session) / 1000), label: "Combo", color: bull ? "#95EB45" : "#F2643C", position: (bull ? "below" : "above") as "below" | "above" }] : [];
    const withVcp = !!sm.vcp_status && r.timeframe === "1d";
    openChart({
      symbol: r.symbol,
      timeframe: r.timeframe,
      asof: asof || null,
      show: ["breakouts", "marubozu", ...(withVcp ? ["vcp"] : [])],
      context: {
        title: `Confluence ${r.score.toFixed(0)} - ${r.band} (${r.direction})`,
        lines: [
          { k: "Score", v: `${r.score.toFixed(1)} (base ${r.base_score.toFixed(1)} + combination ${r.bonus.toFixed(0)})` },
          { k: "Coverage", v: `${(r.coverage * 100).toFixed(0)}%${r.ranked ? "" : " (not ranked)"}` },
          ...r.explanation.map((c) => ({ k: c.label, v: c.available ? `${(c.score * 100).toFixed(0)}% -> ${fmtNum(c.points, 1)} pts` : "unavailable" })),
        ],
        levels,
        markers,
        vcp: withVcp,
      },
    });
  };

  const cols: Column<ConfluenceRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="inline-block max-w-[170px] truncate align-bottom text-muted" title={r.company ?? ""}>{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "score", label: "Score", render: (r) => <ScoreCell row={r} />, sort: (r) => r.score, title: "100 x weighted mean of the available categories + same-session combination bonus (cap 100). Not a probability." },
    { key: "band", label: "Band", render: (r) => <BandChip band={r.band} />, sort: (r) => r.score },
    { key: "cov", label: "Coverage", align: "right", render: (r) => `${(r.coverage * 100).toFixed(0)}%`, sort: (r) => r.coverage, title: "Share of the category weight with available data" },
    { key: "active", label: "Signals", align: "right", render: (r) => r.active_signals, sort: (r) => r.active_signals, title: "Evidence that counted (fully or as a capped bonus)" },
    { key: "vcp", label: "VCP", render: (r) => (r.summary.vcp_label ? <span className={r.summary.vcp_status?.startsWith("breakout") ? "text-up" : r.summary.vcp_status === "near_pivot" ? "text-accent" : "text-ink"}>{r.summary.vcp_label}</span> : <span className="text-muted">-</span>), sort: (r) => r.summary.vcp_status ?? "" },
    {
      key: "fb",
      label: "10D",
      render: (r) =>
        r.summary.fresh_breakout ? (
          <span className={r.direction === "bullish" ? "text-up" : "text-down"} title={r.summary.fresh_breakout.first_in_lookback === false ? "not the first breakout in its lookback" : "first breakout in its lookback"}>
            {ago(r.summary.fresh_breakout.age, r.timeframe)}
            {r.summary.fresh_breakout.first_in_lookback === false ? " (not first)" : ""}
          </span>
        ) : r.summary.setup_state && ["approaching", "very_near", "touched"].includes(r.summary.setup_state) ? (
          <span className="text-accent" title="About to break out of the 10-day level (before the breakout)" data-testid="cf-setup">
            {r.summary.setup_label} {r.summary.setup_distance_pct != null ? `${r.summary.setup_distance_pct.toFixed(1)}%` : ""}
          </span>
        ) : (
          <span className="text-muted">-</span>
        ),
      sort: (r) => (r.summary.fresh_breakout ? -r.summary.fresh_breakout.age : r.summary.setup_distance_pct != null ? -100 - r.summary.setup_distance_pct : null),
      title: "Fresh 10-day breakout (still fresh), or the about-to-break-out state before it",
    },
    { key: "ipo", label: "IPO base", render: (r) => (r.summary.ipo_label ? <span className={r.summary.ipo_status === "breakout" ? "text-up" : r.summary.ipo_status === "near_pivot" ? "text-accent" : "text-muted"}>{r.summary.ipo_label}</span> : <span className="text-muted">-</span>), sort: (r) => r.summary.ipo_status ?? "" },
    { key: "w52", label: "52W", render: (r) => (r.summary.week52 ? <span className={r.direction === "bullish" ? "text-up" : "text-down"}>{ago(r.summary.week52.age, r.timeframe)}</span> : <span className="text-muted">-</span>), sort: (r) => (r.summary.week52 ? -r.summary.week52.age : null), title: "52-week breakout (still fresh)" },
    { key: "maru", label: "Marubozu", render: (r) => (r.summary.marubozu ? ago(r.summary.marubozu.age, r.timeframe) : <span className="text-muted">-</span>), sort: (r) => (r.summary.marubozu ? -r.summary.marubozu.age : null) },
    { key: "vs", label: "Vol shock", render: (r) => (r.summary.volume_shock ? <span title={`${r.summary.volume_shock.direction} volume shock`}>{r.summary.volume_shock.rvol.toFixed(1)}x · {ago(r.summary.volume_shock.age, r.timeframe)}</span> : <span className="text-muted">-</span>), sort: (r) => r.summary.volume_shock?.rvol ?? null },
    { key: "pat", label: "Pattern", render: (r) => (r.summary.pattern ? <span title={`quality ${fmtNum(r.summary.pattern.quality, 0)}`}>{r.summary.pattern.name} <span className="text-muted">({humanize(r.summary.pattern.status)})</span></span> : <span className="text-muted">-</span>), sort: (r) => r.summary.pattern?.quality ?? null },
    { key: "ema", label: "EMA alignment", render: (r) => (r.summary.ema_alignment ? humanize(r.summary.ema_alignment) : "-"), sort: (r) => r.summary.ema_alignment ?? "" },
    { key: "stage", label: "Stage", render: (r) => <span className={r.summary.stage === "stage2_confirmed" || r.summary.stage === "early_stage2" ? "text-validated" : "text-muted"}>{r.summary.stage_label ?? "no weekly scan"}</span>, sort: (r) => r.summary.stage ?? "" },
    {
      key: "rs",
      label: "RS / RRG",
      render: (r) =>
        r.summary.rs === null && !r.summary.rrg_quadrant ? (
          <span className="text-muted">-</span>
        ) : (
          <span title={r.summary.sector_index ? `Sector ${r.summary.sector_index}: ${r.summary.sector_quadrant}` : undefined}>
            {r.summary.rs !== null ? `RS ${r.summary.rs >= 0 ? "+" : ""}${r.summary.rs.toFixed(3)}` : ""}
            {r.summary.rs !== null && r.summary.rrg_quadrant ? " · " : ""}
            {r.summary.rrg_quadrant ? humanize(r.summary.rrg_quadrant) : ""}
            {r.summary.sector_quadrant ? <span className="text-muted"> · sector {humanize(r.summary.sector_quadrant)}</span> : null}
          </span>
        ),
      sort: (r) => r.summary.rs,
    },
    { key: "latest", label: "Latest signal", render: (r) => fmtBarTime(r.latest_signal_time, r.timeframe), sort: (r) => r.latest_signal_time ?? "" },
  ];

  const counts = res?.snapshot?.counts ?? {};
  return (
    <div className="mx-auto flex max-w-[1800px] flex-col gap-3 px-4 py-3" data-testid="confluence-dashboard">
      <TechScanHeader
        title="Confluence Dashboard"
        subtitle="Stocks supported by several independent kinds of evidence, scored from the stored scanner results (trend, setup, trigger, volume + candlestick, relative strength). Analytical filters, not trade recommendations."
        universe={universe}
        timeframe={tf}
        timeframes={TFS}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, tf: (p.timeframe as string) ?? tf, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <div className="flex w-full flex-col gap-1">
          <span className="label">Screen</span>
          <div className="flex flex-wrap gap-1">
            <button className={`chip h-7 ${screen === "" ? "chip-on" : ""}`} onClick={() => set({ screen: "" })} data-testid="cf-screen-all">
              All stocks
            </button>
            {screens.map((s) => (
              <button key={s.key} className={`chip h-7 ${screen === s.key ? "chip-on" : ""}`} onClick={() => set({ screen: s.key })} title={s.description} data-testid={`cf-screen-${s.key}`}>
                {s.label}
              </button>
            ))}
          </div>
          {screenDef && <div className="text-[11px] text-muted" data-testid="cf-screen-description">{screenDef.description}</div>}
        </div>
        <div className="flex flex-col gap-1">
          <span className="label">Direction</span>
          <div className="flex gap-1">
            {(["bullish", "bearish"] as const).map((d) => (
              <button key={d} className={`chip h-8 ${activeDir === d ? "chip-on" : ""}`} disabled={!!screenDef} onClick={() => set({ dir: d === "bullish" ? "" : d })} title={screenDef ? "Fixed by the selected screen" : `Score ${d} evidence`} data-testid={`cf-dir-${d}`}>
                {d === "bullish" ? "Bullish" : "Bearish"}
              </button>
            ))}
          </div>
        </div>
        <Field label="Stage Analysis">
          <select className="ctl" value={stage} onChange={(e) => set({ stage: e.target.value })} data-testid="cf-stage">
            {STAGE_OPTS.map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="VCP status">
          <select className="ctl" value={vcp} onChange={(e) => set({ vcp: e.target.value })} data-testid="cf-vcp">
            {VCP_OPTS.map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="F&O">
          <select className="ctl" value={fno} onChange={(e) => set({ fno: e.target.value })} data-testid="cf-fno">
            <option value="">All</option>
            <option value="YES">F&amp;O only</option>
            <option value="NO">Non-F&amp;O</option>
          </select>
        </Field>
        <Field label="Band">
          <select className="ctl" value={band} onChange={(e) => set({ band: e.target.value })} data-testid="cf-band">
            <option value="">All bands</option>
            {BANDS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Min score">
          <input className="ctl w-20" type="number" min={0} max={100} step={5} value={minScore} onChange={(e) => set({ min: e.target.value })} data-testid="cf-min-score" />
        </Field>
        <Field label="Search">
          <input className="ctl w-36" placeholder="symbol / company" value={q} onChange={(e) => set({ q: e.target.value })} data-testid="cf-search" />
        </Field>
        <div className="flex flex-col gap-1 text-xs text-muted">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={fb} onChange={(e) => set({ fb: e.target.checked ? "1" : "" })} data-testid="cf-fresh" /> Fresh breakout (10D / 52W)
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={vs} onChange={(e) => set({ vs: e.target.checked ? "1" : "" })} data-testid="cf-volume" /> Volume shock
          </label>
          <label className="flex items-center gap-1" title="Stocks whose available data covers less than the minimum share of the category weight">
            <input type="checkbox" checked={unranked} onChange={(e) => set({ unranked: e.target.checked ? "1" : "" })} data-testid="cf-unranked" /> Include unranked
          </label>
        </div>
        <button className="btn h-8" onClick={recompute} disabled={busy || !res?.snapshot} title="Recompute the snapshot from the stored scanner evidence (no scanner runs)" data-testid="cf-recompute">
          {busy ? "Recomputing..." : "Recompute"}
        </button>
        <span className="text-xs text-muted" data-testid="result-count">
          {rows.length} results
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.snapshot ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted" data-testid="cf-snapshot">
          <span>
            Snapshot {res.snapshot.snapshot_id} · {fmtDateTime(res.snapshot.created_at)} · confluence config v{res.snapshot.config_version} ({res.snapshot.config_hash})
          </span>
          <span>Context: {res.context_scan ? `weekly scan ${res.context_scan.scan_id}` : "no weekly scan (Stage / RS / RRG unavailable)"}</span>
          <span className="flex items-center gap-1.5" data-testid="cf-band-counts">
            {BANDS.map((b) => (
              <span key={b} className="inline-flex items-center gap-1">
                <BandChip band={b} />
                <span className="num text-ink">{counts[`${activeDir}:${b}`] ?? 0}</span>
              </span>
            ))}
            <span>(ranked, {activeDir})</span>
          </span>
        </div>
      ) : null}
      {res?.note && <div className="text-[11px] text-muted" data-testid="cf-note">{res.note}</div>}
      <div className={`grid gap-3 ${selected ? "xl:grid-cols-[minmax(0,1fr)_400px]" : ""}`}>
        <DataTable
          rows={rows}
          columns={cols}
          testid="confluence-table"
          onRow={(r) => setSelected(r)}
          empty={res?.snapshot ? "No stock matches these filters." : "No confluence snapshot yet: run a 1D scan (and a 1W scan for Stage Analysis / RS context)."}
          initialSort={{ key: "score", desc: true }}
        />
        {selected && (
          <div className="flex flex-col gap-2" data-testid="cf-detail">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                <SymbolButton symbol={selected.symbol} onOpen={() => open(selected)} testid="cf-detail-symbol" />
                <span className="text-[11px] text-muted">{selected.company}</span>
              </span>
              <button className="chip h-6" onClick={() => setSelected(null)} data-testid="cf-detail-close">
                Close
              </button>
            </div>
            <ConfluenceBreakdown row={selected} minCoverage={res?.min_coverage ?? 0.6} />
          </div>
        )}
      </div>
    </div>
  );
}
