import type { RSLinePayload, StageDetail, StageGate, StageMetrics } from "../api/types";
import { fmtDate, fmtNum, humanize } from "../lib/format";
import { KV, Panel } from "./common";

export const STAGE_COLOR: Record<string, string> = {
  stage2_confirmed: "text-validated",
  early_stage2: "text-accent",
  stage1: "text-forming",
  other: "text-muted",
  insufficient_history: "text-muted",
  data_unavailable: "text-down",
};

export function GateList({ gates }: { gates: StageGate[] }) {
  const groups = ["setup", "base", "stage2_confirmed", "early_stage2", "stage1"];
  const label: Record<string, string> = { setup: "Your entry setup (all must pass)", base: "Base", stage2_confirmed: "Stage 2 - Confirmed", early_stage2: "Early Stage 2", stage1: "Stage 1" };
  return (
    <div className="space-y-1.5" data-testid="stage-gates">
      {groups.map((g) => (
        <div key={g}>
          <div className={`label ${g === "setup" ? "text-accent" : ""}`}>{label[g]}</div>
          {gates
            .filter((x) => x.stage === g)
            .map((x) => (
              <div key={`${g}${x.name}`} className="flex items-start justify-between gap-2 text-[11px]" title={x.rule}>
                <span className={x.passed === true ? "text-validated" : x.passed === false ? "text-down" : "text-muted"}>
                  {x.passed === true ? "PASS" : x.passed === false ? "FAIL" : "n/a"} {humanize(x.name)}
                  {x.basis === "assumption" && <span className="ml-1 rounded border border-forming/60 px-1 text-[9px] text-forming" data-testid="assumption-tag">ASSUMPTION</span>}
                </span>
                <span className="num text-muted">{typeof x.value === "number" ? fmtNum(x.value, 3) : String(x.value ?? "-")}</span>
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

/** Base window, tight / wide label and the display-only targets (StageConfig 1.5.0). */
export function StageBaseTargets({ m }: { m: StageMetrics }) {
  return (
    <div className="mb-2" data-testid="stage-base-targets">
      <KV k="Base" v={`${m.base_duration_weeks ?? "-"} weeks${m.base_start_week ? ` since ${String(m.base_start_week).slice(0, 10)}` : ""} · ${fmtNum(m.base_range_pct, 1)}%`} />
      <KV k="Tight / wide" v={m.base_tightness ? `${m.base_tightness} (${m.tight_base_weeks ?? "-"} weeks inside 30%)` : "no valid base"} title="Tight = the stock also holds a base of 52+ weeks inside a 30% band (preferred); wide = only inside the 40% band" />
      <KV k="Target: base height" v={m.target_base_height == null ? "-" : `${fmtNum(m.target_base_height)} (${fmtNum(m.target_base_height_pct, 1)}% from close)`} title="Base high + (base high - base low). Display only - an approximation, not a forecast." />
      <KV k="Target: Weinstein swing rule" v={m.target_swing_rule == null ? "-" : `${fmtNum(m.target_swing_rule)} (${fmtNum(m.target_swing_rule_pct, 1)}%; old peak ${fmtNum(m.swing_rule_peak)})`} title="Old peak + (old peak - base low); Weinstein applies it once price betters the old peak. Display only." />
    </div>
  );
}

export function StagePanel({ stage, rs }: { stage: StageDetail | null; rs: RSLinePayload | null }) {
  if (!stage) return null;
  const m = stage.metrics;
  return (
    <Panel title="Stage Analysis (weekly)" right={<span className="text-[10px] text-muted">v{stage.config.version}</span>}>
      <div className="text-xs" data-testid="stage-panel">
        <div className={`text-sm font-semibold ${STAGE_COLOR[stage.stage] ?? ""}`} data-testid="stage-label">
          {stage.stage_label}
        </div>
        <div className={`mb-1 rounded px-2 py-1 text-[11px] ${m.setup_met ? "bg-validated/15 text-validated" : "bg-panel2 text-muted"}`} data-testid="setup-verdict">
          {m.setup_met ? "Entry setup: all checks pass" : `Entry setup not met: ${(m.setup_failed ?? []).map((x) => x.replace(/_/g, " ")).join(", ")}`}
        </div>
        <div className="mb-1 text-[11px] text-muted">
          since week of {fmtDate(stage.stage_since)}
          {stage.previous_stage ? ` (from ${humanize(stage.previous_stage)})` : ""} · latest completed week {fmtDate(m.week as string)}
        </div>
        <KV k="30W EMA / distance / direction" v={`${fmtNum(m.ema30)} / ${fmtNum(m.ema30_distance_pct, 2)}% / ${m.ema30_direction ?? "-"}`} />
        <KV k="Base high / low (current week excluded)" v={`${fmtNum(m.base_high)} / ${fmtNum(m.base_low)}`} />
        <KV k="Base range / length" v={`${fmtNum(m.base_range_pct, 2)}% ${m.base_tightness ? `(${m.base_tightness}) ` : ""}/ ${m.base_duration_weeks ?? "-"} weeks${m.base_start_week ? ` since ${String(m.base_start_week).slice(0, 10)}` : ""}`} />
        <KV k="Target: base height (display only)" v={m.target_base_height == null ? "-" : `${fmtNum(m.target_base_height)} (${fmtNum(m.target_base_height_pct, 1)}% from close)`} title="Base high + (base high - base low): the base height projected above the breakout level. An approximation, not a forecast." />
        <KV k="Target: Weinstein swing rule" v={m.target_swing_rule == null ? "-" : `${fmtNum(m.target_swing_rule)} (${fmtNum(m.target_swing_rule_pct, 1)}%; old peak ${fmtNum(m.swing_rule_peak)})`} title="Old peak + (old peak - base low). Weinstein applies it once price betters the old peak. An approximation, not a forecast." />
        <KV k="Volume: last 4W vs sideways base" v={`${fmtNum(m.volume_pickup_ratio, 2)}x (needs 1.5x)`} />
        <KV k="Base volume trend (later / earlier half)" v={`${fmtNum(m.base_volume_trend, 2)}x ${m.base_volume_drying_up ? "(drying up)" : m.base_volume_drying_up === false ? "(not drying up)" : ""}`} />
        <KV k="Distance to breakout" v={`${fmtNum(m.distance_to_breakout_pct, 2)}%`} />
        <KV k="Prior decline (assumption)" v={`${fmtNum(m.prior_decline_pct, 1)}%`} />
        <KV k="Higher lows (assumption)" v={m.higher_lows === null || m.higher_lows === undefined ? "n/a" : m.higher_lows ? "yes" : "no"} />
        <KV k={`RS ${m.rs_length ?? 52}W vs ${stage.benchmark}`} v={`${fmtNum(m.rs, 4)} (${m.rs_zero_line ?? "-"} zero, ${m.rs_trend ?? "-"} over 5W)`} />
        <KV k="RS 50 SMA" v={`${fmtNum(m.rs_sma, 4)} (${m.rs_sma_direction ?? "-"})`} />
        <KV k="Price confirmation" v={String(m.price_confirmation ?? "-")} />
        <KV k="Volume contraction / expansion" v={`${m.volume_contraction === null ? "n/a" : m.volume_contraction ? "yes" : "no"} (${fmtNum(m.volume_contraction_ratio, 2)}) / ${m.volume_expansion === null ? "n/a" : m.volume_expansion ? "yes" : "no"} (${fmtNum(m.volume_expansion_ratio, 2)}x)`} />
        {rs?.reference && <KV k="RS Line reference" v={rs.reference.label} />}
        <div className="mt-2 border-t border-line pt-2">
          <GateList gates={stage.gates} />
        </div>
        <ul className="mt-2 space-y-0.5 border-t border-line pt-2 text-[11px] text-muted" data-testid="stage-reasons">
          {stage.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        {stage.history && stage.history.length > 0 && (
          <div className="mt-2 border-t border-line pt-2 text-[11px] text-muted">
            <div className="label">Recent stage transitions</div>
            {stage.history.slice(-5).reverse().map((h) => (
              <div key={h.t}>
                {fmtDate(h.week)}: {humanize(h.from)} -&gt; {humanize(h.to)}
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-[10px] text-muted">Rules from stage_analysis.txt and rs_line.txt; ASSUMPTION marks values the source does not specify. Screening aid only.</p>
      </div>
    </Panel>
  );
}
