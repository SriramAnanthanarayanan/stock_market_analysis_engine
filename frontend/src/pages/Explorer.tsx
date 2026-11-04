import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChartDetection, ChartResponse, LineElement, Match, MarkerElement, PatternInfo, StatusSlug, Timeframe } from "../api/types";
import type { OverlayShape } from "../charts/OverlayPrimitive";
import { LabelPanel } from "../components/LabelPanel";
import { PatternChart } from "../charts/PatternChart";
import { useChartTerminal } from "../charts/useChartTerminal";
import { ChartTerminalBar } from "../components/ChartTerminalBar";
import { DataSourcePanel, TechnicalPanel } from "../components/TechnicalPanel";
import { StagePanel } from "../components/StagePanel";
import { VcpPanel } from "../components/VcpPanel";
import { ErrorNote, FnoBadge, KV, Panel, QualityCell, StatusBadge } from "../components/common";
import { fmtBarTime, fmtNum, fmtPct, humanize } from "../lib/format";
import { CATEGORY_LABELS, TIMEFRAMES } from "../lib/status";

type Toggles = { overlay: boolean; swings: boolean; sr: boolean };

function metric(d: ChartDetection, k: string): number | null {
  const v = d.metrics[k];
  return typeof v === "number" ? v : null;
}

function MetricsPanel({ d }: { d: ChartDetection }) {
  const tf = d.timeframe;
  const m = (k: string, digits = 2) => fmtNum(metric(d, k), digits);
  let body: JSX.Element;
  if (d.family === "converging") {
    body = (
      <>
        <KV k="Width start / end (W0 / W1)" v={`${m("width_start")} / ${m("width_end")}`} />
        <KV k="Convergence ratio CR" v={m("convergence_ratio", 3)} />
        <KV k="Line states (upper / lower)" v={`${d.metrics.upper_state} / ${d.metrics.lower_state}`} />
        <KV k="Apex (bars after formation end)" v={m("apex_bars_after_formation_end", 1)} />
        <KV k="Apex price" v={m("apex_price")} />
      </>
    );
  } else if (d.family === "parallel") {
    body = (
      <>
        <KV k="Mean width (independent fit)" v={m("width_mean")} />
        <KV k="Drawn width (shared slope)" v={m("width_drawn")} />
        <KV k="Parallelism error" v={m("parallelism_error", 3)} />
        <KV k="Movement M / W_mean" v={m("movement_vs_mean_width", 3)} />
        <KV k="Close OLS R²" v={m("close_r2", 3)} />
      </>
    );
  } else if (d.family === "continuation") {
    body = (
      <>
        <KV k="Pole" v={`${d.metrics.pole_bars} bars, H ${m("pole_height")} (${m("pole_move_pct", 1)}%)`} />
        <KV k="Pole height / ATR at start" v={m("pole_height_atr", 2)} />
        <KV k="Directional pole closes" v={`${((metric(d, "pole_directional_closes") ?? 0) * 100).toFixed(0)}%`} />
        <KV k="Consolidation" v={`${d.metrics.consolidation_bars} bars`} />
        <KV k="Retracement of pole" v={`${((metric(d, "retracement") ?? 0) * 100).toFixed(1)}%`} />
        <KV k="CR (W1 / W0)" v={m("convergence_ratio", 3)} />
        <KV k="Movement / H" v={m("movement_vs_pole", 3)} />
        <KV k="Consolidation / pole volume" v={m("consolidation_pole_volume_ratio", 2)} />
        {d.metrics.apex_bars_after_formation_end !== undefined && <KV k="Apex (bars after end)" v={m("apex_bars_after_formation_end", 1)} />}
        {d.metrics.provisional ? <KV k="Provisional" v="3-4 bar consolidation (Forming)" /> : null}
      </>
    );
  } else {
    const hs = d.pattern.includes("head");
    body = hs ? (
      <>
        <KV k="Head height HH" v={`${m("head_height")} (${m("head_height_atr", 2)} ATR)`} />
        <KV k="Head prominence" v={m("head_prominence")} />
        <KV k="Shoulder difference / allowed" v={`${m("shoulder_difference")} / ${m("allowed_shoulder_difference")}`} />
        <KV k="Time symmetry LS-H / H-RS" v={m("time_symmetry_ratio", 2)} />
        <KV k="Neckline tilt |T1 - T2|" v={m("neckline_tilt")} />
        <KV k="Neckline at head" v={m("neckline_at_head")} />
        <KV k="Prior trend move" v={m("prior_trend_move")} />
        {d.metrics.provisional_pivot ? <KV k="Right shoulder" v="unconfirmed (Forming)" /> : null}
      </>
    ) : (
      <>
        <KV k="P1 / P2" v={`${m("p1_price")} / ${m("p2_price")}`} />
        <KV k="Neckline (T)" v={m("neckline")} />
        <KV k="Depth D" v={`${m("depth")} (${m("depth_atr", 2)} ATR)`} />
        <KV k="Peak difference / allowed" v={`${m("peak_difference")} / ${m("allowed_difference")}`} />
        <KV k="Separation" v={`${d.metrics.separation} bars`} />
        <KV k="Prior trend move" v={m("prior_trend_move")} />
        <KV k="P2 / P1 volume" v={m("p2_p1_volume_ratio", 2)} />
        {d.metrics.provisional_pivot ? <KV k="P2" v="unconfirmed (Forming)" /> : null}
      </>
    );
  }
  const reversal = d.family === "reversal";
  return (
    <Panel title="Pattern metrics">
      <KV k="Formation start" v={fmtBarTime(d.formation_start, tf)} />
      <KV k={reversal ? "Formation end (latest pivot)" : "Formation end (last fitted bar)"} v={fmtBarTime(d.formation_end, tf)} />
      <KV k="Formation length" v={`${d.formation_length} bars`} />
      {!reversal && <KV k="Touches (upper / lower)" v={`${d.upper_touches ?? "-"} / ${d.lower_touches ?? "-"}`} />}
      {body}
      <KV k={reversal ? "Pattern height (% of price)" : "Width (% of close)"} v={d.width_pct === null ? "-" : `${d.width_pct.toFixed(2)}%`} />
      <KV k={reversal ? "Prior trend (%)" : d.family === "continuation" ? "Pole move" : "Midline move"} v={fmtPct(d.move_pct)} />
      {!reversal && metric(d, "containment") !== null && <KV k="Containment" v={`${((metric(d, "containment") ?? 0) * 100).toFixed(1)}%`} />}
      <KV k="ATR(14) at formation end" v={m("atr_at_formation_end")} />
      <KV k="ATR(14) latest" v={m("atr_latest")} />
      <KV k={reversal ? "Neckline now" : "Upper / lower now"} v={reversal ? fmtNum(metric(d, "upper_now") ?? metric(d, "lower_now")) : `${m("upper_now")} / ${m("lower_now")}`} />
      <KV k="Volume ratio" v={d.volume_ratio === null ? "-" : d.volume_ratio.toFixed(2)} title="Latest completed bar volume / mean formation volume" />
      <KV k="Config" v={`v${d.config_version} (${d.config_hash})`} />
    </Panel>
  );
}

function QualityPanel({ d }: { d: ChartDetection }) {
  return (
    <Panel title="Quality breakdown" right={<QualityCell score={d.quality} label={d.quality_label} />}>
      <ul className="space-y-2" data-testid="quality-breakdown">
        {d.quality_components.map((c) => (
          <li key={c.name} className="text-xs" title={c.formula}>
            <div className="flex justify-between">
              <span className="text-ink">{humanize(c.name)}</span>
              <span className="num text-muted">
                {(c.score * 100).toFixed(0)}% x w{c.weight}
              </span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-page">
              <div className="h-1.5 rounded-full bg-accent/70" style={{ width: `${c.score * 100}%` }} />
            </div>
            <div className="mt-0.5 text-[10px] text-muted">{c.formula}</div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-muted">Geometric fit score with placeholder weights - not a probability of success.</p>
    </Panel>
  );
}

function LifecyclePanel({ d }: { d: ChartDetection }) {
  return (
    <Panel title="Lifecycle" right={<StatusBadge status={d.status as StatusSlug} reason={d.status_reason} />}>
      <ol className="space-y-1.5 text-xs" data-testid="lifecycle-events">
        {d.lifecycle.map((e, i) => (
          <li key={`${e.time}-${i}`} className="flex items-start justify-between gap-2">
            <span className="text-muted">{fmtBarTime(e.time, d.timeframe)}</span>
            <span className="text-right">
              <span className="text-ink">{humanize(e.event)}</span>
              {e.status && (
                <span className="ml-1.5">
                  <StatusBadge status={e.status as StatusSlug} />
                </span>
              )}
              {"reason" in e.detail && e.detail.reason ? <div className="text-[10px] text-muted">reason: {String(e.detail.reason)}</div> : null}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[10px] text-muted">
        Status uses all completed bars with frozen geometry; the History page follows the formation across scans.
      </p>
    </Panel>
  );
}

export function Explorer() {
  const { symbol = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // The dashboard passes its own URL (filters included) so "Scanner" returns to it.
  const backTo = (location.state as { from?: string } | null)?.from ?? "/";
  const [params, setParams] = useSearchParams();
  const timeframe = (params.get("tf") as Timeframe) || "1d";
  const patternId = params.get("pattern_id");
  const asof = params.get("asof");
  const [data, setData] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [toggles, setToggles] = useState<Toggles>({ overlay: true, swings: false, sr: false });
  const [resetKey, setResetKey] = useState(0);
  const [latestKey, setLatestKey] = useState(0);
  const [related, setRelated] = useState<Match[]>([]);
  const [patternList, setPatternList] = useState<PatternInfo[]>([]);
  const [labelOpen, setLabelOpen] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [lastClick, setLastClick] = useState<{ t: number; price: number; n: number } | null>(null);
  const [labelPreview, setLabelPreview] = useState<OverlayShape[]>([]);
  const onLabelShapes = useCallback((sh: OverlayShape[]) => setLabelPreview(sh), []);
  const onCaptureChange = useCallback((c: boolean) => setCapturing(c), []);

  useEffect(() => {
    api.patterns().then(setPatternList).catch(() => setPatternList([]));
  }, []);

  useEffect(() => {
    // Selecting another detection of the loaded chart only changes the URL.
    if (data && data.symbol === symbol.toUpperCase() && data.timeframe === timeframe && (data.asof ?? null) === (asof ?? null)) {
      if (!patternId || data.detections.some((d) => d.pattern_id === patternId)) {
        if (patternId) setSelected(patternId);
        return;
      }
    }
    let cancelled = false;
    setLoading(true);
    api
      .chart(symbol, timeframe, patternId, asof)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setSelected(d.selected_detection_id);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe, patternId, asof]);

  useEffect(() => {
    api
      .history(symbol, 200)
      .then((h) => setRelated(h.items))
      .catch(() => setRelated([]));
  }, [symbol]);

  const det = data?.detections.find((d) => d.pattern_id === selected) ?? null;
  const termSrc = useMemo(
    () => (data ? { symbol: data.symbol, timeframe: data.timeframe, asof: data.asof, scanId: data.scan_id, candles: data.candles } : null),
    [data],
  );
  const term = useChartTerminal("stock", termSrc, params.get("show"));
  const chartShapes = useMemo(
    () => [...(labelOpen ? labelPreview : []), ...term.stageOverlay.shapes, ...term.vcpOverlay.shapes],
    [labelOpen, labelPreview, term.stageOverlay, term.vcpOverlay],
  );

  const setTf = (tf: Timeframe) => {
    const next = new URLSearchParams(params);
    next.set("tf", tf);
    next.delete("pattern_id"); // a detection belongs to one timeframe; never reuse coordinates
    setParams(next, { state: location.state });
  };
  const select = (id: string) => {
    setSelected(id);
    const next = new URLSearchParams(params);
    next.set("pattern_id", id);
    setParams(next, { replace: true, state: location.state });
  };

  const markers = useMemo(
    () =>
      (det?.geometry.elements.filter((e) => e.type === "marker") as MarkerElement[] | undefined)?.filter((m) =>
        ["touch_upper", "touch_lower", "swing_high", "swing_low"].includes(m.role),
      ) ?? [],
    [det],
  );
  const lines = (det?.geometry.elements.filter((e) => e.type === "line") as LineElement[] | undefined) ?? [];
  const otherTf = useMemo(() => {
    const seen = new Set<string>();
    return related.filter((m) => {
      if (m.timeframe === timeframe || seen.has(`${m.timeframe}:${m.pattern}`)) return false;
      seen.add(`${m.timeframe}:${m.pattern}`);
      return true;
    });
  }, [related, timeframe]);

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="explorer">
      <div className="panel flex flex-wrap items-center gap-x-5 gap-y-2 px-3 py-2.5">
        <button className="text-xs text-muted hover:text-ink" onClick={() => navigate(backTo)} data-testid="back">
          &larr; Scanner
        </button>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-ink" data-testid="explorer-symbol">
              {symbol.toUpperCase()}
            </h1>
            {data && <FnoBadge value={data.fno_status} />}
          </div>
          <div className="text-xs text-muted">{data?.company ?? ""}</div>
        </div>
        {det && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-ink" data-testid="explorer-pattern">
              {det.pattern_name}
            </span>
            <span className="text-xs text-muted">{CATEGORY_LABELS[det.category]}</span>
            <span className="text-xs text-muted">{timeframe.toUpperCase()}</span>
            <StatusBadge status={det.status as StatusSlug} reason={det.status_reason} />
            <QualityCell score={det.quality} label={det.quality_label} />
          </div>
        )}
        <div className="ml-auto text-right text-[11px] text-muted">
          {data?.source === "adhoc" ? "Computed on demand (no stored scan)" : data?.scan_id ? `Scan ${data.scan_id}` : ""}
          {data?.data_cutoff && <div>Data cutoff {fmtBarTime(data.data_cutoff, "1h")}</div>}
          {asof && <div className="text-forming">As-of {asof}: only candles completed by then</div>}
        </div>
      </div>

      <div className="panel flex flex-wrap items-center gap-2 px-3 py-2" data-testid="chart-toolbar">
        <div className="flex gap-1" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <button key={t.value} className={`chip ${t.value === timeframe ? "chip-on" : ""}`} onClick={() => setTf(t.value)} data-testid={`explorer-tf-${t.value}`}>
              {t.label}
            </button>
          ))}
        </div>
        <span className="mx-1 h-5 w-px bg-line" />
        {(
          [
            ["overlay", "Pattern overlay"],
            ["swings", "Swing points"],
            ["sr", "S/R zones"],
          ] as [keyof Toggles, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            className={`chip ${toggles[k] ? "chip-on" : ""}`}
            onClick={() => setToggles((t) => ({ ...t, [k]: !t[k] }))}
            title={k === "sr" ? data?.sr_zones_note ?? "" : `Show/hide ${label.toLowerCase()}`}
            data-testid={`toggle-${k}`}
          >
            {label}
          </button>
        ))}
        <button
          className={`chip ${term.prefs.showVolume ? "chip-on" : ""}`}
          onClick={() => term.setPrefs({ ...term.prefs, showVolume: !term.prefs.showVolume })}
          title="Show/hide the volume pane"
          data-testid="toggle-volume"
        >
          Volume
        </button>
        <span className="mx-1 h-5 w-px bg-line" />
        <button className={`chip h-7 ${labelOpen ? "chip-on" : ""}`} onClick={() => setLabelOpen((x) => !x)} data-testid="toggle-label">
          Label chart
        </button>
        {capturing && <span className="text-xs text-forming">Click a candle on the chart...</span>}
        {loading && <span className="text-xs text-muted">Loading...</span>}
        {term.message && <span className="text-xs text-accent" data-testid="terminal-message">{term.message}</span>}
        <div className="w-full">
          <ChartTerminalBar
            prefs={term.prefs}
            setPrefs={term.setPrefs}
            tool={labelOpen ? "none" : term.tool}
            setTool={(t) => (labelOpen ? undefined : term.setTool(t))}
            drawStyle={term.drawStyle}
            setDrawStyle={term.setDrawStyle}
            drawings={term.drawings}
            setDrawings={term.setDrawings}
            selectedDrawing={term.selectedDrawing}
            setSelectedDrawing={term.setSelectedDrawing}
            onFit={term.actions.onFit}
            onReset={() => setResetKey((x) => x + 1)}
            onLatest={() => setLatestKey((x) => x + 1)}
            onSaveLayout={term.actions.onSaveLayout}
            onRestoreLayout={term.actions.onRestoreLayout}
            onScreenshot={term.actions.onScreenshot}
            onExportDrawings={term.actions.onExportDrawings}
            onImportDrawings={term.actions.onImportDrawings}
            dailyOnlyNote={timeframe !== "1d"}
          />
        </div>
      </div>

      {error && <ErrorNote message={error} />}
      {data?.notes.map((n) => (
        <div key={n} className="rounded-md border border-forming/40 bg-forming/10 px-3 py-1.5 text-xs text-forming">
          {n}
        </div>
      ))}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className={`panel self-start overflow-hidden ${loading ? "opacity-60" : ""}`}>
          {data && (
            <PatternChart
              candles={data.candles}
              futureBars={data.future_bars ?? []}
              timeframe={data.timeframe}
              detection={det}
              swings={data.swing_points}
              showOverlay={toggles.overlay}
              showSwings={toggles.swings}
              showVolume={term.prefs.showVolume}
              zones={data.sr_zones ?? []}
              showZones={toggles.sr}
              extraShapes={chartShapes}
              extraMarkers={term.vcpOverlay.markers}
              onCandleClick={labelOpen ? (c) => setLastClick({ ...c, n: Date.now() }) : undefined}
              resetKey={resetKey}
              latestKey={latestKey}
              indicators={term.indicators}
              tech={term.tech}
              prefs={term.prefs}
              drawings={term.drawings}
              onDrawingsChange={term.setDrawings}
              tool={labelOpen ? "none" : term.tool}
              onToolDone={() => term.setTool("none")}
              drawStyle={term.drawStyle}
              selectedDrawing={term.selectedDrawing}
              onSelectDrawing={term.setSelectedDrawing}
              fitKey={term.fitKey}
              onReady={term.onReady}
              extraPanes={term.extraPanes}
              overlayLines={term.stageOverlay.lines}
            />
          )}
          {!data && !error && <div className="h-[62vh] min-h-[420px]" />}
        </div>
        <div className="flex flex-col gap-3">
          {labelOpen && data && (
            <LabelPanel
              symbol={data.symbol}
              timeframe={data.timeframe}
              asof={data.asof ?? (data.data_cutoff ?? "").slice(0, 10)}
              detection={det}
              patterns={patternList}
              click={lastClick}
              onShapes={onLabelShapes}
              onCaptureChange={onCaptureChange}
            />
          )}
          <Panel title={`Detections on ${timeframe.toUpperCase()} (${data?.detections.length ?? 0})`}>
            <ul className="space-y-1" data-testid="detection-list">
              {data?.detections.map((d) => (
                <li key={d.pattern_id}>
                  <button
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-panel2 ${d.pattern_id === selected ? "bg-panel2 ring-1 ring-accent/60" : ""}`}
                    onClick={() => select(d.pattern_id)}
                    data-testid="detection-item"
                    data-pattern-id={d.pattern_id}
                  >
                    <span>
                      <span className="text-ink">{d.pattern_name}</span>
                      <span className="ml-1 text-muted">{d.formation_length} bars</span>
                    </span>
                    <span className="flex items-center gap-2">
                      <StatusBadge status={d.status as StatusSlug} />
                      <span className="num text-ink">{d.quality.toFixed(0)}</span>
                    </span>
                  </button>
                </li>
              ))}
              {data && !data.detections.length && <li className="text-xs text-muted">No pattern detected on this timeframe.</li>}
            </ul>
          </Panel>
          {det && <MetricsPanel d={det} />}
          {det && <QualityPanel d={det} />}
          {det && <LifecyclePanel d={det} />}
          {term.vcp?.vcp && term.prefs.showVcp && <VcpPanel v={term.vcp.vcp} stage={term.vcp.stage} />}
          {term.prefs.showVcp && term.vcp && !term.vcp.vcp && <div className="panel px-3 py-2 text-[11px] text-muted" data-testid="vcp-none">VCP: {term.vcp.note ?? "no VCP candidate"}</div>}
          <StagePanel stage={term.stage} rs={term.rsLine} />
          <TechnicalPanel tech={term.tech} ind={term.indicators} timeframe={timeframe} error={term.techError} />
          <DataSourcePanel ds={data?.data_source} />
        </div>
      </div>

      {det && (
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel title="Detection explanation">
            <ul className="space-y-0.5 text-xs" data-testid="explanation">
              {det.explanation.map((line, i) => (
                <li key={i} className={line.startsWith("FAIL") ? "text-down" : line.startsWith("PASS") ? "text-ink" : "text-muted"}>
                  {line}
                </li>
              ))}
            </ul>
          </Panel>
          <div className="flex flex-col gap-3">
            <Panel title="Boundary coordinates">
              <table className="w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="py-1 text-left font-normal">Line</th>
                    <th className="text-left font-normal">From</th>
                    <th className="text-left font-normal">To</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l) => (
                    <tr key={l.id} className="border-t border-line/50">
                      <td className="py-1 text-ink">
                        {humanize(l.id)}
                        {l.style === "dashed" ? " (projected)" : ""}
                      </td>
                      <td className="num text-muted">
                        {fmtBarTime(l.points[0].t, timeframe)} @ {fmtNum(l.points[0].p)}
                      </td>
                      <td className="num text-muted">
                        {fmtBarTime(l.points[l.points.length - 1].t, timeframe)} @ {fmtNum(l.points[l.points.length - 1].p)}
                        {l.points[l.points.length - 1].projected ? " *" : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-[10px] text-muted">* projected bar time from the NSE trading calendar. {det.geometry.price_basis}.</p>
            </Panel>
            <Panel title="Related patterns">
              <ul className="space-y-1 text-xs" data-testid="related">
                {data?.detections
                  .filter((d) => d.pattern_id !== det.pattern_id)
                  .map((d) => (
                    <li key={d.pattern_id}>
                      <button className="text-accent hover:underline" onClick={() => select(d.pattern_id)}>
                        {d.pattern_name} ({timeframe.toUpperCase()})
                      </button>
                    </li>
                  ))}
                {otherTf.map((m) => (
                  <li key={m.pattern_id}>
                    <Link className="text-accent hover:underline" to={`/explorer/${m.symbol}?tf=${m.timeframe}&pattern_id=${m.pattern_id}`} state={location.state}>
                      {m.pattern_name} ({m.timeframe.toUpperCase()})
                    </Link>
                    <span className="ml-2 text-muted">{fmtBarTime(m.formation_end, m.timeframe)}</span>
                  </li>
                ))}
                {!otherTf.length && (data?.detections.length ?? 0) <= 1 && <li className="text-muted">None in stored scans.</li>}
              </ul>
            </Panel>
          </div>
          <Panel title={`Swing points in the formation (k=${data?.swing_k ?? 3})`} className="lg:col-span-2">
            <div className="max-h-64 overflow-y-auto">
              <table className="w-full text-xs" data-testid="swing-table">
                <thead className="sticky top-0 bg-panel text-muted">
                  <tr>
                    <th className="py-1 text-left font-normal">Bar</th>
                    <th className="text-left font-normal">Time</th>
                    <th className="text-left font-normal">Kind</th>
                    <th className="text-right font-normal">Price</th>
                    <th className="text-right font-normal">Touch residual / tolerance</th>
                  </tr>
                </thead>
                <tbody>
                  {markers
                    .slice()
                    .sort((a, b) => a.i - b.i)
                    .map((m) => (
                      <tr key={m.id} className="border-t border-line/50">
                        <td className="num py-1 text-muted">{m.i}</td>
                        <td className="text-muted">{fmtBarTime(m.t, timeframe)}</td>
                        <td className={m.role.startsWith("touch") ? "text-ink" : "text-muted"}>{humanize(m.role)}</td>
                        <td className="num text-right text-ink">{fmtNum(m.p)}</td>
                        <td className="num text-right text-muted">
                          {m.residual !== undefined ? `${fmtNum(m.residual)} / ${fmtNum(m.tolerance ?? null)}` : "-"}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title={`Support / resistance zones (${data?.sr_zones.length ?? 0})`} className="lg:col-span-2">
            <table className="w-full text-xs" data-testid="sr-table">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-left font-normal">Zone</th>
                  <th className="text-left font-normal">Role</th>
                  <th className="text-left font-normal">Status</th>
                  <th className="text-right font-normal">Touches</th>
                  <th className="text-right font-normal">Width</th>
                  <th className="text-right font-normal">Distance</th>
                  <th className="text-right font-normal">Distance (ATR)</th>
                  <th className="text-left font-normal">Last touch</th>
                </tr>
              </thead>
              <tbody>
                {data?.sr_zones.map((z) => (
                  <tr key={z.zone_id} className="border-t border-line/50">
                    <td className="num py-1 text-ink">
                      {fmtNum(z.p_low)} - {fmtNum(z.p_high)}
                    </td>
                    <td className={z.role === "support" ? "text-validated" : "text-down"}>{z.role}</td>
                    <td className="text-muted">
                      {z.status}
                      {z.broken_time ? ` (${fmtBarTime(z.broken_time, timeframe)})` : ""}
                    </td>
                    <td className="num text-right">{z.touches}</td>
                    <td className="num text-right">{fmtNum(z.width)}</td>
                    <td className="num text-right">{`${z.side === "above" ? "+" : "-"}${z.distance_pct.toFixed(2)}%`}</td>
                    <td className="num text-right">{z.distance_atr === null ? "-" : z.distance_atr.toFixed(2)}</td>
                    <td className="text-muted">{fmtBarTime(z.last_touch_time, timeframe)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[10px] text-muted">{data?.sr_zones_note}</p>
          </Panel>
        </div>
      )}
    </div>
  );
}
