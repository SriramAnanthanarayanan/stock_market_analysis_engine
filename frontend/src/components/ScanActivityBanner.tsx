import { useScanActivity } from "../lib/scanActivity";

const TF_LABEL: Record<string, string> = { "1d": "1D", "1w": "1W", "4h": "4H", "1h": "1H" };
const tfLabel = (tf: string) => TF_LABEL[tf] ?? tf.toUpperCase();

/**
 * Thin strip under the app header while the backend updates data by itself
 * (startup scans, after-close job, scheduler) or a scan started on a page runs.
 * Pages reload on their own when a scan of their timeframe finishes.
 */
export function ScanActivityBanner() {
  const act = useScanActivity();
  if (!act.ready) return null;
  const running = act.active;
  const waiting = act.startupPending.filter((tf) => !running.some((s) => s.timeframe === tf && !s.asof));
  if (!running.length && !waiting.length && !act.postprocessing) return null;
  const parts = running.map((s) => {
    const prog = s.total_symbols ? ` ${s.processed_symbols ?? 0}/${s.total_symbols}` : "";
    const phase = s.phase && s.phase !== "done" ? ` - ${s.phase}` : "";
    const asof = s.asof ? ` as of ${s.asof}` : "";
    return `${tfLabel(s.timeframe)} ${s.universe}${asof} (${s.trigger})${prog}${phase}`;
  });
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-panel2/70 px-4 py-1.5 text-[11px] text-ink" data-testid="scan-activity">
      <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-forming" aria-hidden />
      <span className="font-semibold text-forming">Updating market data</span>
      {parts.length > 0 && <span data-testid="scan-activity-running">{parts.join(" | ")}</span>}
      {!parts.length && act.postprocessing && <span>building the confluence snapshot</span>}
      {waiting.length > 0 && (
        <span className="text-muted" data-testid="scan-activity-pending">
          {parts.length ? "then " : "startup check: "}
          {waiting.map(tfLabel).join(", ")}
        </span>
      )}
      <span className="ml-auto text-muted">Pages reload by themselves when it finishes - no need to press Run scan.</span>
    </div>
  );
}
