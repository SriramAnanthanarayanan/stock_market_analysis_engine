import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { OutcomeGroup, PatternInfo, StatusSlug, Track, TrackDetail } from "../api/types";
import { ErrorNote, Panel, StatusBadge } from "../components/common";
import { fmtDateTime, fmtNum, fmtPct, humanize } from "../lib/format";
import { TIMEFRAMES } from "../lib/status";

function OutcomeCells({ t, horizons }: { t: Track; horizons: number[] }) {
  return (
    <>
      {horizons.map((h) => {
        const o = t.outcome?.horizons?.[String(h)];
        return (
          <td key={h} className="num px-2 py-1.5 text-right" title={o?.status === "complete" ? `MFE ${fmtPct(o.mfe_pct)} / MAE ${fmtPct(o.mae_pct)}` : o ? `pending (${o.bars_available} bars)` : ""}>
            {o?.status === "complete" ? fmtPct(o.return_pct) : o ? "pending" : "-"}
          </td>
        );
      })}
    </>
  );
}

export function History() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [patterns, setPatterns] = useState<PatternInfo[]>([]);
  const [filters, setFilters] = useState({ symbol: "", pattern: "", timeframe: "", state: "", breakouts_only: false });
  const [detail, setDetail] = useState<TrackDetail | null>(null);
  const [outcomes, setOutcomes] = useState<{ horizons: number[]; min_sample: number; groups: OutcomeGroup[]; note: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const PAGE = 50;
  const pages = Math.max(1, Math.ceil(tracks.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = tracks.slice(current * PAGE, current * PAGE + PAGE);
  const horizons = outcomes?.horizons ?? [5, 10, 20];

  useEffect(() => {
    api.patterns().then(setPatterns).catch(() => setPatterns([]));
    api.outcomes().then(setOutcomes).catch(() => setOutcomes(null));
  }, []);

  useEffect(() => {
    const q = { ...filters, symbol: filters.symbol.toUpperCase() || undefined, breakouts_only: filters.breakouts_only || undefined, limit: 500 };
    api
      .tracks(q)
      .then((t) => {
        setTracks(t);
        setPage(0);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [filters]);

  const open = (t: Track) => api.track(t.track_id).then(setDetail).catch((e) => setError(String(e)));

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="history">
      <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5">
        <label className="flex flex-col gap-1">
          <span className="label">Symbol</span>
          <input className="ctl w-36" value={filters.symbol} onChange={(e) => setFilters({ ...filters, symbol: e.target.value })} placeholder="e.g. INFY" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Pattern</span>
          <select className="ctl w-52" value={filters.pattern} onChange={(e) => setFilters({ ...filters, pattern: e.target.value })}>
            <option value="">All patterns</option>
            {patterns.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Timeframe</span>
          <select className="ctl" value={filters.timeframe} onChange={(e) => setFilters({ ...filters, timeframe: e.target.value })}>
            <option value="">All</option>
            {TIMEFRAMES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Track state</span>
          <select className="ctl" value={filters.state} onChange={(e) => setFilters({ ...filters, state: e.target.value })} data-testid="history-state">
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="frozen">Frozen (after breakout)</option>
            <option value="closed">Closed</option>
          </select>
        </label>
        <label className="flex h-8 items-center gap-2 text-xs text-muted">
          <input type="checkbox" className="accent-[#19C3DA]" checked={filters.breakouts_only} onChange={(e) => setFilters({ ...filters, breakouts_only: e.target.checked })} />
          Breakouts only
        </label>
        <span className="ml-auto text-xs text-muted">{tracks.length} tracks (live scans only)</span>
      </div>
      {error && <ErrorNote message={error} />}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="panel overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left text-xs" data-testid="tracks-table">
            <thead className="bg-panel2 text-[11px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-2 py-2 font-medium">Symbol</th>
                <th className="px-2 font-medium">Pattern</th>
                <th className="px-2 font-medium">TF</th>
                <th className="px-2 font-medium">Track</th>
                <th className="px-2 font-medium">Status</th>
                <th className="px-2 font-medium">First detected</th>
                <th className="px-2 font-medium">Last seen</th>
                <th className="px-2 text-right font-medium">Quality first / last / max</th>
                <th className="px-2 font-medium">Breakout</th>
                {horizons.map((h) => (
                  <th key={h} className="px-2 text-right font-medium" title="Return in the breakout direction from the confirming close">
                    +{h} bars
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => (
                <tr key={t.track_id} className={`cursor-pointer border-t border-line/60 hover:bg-panel2 ${detail?.track.track_id === t.track_id ? "bg-panel2" : ""}`} onClick={() => open(t)} data-testid="track-row">
                  <td className="px-2 py-1.5 font-semibold text-ink">{t.symbol}</td>
                  <td className="px-2 text-ink">{t.pattern_name}</td>
                  <td className="px-2 text-muted">{t.timeframe.toUpperCase()}</td>
                  <td className="px-2 text-muted">{t.state}{t.closed_reason ? ` (${t.closed_reason})` : ""}</td>
                  <td className="px-2">
                    <StatusBadge status={t.status as StatusSlug} reason={t.status_reason} />
                  </td>
                  <td className="whitespace-nowrap px-2 text-muted">{fmtDateTime(t.first_detected_at)}</td>
                  <td className="whitespace-nowrap px-2 text-muted">{fmtDateTime(t.last_detected_at)}</td>
                  <td className="num px-2 text-right">
                    {fmtNum(t.quality_first, 0)} / {fmtNum(t.quality_last, 0)} / {fmtNum(t.quality_max, 0)}
                  </td>
                  <td className="whitespace-nowrap px-2 text-muted">{t.breakout_time ? `${t.breakout_direction} ${fmtDateTime(t.breakout_time)}` : "-"}</td>
                  <OutcomeCells t={t} horizons={horizons} />
                </tr>
              ))}
              {!tracks.length && (
                <tr>
                  <td colSpan={9 + horizons.length} className="px-3 py-8 text-center text-muted">
                    No tracks yet. Tracks are built by live full scans; run a scan or wait for the scheduler.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <footer className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
            <span data-testid="track-count">
              {tracks.length} tracks{tracks.length > PAGE ? ` - page ${current + 1} of ${pages}` : ""}
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
        <div className="flex flex-col gap-3">
          {detail ? (
            <Panel title={`Track ${detail.track.track_id}`} right={<StatusBadge status={detail.track.status as StatusSlug} />}>
              <div className="space-y-2 text-xs" data-testid="track-detail">
                <div className="text-ink">
                  {detail.track.symbol} - {detail.track.pattern_name} - {detail.track.timeframe.toUpperCase()}
                </div>
                <div className="text-muted">
                  Formation {fmtDateTime(detail.track.formation_start)} to {fmtDateTime(detail.track.formation_end)}
                </div>
                <Link className="text-accent hover:underline" to={`/explorer/${detail.track.symbol}?tf=${detail.track.timeframe}&pattern_id=${detail.track.current_detection_id}`}>
                  Open chart
                </Link>
                <div className="label pt-1">Lifecycle events</div>
                <ol className="max-h-64 space-y-1 overflow-y-auto">
                  {detail.events.map((e) => (
                    <li key={e.id} className="flex justify-between gap-2">
                      <span className="text-muted">{fmtDateTime(e.event_time)}</span>
                      <span className="text-right text-ink">
                        {humanize(e.event)}
                        {e.status && <span className="ml-1 text-muted">({e.status})</span>}
                        {e.quality !== null && <span className="ml-1 text-muted">q {fmtNum(e.quality, 0)}</span>}
                      </span>
                    </li>
                  ))}
                </ol>
                <div className="label pt-1">Quality across scans</div>
                <div className="flex flex-wrap gap-1">
                  {detail.snapshots.map((s) => (
                    <span key={`${s.scan_id}-${s.detection_id}`} className="rounded border border-line px-1 text-[10px] text-muted" title={`${s.scan_id} - ${s.status}`}>
                      {fmtNum(s.quality, 0)}
                    </span>
                  ))}
                </div>
                {detail.track.outcome && (
                  <>
                    <div className="label pt-1">Post-breakout movement (from {fmtDateTime(detail.track.outcome.breakout_time)})</div>
                    <table className="w-full">
                      <tbody>
                        {Object.entries(detail.track.outcome.horizons).map(([h, o]) => (
                          <tr key={h} className="border-t border-line/50">
                            <td className="py-0.5 text-muted">+{h} bars</td>
                            <td className="num text-right">{o.status === "complete" ? fmtPct(o.return_pct) : "pending"}</td>
                            <td className="num text-right text-muted">{o.status === "complete" ? `MFE ${fmtPct(o.mfe_pct)} / MAE ${fmtPct(o.mae_pct)}` : `${o.bars_available} bars so far`}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
                <p className="text-[10px] text-muted">{detail.note}</p>
              </div>
            </Panel>
          ) : (
            <Panel title="Track detail">
              <p className="text-xs text-muted">Select a track to see its lifecycle events, quality history and post-breakout movement.</p>
            </Panel>
          )}
        </div>
      </div>

      <Panel title="Outcome analytics (descriptive)">
        {outcomes && (
          <>
            <table className="w-full text-xs" data-testid="outcome-summary">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-left font-normal">Pattern</th>
                  <th className="text-left font-normal">TF</th>
                  <th className="text-left font-normal">Break</th>
                  <th className="text-right font-normal">Tracks</th>
                  {outcomes.horizons.map((h) => (
                    <th key={h} className="text-right font-normal">
                      +{h}: n / median / share +
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {outcomes.groups.map((g) => (
                  <tr key={`${g.pattern}-${g.timeframe}-${g.direction}`} className="border-t border-line/50">
                    <td className="py-1 text-ink">{g.pattern_name}</td>
                    <td className="text-muted">{g.timeframe.toUpperCase()}</td>
                    <td className="text-muted">{g.direction}</td>
                    <td className="num text-right">{g.tracks}</td>
                    {outcomes.horizons.map((h) => {
                      const a = g.horizons[String(h)];
                      return (
                        <td key={h} className={`num text-right ${a?.insufficient_sample ? "text-muted" : "text-ink"}`} title={a?.insufficient_sample ? `fewer than ${outcomes.min_sample} complete observations` : ""}>
                          {a && a.n ? `${a.n} / ${fmtPct(a.median_return_pct)} / ${((a.share_positive ?? 0) * 100).toFixed(0)}%${a.insufficient_sample ? " *" : ""}` : "-"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {!outcomes.groups.length && (
                  <tr>
                    <td colSpan={4 + outcomes.horizons.length} className="py-4 text-center text-muted">
                      No completed post-breakout observations yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <p className="mt-2 text-[10px] text-muted">
              * insufficient sample (fewer than {outcomes.min_sample}). {outcomes.note}
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}
