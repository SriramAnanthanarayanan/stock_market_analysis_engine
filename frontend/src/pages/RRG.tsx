import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useOpenChart } from "../lib/openChart";
import { api } from "../api/client";
import type { RRGResult } from "../api/types";
import { ErrorNote, Panel } from "../components/common";
import { RRGChart, RRG_PALETTE } from "../components/RRGChart";
import { Field } from "../components/TechScanHeader";
import { fmtDate } from "../lib/format";

const CATS: [string, string][] = [
  ["broad", "Broad market"],
  ["sector", "Sectoral"],
  ["thematic", "Thematic"],
  ["strategy", "Factor / strategy"],
];

export function RRG() {
  const openChart = useOpenChart();
  const [params, setParams] = useSearchParams();
  const benchmark = params.get("bench") ?? "NIFTY 500";
  const trail = Number(params.get("trail") ?? 12);
  const asof = params.get("asof") ?? "";
  const cats = (params.get("cats") ?? "sector").split(",").filter(Boolean);
  const maxVisible = Number(params.get("max") ?? 20);
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [data, setData] = useState<RRGResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    setLoading(true);
    api
      .rrg({ benchmark, trail, asof: asof || null })
      .then((r) => (setData(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [benchmark, trail, asof]);

  const all = data?.items ?? [];
  const colorMap = useMemo(() => new Map([...all].sort((a, b) => a.code.localeCompare(b.code)).map((it, i) => [it.code, RRG_PALETTE[i % RRG_PALETTE.length]])), [all]);
  const candidates = all.filter((it) => cats.includes(it.category) && (!q || it.code.toLowerCase().includes(q.toLowerCase()) || (it.name ?? "").toLowerCase().includes(q.toLowerCase())));
  const plottable = candidates.filter((it) => it.status === "ok" && it.trail.length > 0);
  const visible = plottable.filter((it) => !hidden.has(it.code)).slice(0, maxVisible);
  const unavailable = candidates.filter((it) => it.status !== "ok");
  const toggle = (code: string) =>
    setHidden((h) => {
      const n = new Set(h);
      n.has(code) ? n.delete(code) : n.add(code);
      return n;
    });

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="rrg-page">
      <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5">
        <div className="mr-2">
          <h1 className="text-base font-semibold text-ink">Relative Rotation Graph</h1>
          <div className="text-[11px] text-muted">Internally implemented RRG-style rotation on completed weekly closes - not the proprietary JdK RRG. Relative rotation, not a forecast.</div>
        </div>
        <Field label="Benchmark">
          <select className="ctl" value={benchmark} onChange={(e) => set({ bench: e.target.value })} data-testid="rrg-benchmark">
            {(data?.benchmarks ?? ["NIFTY 500", "NIFTY 50"]).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Trail">
          <div className="flex gap-1">
            {(data?.trail_options ?? [4, 8, 12]).map((t) => (
              <button key={t} className={`chip h-8 ${t === trail ? "chip-on" : ""}`} onClick={() => set({ trail: String(t) })} data-testid={`rrg-trail-${t}`}>
                {t}W
              </button>
            ))}
          </div>
        </Field>
        <Field label="As of">
          <input type="date" className="ctl" max={today} value={asof} onChange={(e) => set({ asof: e.target.value })} data-testid="rrg-asof" />
        </Field>
        <Field label="Categories">
          <div className="flex gap-1">
            {CATS.map(([c, label]) => (
              <button key={c} className={`chip h-8 ${cats.includes(c) ? "chip-on" : ""}`} onClick={() => set({ cats: (cats.includes(c) ? cats.filter((x) => x !== c) : [...cats, c]).join(",") })} data-testid={`rrg-cat-${c}`}>
                {label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="Search">
          <input className="ctl w-40" value={q} onChange={(e) => setQ(e.target.value)} placeholder="index name" data-testid="rrg-search" />
        </Field>
        <Field label="Max visible">
          <input className="ctl w-20" type="number" min={1} max={60} value={maxVisible} onChange={(e) => set({ max: e.target.value })} data-testid="rrg-max" />
        </Field>
        {loading && <span className="text-xs text-muted">Loading...</span>}
      </div>
      {error && <ErrorNote message={error} />}
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_330px]">
        <div className="panel p-2">
          {data && <RRGChart items={visible} color={(c) => colorMap.get(c) ?? "#19C3DA"} center={data.params.center} onSelect={(code) => openChart({ symbol: code, instrument: "index", timeframe: "1w" })} />}
          {data && (
            <div className="px-2 pb-1 text-[11px] text-muted" data-testid="rrg-meta">
              Benchmark {data.benchmark_name} · latest completed week {fmtDate(data.benchmark_last_week)} · RS-Ratio = 100 x RS / EMA(RS, {data.params.rs_period}w) · RS-Momentum = 100 x RS-Ratio / EMA(RS-Ratio, {data.params.momentum_period}w) · {data.data_basis} · config v{data.config_version}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <Panel title={`Indices (${visible.length} shown of ${plottable.length})`}>
            <ul className="max-h-[520px] space-y-0.5 overflow-y-auto text-xs" data-testid="rrg-legend">
              {plottable.map((it) => {
                const on = !hidden.has(it.code) && visible.includes(it);
                return (
                  <li key={it.code} className="flex items-center gap-2">
                    <input type="checkbox" checked={!hidden.has(it.code)} onChange={() => toggle(it.code)} data-testid="rrg-toggle" data-code={it.code} />
                    <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: colorMap.get(it.code) }} />
                    <button className={`flex-1 truncate text-left ${on ? "text-ink" : "text-muted"} hover:underline`} onClick={() => openChart({ symbol: it.code, instrument: "index", timeframe: "1w" })} title={it.name}>
                      {it.name}
                    </button>
                    <span className="text-[10px] text-muted">{it.latest?.quadrant}</span>
                  </li>
                );
              })}
            </ul>
            {plottable.length > maxVisible && <p className="mt-1 text-[10px] text-muted">Only the first {maxVisible} are drawn (max visible); hide some or raise the limit.</p>}
          </Panel>
          {unavailable.length > 0 && (
            <Panel title={`Not plotted (${unavailable.length})`}>
              <ul className="space-y-0.5 text-[11px] text-muted" data-testid="rrg-unavailable">
                {unavailable.map((it) => (
                  <li key={it.code}>
                    {it.name}: {it.status.replace("_", " ")}
                    {it.max_gap_weeks ? ` (${it.max_gap_weeks} consecutive weeks missing)` : ""}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
