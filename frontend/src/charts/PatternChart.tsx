import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  TickMarkType,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import type {
  IChartApi,
  IPriceLine,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  MouseEventParams,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import type { Candle, ChartDetection, ChartTf, IndicatorPayload, PartialCandle, SRZone, Swing, TechnicalPayload } from "../api/types";
import { CHART_COLORS as C } from "../lib/status";
import { fmtBarTime, fmtNum, fmtVolume, isIntraday, tickLabel } from "../lib/format";
import { DEFAULT_PREFS, type ChartPrefs } from "../lib/prefs";
import { OverlayPrimitive, type OverlayShape } from "./OverlayPrimitive";
import { DrawingPrimitive, TimeMapper, type Anchor, type Drawing, type DrawingType } from "./drawings";
import { useDrawingTools, type DrawStyle, type DrawTool } from "./useDrawingTools";
import { chartDebug } from "./debug";

/** Extra line pane supplied by a page (e.g. the Stage Analysis RS Line, E3). */
export interface ExtraPane {
  id: string;
  title: string;
  color: string;
  times: number[];
  values: (number | null)[];
  zeroLine?: boolean;
  /** Optional per-point colours (e.g. RS above/below zero). */
  colors?: (string | null)[];
  /** Optional circle markers on the line (e.g. RS confirmation bubbles). */
  markers?: { t: number; color: string }[];
}

/** Extra line on the price pane supplied by a page (e.g. the Stage Analysis 30-week EMA). */
export interface OverlayLine {
  id: string;
  color: string;
  times: number[];
  values: (number | null)[];
  width?: 1 | 2 | 3;
  dashed?: boolean;
}

/** Extra candle marker supplied by a page (e.g. a breakout candle, a VCP breakout). */
export interface ChartMarker {
  t: number;
  position: "above" | "below" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text?: string;
}

export interface ChartHandle {
  screenshot: () => HTMLCanvasElement | null;
  paneStretch: () => number[];
  setPaneStretch: (s: number[]) => void;
  fit: () => void;
}

export interface PatternChartProps {
  candles: Candle[];
  futureBars: { i: number; t: number }[];
  zones: SRZone[];
  showZones: boolean;
  /** Extra shapes drawn on top (e.g. the label being edited). */
  extraShapes?: OverlayShape[];
  /** Extra candle markers (e.g. a scanner event of the row that opened the chart). */
  extraMarkers?: ChartMarker[];
  /** Without a detection: open the view so this candle time (e.g. a VCP base start) is visible. */
  focusT?: number | null;
  /** Called when a candle is clicked: its start time and the clicked price. */
  onCandleClick?: (info: { t: number; price: number }) => void;
  timeframe: ChartTf;
  detection: ChartDetection | null;
  /** Multi-month timeframes: the running period (completed daily candles so far), drawn muted. */
  partialCandle?: PartialCandle | null;
  swings: Swing[];
  showOverlay: boolean;
  showSwings: boolean;
  showVolume: boolean;
  resetKey: number;
  latestKey: number;
  // ---- E1 technical analysis (all optional; defaults render the P1-P3 chart)
  indicators?: IndicatorPayload | null;
  tech?: TechnicalPayload | null;
  prefs?: ChartPrefs;
  extraPanes?: ExtraPane[];
  overlayLines?: OverlayLine[];
  drawings?: Drawing[];
  onDrawingsChange?: (d: Drawing[]) => void;
  makeDrawing?: (type: DrawingType, anchors: Anchor[], style: DrawStyle) => Drawing;
  tool?: DrawTool;
  onToolDone?: () => void;
  drawStyle?: DrawStyle;
  selectedDrawing?: string | null;
  onSelectDrawing?: (id: string | null) => void;
  fitKey?: number;
  onReady?: (h: ChartHandle | null) => void;
  height?: string;
}

interface Legend {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number | null;
}

const ts = (t: number) => t as UTCTimestamp;

function tickFormatter(time: Time, type: TickMarkType): string {
  const t = time as number;
  if (type === TickMarkType.Year) return tickLabel(t, "year");
  if (type === TickMarkType.Month) return tickLabel(t, "month");
  if (type === TickMarkType.DayOfMonth) return tickLabel(t, "day");
  return tickLabel(t, "time");
}

const MARKER_STYLE: Record<string, Pick<SeriesMarker<Time>, "position" | "shape" | "color"> & { text?: string }> = {
  touch_upper: { position: "aboveBar", shape: "circle", color: C.touch },
  touch_lower: { position: "belowBar", shape: "circle", color: C.touch },
  breakout_up: { position: "belowBar", shape: "arrowUp", color: C.breakUp, text: "Broke Above" },
  breakout_down: { position: "aboveBar", shape: "arrowDown", color: C.breakDown, text: "Broke Below" },
  invalidation: { position: "aboveBar", shape: "square", color: C.invalid, text: "Invalidated" },
  peak: { position: "aboveBar", shape: "circle", color: C.pivot },
  trough: { position: "belowBar", shape: "circle", color: C.pivot },
  pole_start: { position: "belowBar", shape: "square", color: C.pole },
  pole_end: { position: "aboveBar", shape: "square", color: C.pole },
  provisional_pivot: { position: "aboveBar", shape: "square", color: C.provisional, text: "unconfirmed" },
};

// Text comes from the backend label for pivots/poles (P1, T, P2, LS, H, RS, ...).
const LABELLED_ROLES = new Set(["peak", "trough", "pole_start", "pole_end"]);

export const TECH_COLORS = {
  maruBull: "#7CFC9A",
  maruBear: "#FF7AA2",
  breakoutUp: "#95EB45",
  breakdown: "#F2643C",
  levelHigh: "rgba(149,235,69,0.85)",
  levelLow: "rgba(242,100,60,0.85)",
  divBull: "#7FDBFF",
  divBear: "#FFB86C",
  rsi: "#C4A7E7",
  atr: "#F6C177",
  rsiLevel: "rgba(139,155,165,0.55)",
};

function lineColor(role: string, dashed: boolean): string {
  if (role === "neckline") return dashed ? "rgba(199,146,234,0.7)" : C.neckline;
  if (role === "pole") return C.pole;
  if (role === "invalidation_level") return "rgba(139,155,165,0.7)";
  return dashed ? C.projection : C.boundary;
}

function lineData(times: number[], values: (number | null)[]) {
  return times.map((t, i) => (values[i] === null || values[i] === undefined ? { time: ts(t) } : { time: ts(t), value: values[i] as number }));
}

const shortDate = (iso: string) => {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
};

type SubPanes = {
  volume?: ISeriesApi<"Histogram">;
  rsi?: ISeriesApi<"Line">;
  rsiLines: IPriceLine[];
  rsiOverlay?: OverlayPrimitive;
  atr?: ISeriesApi<"Line">;
  extra: ISeriesApi<"Line">[];
  extraMarkers: ISeriesMarkersPluginApi<Time>[];
};

export function PatternChart(props: PatternChartProps) {
  const { candles, futureBars, zones, showZones, timeframe, detection, swings, showOverlay, showSwings, showVolume, resetKey, latestKey, extraShapes, onCandleClick } = props;
  const prefs = props.prefs ?? DEFAULT_PREFS;
  const { indicators, tech } = props;
  const clickRef = useRef(onCandleClick);
  clickRef.current = onCandleClick;
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlayRef = useRef<OverlayPrimitive | null>(null);
  const drawRef = useRef<DrawingPrimitive | null>(null);
  const linesRef = useRef<ISeriesApi<"Line">[]>([]);
  const emaRef = useRef<Map<number, ISeriesApi<"Line">>>(new Map());
  const subRef = useRef<SubPanes>({ rsiLines: [], extra: [], extraMarkers: [] });
  const overlayLinesRef = useRef<ISeriesApi<"Line">[]>([]);
  // Intended viewport of the selected formation. Adding / removing series (EMAs,
  // sub panes, overlay lines) after the candles arrive makes the time scale move
  // the visible range when it extends into the future whitespace (projected apex),
  // so the range is re-applied after those updates until the user pans or zooms.
  const viewRef = useRef<{ from: number; to: number } | null>(null);
  const userMovedRef = useRef(false);
  const reapplyView = () => {
    const v = viewRef.current;
    if (!v || userMovedRef.current) return;
    requestAnimationFrame(() => {
      if (viewRef.current === v && !userMovedRef.current) chartRef.current?.timeScale().setVisibleLogicalRange(v);
    });
  };
  const byTime = useMemo(() => new Map(candles.map((c) => [c.t, c])), [candles]);
  const byTimeRef = useRef(byTime);
  byTimeRef.current = byTime;
  const partialRef = useRef<PartialCandle | null>(null);
  partialRef.current = props.partialCandle ?? null;
  const indIndex = useMemo(() => new Map((indicators?.times ?? []).map((t, i) => [t, i])), [indicators]);
  const indRef = useRef({ indicators, indIndex });
  indRef.current = { indicators, indIndex };
  const toolRef = useRef<DrawTool>(props.tool ?? "none");
  toolRef.current = props.tool ?? "none";
  const [legend, setLegend] = useState<Legend | null>(null);
  const [hoverT, setHoverT] = useState<number | null>(null);
  const [inspect, setInspect] = useState<{ x: number; y: number; price: number; t: number | null } | null>(null);

  // ---- create / dispose (once per mount)
  useEffect(() => {
    const el = containerRef.current!;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: C.background },
        textColor: C.text,
        fontFamily: "Inter, Segoe UI, system-ui, sans-serif",
        fontSize: 11,
        panes: { separatorColor: C.border, separatorHoverColor: "rgba(25,195,218,0.15)", enableResize: true },
        attributionLogo: true,
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: C.border },
      // minBarSpacing 0.05 px (default 0.5): zooming out / "Fit" can show the whole
      // cached history (daily since 1996 is ~7,800 bars) instead of stopping at ~2,800.
      timeScale: { borderColor: C.border, rightOffset: 6, minBarSpacing: 0.05, tickMarkFormatter: tickFormatter },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: C.candleUp,
      downColor: C.candleDown,
      borderUpColor: C.candleUp,
      borderDownColor: C.candleDown,
      wickUpColor: C.candleUp,
      wickDownColor: C.candleDown,
      priceLineVisible: false,
    });
    const overlay = new OverlayPrimitive();
    series.attachPrimitive(overlay);
    const drawingLayer = new DrawingPrimitive();
    series.attachPrimitive(drawingLayer);
    const markers = createSeriesMarkers(series, []);
    const moved = () => {
      userMovedRef.current = true;
    };
    for (const ev of ["mousedown", "wheel", "touchstart"]) el.addEventListener(ev, moved, { passive: true });
    chart.timeScale().subscribeVisibleTimeRangeChange((r) => {
      chartDebug.visibleFrom = r ? Number(r.from) : null;
      chartDebug.visibleTo = r ? Number(r.to) : null;
    });
    chartRef.current = chart;
    candleRef.current = series;
    overlayRef.current = overlay;
    drawRef.current = drawingLayer;
    markersRef.current = markers;
    chartDebug.created += 1;

    const onMove = (param: MouseEventParams<Time>) => {
      const c = param.time !== undefined ? byTimeRef.current.get(param.time as number) ?? (partialRef.current && partialRef.current.t === param.time ? partialRef.current : undefined) : undefined;
      setLegend(c ? { t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v } : null);
      setHoverT(param.time !== undefined ? (param.time as number) : null);
      if (toolRef.current === "crosshair" && param.point && (param.paneIndex ?? 0) === 0) {
        const price = series.coordinateToPrice(param.point.y);
        setInspect(price === null ? null : { x: param.point.x, y: param.point.y, price: Number(price), t: param.time !== undefined ? (param.time as number) : null });
      } else setInspect(null);
    };
    chart.subscribeCrosshairMove(onMove);
    // Candle capture for labelling uses the DOM click event: Lightweight Charts
    // drops a second click that follows within 500 ms at a different position
    // (it is held back as a potential double click), which loses label points.
    const onDomClick = (ev: MouseEvent) => {
      if (!clickRef.current) return;
      const rect = el.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      const paneHeight = chart.panes()[0]?.getHeight() ?? rect.height;
      if (x < 0 || x > chart.timeScale().width() || y < 0 || y > paneHeight) return; // price axis / sub panes
      const t = chart.timeScale().coordinateToTime(x);
      if (t === null || !byTimeRef.current.has(t as number)) return; // labels reference real candles only
      const price = series.coordinateToPrice(y);
      if (price !== null) clickRef.current({ t: t as number, price: Number(price) });
    };
    el.addEventListener("click", onDomClick);
    const ro = new ResizeObserver(() => {
      chartDebug.width = el.clientWidth;
      chartDebug.height = el.clientHeight;
    });
    ro.observe(el);
    props.onReady?.({
      screenshot: () => chartRef.current?.takeScreenshot() ?? null,
      paneStretch: () => chartRef.current?.panes().map((p) => p.getStretchFactor()) ?? [],
      setPaneStretch: (s) => chartRef.current?.panes().forEach((p, i) => s[i] !== undefined && p.setStretchFactor(s[i])),
      fit: () => chartRef.current?.timeScale().fitContent(),
    });

    return () => {
      props.onReady?.(null);
      ro.disconnect();
      chart.unsubscribeCrosshairMove(onMove);
      el.removeEventListener("click", onDomClick);
      for (const ev of ["mousedown", "wheel", "touchstart"]) el.removeEventListener(ev, moved);
      markers.detach();
      series.detachPrimitive(overlay);
      series.detachPrimitive(drawingLayer);
      subRef.current.rsiOverlay && subRef.current.rsi?.detachPrimitive(subRef.current.rsiOverlay);
      linesRef.current = [];
      overlayLinesRef.current = [];
      emaRef.current = new Map();
      subRef.current = { rsiLines: [], extra: [], extraMarkers: [] };
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      overlayRef.current = null;
      drawRef.current = null;
      markersRef.current = null;
      chartDebug.disposed += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- timeframe-dependent formatting (IST everywhere)
  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: { timeVisible: isIntraday(timeframe), secondsVisible: false },
      localization: { timeFormatter: (time: Time) => fmtBarTime(time as number, timeframe) },
    });
    chartDebug.timeframe = timeframe;
  }, [timeframe]);

  // ---- price axis mode (candle pane)
  useEffect(() => {
    chartRef.current?.priceScale("right").applyOptions({ mode: prefs.logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal });
    chartDebug.logScale = !!prefs.logScale;
  }, [prefs.logScale]);

  // ---- candles
  const partial = props.partialCandle && (!candles.length || props.partialCandle.t > candles[candles.length - 1].t) ? props.partialCandle : null;
  const dataTimes = useMemo(() => {
    const lastT = candles.length ? candles[candles.length - 1].t : -Infinity;
    return [...candles.map((c) => c.t), ...(partial ? [partial.t] : []), ...futureBars.filter((f) => f.t > Math.max(lastT, partial?.t ?? -Infinity)).map((f) => f.t)];
  }, [candles, futureBars, partial]);
  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    // Future bars are whitespace: they give projected geometry its true bar
    // spacing on the index-based time scale without inventing candles.
    const lastT = candles.length ? candles[candles.length - 1].t : -Infinity;
    const muted = "rgba(139,155,165,0.55)";
    series.setData([
      ...candles.map((c) => ({ time: ts(c.t), open: c.o, high: c.h, low: c.l, close: c.c })),
      // The running multi-month period: a separate, muted candle (incomplete).
      ...(partial ? [{ time: ts(partial.t), open: partial.o, high: partial.h, low: partial.l, close: partial.c, color: muted, borderColor: muted, wickColor: muted }] : []),
      ...futureBars.filter((f) => f.t > Math.max(lastT, partial?.t ?? -Infinity)).map((f) => ({ time: ts(f.t) })),
    ]);
    chartDebug.partialCandle = !!partial;
    drawRef.current?.update({ timeMap: new TimeMapper(dataTimes) });
    chartDebug.candles = candles.length;
    chartDebug.firstCandleTime = candles[0]?.t ?? null;
    chartDebug.lastCandleTime = candles[candles.length - 1]?.t ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, futureBars, dataTimes, partial?.t, partial?.c]);

  // ---- EMA overlays (backend values only; nothing is computed here)
  const emaKey = JSON.stringify(prefs.emas);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    for (const s of emaRef.current.values()) chart.removeSeries(s);
    emaRef.current = new Map();
    if (indicators) {
      for (const e of prefs.emas) {
        const vals = indicators.ema[String(e.period)];
        if (!e.visible || !vals) continue;
        const s = chart.addSeries(LineSeries, { color: e.color, lineWidth: e.width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
        s.setData(lineData(indicators.times, vals));
        emaRef.current.set(e.period, s);
      }
    }
    chartDebug.emaSeries = [...emaRef.current.keys()];
    reapplyView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indicators, emaKey]);

  // ---- page overlay lines on the price pane (backend values)
  const overlayKey = (props.overlayLines ?? []).map((l) => `${l.id}:${l.values.length}:${l.color}`).join(",");
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    for (const s of overlayLinesRef.current) chart.removeSeries(s);
    overlayLinesRef.current = [];
    for (const l of props.overlayLines ?? []) {
      const s = chart.addSeries(LineSeries, { color: l.color, lineWidth: l.width ?? 2, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: l.id }, 0);
      s.setData(lineData(l.times, l.values));
      overlayLinesRef.current.push(s);
    }
    chartDebug.overlayLines = overlayLinesRef.current.length;
    reapplyView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayKey, candles]);

  // ---- sub panes: volume, RSI, ATR, extra (rebuilt in order on any change)
  const extraKey = (props.extraPanes ?? []).map((p) => `${p.id}:${p.values.length}:${p.values[p.values.length - 1]}:${p.markers?.length ?? 0}`).join(",");
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const sub = subRef.current;
    if (sub.rsiOverlay && sub.rsi) sub.rsi.detachPrimitive(sub.rsiOverlay);
    for (const s of [sub.volume, sub.rsi, sub.atr, ...sub.extra]) if (s) chart.removeSeries(s);
    for (const m of sub.extraMarkers) m.detach();
    const next: SubPanes = { rsiLines: [], extra: [], extraMarkers: [] };
    let pane = 1;
    if (showVolume) {
      next.volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, pane++);
      next.volume.setData([
        ...candles.map((c) => (c.v === null ? { time: ts(c.t) } : { time: ts(c.t), value: c.v, color: c.c >= c.o ? C.volumeUp : C.volumeDown })),
        ...(partial && partial.v !== null ? [{ time: ts(partial.t), value: partial.v, color: "rgba(139,155,165,0.3)" }] : []),
      ]);
    }
    if (prefs.showRsi && indicators) {
      const rsi = chart.addSeries(
        LineSeries,
        { color: TECH_COLORS.rsi, lineWidth: 1, priceLineVisible: false, priceFormat: { type: "price", precision: 1, minMove: 0.1 }, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) },
        pane++,
      );
      rsi.setData(lineData(indicators.times, indicators.rsi));
      const levels = [
        { p: prefs.rsiOverbought, title: "OB" },
        { p: prefs.rsiOversold, title: "OS" },
        ...indicators.params.rsi_reference_levels.map((p) => ({ p, title: "" })),
      ];
      for (const lv of levels) next.rsiLines.push(rsi.createPriceLine({ price: lv.p, color: TECH_COLORS.rsiLevel, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: lv.title !== "", title: lv.title }));
      const ov = new OverlayPrimitive();
      rsi.attachPrimitive(ov);
      next.rsi = rsi;
      next.rsiOverlay = ov;
    }
    if (prefs.showAtr && indicators) {
      next.atr = chart.addSeries(LineSeries, { color: TECH_COLORS.atr, lineWidth: 1, priceLineVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 } }, pane++);
      next.atr.setData(lineData(indicators.times, indicators.atr));
    }
    for (const xp of props.extraPanes ?? []) {
      const s = chart.addSeries(LineSeries, { color: xp.color, lineWidth: 2, priceLineVisible: false, title: xp.title, priceFormat: { type: "price", precision: 3, minMove: 0.001 } }, pane++);
      s.setData(xp.times.map((t, i) => (xp.values[i] === null ? { time: ts(t) } : { time: ts(t), value: xp.values[i] as number, ...(xp.colors?.[i] ? { color: xp.colors[i] as string } : {}) })));
      if (xp.zeroLine) s.createPriceLine({ price: 0, color: "rgba(139,155,165,0.7)", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: "" });
      if (xp.markers?.length) next.extraMarkers.push(createSeriesMarkers(s, xp.markers.map((m) => ({ time: ts(m.t), position: "inBar", shape: "circle", color: m.color, size: 0.6 }) as SeriesMarker<Time>)));
      next.extra.push(s);
      chartDebug.rsLinePoints = xp.values.filter((v) => v !== null).length;
    }
    subRef.current = next;
    const panes = chart.panes();
    const n = panes.length;
    if (n > 1) {
      const subShare = Math.min(0.45, 0.18 * (n - 1));
      panes.forEach((p, i) => p.setStretchFactor(i === 0 ? 1 - subShare : subShare / (n - 1)));
    }
    chartDebug.panes = n;
    chartDebug.volumePane = !!next.volume;
    chartDebug.volumeBars = next.volume ? candles.filter((c) => c.v !== null).length : 0;
    chartDebug.rsiPane = !!next.rsi;
    chartDebug.atrPane = !!next.atr;
    chartDebug.rsLinePane = next.extra.length > 0;
    chartDebug.rsiLevels = next.rsiLines.length;
    reapplyView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, showVolume, indicators, prefs.showRsi, prefs.showAtr, prefs.rsiOverbought, prefs.rsiOversold, extraKey, partial?.t, partial?.v]);

  // ---- backend geometry + technical overlays -> line series, markers, primitives
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !markersRef.current || !overlayRef.current) return;
    for (const s of linesRef.current) chart.removeSeries(s);
    linesRef.current = [];
    const shapes: OverlayShape[] = [];
    const rsiShapes: OverlayShape[] = [];
    const markers: SeriesMarker<Time>[] = [];
    const roles: Record<string, number> = {};
    const techRoles: Record<string, number> = {};
    const occupied = new Set<string>();

    // Candles are a contiguous slice, so a geometry bar index maps to a chart
    // logical position by subtracting the first returned candle's index.
    const base = candles.length ? candles[0].i : 0;
    if (detection && showOverlay) {
      for (const el of detection.geometry.elements) {
        if (el.type === "line") {
          const line = chart.addSeries(
            LineSeries,
            {
              color: lineColor(el.role, el.style === "dashed"),
              lineWidth: el.role === "invalidation_level" ? 1 : 2,
              lineStyle: el.role === "invalidation_level" ? LineStyle.Dotted : el.style === "dashed" ? LineStyle.Dashed : LineStyle.Solid,
              priceLineVisible: false,
              lastValueVisible: false,
              crosshairMarkerVisible: false,
              pointMarkersVisible: false,
            },
            0,
          );
          const pts = [...el.points].sort((a, b) => a.t - b.t).filter((p, i, arr) => i === 0 || p.t !== arr[i - 1].t);
          line.setData(pts.map((p) => ({ time: ts(p.t), value: p.p })));
          linesRef.current.push(line);
        } else if (el.type === "band") {
          const pts = [...el.upper, ...[...el.lower].reverse()].map((p) => ({ t: p.t, p: p.p, logical: p.i - base }));
          shapes.push({ kind: "polygon", points: pts, fill: C.band });
        } else if (el.type === "zone") {
          shapes.push({ kind: "zone", t0: el.t_start, t1: el.t_end, p0: el.p_low, p1: el.p_high, fill: "rgba(25,195,218,0.07)", label: el.label });
        } else if (el.type === "marker") {
          if (el.role === "apex") {
            shapes.push({ kind: "point", t: el.t, p: el.p, logical: el.i - base, color: C.apex, radius: 4, label: "Apex" });
          } else if (MARKER_STYLE[el.role] && byTimeRef.current.has(el.t)) {
            const st = MARKER_STYLE[el.role];
            const text = LABELLED_ROLES.has(el.role) ? el.label : st.text;
            markers.push({ time: ts(el.t), position: st.position, shape: st.shape, color: st.color, text, size: 1 } as SeriesMarker<Time>);
            occupied.add(`${el.t}:${st.position}`);
          } else {
            continue;
          }
          roles[el.role] = (roles[el.role] ?? 0) + 1;
        }
      }
    }
    if (showZones && candles.length) {
      const lastT = candles[candles.length - 1].t;
      for (const z of zones) {
        const support = z.role === "support";
        shapes.push({
          kind: "zone",
          t0: byTimeRef.current.has(z.t_start) ? z.t_start : candles[0].t,
          t1: byTimeRef.current.has(z.t_end) ? z.t_end : lastT,
          p0: z.p_low,
          p1: z.p_high,
          fill: support ? C.supportFill : C.resistanceFill,
          stroke: support ? C.supportEdge : C.resistanceEdge,
          label: `${support ? "S" : "R"} ${z.touches}T${z.status === "intact" ? "" : ` ${z.status}`}`,
        });
      }
      roles.sr_zone = zones.length;
    }
    if (showSwings) {
      for (const s of swings) {
        const position = s.kind === "high" ? "aboveBar" : "belowBar";
        if (occupied.has(`${s.t}:${position}`) || !byTimeRef.current.has(s.t)) continue;
        markers.push({ time: ts(s.t), position, shape: "circle", color: C.swing, size: 0.5 } as SeriesMarker<Time>);
        roles[`swing_${s.kind}`] = (roles[`swing_${s.kind}`] ?? 0) + 1;
      }
    }
    // ---- E1 overlays
    if (tech && candles.length) {
      const firstT = candles[0].t;
      const lastT = candles[candles.length - 1].t;
      if (prefs.showMarubozu) {
        for (const m of tech.marubozu) {
          if (!byTimeRef.current.has(m.t)) continue;
          const bull = m.direction === "bullish";
          markers.push({ time: ts(m.t), position: bull ? "belowBar" : "aboveBar", shape: "square", color: bull ? TECH_COLORS.maruBull : TECH_COLORS.maruBear, text: "M", size: 1 } as SeriesMarker<Time>);
          techRoles[m.kind] = (techRoles[m.kind] ?? 0) + 1;
        }
      }
      if (prefs.showBreakouts && timeframe === "1d") {
        for (const b of tech.daily.breakouts) {
          if (!byTimeRef.current.has(b.t) || b.status === "pending") continue;
          const up = b.direction === "bullish";
          const failed = b.status === "failed";
          markers.push({
            time: ts(b.t),
            position: up ? "belowBar" : "aboveBar",
            shape: failed ? "circle" : up ? "arrowUp" : "arrowDown",
            color: failed ? C.invalid : up ? TECH_COLORS.breakoutUp : TECH_COLORS.breakdown,
            text: `${b.lookback_days}D ${up ? "high" : "low"}${failed ? " failed" : ""}`,
            size: 1,
          } as SeriesMarker<Time>);
          techRoles[`${b.kind}_${b.status}`] = (techRoles[`${b.kind}_${b.status}`] ?? 0) + 1;
        }
      }
      if (prefs.showDailyLevels) {
        let nLevels = 0;
        for (const lv of Object.values(tech.daily.levels)) {
          if (!lv) continue;
          const t0 = Math.max(firstT, byTimeRef.current.has(lv.t_start) ? lv.t_start : firstT);
          const ref = `${shortDate(lv.reference_start)}-${shortDate(lv.reference_end)}`;
          const tag = timeframe === "1d" ? "" : " (daily-derived)";
          shapes.push({ kind: "segment", t0, t1: lastT, p0: lv.high, p1: lv.high, color: TECH_COLORS.levelHigh, dashed: true, width: 1, label: `${lv.lookback_days}D high ${lv.high.toFixed(2)}${tag} · ref ${ref}` });
          shapes.push({ kind: "segment", t0, t1: lastT, p0: lv.low, p1: lv.low, color: TECH_COLORS.levelLow, dashed: true, width: 1, label: `${lv.lookback_days}D low ${lv.low.toFixed(2)}${tag}` });
          nLevels += 2;
        }
        chartDebug.dailyLevels = nLevels;
      } else chartDebug.dailyLevels = 0;
      let segs = 0;
      if (prefs.showDivergence) {
        for (const d of tech.divergences) {
          if (d.status === "superseded" || !byTimeRef.current.has(d.p1.t) || !byTimeRef.current.has(d.p2.t)) continue;
          const bull = d.direction === "bullish";
          const color = bull ? TECH_COLORS.divBull : TECH_COLORS.divBear;
          const dashed = d.status !== "confirmed";
          const label = `${bull ? "Bull" : "Bear"} div${d.status === "confirmed" ? "" : ` (${d.status})`}`;
          shapes.push({ kind: "segment", t0: d.p1.t, p0: d.p1.price, t1: d.p2.t, p1: d.p2.price, color, dashed, width: 2, label });
          rsiShapes.push({ kind: "segment", t0: d.p1.t, p0: d.p1.rsi, t1: d.p2.t, p1: d.p2.rsi, color, dashed, width: 2 });
          segs += 1;
          if (d.confirmed_t && byTimeRef.current.has(d.confirmed_t)) {
            markers.push({ time: ts(d.confirmed_t), position: bull ? "belowBar" : "aboveBar", shape: "circle", color, text: bull ? "Bull div" : "Bear div", size: 0.8 } as SeriesMarker<Time>);
            techRoles[`divergence_${d.direction}`] = (techRoles[`divergence_${d.direction}`] ?? 0) + 1;
          }
        }
      }
      chartDebug.divergenceSegments = segs;
    } else {
      chartDebug.divergenceSegments = 0;
      chartDebug.dailyLevels = 0;
    }
    for (const m of props.extraMarkers ?? []) {
      if (!byTimeRef.current.has(m.t)) continue;
      markers.push({ time: ts(m.t), position: m.position === "above" ? "aboveBar" : m.position === "below" ? "belowBar" : "inBar", shape: m.shape, color: m.color, text: m.text, size: 1 } as SeriesMarker<Time>);
      techRoles.extra_marker = (techRoles.extra_marker ?? 0) + 1;
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersRef.current.setMarkers(markers);
    if (extraShapes?.length) shapes.push(...extraShapes);
    overlayRef.current.setShapes(shapes);
    subRef.current.rsiOverlay?.setShapes(rsiShapes);
    chartDebug.lineSeries = linesRef.current.length;
    chartDebug.markers = markers.length;
    chartDebug.markerRoles = roles;
    chartDebug.techMarkers = techRoles;
    chartDebug.overlayShapes = shapes.length;
    chartDebug.detectionId = detection?.pattern_id ?? null;
    reapplyView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detection, showOverlay, showSwings, swings, candles, zones, showZones, extraShapes, props.extraMarkers, tech, prefs.showMarubozu, prefs.showBreakouts, prefs.showDailyLevels, prefs.showDivergence, timeframe, indicators, prefs.showRsi]);

  // ---- manual drawings (separate primitive; never touches detector geometry)
  const drawings = props.drawings ?? [];
  useEffect(() => {
    drawRef.current?.update({ drawings, selectedId: props.selectedDrawing ?? null, visible: prefs.showDrawings });
    chartDebug.drawings = drawings.length;
    chartDebug.selectedDrawing = props.selectedDrawing ?? null;
  }, [drawings, props.selectedDrawing, prefs.showDrawings]);
  useEffect(() => {
    chartDebug.drawingTool = props.tool ?? "none";
    const creating = props.tool && props.tool !== "none" && props.tool !== "crosshair";
    chartRef.current?.applyOptions({ crosshair: { mode: props.tool === "crosshair" ? CrosshairMode.Magnet : CrosshairMode.Normal }, handleScroll: { pressedMouseMove: !creating } });
    if (containerRef.current) containerRef.current.style.cursor = creating ? "crosshair" : "";
  }, [props.tool]);
  useDrawingTools({
    container: containerRef,
    chart: chartRef,
    primitive: drawRef,
    drawings,
    visible: prefs.showDrawings,
    tool: props.tool ?? "none",
    style: props.drawStyle ?? { color: "#19C3DA", width: 2, line_style: "solid" },
    makeDrawing: props.makeDrawing,
    selectedId: props.selectedDrawing ?? null,
    onChange: props.onDrawingsChange,
    onSelect: props.onSelectDrawing,
    onToolDone: props.onToolDone,
  });

  // ---- viewport: focus the selected formation (also "Reset zoom")
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || candles.length === 0) return;
    const n = candles.length;
    let from = Math.max(0, n - 160);
    let to = n + 5;
    if (!detection && props.focusT) {
      const fi = candles.findIndex((c) => c.t >= props.focusT!);
      if (fi >= 0) from = Math.max(0, Math.min(fi - 30, n - 120));
    }
    if (detection) {
      const geo = detection.geometry;
      const start = candles.findIndex((c) => c.t === geo.formation.start.t);
      if (start >= 0) from = Math.max(0, start - 25);
      // Bars the projected boundaries extend past the last candle (apex).
      const ahead = Math.max(0, ...geo.elements.flatMap((e) => (e.type === "line" ? e.points.map((p) => p.i - geo.last_candle.i) : [])));
      to = n - 1 + Math.min(Math.max(6, ahead + 4), 40); // far apexes: scroll right to see them
    }
    viewRef.current = { from, to };
    userMovedRef.current = false;
    chart.timeScale().setVisibleLogicalRange({ from, to });
    reapplyView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, detection?.pattern_id, candles, props.focusT]);

  useEffect(() => {
    // Fit / Latest are explicit view choices: later series updates must not snap
    // the view back to the formation (reapplyView stops once the user moved it).
    if (latestKey > 0) {
      userMovedRef.current = true;
      chartRef.current?.timeScale().scrollToRealTime();
    }
  }, [latestKey]);
  useEffect(() => {
    if ((props.fitKey ?? 0) > 0) {
      userMovedRef.current = true;
      chartRef.current?.timeScale().fitContent();
    }
  }, [props.fitKey]);

  const shown = legend ?? (candles.length ? { ...candles[candles.length - 1] } : null);
  const hoverPartial = partial && (legend?.t === partial.t || (!legend && hoverT === partial.t));
  const li = (() => {
    if (!indicators) return null;
    const t = hoverT ?? indicators.times[indicators.times.length - 1];
    const i = indIndex.get(t);
    return i === undefined ? null : i;
  })();
  return (
    <div className={`relative ${props.height ?? "h-[62vh] min-h-[420px]"} w-full`} data-testid="pattern-chart">
      <div ref={containerRef} className="absolute inset-0" />
      {shown && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-page/80 px-2 py-1 text-[11px] text-muted num" data-testid="chart-legend">
          <span className="text-ink">{fmtBarTime(shown.t, timeframe)}</span>
          <span className="ml-3">O <span className="text-ink">{fmtNum(shown.o)}</span></span>
          <span className="ml-2">H <span className="text-ink">{fmtNum(shown.h)}</span></span>
          <span className="ml-2">L <span className="text-ink">{fmtNum(shown.l)}</span></span>
          <span className="ml-2">C <span className="text-ink">{fmtNum(shown.c)}</span></span>
          <span className="ml-2">Vol <span className="text-ink">{fmtVolume(shown.v)}</span></span>
          {hoverPartial && partial && (
            <span className="ml-2 text-forming" data-testid="partial-legend">
              incomplete period: {partial.sessions} sessions to {fmtBarTime(partial.last_session, "1d")}
            </span>
          )}
          {indicators && li !== null && (
            <div className="mt-0.5 flex flex-wrap gap-x-2" data-testid="indicator-legend">
              {prefs.emas
                .filter((e) => e.visible && indicators.ema[String(e.period)])
                .map((e) => (
                  <span key={e.period} style={{ color: e.color }}>
                    EMA{e.period} {fmtNum(indicators.ema[String(e.period)][li])}
                  </span>
                ))}
              {prefs.showRsi && <span style={{ color: TECH_COLORS.rsi }}>RSI({indicators.params.rsi_period}) {fmtNum(indicators.rsi[li], 1)}</span>}
              {prefs.showAtr && (
                <span style={{ color: TECH_COLORS.atr }}>
                  ATR({indicators.params.atr_period}) {fmtNum(indicators.atr[li])} ({fmtNum(indicators.atr_pct[li], 2)}%)
                </span>
              )}
            </div>
          )}
        </div>
      )}
      {inspect && (
        <div className="pointer-events-none absolute z-20 rounded border border-accent/50 bg-page/90 px-2 py-1 text-[11px] text-ink num" style={{ left: inspect.x + 14, top: inspect.y + 14 }} data-testid="crosshair-readout">
          <div>{fmtNum(inspect.price)}</div>
          {inspect.t !== null && <div className="text-muted">{fmtBarTime(inspect.t, timeframe)}</div>}
          {candles.length > 0 && <div className="text-muted">{(((inspect.price / candles[candles.length - 1].c) - 1) * 100).toFixed(2)}% vs last close</div>}
        </div>
      )}
    </div>
  );
}
