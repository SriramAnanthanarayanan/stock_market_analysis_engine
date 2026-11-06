import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ChartResponse, IndexChart } from "../api/types";
import type { OverlayShape } from "../charts/OverlayPrimitive";
import { PatternChart, type ChartMarker } from "../charts/PatternChart";
import { useChartTerminal } from "../charts/useChartTerminal";
import { fmtBarTime, fmtNum, fmtPct } from "../lib/format";
import { ChartOpenerProvider, explorerUrl, type ChartRequest } from "../lib/openChart";
import { CHART_TIMEFRAMES, isMultiMonth, type ChartTimeframe } from "../lib/timeframes";
import { ExplorerView, PerformanceStrip } from "../pages/Explorer";
import { ChartTerminalBar } from "./ChartTerminalBar";
import { ErrorNote, FnoBadge, KV } from "./common";

const PREVIEW_CANDLES = 600;

type Loaded = { kind: "stock"; data: ChartResponse } | { kind: "index"; data: IndexChart };

/** One shared chart preview for every results table (docs/chart-preview.md).
 *  Clicking a symbol opens it in a right-side drawer over the page; the page
 *  stays mounted underneath, so its filters, pagination and scroll position are
 *  kept. "Expand" opens the full charting terminal (same chart component, same
 *  APIs) in a fullscreen layer; closing it returns to the preview, Esc closes the
 *  top layer. */
export function ChartPreviewHost({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<ChartRequest | null>(null);
  const [full, setFull] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const open = useCallback((r: ChartRequest) => {
    setReq(r);
  }, []);
  useEffect(() => {
    setReq(null);
    setFull(false);
  }, [location.pathname]);
  useEffect(() => {
    if (!req) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      const target = ev.target as HTMLElement | null;
      if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      // Deferred: a drawing tool that used this Esc (cancel / deselect) marks it handled.
      window.setTimeout(() => {
        if (ev.defaultPrevented) return;
        if (full) setFull(false);
        else setReq(null);
      }, 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [req, full]);
  const setTf = (tf: ChartTimeframe) => req && setReq({ ...req, timeframe: tf, patternId: tf === req.timeframe ? req.patternId : null });
  return (
    <ChartOpenerProvider value={open}>
      {children}
      {req && !full && (
        <PreviewDrawer
          key={`${req.instrument ?? "stock"}:${req.symbol}`}
          req={req}
          onClose={() => setReq(null)}
          onTimeframe={setTf}
          onExpand={() => {
            if (req.instrument === "index") navigate(explorerUrl(req), { state: { from: `${location.pathname}${location.search}` } });
            else setFull(true);
          }}
          onOpenPage={() => navigate(explorerUrl(req), { state: { from: `${location.pathname}${location.search}` } })}
        />
      )}
      {req && full && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-page" data-testid="chart-fullscreen">
          <ExplorerView
            embedded
            symbol={req.symbol}
            timeframe={req.timeframe}
            patternId={req.patternId ?? null}
            asof={req.asof ?? null}
            show={req.show?.join(",") ?? null}
            onTimeframe={setTf}
            onSelectPattern={(id) => setReq({ ...req, patternId: id })}
            onBack={() => setFull(false)}
            backLabel="Back to the list (Esc)"
          />
        </div>
      )}
    </ChartOpenerProvider>
  );
}

function contextShapes(req: ChartRequest, firstT: number | null, lastT: number | null): OverlayShape[] {
  const c = req.context;
  if (!c?.levels || firstT === null || lastT === null) return [];
  return c.levels.map((lv) => ({ kind: "segment", t0: lv.t0 && lv.t0 >= firstT ? lv.t0 : firstT, t1: lastT, p0: lv.price, p1: lv.price, color: lv.color ?? "#F5C542", dashed: true, width: 1, label: lv.label }));
}

function contextMarkers(req: ChartRequest): ChartMarker[] {
  return (req.context?.markers ?? []).map((m) => ({ t: m.t, position: m.position ?? "below", shape: m.position === "above" ? "arrowDown" : "arrowUp", color: m.color ?? "#95EB45", text: m.label }));
}

function PreviewDrawer({ req, onClose, onExpand, onOpenPage, onTimeframe }: { req: ChartRequest; onClose: () => void; onExpand: () => void; onOpenPage: () => void; onTimeframe: (tf: ChartTimeframe) => void }) {
  const instrument = req.instrument ?? "stock";
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showZones, setShowZones] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [latestKey, setLatestKey] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const p: Promise<Loaded> =
      instrument === "index"
        ? api.indexChart(req.symbol, req.timeframe, req.asof).then((data) => ({ kind: "index" as const, data }))
        : api.chart(req.symbol, req.timeframe, req.patternId ?? null, req.asof ?? null, PREVIEW_CANDLES).then((data) => ({ kind: "stock" as const, data }));
    p.then((d) => !cancelled && (setLoaded(d), setError(null)))
      .catch((e) => !cancelled && (setLoaded(null), setError(e instanceof Error ? e.message : String(e))))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [instrument, req.symbol, req.timeframe, req.patternId, req.asof]);
  const data = loaded?.data ?? null;
  const candles = data?.candles ?? [];
  const src = useMemo(
    () => (data ? { symbol: loaded!.kind === "index" ? (data as IndexChart).code : data.symbol, timeframe: data.timeframe, asof: data.asof, scanId: loaded!.kind === "stock" ? (data as ChartResponse).scan_id : null, candles: data.candles, instrument } : null),
    [data, loaded, instrument],
  );
  const term = useChartTerminal(instrument === "index" ? "index" : "stock", src, req.show?.join(",") ?? null);
  const det = loaded?.kind === "stock" ? loaded.data.detections.find((d) => d.pattern_id === (req.patternId ?? loaded.data.selected_detection_id)) ?? null : null;
  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2];
  const chg = last && prev ? ((last.c / prev.c) - 1) * 100 : null;
  const firstT = candles[0]?.t ?? null;
  const lastT = last?.t ?? null;
  const vcp = term.vcp;
  const shapes = useMemo(() => [...contextShapes(req, firstT, lastT), ...term.stageOverlay.shapes, ...term.vcpOverlay.shapes, ...term.ipoOverlay.shapes], [req, firstT, lastT, term.stageOverlay, term.vcpOverlay, term.ipoOverlay]);
  const markers = useMemo(() => [...contextMarkers(req), ...term.vcpOverlay.markers, ...term.ipoOverlay.markers], [req, term.vcpOverlay, term.ipoOverlay]);
  const volumeHighlights = useMemo(() => [...term.vcpOverlay.volume, ...term.ipoOverlay.volume], [term.vcpOverlay, term.ipoOverlay]);
  const focusT = vcp?.vcp?.geometry?.base.t_start ?? term.ipo?.geometry?.listing.t ?? req.context?.focusT ?? req.context?.markers?.[0]?.t ?? null;
  const name = loaded?.kind === "index" ? loaded.data.name : loaded?.kind === "stock" ? loaded.data.company : null;
  const zones = data?.sr_zones ?? [];
  const perf = loaded?.kind === "stock" ? loaded.data.performance : null;
  const partial = data?.partial_candle ?? null;

  return (
    <aside className="fixed bottom-0 right-0 top-0 z-40 flex w-full flex-col border-l border-accent/40 bg-page shadow-2xl md:w-[min(1000px,66vw)]" data-testid="chart-preview" data-symbol={req.symbol} role="dialog" aria-label={`${req.symbol} chart preview`}>
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-3 py-2">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-ink" data-testid="preview-symbol">
              {req.symbol}
            </h2>
            {loaded?.kind === "stock" && <FnoBadge value={loaded.data.fno_status} />}
            {instrument === "index" && <span className="rounded border border-line px-1.5 text-[10px] text-muted">INDEX</span>}
          </div>
          <div className="max-w-[300px] truncate text-[11px] text-muted">{name ?? ""}</div>
        </div>
        <div className="num text-sm" data-testid="preview-close-price">
          <span className="text-ink">{fmtNum(last?.c ?? null)}</span>
          {chg !== null && <span className={`ml-2 text-xs ${chg >= 0 ? "text-validated" : "text-down"}`}>{fmtPct(chg, 2)}</span>}
          <div className="text-[10px] text-muted">{last ? `close ${fmtBarTime(last.t, req.timeframe === "1h" || req.timeframe === "4h" ? "1h" : "1d")}` : ""}</div>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button className="btn h-7" onClick={onExpand} title="Open the full charting terminal (fullscreen); close it to come back here" data-testid="preview-expand">
            ⤢ Expand
          </button>
          <button className="btn h-7" onClick={onOpenPage} title="Open the chart page (own URL; the browser's Back button returns here)" data-testid="preview-open-page">
            Open page ↗
          </button>
          <button className="btn h-7" onClick={onClose} title="Close the preview (Esc)" data-testid="preview-close">
            ✕
          </button>
        </div>
        <div className="flex w-full flex-wrap items-center gap-1">
          {CHART_TIMEFRAMES.map((t) => (
            <button key={t.value} className={`chip h-6 ${t.value === req.timeframe ? "chip-on" : ""}`} title={t.title} onClick={() => onTimeframe(t.value)} data-testid={`preview-tf-${t.value}`}>
              {t.label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-line" />
          {term.prefs.emas.map((e, i) => (
            <button
              key={i}
              className={`chip h-6 ${e.visible ? "chip-on" : ""}`}
              style={e.visible ? { borderColor: e.color, color: e.color } : undefined}
              onClick={() => term.setPrefs({ ...term.prefs, emas: term.prefs.emas.map((x, j) => (j === i ? { ...x, visible: !x.visible } : x)) })}
              data-testid={`preview-ema-${e.period}`}
            >
              EMA {e.period}
            </button>
          ))}
          <button className={`chip h-6 ${term.prefs.showVolume ? "chip-on" : ""}`} onClick={() => term.setPrefs({ ...term.prefs, showVolume: !term.prefs.showVolume })}>
            Volume
          </button>
          {!isMultiMonth(req.timeframe) && (
            <button className={`chip h-6 ${showZones ? "chip-on" : ""}`} onClick={() => setShowZones((x) => !x)} data-testid="preview-sr">
              S/R
            </button>
          )}
          <button
            className={`chip h-6 ${term.prefs.chartType === "line" ? "chip-on" : ""}`}
            onClick={() => term.setPrefs({ ...term.prefs, chartType: term.prefs.chartType === "line" ? "candle" : "line" })}
            title="Candlesticks or a line of closing prices (same data, overlays and drawings)"
            data-testid="preview-chart-type"
          >
            {term.prefs.chartType === "line" ? "Line" : "Candles"}
          </button>
          <button className={`chip h-6 ${term.prefs.logScale ? "chip-on" : ""}`} onClick={() => term.setPrefs({ ...term.prefs, logScale: !term.prefs.logScale })}>
            Log
          </button>
          <button className="chip h-6" onClick={() => setResetKey((x) => x + 1)}>
            Reset
          </button>
          <button className="chip h-6" onClick={() => setLatestKey((x) => x + 1)}>
            Latest
          </button>
          <button className={`chip h-6 ${showTools ? "chip-on" : ""}`} onClick={() => setShowTools((x) => !x)} title="Indicators and drawing tools (drawings are saved for this symbol)" data-testid="preview-tools">
            Tools ▾
          </button>
          {loading && <span className="text-[11px] text-muted">Loading...</span>}
        </div>
        {showTools && (
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
              timeframe={req.timeframe}
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
              dailyOnlyNote={req.timeframe !== "1d"}
            />
          </div>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error && (
          <div className="p-3">
            <ErrorNote message={error} />
          </div>
        )}
        <div className={loading ? "opacity-60" : ""}>
          {data && (
            <PatternChart
              candles={data.candles}
              futureBars={data.future_bars ?? []}
              timeframe={data.timeframe}
              detection={det}
              swings={[]}
              showOverlay
              showSwings={false}
              showVolume={term.prefs.showVolume}
              zones={zones}
              showZones={showZones}
              extraShapes={shapes}
              extraMarkers={markers}
              resetKey={resetKey}
              latestKey={latestKey}
              indicators={term.indicators}
              tech={term.tech}
              prefs={term.prefs}
              drawings={term.drawings}
              onDrawingsChange={term.setDrawings}
              makeDrawing={term.makeDrawing}
              tool={term.tool}
              onToolDone={() => term.setTool("none")}
              drawStyle={term.drawStyle}
              selectedDrawing={term.selectedDrawing}
              onSelectDrawing={term.setSelectedDrawing}
              fitKey={term.fitKey}
              onReady={term.onReady}
              extraPanes={term.extraPanes}
              overlayLines={term.stageOverlay.lines}
              partialCandle={partial}
              volumeHighlights={volumeHighlights}
              focusT={focusT}
              height="h-[58vh] min-h-[360px]"
            />
          )}
          {!data && !error && <div className="h-[58vh] min-h-[360px]" />}
        </div>
        <div className="grid gap-3 p-3 md:grid-cols-2">
          <section className="panel p-3" data-testid="preview-context">
            <div className="label mb-1">{req.context?.title ?? (det ? det.pattern_name : "Chart")}</div>
            {req.context?.lines.map((l) => <KV key={l.k} k={l.k} v={l.v} />)}
            {!req.context && det && (
              <>
                <KV k="Status" v={det.status_label} />
                <KV k="Quality (fit, not a probability)" v={`${det.quality.toFixed(0)} (${det.quality_label})`} />
                <KV k="Formation" v={`${fmtBarTime(det.formation_start, det.timeframe)} - ${fmtBarTime(det.formation_end, det.timeframe)}`} />
              </>
            )}
            {vcp?.vcp && <KV k="VCP" v={`${vcp.vcp.status_label} · ${vcp.vcp.depths_pct.map((d, i) => `T${i + 1} ${d.toFixed(0)}%`).join(" > ")} · pivot ${fmtNum(vcp.vcp.pivot)}`} />}
            {term.ipo && <KV k="IPO base" v={`${term.ipo.status_label}${term.ipo.pivot ? ` · pivot ${fmtNum(term.ipo.pivot)}` : ""} · listed ${term.ipo.listing.date}`} />}
            {partial && <KV k="Running period (incomplete)" v={`${partial.sessions} sessions to ${fmtBarTime(partial.last_session, "1d")}`} />}
          </section>
          <section className="panel p-3 text-[11px]">
            <PerformanceStrip perf={perf} />
            {data?.notes.map((n) => (
              <p key={n} className="mt-1 text-forming">
                {n}
              </p>
            ))}
            <p className="mt-1 text-muted">Drawings: {term.drawingStore.all.length} on this symbol ({term.drawings.length} shown on {req.timeframe}). Rule-based chart data, not trade recommendations.</p>
          </section>
        </div>
      </div>
    </aside>
  );
}
