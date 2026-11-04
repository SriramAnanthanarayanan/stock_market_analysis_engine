import { useMemo, useState } from "react";
import type { Match, PatternInfo, StatusSlug } from "../api/types";
import { fmtBarTime, fmtNum } from "../lib/format";
import { CATEGORY_LABELS, STATUS_META, STATUS_ORDER } from "../lib/status";
import { FnoBadge, QualityCell, StatusBadge } from "./common";
import { SymbolButton } from "../lib/openChart";

export interface Filters {
  pattern: string;
  category: string;
  status: string;
  fno: string;
  minQuality: number;
  search: string;
  developing: boolean;
}

export const DEFAULT_FILTERS: Filters = { pattern: "", category: "", status: "", fno: "", minQuality: 0, search: "", developing: false };

export function FilterBar({ value, onChange, patterns }: { value: Filters; onChange: (f: Partial<Filters>) => void; patterns: PatternInfo[] }) {
  return (
    <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5" data-testid="filters">
      <label className="flex flex-col gap-1">
        <span className="label">Pattern</span>
        <select className="ctl w-52" value={value.pattern} onChange={(e) => onChange({ pattern: e.target.value })} data-testid="filter-pattern">
          <option value="">All patterns</option>
          {patterns.map((p) => (
            <option key={p.slug} value={p.slug} disabled={!p.implemented}>
              {p.name}
              {p.implemented ? "" : ` (${p.phase})`}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Category</span>
        <select className="ctl" value={value.category} onChange={(e) => onChange({ category: e.target.value })} data-testid="filter-category">
          <option value="">All categories</option>
          {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Lifecycle status</span>
        <select className="ctl" value={value.status} onChange={(e) => onChange({ status: e.target.value })} data-testid="filter-status">
          <option value="">All (except Developing)</option>
          {STATUS_ORDER.map((s) => (
            <option key={s} value={s}>
              {STATUS_META[s].label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">F&amp;O</span>
        <select className="ctl" value={value.fno} onChange={(e) => onChange({ fno: e.target.value })} data-testid="filter-fno">
          <option value="">All</option>
          <option value="YES">F&amp;O only</option>
          <option value="NO">Non-F&amp;O only</option>
          <option value="UNKNOWN">Unknown</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Min quality: {value.minQuality}</span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={value.minQuality}
          onChange={(e) => onChange({ minQuality: Number(e.target.value) })}
          className="h-8 w-36 accent-[#19C3DA]"
          data-testid="filter-quality"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Search</span>
        <input
          className="ctl w-48"
          placeholder="Symbol or company"
          value={value.search}
          onChange={(e) => onChange({ search: e.target.value })}
          data-testid="filter-search"
        />
      </label>
      <label className="flex h-8 items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={value.developing} onChange={(e) => onChange({ developing: e.target.checked })} className="accent-[#19C3DA]" />
        Show Developing
      </label>
    </div>
  );
}

type SortKey =
  | "symbol"
  | "company"
  | "fno_status"
  | "pattern_name"
  | "timeframe"
  | "status"
  | "quality"
  | "formation_length"
  | "upper_touches"
  | "lower_touches"
  | "width_pct"
  | "volume_ratio"
  | "last_close"
  | "last_candle_time";

const COLUMNS: { key: SortKey; label: string; numeric?: boolean; title?: string }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "company", label: "Company" },
  { key: "fno_status", label: "F&O" },
  { key: "pattern_name", label: "Pattern" },
  { key: "timeframe", label: "TF" },
  { key: "status", label: "Lifecycle" },
  { key: "quality", label: "Quality", numeric: true, title: "Geometric fit score 0-100 (not a probability)" },
  { key: "formation_length", label: "Bars", numeric: true, title: "Formation length (bars)" },
  { key: "upper_touches", label: "Up T", numeric: true, title: "Upper boundary touches" },
  { key: "lower_touches", label: "Lo T", numeric: true, title: "Lower boundary touches" },
  { key: "width_pct", label: "Width", numeric: true, title: "Boundary width at formation end, % of close" },
  { key: "volume_ratio", label: "Vol ratio", numeric: true, title: "Latest completed bar volume / mean volume over the formation" },
  { key: "last_close", label: "Close", numeric: true },
  { key: "last_candle_time", label: "Latest candle" },
];

const PAGE = 50;

function value(m: Match, k: SortKey): string | number {
  if (k === "status") return STATUS_META[m.status].order;
  const v = m[k as keyof Match];
  return typeof v === "number" ? v : typeof v === "string" ? v.toLowerCase() : v === null || v === undefined ? -Infinity : String(v);
}

export function ResultsTable({ rows, onOpen, loading }: { rows: Match[]; onOpen: (m: Match) => void; loading: boolean }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "quality", dir: -1 });
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => {
    const out = [...rows];
    out.sort((a, b) => {
      const va = value(a, sort.key);
      const vb = value(b, sort.key);
      if (va < vb) return -1 * sort.dir;
      if (va > vb) return 1 * sort.dir;
      return b.quality - a.quality;
    });
    return out;
  }, [rows, sort]);
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
  const current = Math.min(page, pages - 1);
  const slice = sorted.slice(current * PAGE, current * PAGE + PAGE);
  const toggle = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: COLUMNS.find((c) => c.key === key)?.numeric ? -1 : 1 }));
    setPage(0);
  };
  return (
    <section className={`panel overflow-hidden transition-opacity ${loading ? "opacity-60" : ""}`} data-testid="results">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] text-left text-xs">
          <thead className="bg-panel2 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={`cursor-pointer select-none px-2 py-2 font-medium hover:text-ink ${c.numeric ? "text-right" : ""}`} onClick={() => toggle(c.key)} title={c.title} data-testid={`sort-${c.key}`}>
                  {c.label}
                  {sort.key === c.key ? (sort.dir === -1 ? " ▾" : " ▴") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.map((m) => (
              <tr
                key={`${m.scan_id}-${m.pattern_id}`}
                className="cursor-pointer border-t border-line/60 hover:bg-panel2"
                onClick={() => onOpen(m)}
                data-testid="result-row"
                data-symbol={m.symbol}
                data-pattern={m.pattern}
              >
                <td className="px-2 py-1.5">
                  <SymbolButton symbol={m.symbol} onOpen={() => onOpen(m)} />
                </td>
                <td className="max-w-[220px] truncate px-2 py-1.5 text-muted" title={m.company ?? ""}>
                  {m.company ?? "-"}
                </td>
                <td className="px-2 py-1.5">
                  <FnoBadge value={m.fno_status} />
                </td>
                <td className="px-2 py-1.5 text-ink">{m.pattern_name}</td>
                <td className="px-2 py-1.5 text-muted">{m.timeframe.toUpperCase()}</td>
                <td className="px-2 py-1.5">
                  <StatusBadge status={m.status as StatusSlug} reason={m.status_reason} />
                </td>
                <td className="px-2 py-1.5 text-right">
                  <QualityCell score={m.quality} label={m.quality_label} />
                </td>
                <td className="num px-2 py-1.5 text-right">{m.formation_length}</td>
                <td className="num px-2 py-1.5 text-right">{m.upper_touches ?? "-"}</td>
                <td className="num px-2 py-1.5 text-right">{m.lower_touches ?? "-"}</td>
                <td className="num px-2 py-1.5 text-right">{m.width_pct === null ? "-" : `${m.width_pct.toFixed(1)}%`}</td>
                <td className="num px-2 py-1.5 text-right">{m.volume_ratio === null ? "-" : m.volume_ratio.toFixed(2)}</td>
                <td className="num px-2 py-1.5 text-right">{fmtNum(m.last_close)}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-muted">{fmtBarTime(m.last_candle_time, m.timeframe)}</td>
              </tr>
            ))}
            {!slice.length && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-3 py-8 text-center text-muted">
                  {loading ? "Loading..." : "No detections match the current filters."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <footer className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
        <span data-testid="row-count">
          {sorted.length} detections {sorted.length > PAGE ? `- page ${current + 1} of ${pages}` : ""}
        </span>
        <span className="flex gap-1">
          <button className="btn h-7" disabled={current === 0} onClick={() => setPage(current - 1)}>
            Prev
          </button>
          <button className="btn h-7" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
            Next
          </button>
        </span>
      </footer>
    </section>
  );
}

