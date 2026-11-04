import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, exportUrl } from "../api/client";
import type { CompareResult, ConfigParam, EvalCase, EvalOptions, EvalResult, Metric, PatternInfo, SweepResult } from "../api/types";
import { ErrorNote, Panel } from "../components/common";
import { TIMEFRAMES } from "../lib/status";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${(v * 100).toFixed(1)}%`);
const delta = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)} pp`);

function MetricCells({ m }: { m: Metric | null | undefined }) {
  return (
    <>
      <td className="num px-2 text-right">{pct(m?.precision)}</td>
      <td className="num px-2 text-right">{pct(m?.recall)}</td>
      <td className="num px-2 text-right text-muted">
        {m ? `${m.tp} / ${m.fp} / ${m.fn}` : "-"}
        {m?.duplicates ? ` (${m.duplicates} dup)` : ""}
      </td>
    </>
  );
}

function MetricTable({ rows, patterns, testid }: { rows: Record<string, Metric>; patterns: PatternInfo[]; testid: string }) {
  const name = (k: string) => {
    const [p, tf] = k.split("|");
    return `${patterns.find((x) => x.slug === p)?.name ?? p}${tf ? ` - ${tf.toUpperCase()}` : ""}`;
  };
  return (
    <table className="w-full text-xs" data-testid={testid}>
      <thead className="text-muted">
        <tr>
          <th className="py-1 text-left font-normal">Group</th>
          <th className="px-2 text-right font-normal">Precision</th>
          <th className="px-2 text-right font-normal">Recall</th>
          <th className="px-2 text-right font-normal">TP / FP / FN</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(rows).map(([k, m]) => (
          <tr key={k} className="border-t border-line/50">
            <td className="py-1 text-ink">{name(k)}</td>
            <MetricCells m={m} />
          </tr>
        ))}
        {!Object.keys(rows).length && (
          <tr>
            <td colSpan={4} className="py-3 text-center text-muted">
              No labels in this set.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function CaseList({ cases, title }: { cases: EvalCase[]; title: string }) {
  return (
    <div>
      <div className="label mb-1">
        {title} ({cases.length})
      </div>
      <ul className="max-h-56 space-y-0.5 overflow-y-auto text-xs">
        {cases.map((c, i) => (
          <li key={`${c.label_id}-${c.detection_id}-${i}`} className="flex justify-between gap-2">
            <Link className="text-accent hover:underline" to={`/explorer/${c.symbol}?tf=${c.timeframe}&asof=${c.asof}`}>
              {c.symbol} {c.timeframe.toUpperCase()} {c.pattern} as of {c.asof}
            </Link>
            <span className="text-muted">
              {c.reason}
              {c.iou !== null ? ` IoU ${c.iou.toFixed(2)}` : ""} {c.dataset}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Evaluation() {
  const [patterns, setPatterns] = useState<PatternInfo[]>([]);
  const [params, setParams] = useState<ConfigParam[]>([]);
  const [options, setOptions] = useState<EvalOptions>({ iou_threshold: 0.5, include_forming: false, fp_scope: "labelled_patterns" });
  const [labelFilter, setLabelFilter] = useState({ timeframe: "", pattern: "", dataset: "" });
  const [labelCount, setLabelCount] = useState<number | null>(null);
  const [result, setResult] = useState<EvalResult | null>(null);
  const [compareB, setCompareB] = useState('{"converging_cr_max": 0.65}');
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [sweepParam, setSweepParam] = useState("converging_cr_max");
  const [sweepValues, setSweepValues] = useState("0.50, 0.55, 0.60, 0.65, 0.70");
  const [sweep, setSweep] = useState<SweepResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ds, setDs] = useState<"tuning" | "validation" | "all">("tuning");

  useEffect(() => {
    api.patterns().then(setPatterns).catch(() => setPatterns([]));
    api.config().then((c) => setParams(c.parameters.filter((p) => typeof p.value === "number" || typeof p.value === "boolean"))).catch(() => setParams([]));
    api.labels().then((l) => setLabelCount(l.length)).catch(() => setLabelCount(null));
  }, []);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const block = result?.metrics[ds];
  const cases = useMemo(() => (result?.cases ?? []).filter((c) => ds === "all" || c.dataset === ds), [result, ds]);

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="evaluation">
      <div className="panel flex flex-wrap items-end gap-3 px-3 py-2.5">
        <label className="flex flex-col gap-1">
          <span className="label">IoU threshold</span>
          <input type="number" min={0.05} max={1} step={0.05} className="ctl w-24" value={options.iou_threshold} onChange={(e) => setOptions({ ...options, iou_threshold: Number(e.target.value) })} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">FP scope</span>
          <select className="ctl" value={options.fp_scope} onChange={(e) => setOptions({ ...options, fp_scope: e.target.value as EvalOptions["fp_scope"] })}>
            <option value="labelled_patterns">Labelled pattern types on each chart</option>
            <option value="all">Every pattern type on labelled charts</option>
          </select>
        </label>
        <label className="flex h-8 items-center gap-2 text-xs text-muted">
          <input type="checkbox" className="accent-[#19C3DA]" checked={options.include_forming} onChange={(e) => setOptions({ ...options, include_forming: e.target.checked })} />
          Count Forming detections
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Labels: timeframe</span>
          <select className="ctl" value={labelFilter.timeframe} onChange={(e) => setLabelFilter({ ...labelFilter, timeframe: e.target.value })}>
            <option value="">All</option>
            {TIMEFRAMES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Labels: pattern</span>
          <select className="ctl w-48" value={labelFilter.pattern} onChange={(e) => setLabelFilter({ ...labelFilter, pattern: e.target.value })}>
            <option value="">All</option>
            {patterns.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <button className="btn-primary" disabled={busy} onClick={() => run(async () => setResult(await api.evaluate(options, labelFilter)))} data-testid="run-evaluation">
          {busy ? "Running..." : "Run evaluation"}
        </button>
        <div className="ml-auto flex gap-2 text-xs">
          <span className="text-muted">{labelCount ?? "-"} labels</span>
          <a className="text-accent hover:underline" href={exportUrl.labels("csv")}>
            Labels CSV
          </a>
          <a className="text-accent hover:underline" href={exportUrl.labels("json")}>
            Labels JSON
          </a>
        </div>
      </div>
      {error && <ErrorNote message={error} />}
      {labelCount === 0 && (
        <div className="panel px-4 py-4 text-sm text-muted">
          No labels yet. Open a chart in the Pattern Explorer, press <span className="text-ink">Label chart</span> and record what you see. Keep a separate validation set (different stocks, later periods) untouched while tuning.
        </div>
      )}

      {result && block && (
        <>
          <div className="flex items-center gap-2 text-xs">
            {(["tuning", "validation", "all"] as const).map((k) => (
              <button key={k} className={`chip ${ds === k ? "chip-on" : ""}`} onClick={() => setDs(k)}>
                {k === "all" ? "All labels" : `${k[0].toUpperCase()}${k.slice(1)} set`}
              </button>
            ))}
            <span className="text-muted">
              config v{result.config.version} ({result.config.hash}) - {result.label_count} labels on {result.charts_evaluated} charts - targets P &gt;= {pct(result.targets.precision)}, R &gt;= {pct(result.targets.recall)}
            </span>
            <span className="ml-auto flex gap-2">
              {(["all", "fp", "fn"] as const).map((c) => (
                <a key={c} className="text-accent hover:underline" href={exportUrl.run(result.run_id, "csv", c)}>
                  {c.toUpperCase()} CSV
                </a>
              ))}
              <a className="text-accent hover:underline" href={exportUrl.run(result.run_id, "json", "all")}>
                JSON
              </a>
            </span>
          </div>
          <div className="grid gap-3 lg:grid-cols-3">
            <Panel title="Overall">
              <MetricTable rows={block.overall} patterns={patterns} testid="metrics-overall" />
              <div className="label mb-1 mt-3">By timeframe</div>
              <MetricTable rows={block.by_timeframe} patterns={patterns} testid="metrics-timeframe" />
              <div className="label mb-1 mt-3">By F&amp;O segment</div>
              <MetricTable rows={block.by_segment} patterns={patterns} testid="metrics-segment" />
            </Panel>
            <Panel title="By pattern and timeframe" className="lg:col-span-2">
              <MetricTable rows={block.by_pattern_timeframe} patterns={patterns} testid="metrics-pattern-tf" />
              {result.notes.length > 0 && (
                <ul className="mt-2 space-y-0.5 text-[11px] text-forming">
                  {result.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
          <Panel title="Cases">
            <div className="grid gap-3 lg:grid-cols-3">
              <CaseList title="False negatives (missed)" cases={cases.filter((c) => c.type === "FN")} />
              <CaseList title="False positives" cases={cases.filter((c) => c.type === "FP")} />
              <CaseList title="True positives" cases={cases.filter((c) => c.type === "TP")} />
            </div>
            {result.skipped.length > 0 && <p className="mt-2 text-[11px] text-muted">{result.skipped.length} labels skipped (e.g. {result.skipped[0].reason}).</p>}
          </Panel>
        </>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Compare two configurations">
          <div className="space-y-2 text-xs">
            <p className="text-muted">A = current configuration. B = current plus these overrides (numeric/boolean parameters only). Nothing is deployed.</p>
            <textarea className="ctl h-16 w-full py-1 font-mono" value={compareB} onChange={(e) => setCompareB(e.target.value)} data-testid="compare-overrides" />
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  let b: Record<string, unknown>;
                  try {
                    b = JSON.parse(compareB);
                  } catch {
                    throw new Error("Overrides must be a JSON object, e.g. {\"converging_cr_max\": 0.65}");
                  }
                  setComparison(await api.compare(options, {}, b, labelFilter));
                })
              }
              data-testid="run-compare"
            >
              Compare
            </button>
            {comparison && (
              <>
                <table className="w-full" data-testid="compare-table">
                  <thead className="text-muted">
                    <tr>
                      <th className="py-1 text-left font-normal">Group ({ds})</th>
                      <th className="text-right font-normal">P A / B</th>
                      <th className="text-right font-normal">dP</th>
                      <th className="text-right font-normal">R A / B</th>
                      <th className="text-right font-normal">dR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(comparison.diff[ds]).map(([k, r]) => (
                      <tr key={k} className="border-t border-line/50">
                        <td className="py-1 text-ink">{k}</td>
                        <td className="num text-right">
                          {pct(r.a?.precision)} / {pct(r.b?.precision)}
                        </td>
                        <td className="num text-right">{delta(r.precision_delta)}</td>
                        <td className="num text-right">
                          {pct(r.a?.recall)} / {pct(r.b?.recall)}
                        </td>
                        <td className="num text-right">{delta(r.recall_delta)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="grid grid-cols-2 gap-2">
                  <CaseList title="FP added" cases={comparison.fp_added} />
                  <CaseList title="FP removed" cases={comparison.fp_removed} />
                  <CaseList title="FN added" cases={comparison.fn_added} />
                  <CaseList title="FN removed" cases={comparison.fn_removed} />
                </div>
                <p className="text-[10px] text-muted">{comparison.note}</p>
              </>
            )}
          </div>
        </Panel>
        <Panel title="Parameter sweep">
          <div className="space-y-2 text-xs">
            <div className="flex gap-2">
              <select className="ctl w-64" value={sweepParam} onChange={(e) => setSweepParam(e.target.value)} data-testid="sweep-param">
                {params.map((p) => (
                  <option key={p.name} value={p.name} title={p.description}>
                    {p.name} (now {String(p.value)})
                  </option>
                ))}
              </select>
              <input className="ctl flex-1" value={sweepValues} onChange={(e) => setSweepValues(e.target.value)} />
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const values = sweepValues
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean)
                      .map((v) => (v === "true" ? true : v === "false" ? false : Number(v)));
                    setSweep(await api.sweep(options, sweepParam, values, labelFilter));
                  })
                }
                data-testid="run-sweep"
              >
                Sweep
              </button>
            </div>
            {sweep && (
              <>
                <table className="w-full" data-testid="sweep-table">
                  <thead className="text-muted">
                    <tr>
                      <th className="py-1 text-left font-normal">{sweep.param}</th>
                      <th className="px-2 text-right font-normal">Tuning P</th>
                      <th className="px-2 text-right font-normal">Tuning R</th>
                      <th className="px-2 text-right font-normal">TP / FP / FN</th>
                      <th className="px-2 text-right font-normal">Validation P</th>
                      <th className="px-2 text-right font-normal">Validation R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sweep.rows.map((r) => (
                      <tr key={String(r.value)} className="border-t border-line/50">
                        <td className="py-1 text-ink">
                          {String(r.value)}
                          {r.is_current ? " (current)" : ""}
                        </td>
                        <MetricCells m={r.tuning} />
                        <td className="num px-2 text-right">{pct(r.validation.precision)}</td>
                        <td className="num px-2 text-right">{pct(r.validation.recall)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="text-[10px] text-muted">{sweep.note}</p>
              </>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
