import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { ScanSummary, Timeframe, Universe } from "../api/types";

/** The scan fields the header shows (technical and advanced scan summaries both fit). */
export interface HeaderScan {
  scan_id: string;
  finished_at: string | null;
  successful_symbols: number;
  price_basis: string | null;
  tech_config_version?: string | null;
  signal_config_version?: string | null;
}
import { fmtDateTime } from "../lib/format";
import { TIMEFRAMES, UNIVERSES } from "../lib/status";

interface Props {
  title: string;
  subtitle: string;
  universe: Universe;
  timeframe: Timeframe | null; // null = fixed (breakouts are always daily)
  /** Selectable timeframes (default: every scan timeframe). */
  timeframes?: { value: Timeframe; label: string }[];
  asof: string;
  onChange: (patch: { universe?: Universe; timeframe?: Timeframe; asof?: string }) => void;
  scan: HeaderScan | null;
  onScanDone: () => void;
  children?: ReactNode;
}

/** Universe / timeframe / as-of controls shared by the technical scanners. The
 *  results come from the same scan as the pattern scanner ("Run scan" queues one). */
export function TechScanHeader(p: Props) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const [active, setActive] = useState<ScanSummary | null>(null);
  const tf = p.timeframe ?? "1d";
  useEffect(() => {
    if (!active || !["queued", "running"].includes(active.state)) return;
    const id = window.setTimeout(() => {
      api
        .scanStatus(active.scan_id)
        .then((s) => {
          setActive(s);
          if (!["queued", "running"].includes(s.state)) p.onScanDone();
        })
        .catch(() => setActive(null));
    }, 1200);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  const run = () => {
    api
      .refresh({ universe: p.universe, timeframe: tf, asof: p.asof || null })
      .then((r) => api.scanStatus(r.scan_id))
      .then(setActive)
      .catch(() => setActive(null));
  };
  const running = active && ["queued", "running"].includes(active.state);
  return (
    <div className="panel flex flex-col gap-2 px-3 py-2.5" data-testid="tech-scan-header">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-2">
          <h1 className="text-base font-semibold text-ink">{p.title}</h1>
          <div className="text-[11px] text-muted">{p.subtitle}</div>
        </div>
        <label className="flex flex-col gap-1">
          <span className="label">Universe</span>
          <select className="ctl" value={p.universe} onChange={(e) => p.onChange({ universe: e.target.value as Universe })} data-testid="universe-select">
            {UNIVERSES.map((u) => (
              <option key={u.value} value={u.value}>
                {u.label}
              </option>
            ))}
          </select>
        </label>
        {p.timeframe && (
          <div className="flex flex-col gap-1">
            <span className="label">Timeframe</span>
            <div className="flex gap-1">
              {(p.timeframes ?? TIMEFRAMES).map((t) => (
                <button key={t.value} className={`chip h-8 ${t.value === p.timeframe ? "chip-on" : ""}`} onClick={() => p.onChange({ timeframe: t.value })} data-testid={`tf-${t.value}`}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className="label">As of</span>
          <input type="date" className="ctl" max={today} value={p.asof} onChange={(e) => p.onChange({ asof: e.target.value })} data-testid="asof-input" />
        </label>
        <button className="btn h-8" onClick={run} disabled={!!running} data-testid="run-scan">
          {running ? `Scanning ${active?.processed_symbols ?? 0}/${active?.total_symbols ?? 0}...` : "Run scan"}
        </button>
        <div className="ml-auto text-right text-[11px] text-muted" data-testid="scan-info">
          {p.scan ? (
            <>
              <div>
                Scan {p.scan.scan_id} · {fmtDateTime(p.scan.finished_at)}
              </div>
              <div>
                {p.scan.successful_symbols} symbols · basis {p.scan.price_basis ?? "?"} ·{" "}
                {p.scan.signal_config_version !== undefined ? `signal config v${p.scan.signal_config_version ?? "-"}` : `technical config v${p.scan.tech_config_version ?? "-"}`}
              </div>
            </>
          ) : (
            <div>No completed scan yet</div>
          )}
        </div>
      </div>
      {p.children && <div className="flex flex-wrap items-end gap-3 border-t border-line pt-2">{p.children}</div>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}
