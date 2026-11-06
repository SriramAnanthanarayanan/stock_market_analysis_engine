import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Candle, IndicatorPayload, RSLinePayload, StageDetail, TechnicalPayload } from "../api/types";
import type { OverlayShape } from "./OverlayPrimitive";
import { ipoApi, multiYearApi, vcpApi, type IpoRow, type MultiYearEvent, type VcpSymbol } from "../api/advanced";
import { ipoMarkers, ipoShapes, ipoVolume, multiYearMarkers, multiYearShapes, vcpMarkers, vcpShapes, vcpVolume } from "./vcpOverlay";
import { loadLayout, loadPrefs, newId, saveLayout, savePrefs, type ChartPrefs } from "../lib/prefs";
import { drawingsApi } from "../api/advanced";
import { useDrawingStore } from "../lib/drawingStore";
import { defaultFibSettings } from "./fib";
import { isFib, type Anchor, type Drawing, type DrawingType } from "./drawings";
import { isScanTimeframe, type ChartTimeframe } from "../lib/timeframes";
import type { ChartHandle, ChartMarker, ExtraPane, OverlayLine, VolumeHighlight } from "./PatternChart";
import type { DrawStyle, DrawTool } from "./useDrawingTools";

interface Source {
  symbol: string;
  timeframe: ChartTimeframe;
  asof: string | null;
  scanId: string | null;
  candles: Candle[];
  instrument?: "stock" | "index";
}

/** State shared by every chart page (stock and index explorers): personal
 *  preferences, drawings, backend indicator series and technical overlays. */
export function useChartTerminal(chartId: string, src: Source | null, showParam?: string | null) {
  const [prefs, setPrefsState] = useState<ChartPrefs>(() => loadPrefs());
  const setPrefs = useCallback((p: ChartPrefs) => {
    setPrefsState(p);
    savePrefs(p);
  }, []);
  // "?show=rsi,marubozu,..." from a scanner row enables those overlays for this
  // chart only: the override is not written to the saved preferences, so opening a
  // preview from a scanner never changes the user's default chart layout.
  useEffect(() => {
    if (!showParam) return;
    const s = new Set(showParam.split(","));
    setPrefsState((p) => {
      const next = {
        ...p,
        showRsi: p.showRsi || s.has("rsi") || s.has("divergence"),
        showAtr: p.showAtr || s.has("atr"),
        showMarubozu: p.showMarubozu || s.has("marubozu"),
        showDailyLevels: p.showDailyLevels || s.has("levels"),
        showBreakouts: p.showBreakouts || s.has("breakouts"),
        showDivergence: p.showDivergence || s.has("divergence"),
        showRsLine: p.showRsLine || s.has("rs"),
        showStage: p.showStage || s.has("stage"),
        showVcp: p.showVcp || s.has("vcp"),
        showIpo: p.showIpo || s.has("ipo"),
        showMultiYear: p.showMultiYear || s.has("multiyear"),
        emas: s.has("ema") ? p.emas.map((e) => ({ ...e, visible: e.visible || [20, 50, 200].includes(e.period) })) : p.emas,
      };
      return next;
    });
  }, [showParam]);

  const [tool, setTool] = useState<DrawTool>("none");
  const [drawStyle, setDrawStyle] = useState<DrawStyle>({ color: "#19C3DA", width: 2, line_style: "solid" });
  const [selectedDrawing, setSelectedDrawing] = useState<string | null>(null);
  // Persistent drawings (database): per symbol, filtered by timeframe visibility.
  const instr = src?.instrument ?? "stock";
  const store = useDrawingStore(instr, src?.symbol ?? null, src?.timeframe ?? "1d");
  const drawings = useMemo(() => store.onTimeframe.filter((d) => d.visible), [store.onTimeframe]);
  useEffect(() => setSelectedDrawing(null), [src?.symbol, instr]);
  const setDrawings = useCallback((d: Drawing[]) => store.commit(drawings, d), [store, drawings]);
  const makeDrawing = useCallback(
    (type: DrawingType, anchors: Anchor[], style: DrawStyle): Drawing => ({
      drawing_id: newId(),
      instrument: instr,
      symbol: src?.symbol ?? "",
      drawing_type: type,
      anchors,
      color: isFib(type) ? "#8B9BA5" : style.color,
      line_style: style.line_style,
      line_width: style.width,
      style: {},
      settings: isFib(type) ? defaultFibSettings(type as "fib_retracement" | "fib_extension") : null,
      visible: true,
      timeframes: null,
      created_timeframe: src?.timeframe ?? null,
    }),
    [instr, src?.symbol, src?.timeframe],
  );
  void chartId;

  const [indicators, setIndicators] = useState<IndicatorPayload | null>(null);
  const [tech, setTech] = useState<TechnicalPayload | null>(null);
  const [techError, setTechError] = useState<string | null>(null);
  const periods = useMemo(() => [...new Set(prefs.emas.map((e) => e.period))].sort((a, b) => a - b).join(","), [prefs.emas]);
  const startT = src?.candles[0]?.t;
  useEffect(() => {
    if (!src || startT === undefined) return;
    let cancelled = false;
    const q = { symbol: src.symbol, timeframe: src.timeframe, asof: src.asof, scan_id: src.scanId, start_t: startT, instrument: src.instrument ?? "stock" };
    const withPartial = !!prefs.indicatorsIncludePartial && !["1h", "4h", "1d"].includes(src.timeframe);
    api
      .indicators({ ...q, ema: periods, rsi_period: prefs.rsiPeriod, atr_period: prefs.atrPeriod, include_partial: withPartial || null })
      .then((r) => !cancelled && (setIndicators(r), setTechError(null)))
      .catch((e) => !cancelled && (setIndicators(null), setTechError(e instanceof Error ? e.message : String(e))));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, src?.scanId, src?.instrument, startT, periods, prefs.rsiPeriod, prefs.atrPeriod, prefs.indicatorsIncludePartial]);
  useEffect(() => {
    if (!src || startT === undefined) return;
    let cancelled = false;
    api
      .technical({ symbol: src.symbol, timeframe: src.timeframe, asof: src.asof, scan_id: src.scanId, start_t: startT, confirm: prefs.breakoutConfirm, instrument: src.instrument ?? "stock" })
      .then((r) => !cancelled && setTech(r))
      .catch(() => !cancelled && setTech(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, src?.scanId, src?.instrument, startT, prefs.breakoutConfirm]);

  // ---- E3: RS Line pane (rs_line.txt) and Stage Analysis (weekly)
  const [rsLine, setRsLine] = useState<RSLinePayload | null>(null);
  const [stage, setStage] = useState<StageDetail | null>(null);
  const instrument = src?.instrument ?? "stock";
  useEffect(() => {
    if (!src || startT === undefined || !prefs.showRsLine || !isScanTimeframe(src.timeframe)) {
      setRsLine(null);
      return;
    }
    const tf = src.timeframe;
    let cancelled = false;
    api
      .rsline({ symbol: src.symbol, instrument, timeframe: tf, asof: src.asof, scan_id: instrument === "stock" ? src.scanId : null, start_t: startT })
      .then((r) => !cancelled && setRsLine(r))
      .catch(() => !cancelled && setRsLine(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, src?.scanId, instrument, startT, prefs.showRsLine]);
  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    api
      .stageSymbol({ symbol: src.symbol, instrument, asof: src.asof, start_t: src.timeframe === "1w" ? startT : undefined })
      .then((r) => !cancelled && setStage(r))
      .catch(() => !cancelled && setStage(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.asof, src?.timeframe, instrument, startT]);
  const extraPanes = useMemo<ExtraPane[]>(() => {
    if (!rsLine || !prefs.showRsLine) return [];
    return [
      {
        id: "rs",
        title: `RS-${rsLine.length} vs ${rsLine.benchmark}`,
        color: "#3FBF9A",
        times: rsLine.times,
        values: rsLine.rs,
        colors: rsLine.positive.map((p) => (p === null ? null : p ? "#3FBF9A" : "#E8684A")), // rs_line.txt L30
        zeroLine: true,
        markers: rsLine.bubbles.map((b) => ({ t: b.t, color: b.kind === "bullish" ? "rgba(63,191,154,0.55)" : "rgba(232,104,74,0.55)" })), // L54 confirmation bubbles
      },
    ];
  }, [rsLine, prefs.showRsLine]);
  const stageOverlay = useMemo<{ lines: OverlayLine[]; shapes: OverlayShape[] }>(() => {
    if (!stage?.overlay || !prefs.showStage || src?.timeframe !== "1w") return { lines: [], shapes: [] };
    const ov = stage.overlay;
    const shapes: OverlayShape[] = [];
    if (ov.base && ov.base.high !== null && ov.base.low !== null) {
      shapes.push({ kind: "segment", t0: ov.base.t_start, t1: ov.base.t_end, p0: ov.base.high, p1: ov.base.high, color: "#F5C542", dashed: true, width: 1, label: `Base resistance ${ov.base.high.toFixed(2)}` });
      shapes.push({ kind: "segment", t0: ov.base.t_start, t1: ov.base.t_end, p0: ov.base.low, p1: ov.base.low, color: "#F5C542", dashed: true, width: 1, label: `Base support ${ov.base.low.toFixed(2)}` });
    }
    return { lines: [{ id: "30W EMA", color: "#E3A33B", times: ov.times, values: ov.ema30, width: 2 }], shapes };
  }, [stage, prefs.showStage, src?.timeframe]);

  // ---- VCP geometry of the latest daily scan (daily charts only; never re-detected here)
  const [vcp, setVcp] = useState<VcpSymbol | null>(null);
  useEffect(() => {
    if (!src || instrument !== "stock" || !prefs.showVcp || src.timeframe !== "1d") {
      setVcp(null);
      return;
    }
    let cancelled = false;
    vcpApi
      .symbol(src.symbol, src.asof)
      .then((r) => !cancelled && setVcp(r))
      .catch(() => !cancelled && setVcp(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, instrument, prefs.showVcp]);
  const vcpOverlay = useMemo(() => {
    const g = vcp?.vcp?.geometry;
    if (!g || !prefs.showVcp || !g.schema_version?.startsWith("vcp-2")) return { shapes: [] as OverlayShape[], markers: [] as ChartMarker[], volume: [] as VolumeHighlight[] };
    return { shapes: vcpShapes(g, vcp?.vcp?.last_close), markers: vcpMarkers(g), volume: vcpVolume(g) };
  }, [vcp, prefs.showVcp]);

  // ---- IPO base geometry of the latest daily scan (daily charts only)
  const [ipo, setIpo] = useState<IpoRow | null>(null);
  useEffect(() => {
    if (!src || instrument !== "stock" || !prefs.showIpo || src.timeframe !== "1d") {
      setIpo(null);
      return;
    }
    let cancelled = false;
    ipoApi
      .symbol(src.symbol, src.asof)
      .then((r) => !cancelled && setIpo(r.ipo))
      .catch(() => !cancelled && setIpo(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, instrument, prefs.showIpo]);
  const ipoOverlay = useMemo(() => {
    const g = ipo?.geometry;
    if (!g || !prefs.showIpo) return { shapes: [] as OverlayShape[], markers: [] as ChartMarker[], volume: [] as VolumeHighlight[] };
    return { shapes: ipoShapes(g), markers: ipoMarkers(g), volume: ipoVolume(g) };
  }, [ipo, prefs.showIpo]);

  // ---- multi-year breakouts of the stock's history (daily detector; drawn on 1D and longer timeframes)
  const [multiYear, setMultiYear] = useState<MultiYearEvent[] | null>(null);
  const myTimeframeOk = !!src && src.timeframe !== "1h" && src.timeframe !== "4h";
  useEffect(() => {
    if (!src || instrument !== "stock" || !prefs.showMultiYear || !myTimeframeOk) {
      setMultiYear(null);
      return;
    }
    let cancelled = false;
    multiYearApi
      .symbol(src.symbol, src.asof)
      .then((r) => !cancelled && setMultiYear(r.events))
      .catch(() => !cancelled && setMultiYear(null));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.asof, instrument, prefs.showMultiYear, myTimeframeOk]);
  const multiYearOverlay = useMemo(() => {
    if (!multiYear || !prefs.showMultiYear) return { shapes: [] as OverlayShape[], markers: [] as ChartMarker[] };
    return { shapes: multiYearShapes(multiYear), markers: multiYearMarkers(multiYear) };
  }, [multiYear, prefs.showMultiYear]);

  const handle = useRef<ChartHandle | null>(null);
  const onReady = useCallback((h: ChartHandle | null) => {
    handle.current = h;
  }, []);
  const [fitKey, setFitKey] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const flash = (m: string) => {
    setMessage(m);
    window.setTimeout(() => setMessage(null), 3500);
  };

  const download = (name: string, href: string) => {
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const actions = {
    onFit: () => setFitKey((x) => x + 1),
    onSaveLayout: () => flash(saveLayout(prefs, handle.current?.paneStretch() ?? []) ? "Layout saved in this browser." : "Could not save: browser storage unavailable."),
    onRestoreLayout: () => {
      const l = loadLayout();
      if (!l) return flash("No saved layout yet.");
      setPrefs(l.prefs);
      window.setTimeout(() => handle.current?.setPaneStretch(l.paneStretch), 150);
      flash(`Layout restored (saved ${new Date(l.savedAt).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}).`);
    },
    onScreenshot: () => {
      const c = handle.current?.screenshot();
      if (!c || !src) return flash("Chart image export is not supported here.");
      download(`${src.symbol}_${src.timeframe}.png`, c.toDataURL("image/png"));
    },
    onExportDrawings: () => {
      // Every drawing of every symbol (database backup), downloaded by the browser.
      void store.saveNow().finally(() => download("chart-drawings.json", drawingsApi.exportUrl()));
    },
    onImportDrawings: (file: File) => {
      file
        .text()
        .then((txt) => drawingsApi.import(JSON.parse(txt)))
        .then((r) => {
          void store.reload();
          flash(`Restore: ${r.inserted} added, ${r.updated} updated, ${r.skipped} unchanged${r.invalid ? `, ${r.invalid} invalid` : ""}.`);
        })
        .catch(() => flash("Restore failed: not a drawings backup file."));
    },
  };

  return {
    prefs,
    setPrefs,
    tool,
    setTool,
    drawStyle,
    setDrawStyle,
    drawings,
    setDrawings,
    makeDrawing,
    drawingStore: store,
    selectedDrawing,
    setSelectedDrawing,
    indicators,
    tech,
    techError,
    rsLine,
    stage,
    vcp,
    vcpOverlay,
    ipo,
    ipoOverlay,
    multiYear,
    multiYearOverlay,
    extraPanes,
    stageOverlay,
    onReady,
    fitKey,
    actions,
    message,
  };
}
