import type { StageContext, VcpRow } from "../api/advanced";
import { fmtBarTime, fmtNum, fmtVolume, humanize } from "../lib/format";
import { KV, Panel } from "./common";

const STATUS_CLS: Record<string, string> = {
  breakout: "text-up",
  breakout_pending: "text-up",
  near_pivot: "text-accent",
  tightening: "text-validated",
  pivot_cross_low_volume: "text-forming",
  forming: "text-forming",
  developing: "text-developing",
  failed_breakout: "text-invalid",
  invalidated: "text-invalid",
};

export function VcpStatus({ v }: { v: Pick<VcpRow, "status" | "status_label" | "status_reason"> }) {
  return (
    <span className={`whitespace-nowrap text-xs font-medium ${STATUS_CLS[v.status] ?? "text-ink"}`} title={v.status_reason ?? v.status_label} data-vcp-status={v.status}>
      {v.status_label}
    </span>
  );
}

export function StageChip({ st }: { st?: StageContext | null }) {
  if (!st) return <span className="text-[11px] text-muted" title="No weekly Stage Analysis result for this stock/date">no stage</span>;
  const cls = st.stage === "stage2_confirmed" || st.stage === "early_stage2" ? "text-validated" : st.stage === "stage1" ? "text-accent" : "text-muted";
  return (
    <span className={`text-[11px] ${cls}`} title={`Your Stage Analysis (latest weekly scan) since ${fmtBarTime(st.stage_since, "1w")}; context only`}>
      {st.stage_label}
    </span>
  );
}

/** Full VCP explanation: why it qualified (or not), contractions, volume, pivot, score. */
export function VcpPanel({ v, stage, compact = false }: { v: VcpRow; stage?: StageContext | null; compact?: boolean }) {
  const m = v.metrics ?? {};
  const cl = v.contraction_list;
  return (
    <Panel title="VCP (daily)" right={<VcpStatus v={v} />}>
      <div data-testid="vcp-panel">
        <KV k="Quality (fit score, not a probability)" v={`${v.quality.toFixed(0)} / 100`} />
        <KV k="Contractions" v={v.depths_pct.map((d, i) => `T${i + 1} ${d.toFixed(1)}%`).join(" > ")} />
        <KV k="Pivot (final contraction high)" v={`${fmtNum(v.pivot)}${v.pivot_distance_pct === null ? "" : v.pivot_distance_pct >= 0 ? ` · ${v.pivot_distance_pct.toFixed(2)}% above close` : ` · close ${(-v.pivot_distance_pct).toFixed(2)}% above`}`} />
        <KV k="Base" v={`${fmtBarTime(v.base_start, "1d")} - ${fmtBarTime(v.base_end, "1d")} (${v.base_sessions} sessions)`} />
        <KV k="Final / T1 volume" v={fmtNum((m.volume_trend_final_vs_first as number | null) ?? null, 2)} />
        <KV k="Final volume / prior 50-day avg" v={fmtNum((m.final_volume_vs_avg50 as number | null) ?? null, 2)} />
        <KV k="Dry-up days in final contraction" v={String(m.dry_up_days_final ?? "-")} />
        <KV k="Preceding advance" v={`${fmtNum((m.prior_advance_pct as number | null) ?? null, 1)}%`} />
        {v.breakout_time && <KV k="Breakout candle" v={fmtBarTime(v.breakout_time, "1d")} />}
        <KV k="Stage Analysis (context)" v={<StageChip st={stage ?? v.stage} />} />
        {v.status_reason && <p className="mt-1 text-[11px] text-forming">{v.status_reason}</p>}
        {!compact && cl && cl.length > 0 && (
          <table className="mt-2 w-full text-[11px]" data-testid="vcp-contractions">
            <thead className="text-muted">
              <tr>
                <th className="text-left font-normal">T</th>
                <th className="text-left font-normal">High → low</th>
                <th className="text-right font-normal">Depth</th>
                <th className="text-right font-normal">Bars</th>
                <th className="text-right font-normal">Avg vol</th>
                <th className="text-right font-normal">vs 50D</th>
              </tr>
            </thead>
            <tbody>
              {cl.map((c) => (
                <tr key={c.seq} className="border-t border-line/50">
                  <td className="py-0.5 text-ink">{c.label}</td>
                  <td className="text-muted">
                    {fmtNum(c.start_price)} → {fmtNum(c.end_price)}
                    {c.provisional ? " *" : ""}
                  </td>
                  <td className="num text-right text-ink">{c.depth_pct.toFixed(1)}%</td>
                  <td className="num text-right">{c.bars}</td>
                  <td className="num text-right">{fmtVolume(c.avg_volume)}</td>
                  <td className="num text-right">{fmtNum(c.volume_vs_avg50, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!compact && (
          <>
            <div className="label mt-3">Score breakdown</div>
            <ul className="mt-1 space-y-1" data-testid="vcp-score">
              {v.quality_components.map((c) => (
                <li key={c.name} className="text-[11px]" title={c.formula}>
                  <div className="flex justify-between">
                    <span className="text-ink">{humanize(c.name)}</span>
                    <span className="num text-muted">
                      {(c.score * 100).toFixed(0)}% x w{c.weight}
                    </span>
                  </div>
                  <div className="mt-0.5 h-1 rounded-full bg-page">
                    <div className="h-1 rounded-full bg-accent/70" style={{ width: `${c.score * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
            <div className="label mt-3">Why</div>
            <ul className="mt-1 space-y-0.5 text-[11px]" data-testid="vcp-explanation">
              {v.explanation.map((line, i) => (
                <li key={i} className={line.startsWith("FAIL") ? "text-down" : line.startsWith("PASS") ? "text-ink" : "text-muted"}>
                  {line}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] text-muted">* provisional final low (not yet a confirmed swing low). Synthetic tests only; real-chart VCP accuracy is not validated.</p>
          </>
        )}
      </div>
    </Panel>
  );
}
