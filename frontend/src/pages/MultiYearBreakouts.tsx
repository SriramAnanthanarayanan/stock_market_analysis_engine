import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { multiYearApi, type MultiYearEvent, type MultiYearResult, type MultiYearRow, type MultiYearStatus, type MultiYearStudy, type MultiYearStudyEvent } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";
import type { ChartTimeframe } from "../lib/timeframes";

const PRESETS = [2, 3, 5, 10, 15, 20];
const STATUS_CLS: Record<MultiYearStatus, string> = { holding: "text-up", below_level: "text-forming", failed: "text-invalid" };
const STATUS_LABEL: Record<MultiYearStatus, string> = { holding: "Holding above", below_level: "Back below level", failed: "Failed" };

const pct = (x: number | null | undefined, d = 1) => (x === null || x === undefined ? "-" : `${x >= 0 ? "+" : ""}${x.toFixed(d)}%`);
const pctCls = (x: number | null | undefined) => (x === null || x === undefined ? "text-muted" : x >= 0 ? "text-up" : "text-down");
const year = (iso: string | null | undefined) => (iso ? iso.slice(0, 4) : "-");
const fwd = (e: MultiYearEvent, months: number) => e.forward?.horizons.find((h) => h.months === months) ?? null;

/** Longer bases need a longer chart: weekly up to ten years, monthly beyond (the preview shows 600 candles). */
function chartTf(years: number): ChartTimeframe {
  return years > 10 ? "1mo" : "1w";
}

/** Multi-year breakouts: closes above a resistance the stock had not exceeded for years
 *  (any number of years), and what happened after earlier ones. Backend detector and
 *  backend statistics; nothing is computed here. */
export function MultiYearBreakouts() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const years = params.get("years") ?? "5";
  const within = params.get("within") ?? "20";
  const status = params.get("status") ?? "";
  const ath = params.get("ath") ?? "";
  const fno = params.get("fno") ?? "";
  const tab = params.get("tab") ?? "recent";
  const [draft, setDraft] = useState(years);
  const [res, setRes] = useState<MultiYearResult | null>(null);
  const [study, setStudy] = useState<MultiYearStudy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => setDraft(years), [years]);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const applyYears = () => {
    const v = Number(draft);
    if (Number.isFinite(v) && v > 0 && String(v) !== years) set({ years: String(v) });
    else setDraft(years);
  };
  const athParam = ath === "" ? null : ath === "yes";
  const load = useCallback(() => {
    const q = { universe, asof: asof || null, min_years: years, fno: fno || null, all_time_high: athParam };
    const p =
      tab === "history"
        ? multiYearApi.study(q).then((r) => (setStudy(r), setError(null)))
        : multiYearApi.scan({ ...q, max_age: within, status: status || null }).then((r) => (setRes(r), setError(null)));
    p.catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, years, within, status, fno, athParam, tab]);
  useEffect(load, [load, tick]);

  const open = (r: { symbol: string; base_years: number; base_start_t: number; level: number; time: string; status: MultiYearStatus } & Partial<MultiYearRow>, extra: { k: string; v: string }[] = []) =>
    openChart({
      symbol: r.symbol,
      timeframe: chartTf(r.base_years),
      asof: asof || null,
      show: ["multiyear"],
      context: {
        title: `${r.base_years.toFixed(1)}-year breakout - ${STATUS_LABEL[r.status]}`,
        lines: [
          { k: "Resistance", v: `${fmtNum(r.level)} (exact high ${fmtBarTime(r.level_time ?? null, "1d")})` },
          { k: "Base", v: `${r.base_years.toFixed(1)} years below it, from ${fmtBarTime(r.base_start_time ?? null, "1d")}${r.base_starts_at_data_start ? " (data start)" : ""}` },
          { k: "Breakout", v: `${fmtBarTime(r.time, "1d")} · close ${fmtNum(r.close ?? null)} (${pct(r.breakout_pct)} above)` },
          { k: "Price level", v: r.all_time_high_in_data ? "all-time high within the data" : `highest since ${fmtBarTime(r.highest_since_time ?? null, "1d")} (${r.years_since_higher?.toFixed(1)} years)` },
          { k: "Resistance tests / base depth", v: `${r.zone_tests ?? "-"} · ${r.base_depth_pct?.toFixed(1) ?? "-"}% deep` },
          { k: "Breakout volume", v: r.volume_ratio ? `${fmtNum(r.volume_ratio, 2)}x the 50-session average` : "-" },
          ...extra,
        ],
        focusT: r.base_start_t,
      },
    });

  const recentCols: Column<MultiYearRow>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r, [{ k: "Since the breakout", v: `${r.age_bars} sessions · ${pct(r.return_since_pct)} · ${STATUS_LABEL[r.status]}` }])} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="inline-block max-w-[170px] truncate align-bottom text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "years", label: "Years", align: "right", render: (r) => <span className="font-semibold text-accent" data-years={r.base_years}>{r.base_years.toFixed(1)}</span>, sort: (r) => r.base_years, title: "Years the stock stayed below this resistance (from the first high within the resistance zone to the breakout)" },
    { key: "level", label: "Resistance", align: "right", render: (r) => <span title={`exact high set ${fmtBarTime(r.level_time, "1d")} (${r.level_years.toFixed(1)} years)`}>{fmtNum(r.level)}</span>, sort: (r) => r.level },
    { key: "ath", label: "Price level", render: (r) => (r.all_time_high_in_data ? <span className="text-validated" title={`all-time high within the data (since ${fmtBarTime(r.data_start, "1d")})`}>ATH</span> : <span className="text-muted" title="highest close since the stock last traded at this price">since {year(r.highest_since_time)}</span>), sort: (r) => (r.all_time_high_in_data ? 1 : 0) },
    { key: "bo", label: "Breakout", render: (r) => fmtBarTime(r.time, "1d"), sort: (r) => r.t },
    { key: "age", label: "Sessions", align: "right", render: (r) => r.age_bars, sort: (r) => r.age_bars, title: "Completed sessions since the breakout candle" },
    { key: "bopct", label: "BO %", align: "right", render: (r) => pct(r.breakout_pct, 2), sort: (r) => r.breakout_pct, title: "Breakout close above the resistance" },
    { key: "vol", label: "Vol x", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio, title: "Breakout volume / mean of the previous 50 sessions" },
    { key: "tests", label: "Tests", align: "right", render: (r) => r.zone_tests, sort: (r) => r.zone_tests, title: "Separate visits to the resistance zone during the base (the old high included)" },
    { key: "depth", label: "Base depth", align: "right", render: (r) => (r.base_depth_pct === null ? "-" : `${r.base_depth_pct.toFixed(0)}%`), sort: (r) => r.base_depth_pct },
    { key: "now", label: "Now vs level", align: "right", render: (r) => <span className={pctCls(r.above_level_pct)}>{pct(r.above_level_pct)}</span>, sort: (r) => r.above_level_pct },
    { key: "since", label: "Since BO", align: "right", render: (r) => <span className={pctCls(r.return_since_pct)}>{pct(r.return_since_pct)}</span>, sort: (r) => r.return_since_pct, title: "Latest close vs the breakout close (price return)" },
    { key: "status", label: "Status", render: (r) => <span className={`font-medium ${STATUS_CLS[r.status]}`} data-status={r.status}>{STATUS_LABEL[r.status]}{r.rebreak ? " · re-break" : ""}</span>, sort: (r) => r.status },
    { key: "past", label: "Earlier", align: "right", render: (r) => (r.past_events.length ? <span title={r.past_events.map((e) => `${fmtBarTime(e.time, "1d")}: ${e.base_years.toFixed(1)} years`).join("\n")}>{r.past_events.length}</span> : "-"), sort: (r) => r.past_events.length, title: "Earlier multi-year breakouts of this stock (any length stored)" },
  ];

  const studyCols: Column<MultiYearStudyEvent>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => open(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="inline-block max-w-[160px] truncate align-bottom text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "bo", label: "Breakout", render: (r) => fmtBarTime(r.time, "1d"), sort: (r) => r.t },
    { key: "years", label: "Years", align: "right", render: (r) => r.base_years.toFixed(1), sort: (r) => r.base_years },
    { key: "level", label: "Resistance", align: "right", render: (r) => fmtNum(r.level), sort: (r) => r.level },
    { key: "ath", label: "ATH", render: (r) => (r.all_time_high_in_data ? "ATH" : `since ${year(r.highest_since_time)}`), sort: (r) => (r.all_time_high_in_data ? 1 : 0) },
    { key: "vol", label: "Vol x", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio },
    ...[3, 6, 12, 24, 36].map((m) => ({
      key: `f${m}`,
      label: `${m}M`,
      align: "right" as const,
      render: (r: MultiYearStudyEvent) => {
        const h = fwd(r, m);
        return h?.status === "pending" ? <span className="text-muted" title="not enough data after the breakout yet">pending</span> : <span className={pctCls(h?.return_pct)}>{pct(h?.return_pct)}</span>;
      },
      sort: (r: MultiYearStudyEvent) => fwd(r, m)?.return_pct ?? null,
      title: `Price return ${m} months after the breakout close`,
    })),
    { key: "x12", label: "12M vs index", align: "right", render: (r) => <span className={pctCls(fwd(r, 12)?.excess_pct)}>{pct(fwd(r, 12)?.excess_pct)}</span>, sort: (r) => fwd(r, 12)?.excess_pct ?? null, title: "12-month return minus the benchmark index over the same window" },
    { key: "mfe", label: "Best 12M", align: "right", render: (r) => pct(r.forward?.mfe_12m_pct), sort: (r) => r.forward?.mfe_12m_pct ?? null, title: "Highest high within 12 months vs the breakout close" },
    { key: "mae", label: "Worst 12M", align: "right", render: (r) => pct(r.forward?.mae_12m_pct), sort: (r) => r.forward?.mae_12m_pct ?? null, title: "Lowest low within 12 months vs the breakout close" },
    { key: "status", label: "Status now", render: (r) => <span className={STATUS_CLS[r.status]}>{STATUS_LABEL[r.status]}</span>, sort: (r) => r.status },
  ];

  const minYears = res?.min_years ?? study?.min_years ?? Number(years);
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="multiyear-scanner">
      <TechScanHeader
        title="Multi-Year Breakouts"
        subtitle="A close above a resistance the stock had not exceeded for years - choose any number of years. The resistance is the highest high since the stock last traded at the breakout price; the base starts at the first high within the resistance zone. Completed daily candles."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={(tab === "history" ? study?.scan : res?.scan) ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <div className="flex flex-col gap-1">
          <span className="label">At least (years below the resistance)</span>
          <div className="flex flex-wrap items-center gap-1">
            {PRESETS.map((y) => (
              <button key={y} className={`chip h-8 ${Number(years) === y ? "chip-on" : ""}`} onClick={() => set({ years: String(y) })} data-testid={`my-years-${y}`}>
                {y}+ yrs
              </button>
            ))}
            <input
              className="ctl h-8 w-20"
              type="number"
              min={1}
              max={60}
              step={0.5}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={applyYears}
              onKeyDown={(e) => e.key === "Enter" && applyYears()}
              title="Any number of years (stored from 1 year)"
              data-testid="my-years"
            />
          </div>
        </div>
        {tab === "recent" && (
          <>
            <Field label="Breakout within">
              <select className="ctl" value={within} onChange={(e) => set({ within: e.target.value === "20" ? "" : e.target.value })} data-testid="my-within">
                <option value="0">Latest session</option>
                <option value="5">5 sessions</option>
                <option value="20">20 sessions (~1 month)</option>
                <option value="60">60 sessions (~3 months)</option>
                <option value="250">250 sessions (~1 year)</option>
              </select>
            </Field>
            <Field label="Status">
              <select className="ctl" value={status} onChange={(e) => set({ status: e.target.value })} data-testid="my-status">
                <option value="">Holding / back below</option>
                <option value="holding">Holding above</option>
                <option value="below_level">Back below level</option>
                <option value="failed">Failed</option>
                <option value="holding,below_level,failed">All</option>
              </select>
            </Field>
          </>
        )}
        <Field label="Price level">
          <select className="ctl" value={ath} onChange={(e) => set({ ath: e.target.value })} data-testid="my-ath">
            <option value="">Any</option>
            <option value="yes">All-time high (in data)</option>
            <option value="no">Below an older peak</option>
          </select>
        </Field>
        <Field label="F&O">
          <select className="ctl" value={fno} onChange={(e) => set({ fno: e.target.value })}>
            <option value="">All</option>
            <option value="YES">F&amp;O only</option>
            <option value="NO">Non-F&amp;O</option>
          </select>
        </Field>
        <span className="text-xs text-muted" data-testid="result-count">
          {tab === "history" ? `${study?.events_total ?? 0} past breakouts` : `${res?.count ?? 0} results`}
        </span>
      </TechScanHeader>
      <div className="flex gap-1">
        <button className={`chip h-8 ${tab !== "history" ? "chip-on" : ""}`} onClick={() => set({ tab: "" })} data-testid="my-tab-recent">
          Recent breakouts
        </button>
        <button className={`chip h-8 ${tab === "history" ? "chip-on" : ""}`} onClick={() => set({ tab: "history" })} data-testid="my-tab-history">
          History - what happened next
        </button>
      </div>
      {error && <ErrorNote message={error} />}
      {tab !== "history" ? (
        <>
          {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
          <DataTable rows={res?.items ?? []} columns={recentCols} testid="multiyear-table" onRow={(r) => open(r)} empty={`No close above a ${minYears}+ year resistance in this window of the latest daily scan.`} initialSort={{ key: "bo", desc: true }} />
        </>
      ) : (
        <StudyView study={study} cols={studyCols} minYears={minYears} />
      )}
    </div>
  );
}

function StudyView({ study, cols, minYears }: { study: MultiYearStudy | null; cols: Column<MultiYearStudyEvent>[]; minYears: number }) {
  if (!study) return null;
  return (
    <div className="flex flex-col gap-3" data-testid="multiyear-study">
      <div className="rounded-md border border-forming/40 bg-forming/5 px-3 py-2 text-[11px] text-forming" data-testid="multiyear-caveats">
        <div className="mb-1 font-semibold">Read this first - descriptive, not a success rate or a probability</div>
        <ul className="list-disc space-y-0.5 pl-4">
          {study.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </div>
      {study.note && <div className="text-[11px] text-muted">{study.note}</div>}
      <div className="panel overflow-x-auto">
        <table className="w-full text-xs" data-testid="multiyear-summary">
          <thead>
            <tr className="text-left text-muted">
              <th className="px-3 py-2">After the breakout ({minYears}+ year bases)</th>
              <th className="px-3 py-2 text-right">Events</th>
              <th className="px-3 py-2 text-right">Median</th>
              <th className="px-3 py-2 text-right">Mean</th>
              <th className="px-3 py-2 text-right">Positive</th>
              <th className="px-3 py-2 text-right">Median vs {study.benchmark ?? "index"}</th>
              <th className="px-3 py-2 text-right">Beat {study.benchmark ?? "index"}</th>
              <th className="px-3 py-2 text-right">Too recent</th>
            </tr>
          </thead>
          <tbody>
            {study.summary.map((s) => (
              <tr key={s.months} className="border-t border-line" data-months={s.months}>
                <td className="px-3 py-1.5 text-ink">{s.months} {s.months === 1 ? "month" : "months"}</td>
                <td className="num px-3 py-1.5 text-right">{s.n}</td>
                <td className={`num px-3 py-1.5 text-right ${pctCls(s.median)}`}>{pct(s.median)}</td>
                <td className={`num px-3 py-1.5 text-right ${pctCls(s.mean)}`}>{pct(s.mean)}</td>
                <td className="num px-3 py-1.5 text-right">{s.pct_positive === null ? "-" : `${s.pct_positive.toFixed(0)}%`}</td>
                <td className={`num px-3 py-1.5 text-right ${pctCls(s.excess_median)}`}>{pct(s.excess_median)}</td>
                <td className="num px-3 py-1.5 text-right">{s.pct_beat_benchmark === null ? "-" : `${s.pct_beat_benchmark.toFixed(0)}% of ${s.excess_n}`}</td>
                <td className="num px-3 py-1.5 text-right text-muted">{s.pending}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="border-t border-line px-3 py-2 text-[11px] text-muted">
          {study.events_total ?? 0} breakouts in {study.stocks ?? 0} stocks. Within 12 months (complete windows): median best move {pct(study.mfe_12m?.median ?? null)}, median worst move {pct(study.mae_12m?.median ?? null)}.
        </div>
      </div>
      <DataTable rows={study.items} columns={cols} testid="multiyear-history-table" empty={`No stored ${minYears}+ year breakout in the history of this universe.`} initialSort={{ key: "bo", desc: true }} />
    </div>
  );
}
