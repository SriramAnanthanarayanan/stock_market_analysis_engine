import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { IndexChart, IndexOverviewRow, RRGItem, Timeframe } from "../api/types";
import { CHART_TIMEFRAMES, isScanTimeframe, type ChartTimeframe } from "../lib/timeframes";
import { PatternChart } from "../charts/PatternChart";
import { useChartTerminal } from "../charts/useChartTerminal";
import { ChartTerminalBar } from "../components/ChartTerminalBar";
import { ErrorNote, KV, Panel } from "../components/common";
import { RRGChart } from "../components/RRGChart";
import { DataSourcePanel, TechnicalPanel } from "../components/TechnicalPanel";
import { StagePanel } from "../components/StagePanel";
import { fmtBarTime, fmtDate, fmtNum, humanize } from "../lib/format";

export function IndexExplorer() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const timeframe = (params.get("tf") as ChartTimeframe) || "1d";
  const asof = params.get("asof");
  const benchmark = params.get("bench") ?? "NIFTY 500";
  const [data, setData] = useState<IndexChart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showZones, setShowZones] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [latestKey, setLatestKey] = useState(0);
  const [rrg, setRrg] = useState<RRGItem | null>(null);
  const [perf, setPerf] = useState<IndexOverviewRow | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .indexChart(code, timeframe, asof)
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && (setData(null), setError(e instanceof Error ? e.message : String(e))))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [code, timeframe, asof]);
  useEffect(() => {
    api
      .rrg({ benchmark, codes: code, asof, trail: 12 })
      .then((r) => setRrg(r.items.find((x) => x.code === code) ?? null))
      .catch(() => setRrg(null));
    api
      .indexOverview({ timeframe: "1d", benchmark, asof, codes: code })
      .then((r) => setPerf(r.items.find((x) => x.code === code) ?? null))
      .catch(() => setPerf(null));
  }, [code, benchmark, asof]);
  const src = useMemo(() => (data ? { symbol: data.code, timeframe: data.timeframe, asof: data.asof, scanId: null, candles: data.candles, instrument: "index" as const } : null), [data]);
  const term = useChartTerminal("index", src, params.get("show"));
  const setTf = (tf: ChartTimeframe) => {
    const next = new URLSearchParams(params);
    next.set("tf", tf);
    setParams(next);
  };
  const intradayOk = data?.intraday_available ?? data?.registry?.intraday_status === "verified";
  const reg = data?.registry;
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="index-explorer">
      <div className="panel flex flex-wrap items-center gap-x-5 gap-y-2 px-3 py-2.5">
        <button className="text-xs text-muted hover:text-ink" onClick={() => navigate("/indices")} data-testid="back">
          &larr; Index Analysis
        </button>
        <div>
          <h1 className="text-xl font-semibold text-ink" data-testid="index-name">
            {data?.name ?? decodeURIComponent(code)}
          </h1>
          <div className="text-xs text-muted">
            {data ? `${data.code} · ${data.category_label}${data.subcategory ? ` · ${humanize(data.subcategory)}` : ""}` : ""}
          </div>
        </div>
        <div className="ml-auto text-right text-[11px] text-muted">
          {data?.data_cutoff && <div>Data cutoff {fmtBarTime(data.data_cutoff, "1h")}</div>}
          {asof && <div className="text-forming">As-of {asof}</div>}
        </div>
      </div>
      <div className="panel flex flex-wrap items-center gap-2 px-3 py-2">
        <div className="flex gap-1" role="group" aria-label="Timeframe">
          {CHART_TIMEFRAMES.map((t) => {
            const intraday = t.value === "1h" || t.value === "4h";
            const disabled = intraday && data !== null && !intradayOk;
            return (
              <button
                key={t.value}
                className={`chip ${t.value === timeframe ? "chip-on" : ""} ${disabled ? "opacity-40" : ""}`}
                onClick={() => setTf(t.value)}
                title={disabled ? "No verified intraday data for this index (official data is daily); the chart says so instead of manufacturing candles." : ""}
                data-testid={`index-tf-${t.value}`}
              >
                {t.label}
              </button>
            );
          })}
        </div>
        <button className={`chip ${showZones ? "chip-on" : ""}`} onClick={() => setShowZones((x) => !x)} title={data?.sr_zones_note} data-testid="toggle-sr">
          S/R zones
        </button>
        <button className={`chip ${term.prefs.showVolume ? "chip-on" : ""}`} onClick={() => term.setPrefs({ ...term.prefs, showVolume: !term.prefs.showVolume })} data-testid="toggle-volume">
          Volume
        </button>
        <Field2 label="Benchmark">
          <select className="ctl h-7" value={benchmark} onChange={(e) => setParams(new URLSearchParams({ ...Object.fromEntries(params), bench: e.target.value }))} data-testid="index-benchmark">
            <option value="NIFTY 500">NIFTY 500</option>
            <option value="NIFTY 50">NIFTY 50</option>
          </select>
        </Field2>
        {loading && <span className="text-xs text-muted">Loading...</span>}
        {term.message && <span className="text-xs text-accent">{term.message}</span>}
        <div className="w-full">
          <ChartTerminalBar
            prefs={term.prefs}
            setPrefs={term.setPrefs}
            tool={term.tool}
            setTool={term.setTool}
            drawStyle={term.drawStyle}
            setDrawStyle={term.setDrawStyle}
            drawings={term.drawings}
            setDrawings={term.setDrawings}
            store={term.drawingStore}
            timeframe={timeframe}
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
          {data ? (
            <PatternChart
              candles={data.candles}
              futureBars={[]}
              timeframe={data.timeframe}
              detection={null}
              swings={[]}
              showOverlay={false}
              showSwings={false}
              showVolume={term.prefs.showVolume}
              zones={data.sr_zones}
              showZones={showZones}
              extraShapes={term.stageOverlay.shapes}
              resetKey={resetKey}
              latestKey={latestKey}
              indicators={term.indicators}
              tech={term.tech}
              prefs={term.prefs}
              drawings={term.drawings}
              onDrawingsChange={term.setDrawings}
              makeDrawing={term.makeDrawing}
              partialCandle={data.partial_candle ?? null}
              tool={term.tool}
              onToolDone={() => term.setTool("none")}
              drawStyle={term.drawStyle}
              selectedDrawing={term.selectedDrawing}
              onSelectDrawing={term.setSelectedDrawing}
              fitKey={term.fitKey}
              onReady={term.onReady}
              extraPanes={term.extraPanes}
              overlayLines={term.stageOverlay.lines}
            />
          ) : (
            <div className="flex h-[62vh] min-h-[420px] items-center justify-center px-6 text-center text-sm text-muted" data-testid="index-chart-unavailable">
              {error ?? "Loading..."}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <Panel title={`Performance vs ${benchmark}`}>
            <div className="text-xs" data-testid="index-performance">
              {perf?.returns ? (
                <>
                  {Object.entries(perf.returns).map(([h, v]) => (
                    <KV key={h} k={`${h} return`} v={v === null ? "n/a" : `${v.toFixed(2)}%`} />
                  ))}
                  <KV k="RS vs benchmark (3M)" v={perf.rs_vs_benchmark === null || perf.rs_vs_benchmark === undefined ? "n/a" : `${perf.rs_vs_benchmark.toFixed(2)}%`} />
                  <KV k="Close vs EMA 50 / 200 (1D)" v={`${perf.ema50 ?? "-"} / ${perf.ema200 ?? "-"}`} />
                </>
              ) : (
                <span className="text-muted">Not available.</span>
              )}
            </div>
          </Panel>
          <Panel title="RRG position (weekly)">
            {rrg && rrg.status === "ok" && rrg.latest ? (
              <div data-testid="index-rrg">
                <RRGChart items={[rrg]} color={() => "#19C3DA"} center={100} compact />
                <div className="text-xs">
                  <KV k="Quadrant" v={`${humanize(rrg.latest.quadrant)} (moving ${rrg.latest.direction})`} />
                  <KV k="RS-Ratio / RS-Momentum" v={`${rrg.latest.ratio.toFixed(2)} / ${rrg.latest.momentum.toFixed(2)}`} />
                  <KV k="Week" v={fmtDate(rrg.latest.week)} />
                </div>
              </div>
            ) : (
              <div className="text-xs text-muted" data-testid="index-rrg">{rrg ? `RRG ${humanize(rrg.status)}` : "Not available (benchmark or this index)."}</div>
            )}
          </Panel>
          <StagePanel stage={term.stage} rs={term.rsLine} />
          <TechnicalPanel tech={term.tech} ind={term.indicators} timeframe={(isScanTimeframe(timeframe) ? timeframe : "1d") as Timeframe} error={term.techError} />
          <DataSourcePanel ds={data?.data_source} />
          {reg && (
            <Panel title="Registry">
              <div className="text-xs" data-testid="index-registry">
                <KV k="Official name / symbol" v={`${reg.name} / ${reg.code}`} />
                <KV k="NSE catalogue group" v={reg.nse_group} />
                <KV k="Type / basis" v={`${reg.index_type} / ${reg.return_basis} (price return)`} />
                <KV k="Daily data" v={`${reg.data_status} · ${fmtDate(reg.first_date)} - ${fmtDate(reg.last_date)} (${reg.rows ?? 0} days)`} />
                <KV k="Official reference check" v={reg.data_verified ? `${reg.data_verified.status} (${reg.data_verified.matched ?? 0}/${reg.data_verified.compared ?? 0})` : "-"} />
                <KV k="Intraday" v={reg.intraday_status === "verified" ? `Yahoo ${reg.intraday_ticker} (verified)` : reg.intraday_status ?? "unavailable"} />
                <p className="mt-1 text-[10px] text-muted">Source: {reg.source_url}</p>
              </div>
            </Panel>
          )}
        </div>
      </div>
      {data && showZones && (
        <Panel title={`Support / resistance zones (${data.sr_zones.length})`}>
          <table className="w-full text-xs">
            <tbody>
              {data.sr_zones.map((z) => (
                <tr key={z.zone_id} className="border-t border-line/50">
                  <td className="num py-1 text-ink">
                    {fmtNum(z.p_low)} - {fmtNum(z.p_high)}
                  </td>
                  <td className={z.role === "support" ? "text-validated" : "text-down"}>{z.role}</td>
                  <td className="text-muted">{z.status}</td>
                  <td className="num text-right">{z.touches} touches</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

function Field2({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-muted">
      {label}
      {children}
    </label>
  );
}
