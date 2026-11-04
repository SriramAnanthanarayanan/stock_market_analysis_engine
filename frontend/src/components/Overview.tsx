import type { HealthResponse, OverviewResponse, PatternInfo, ScanResponse, StatusSlug } from "../api/types";
import { fmtAgo } from "../lib/format";
import { STATUS_ORDER, TIMEFRAMES } from "../lib/status";
import { StatusBadge } from "./common";

function sum(rec: Record<string, number> | undefined): number {
  return Object.values(rec ?? {}).reduce((a, b) => a + b, 0);
}

// Ranked bars, one hue, value printed beside each bar (the list doubles as the table view).
function RankedBars({ rows, testid }: { rows: { label: string; value: number }[]; testid: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <p className="text-xs text-muted">No detections.</p>;
  return (
    <ul className="space-y-1.5" data-testid={testid}>
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(0,9rem)_1fr_2.5rem] items-center gap-2 text-xs" title={`${r.label}: ${r.value}`}>
          <span className="truncate text-muted">{r.label}</span>
          <span className="h-2 rounded-sm bg-page">
            <span className="block h-2 rounded-sm bg-accent/70" style={{ width: `${(100 * r.value) / max}%` }} />
          </span>
          <span className="num text-right text-ink">{r.value}</span>
        </li>
      ))}
    </ul>
  );
}

interface Props {
  scan: ScanResponse | null;
  overview: OverviewResponse | null;
  health: HealthResponse | null;
  patterns: PatternInfo[];
}

export function Overview({ scan, overview, health, patterns }: Props) {
  const counts = scan?.detection_counts ?? {};
  const byStatus = counts.by_status ?? {};
  const visibleTotal = sum(byStatus) - (byStatus.developing ?? 0);
  const name = (slug: string) => patterns.find((p) => p.slug === slug)?.name ?? slug;
  const patternRows = Object.entries(counts.by_pattern ?? {})
    .map(([k, v]) => ({ label: name(k), value: v }))
    .sort((a, b) => b.value - a.value);
  const tfRows = TIMEFRAMES.map((t) => ({ label: t.label, value: sum(overview?.timeframes?.[t.value]?.counts?.by_pattern) }));
  const fno = counts.by_fno_status ?? {};
  const md = health?.market_data ?? {};
  const uni = health?.universe ?? {};
  return (
    <div className="grid gap-3 lg:grid-cols-4" data-testid="overview">
      <section className="panel p-3">
        <h2 className="label">Detected patterns</h2>
        <div className="mt-1 text-3xl font-semibold text-ink" data-testid="total-patterns">
          {visibleTotal}
        </div>
        <p className="text-[11px] text-muted">
          in this scan{byStatus.developing ? ` (+${byStatus.developing} Developing, hidden by default)` : ""}
        </p>
        <h3 className="label mt-3">F&amp;O split</h3>
        <div className="mt-1 flex gap-4 text-xs">
          <span>
            <span className="text-muted">F&amp;O </span>
            <span className="num text-ink">{fno.YES ?? 0}</span>
          </span>
          <span>
            <span className="text-muted">Non-F&amp;O </span>
            <span className="num text-ink">{fno.NO ?? 0}</span>
          </span>
          <span>
            <span className="text-muted">Unknown </span>
            <span className="num text-ink">{fno.UNKNOWN ?? 0}</span>
          </span>
        </div>
      </section>
      <section className="panel p-3">
        <h2 className="label mb-2">Pattern distribution</h2>
        <RankedBars rows={patternRows} testid="pattern-distribution" />
      </section>
      <section className="panel p-3">
        <h2 className="label mb-2">Lifecycle status</h2>
        <ul className="space-y-1" data-testid="status-counts">
          {STATUS_ORDER.filter((s) => byStatus[s]).map((s) => (
            <li key={s} className="flex items-center justify-between text-xs">
              <StatusBadge status={s as StatusSlug} />
              <span className="num text-ink">{byStatus[s]}</span>
            </li>
          ))}
          {!Object.keys(byStatus).length && <li className="text-xs text-muted">No detections.</li>}
        </ul>
        <h2 className="label mb-2 mt-3">By timeframe (latest scans)</h2>
        <RankedBars rows={tfRows} testid="timeframe-distribution" />
      </section>
      <section className="panel p-3 text-xs">
        <h2 className="label mb-2">Data status</h2>
        <div className="space-y-1 text-muted">
          <div>
            Provider: <span className="text-ink">{String(md.provider ?? "-")}</span>
            {md.last_error ? <span className="text-down"> - {String(md.last_error)}</span> : null}
          </div>
          {(["1d", "1wk", "1h"] as const).map((iv) => (
            <div key={iv}>
              {iv} cache: <span className="num text-ink">{md[iv]?.symbols_cached ?? 0}</span> symbols
              {md[iv]?.symbols_error ? <span className="text-down"> ({md[iv].symbols_error} errors)</span> : null}
              <span> - {fmtAgo(md[iv]?.latest_fetch)}</span>
            </div>
          ))}
          <div>
            NIFTY 500 list: <span className="text-ink">{uni.nifty500?.last_success_rows ?? "-"}</span> rows, {fmtAgo(uni.nifty500?.last_success)}
          </div>
          <div>
            F&amp;O list: <span className="text-ink">{uni.fno?.last_success_rows ?? "-"}</span> stocks, first snapshot {uni.fno?.first_snapshot ?? "-"}
          </div>
          <div>
            Last successful scan ({scan?.scan?.timeframe?.toUpperCase() ?? "-"}): <span className="text-ink">{fmtAgo(scan?.scan?.finished_at)}</span>
          </div>
          <div>
            Health:{" "}
            <span className={health?.status === "ok" ? "text-validated" : "text-forming"}>{health?.status ?? "unknown"}</span>
            {health?.degraded_components?.length ? ` (${health.degraded_components.join(", ")})` : ""}
          </div>
        </div>
      </section>
    </div>
  );
}

