import type { ConfluenceCategory, ConfluenceCombo, ConfluenceRow, EvidenceUse } from "../api/advanced";
import { fmtBarTime, fmtNum } from "../lib/format";
import { KV, Panel } from "./common";

const BAND_CLS: Record<string, string> = {
  "Very High Confluence": "text-up border-up/50 bg-up/10",
  "High Confluence": "text-validated border-validated/50 bg-validated/10",
  "Moderate Confluence": "text-forming border-forming/50 bg-forming/10",
  "Low Confluence": "text-muted border-line",
};

/** Presentation band (spec 8.6) - not a calibrated probability. */
export function BandChip({ band }: { band: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded border px-1.5 text-[10px] font-semibold ${BAND_CLS[band] ?? "text-muted border-line"}`} title={`${band} (presentation band, not a probability)`} data-band={band}>
      {band.replace(" Confluence", "")}
    </span>
  );
}

export const COMBO_LABEL: Record<NonNullable<ConfluenceCombo["type"]>, string> = {
  breakout_marubozu_volume: "Fresh breakout + Marubozu + volume shock",
  breakout_volume: "Fresh breakout + volume shock",
  breakout_marubozu: "Fresh breakout + Marubozu",
};

export function ScoreCell({ row }: { row: ConfluenceRow }) {
  return (
    <span className="inline-flex items-center gap-2" title={`Base ${row.base_score.toFixed(1)} + combination ${row.bonus.toFixed(0)} (capped at 100); coverage ${(row.coverage * 100).toFixed(0)}%${row.ranked ? "" : " - below the minimum coverage, not ranked"}`}>
      <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-page">
        <span className={`absolute inset-y-0 left-0 rounded-full ${row.ranked ? "bg-accent/80" : "bg-invalid"}`} style={{ width: `${Math.max(0, Math.min(100, row.score))}%` }} />
      </span>
      <span className={`num font-semibold ${row.ranked ? "text-ink" : "text-muted"}`} data-testid="cf-score">
        {row.score.toFixed(1)}
      </span>
      {row.bonus > 0 && (
        <span className="rounded bg-up/15 px-1 text-[10px] font-semibold text-up" title={row.combo?.type ? COMBO_LABEL[row.combo.type] : "combination bonus"} data-testid="cf-combo-badge">
          +{row.bonus.toFixed(0)}
        </span>
      )}
      {!row.ranked && <span className="text-[10px] text-muted">unranked</span>}
    </span>
  );
}

const USE_CLS: Record<EvidenceUse, string> = {
  used: "text-up border-up/40",
  bonus: "text-accent border-accent/40",
  overlapping: "text-muted border-line",
  expired: "text-invalid border-invalid/40",
  invalidated: "text-down border-down/40",
};
const USE_LABEL: Record<EvidenceUse, string> = {
  used: "counted",
  bonus: "capped bonus",
  overlapping: "overlap - not added",
  expired: "expired",
  invalidated: "invalidated",
};

function CategoryBlock({ c, timeframe }: { c: ConfluenceCategory; timeframe: string }) {
  return (
    <div className="flex flex-col gap-1 border-t border-line/60 pt-2" data-testid={`breakdown-cat-${c.key}`} data-available={c.available ? "1" : "0"}>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium text-ink">
          {c.label} <span className="text-[10px] text-muted">weight {c.weight}</span>
        </span>
        <span className="num text-right text-muted">{c.available ? `${(c.score * 100).toFixed(0)}% -> ${fmtNum(c.points, 1)} pts` : "unavailable (no data)"}</span>
      </div>
      <div className="relative h-1 overflow-hidden rounded-full bg-page">
        {c.available && <span className="absolute inset-y-0 left-0 rounded-full bg-accent/70" style={{ width: `${c.score * 100}%` }} />}
      </div>
      <div className="text-[10px] leading-snug text-muted">{c.rule}</div>
      {c.parts && (
        <div className="text-[10px] text-muted">
          volume strength {fmtNum(c.parts.volume, 2)} · candle strength {fmtNum(c.parts.candle, 2)}
        </div>
      )}
      <ul className="flex flex-col gap-1">
        {c.evidence.map((e, i) => (
          <li key={`${e.evidence_id}-${e.source}-${i}`} className="flex flex-col text-[11px]" data-testid="breakdown-evidence" data-status={e.status} data-source={e.source}>
            <div className="flex items-baseline gap-1.5">
              <span className={`shrink-0 rounded border px-1 text-[9px] font-semibold uppercase ${USE_CLS[e.status]}`}>{USE_LABEL[e.status]}</span>
              <span className={e.status === "used" || e.status === "bonus" ? "text-ink" : "text-muted"}>{e.label}</span>
              <span className="num ml-auto shrink-0 text-muted" title="evidence strength 0-1">
                {e.strength.toFixed(2)}
              </span>
            </div>
            {(e.note || e.time) && (
              <div className="pl-1 text-[10px] text-muted">
                {e.time ? fmtBarTime(e.time, timeframe) : ""}
                {e.time && e.note ? " · " : ""}
                {e.note}
              </div>
            )}
          </li>
        ))}
        {!c.evidence.length && <li className="text-[10px] text-muted">{c.available ? "No evidence in this category." : "The data this category needs is missing (not scored as zero)."}</li>}
      </ul>
    </div>
  );
}

/** Why a stock scored what it scored: every category, every piece of evidence and
 *  whether it counted, was a capped bonus, overlapped, expired or was invalidated. */
export function ConfluenceBreakdown({ row, minCoverage }: { row: ConfluenceRow; minCoverage: number }) {
  return (
    <Panel title={`Evidence breakdown (${row.direction})`} right={<BandChip band={row.band} />}>
      <div className="flex flex-col gap-2" data-testid="confluence-breakdown" data-symbol={row.symbol}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="num text-2xl font-semibold text-ink" data-testid="breakdown-score">
            {row.score.toFixed(1)}
          </span>
          <span className="text-right text-[11px] text-muted">
            base {row.base_score.toFixed(1)} + combination {row.bonus.toFixed(0)} (total capped at 100)
          </span>
        </div>
        <div>
          <KV k="Coverage" v={`${(row.coverage * 100).toFixed(0)}% of the weight`} title="Share of the total category weight with available data; the score is normalized over these categories" />
          {!row.ranked && <div className="text-[11px] text-forming">Below the minimum coverage of {(minCoverage * 100).toFixed(0)}%: shown, not ranked.</div>}
          <KV k="Active signals" v={row.active_signals} title="Evidence that counted (fully or as a capped bonus)" />
          <KV k="Latest signal" v={fmtBarTime(row.latest_signal_time, row.timeframe)} />
        </div>
        {row.combo?.type ? (
          <div className="rounded border border-up/40 bg-up/10 px-2 py-1 text-[11px] text-up" data-testid="breakdown-combo">
            {COMBO_LABEL[row.combo.type]} on the same session ({fmtBarTime(row.combo.session, row.timeframe)}): +{row.combo.points} points
          </div>
        ) : (
          <div className="text-[10px] text-muted">No same-session breakout + Marubozu / volume shock combination.</div>
        )}
        {row.explanation.map((c) => (
          <CategoryBlock key={c.key} c={c} timeframe={row.timeframe} />
        ))}
        <p className="border-t border-line/60 pt-2 text-[10px] leading-snug text-muted">
          Explainable screening aid built from the stored scanner results; weights and bands are placeholders, not calibrated probabilities. Analytical filter, not a trade recommendation.
        </p>
      </div>
    </Panel>
  );
}
