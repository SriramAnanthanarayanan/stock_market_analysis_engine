import type { ScanSummary, Timeframe, Universe } from "../api/types";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { TIMEFRAMES, UNIVERSES } from "../lib/status";

interface Props {
  universe: Universe;
  timeframe: Timeframe;
  asof: string;
  onChange: (patch: { universe?: Universe; timeframe?: Timeframe; asof?: string }) => void;
  onRefresh: () => void;
  refreshing: boolean;
  active: ScanSummary | null;
  lastScan: ScanSummary | null;
}

export function ScanControls({ universe, timeframe, asof, onChange, onRefresh, refreshing, active, lastScan }: Props) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const total = active?.total_symbols ?? 0;
  const done = active?.processed_symbols ?? 0;
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5" data-testid="scan-controls">
      <label className="flex flex-col gap-1">
        <span className="label">Universe</span>
        <select className="ctl" value={universe} onChange={(e) => onChange({ universe: e.target.value as Universe })} data-testid="universe-select">
          {UNIVERSES.map((u) => (
            <option key={u.value} value={u.value}>
              {u.label}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-col gap-1">
        <span className="label">Timeframe</span>
        <div className="flex gap-1" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <button
              key={t.value}
              className={`chip h-8 ${t.value === timeframe ? "chip-on" : ""}`}
              onClick={() => onChange({ timeframe: t.value })}
              data-testid={`tf-${t.value}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <label className="flex flex-col gap-1">
        <span className="label">As of (IST)</span>
        <div className="flex gap-1">
          <input
            type="date"
            className="ctl"
            value={asof}
            max={today}
            onChange={(e) => onChange({ asof: e.target.value })}
            data-testid="asof-input"
          />
          {asof && (
            <button className="btn" onClick={() => onChange({ asof: "" })} title="Back to the latest completed candles">
              Live
            </button>
          )}
        </div>
      </label>
      <button className="btn-primary" onClick={onRefresh} disabled={refreshing || !!active} data-testid="refresh-button">
        {active ? "Scanning..." : refreshing ? "Starting..." : "Refresh scan"}
      </button>
      <div className="ml-auto flex min-w-[260px] flex-col gap-1 text-xs" data-testid="scan-status">
        {active ? (
          <>
            <div className="flex justify-between text-muted">
              <span>
                {active.timeframe.toUpperCase()} scan - {active.phase ?? active.state}
              </span>
              <span className="num">
                {done}/{total || "?"} symbols
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-page">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
            </div>
            {active.message && <div className="truncate text-[11px] text-muted" title={active.message}>{active.message}</div>}
          </>
        ) : (
          <div className="text-right text-muted">
            Last scan:{" "}
            <span className="text-ink" title={lastScan?.finished_at ?? ""}>
              {lastScan?.finished_at ? `${fmtDateTime(lastScan.finished_at)} (${fmtAgo(lastScan.finished_at)})` : "none yet"}
            </span>
            {lastScan && (
              <div className="text-[11px]">
                config v{lastScan.config_version} - {lastScan.successful_symbols ?? 0} ok / {lastScan.failed_symbols ?? 0} failed symbols
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
