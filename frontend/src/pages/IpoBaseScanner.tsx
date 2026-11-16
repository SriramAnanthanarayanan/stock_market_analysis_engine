import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ipoApi, type IpoRow, type IpoScanResult, type IpoStatus } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge, KV, Panel, QualityCell } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum, humanize } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

const STATUS_CLS: Record<IpoStatus, string> = {
  too_young: "text-muted",
  discovery_phase: "text-developing",
  base_forming: "text-forming",
  base_validated: "text-validated",
  near_pivot: "text-accent",
  breakout: "text-up",
  failed_breakout: "text-invalid",
  invalidated: "text-invalid",
  cheat_entry: "text-accent",
  ipo_low_broken: "text-down",
};
const PRESETS: { key: string; label: string; status: string }[] = [
  { key: "", label: "All bases", status: "" },
  { key: "near", label: "Near pivot / breakout", status: "near_pivot,breakout" },
  { key: "cheat", label: "Cheat entry", status: "cheat_entry" },
  { key: "valid", label: "Base validated", status: "base_validated" },
  { key: "forming", label: "Discovery / forming", status: "discovery_phase,base_forming" },
  { key: "failed", label: "Failed / invalidated", status: "failed_breakout,invalidated" },
  { key: "lowbroken", label: "IPO low broken", status: "ipo_low_broken" },
  { key: "young", label: "Too young", status: "too_young" },
];

export function IpoStatusChip({ v }: { v: Pick<IpoRow, "status" | "status_label" | "status_reason"> }) {
  return (
    <span className={`whitespace-nowrap text-xs font-medium ${STATUS_CLS[v.status] ?? "text-ink"}`} title={v.status_reason ?? v.status_label} data-ipo-status={v.status}>
      {v.status_label}
    </span>
  );
}

function IpoPanel({ v }: { v: IpoRow }) {
  const b = v.base;
  return (
    <Panel title="IPO base (daily)" right={<IpoStatusChip v={v} />}>
      <div data-testid="ipo-panel">
        <KV k="Listed" v={`${v.listing.date} (${v.listing.source === "nse_equity_list" ? "official NSE listing date" : "first candle - fallback"})`} />
        {v.issue_price != null && (
          <KV k="Issue price" v={`${fmtNum(v.issue_price)}${v.vs_issue_pct != null ? ` · last close ${v.vs_issue_pct >= 0 ? "+" : ""}${v.vs_issue_pct.toFixed(1)}% vs issue` : ""}${v.ipo_security_type === "SME" ? " · SME IPO, now on the main board" : ""}`} />
        )}
        {v.issue_price_note && <p className="text-[11px] text-muted">{v.issue_price_note}</p>}
        <KV k="Age" v={`${v.age_bars} completed sessions`} />
        <KV k="Discovery period" v={`${v.discovery.bars} sessions · ${fmtNum(v.discovery.low)} - ${fmtNum(v.discovery.high)}`} />
        {b && (
          <>
            <KV k="Base" v={`${fmtBarTime(b.start, "1d")} - ${fmtBarTime(b.end, "1d")} (${b.length} sessions)`} />
            <KV k="Base high (pivot) / low" v={`${fmtNum(b.high)} / ${fmtNum(b.low)} · ${b.depth_pct.toFixed(1)}% deep`} />
            <KV k="Resistance touches" v={b.touches.length} />
            <KV k="Right side vs discovery (range / volume)" v={`${fmtNum(b.settle_ratio, 2)} / ${fmtNum(b.volume_dry_up_ratio, 2)}`} />
          </>
        )}
        {v.breakout && <KV k="Breakout" v={`${fmtBarTime(v.breakout.time, "1d")} · close ${fmtNum(v.breakout.close)} · volume ${fmtNum(v.breakout.volume_ratio, 2)}x`} />}
        {v.status_reason && <p className="mt-1 text-[11px] text-forming">{v.status_reason}</p>}
        <div className="label mt-3">Quality breakdown (not a probability)</div>
        <ul className="mt-1 space-y-1" data-testid="ipo-score">
          {v.quality_components.map((c) => (
            <li key={c.name} className="text-[11px]" title={c.formula}>
              <div className="flex justify-between">
                <span className="text-ink">{humanize(c.name)}</span>
                <span className="num text-muted">
                  {(c.score * 100).toFixed(0)}% x w{c.weight}
                </span>
              </div>
            </li>
          ))}
        </ul>
        <div className="label mt-3">Why</div>
        <ul className="mt-1 space-y-0.5 text-[11px]" data-testid="ipo-explanation">
          {v.explanation.map((line, i) => (
            <li key={i} className={line.startsWith("FAIL") ? "text-down" : line.startsWith("PASS") ? "text-ink" : "text-muted"}>
              {line}
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}

/** IPO Base Breakout: recent listings through price discovery, their first base,
 *  pivot and breakout on volume. Backend detector; nothing computed here. */
export function IpoBaseScanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const preset = params.get("preset") ?? "";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<IpoScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<IpoRow | null>(null);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const status = PRESETS.find((p) => p.key === preset)?.status ?? "";
  const load = useCallback(() => {
    ipoApi
      .scan({ universe, asof: asof || null, status: status || null, include_too_young: preset === "young" || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, status, preset, fno]);
  useEffect(load, [load, tick]);

  const open = (r: IpoRow) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      show: ["ipo"],
      context: {
        title: `IPO base - ${r.status_label}`,
        lines: [
          { k: "Listed", v: `${r.listing.date} (${r.age_bars} sessions ago)` },
          { k: "Pivot (base high)", v: fmtNum(r.pivot) },
          { k: "Distance to pivot", v: r.pivot_distance_pct === null ? "-" : `${r.pivot_distance_pct.toFixed(2)}%` },
          { k: "Base", v: r.base ? `${r.base.depth_pct.toFixed(1)}% deep · ${r.base.length} sessions · ${r.base.touches.length} touches` : "no base yet" },
          { k: "Breakout volume", v: r.breakout ? `${fmtNum(r.breakout.volume_ratio, 2)}x` : "-" },
          { k: "Quality (not a probability)", v: `${r.quality.toFixed(0)} / 100` },
        ],
        focusT: r.listing.t,
      },
    });

  const cols: Column<IpoRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "status", label: "Status", render: (r) => <IpoStatusChip v={r} />, sort: (r) => r.status },
    { key: "listed", label: "Listed", render: (r) => <span title={r.listing.source === "nse_equity_list" ? "official NSE listing date" : "first candle (fallback)"}>{r.listing.date}</span>, sort: (r) => r.listing.date },
    {
      key: "issue",
      label: "Issue",
      align: "right",
      render: (r) => (
        <span title={r.issue_price_note ?? "IPO issue price (NSE past public issues)"} className={r.split_factor && r.split_factor !== 1 ? "underline decoration-dotted" : ""}>
          {fmtNum(r.issue_price ?? null)}
        </span>
      ),
      sort: (r) => r.issue_price ?? null,
      title: "IPO issue price (NSE past public issues), adjusted for splits / bonus since the IPO like the chart",
    },
    {
      key: "vsissue",
      label: "vs issue",
      align: "right",
      render: (r) => (r.vs_issue_pct == null ? "-" : <span className={r.vs_issue_pct >= 0 ? "text-up" : "text-down"}>{`${r.vs_issue_pct >= 0 ? "+" : ""}${r.vs_issue_pct.toFixed(1)}%`}</span>),
      sort: (r) => r.vs_issue_pct ?? null,
      title: "Last close vs the IPO issue price",
    },
    { key: "age", label: "Age", align: "right", render: (r) => `${r.age_bars}d`, sort: (r) => r.age_bars, title: "Completed sessions since listing" },
    { key: "q", label: "Quality", render: (r) => <QualityCell score={r.quality} label="" />, sort: (r) => r.quality },
    { key: "depth", label: "Base depth", align: "right", render: (r) => (r.base ? `${r.base.depth_pct.toFixed(1)}%` : "-"), sort: (r) => r.base?.depth_pct ?? null },
    { key: "len", label: "Base", align: "right", render: (r) => (r.base ? `${r.base.length}d` : "-"), sort: (r) => r.base?.length ?? null },
    { key: "touch", label: "Touches", align: "right", render: (r) => r.base?.touches.length ?? "-", sort: (r) => r.base?.touches.length ?? null },
    { key: "pivot", label: "Pivot", align: "right", render: (r) => fmtNum(r.pivot), sort: (r) => r.pivot },
    { key: "dist", label: "To pivot", align: "right", render: (r) => (r.pivot_distance_pct === null ? "-" : `${r.pivot_distance_pct.toFixed(2)}%`), sort: (r) => r.pivot_distance_pct },
    { key: "vol", label: "BO volume", align: "right", render: (r) => (r.breakout ? `${fmtNum(r.breakout.volume_ratio, 2)}x` : "-"), sort: (r) => r.breakout_volume_ratio },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="ipo-scanner">
      <TechScanHeader
        title="IPO Base Breakout"
        subtitle="Recent listings: price discovery -> settled volatility -> first post-listing base -> pivot -> breakout on volume. Not an IPO-launch scanner; an IPO base is not a VCP."
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
              <button key={p.key} className={`chip h-8 ${preset === p.key ? "chip-on" : ""}`} onClick={() => set({ preset: p.key })} data-testid={`ipo-preset-${p.key || "all"}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
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
      {res && res.listing_dates_loaded === 0 && <div className="text-[11px] text-forming">No official listing dates loaded yet (NSE equity list); they are refreshed with the universe.</div>}
      {universe !== "ipo" && universe !== "all" && (
        <div className="text-[11px] text-muted">Recent IPOs outside NIFTY 500 / F&O are in the &quot;Recent IPOs&quot; and &quot;All&quot; universes.</div>
      )}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <div className={`grid gap-3 ${selected ? "xl:grid-cols-[minmax(0,1fr)_380px]" : ""}`}>
        <DataTable rows={res?.items ?? []} columns={cols} testid="ipo-table" onRow={(r) => setSelected(r)} empty="No recent listing matches these filters on the latest daily scan." initialSort={{ key: "q", desc: true }} />
        {selected && (
          <div className="flex flex-col gap-2" data-testid="ipo-detail">
            <div className="flex items-center justify-between">
              <SymbolButton symbol={selected.symbol} onOpen={() => open(selected)} testid="ipo-detail-symbol" />
              <button className="chip h-6" onClick={() => setSelected(null)}>
                Close
              </button>
            </div>
            <IpoPanel v={selected} />
          </div>
        )}
      </div>
    </div>
  );
}
