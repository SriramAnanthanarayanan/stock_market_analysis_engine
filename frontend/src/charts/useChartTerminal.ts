import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Candle, IndicatorPayload, RSLinePayload, StageDetail, TechnicalPayload, Timeframe } from "../api/types";
import type { OverlayShape } from "./OverlayPrimitive";
import {
  drawingKey,
  exportAllDrawings,
  importDrawings,
  loadDrawings,
  loadLayout,
  loadPrefs,
  saveDrawings,
  saveLayout,
  savePrefs,
  type ChartPrefs,
  type Drawing,
} from "../lib/prefs";
import type { ChartHandle, ExtraPane, OverlayLine } from "./PatternChart";
import type { DrawStyle, DrawTool } from "./useDrawingTools";

interface Source {
  symbol: string;
  timeframe: Timeframe;
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
  // "?show=rsi,marubozu,..." from a scanner row enables those overlays once.
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
        emas: s.has("ema") ? p.emas.map((e) => ({ ...e, visible: e.visible || [20, 50, 200].includes(e.period) })) : p.emas,
      };
      savePrefs(next);
      return next;
    });
  }, [showParam]);

  const [tool, setTool] = useState<DrawTool>("none");
  const [drawStyle, setDrawStyle] = useState<DrawStyle>({ color: "#19C3DA", width: 2, dashed: false });
  const [selectedDrawing, setSelectedDrawing] = useState<string | null>(null);
  const key = src ? drawingKey(chartId, src.symbol, src.timeframe) : null;
  const [drawings, setDrawingsState] = useState<Drawing[]>([]);
  useEffect(() => {
    setDrawingsState(key ? loadDrawings(key) : []);
    setSelectedDrawing(null);
  }, [key]);
  const setDrawings = useCallback(
    (d: Drawing[]) => {
      setDrawingsState(d);
      if (key) saveDrawings(key, d);
    },
    [key],
  );

  const [indicators, setIndicators] = useState<IndicatorPayload | null>(null);
  const [tech, setTech] = useState<TechnicalPayload | null>(null);
  const [techError, setTechError] = useState<string | null>(null);
  const periods = useMemo(() => [...new Set(prefs.emas.map((e) => e.period))].sort((a, b) => a - b).join(","), [prefs.emas]);
  const startT = src?.candles[0]?.t;
  useEffect(() => {
    if (!src || startT === undefined) return;
    let cancelled = false;
    const q = { symbol: src.symbol, timeframe: src.timeframe, asof: src.asof, scan_id: src.scanId, start_t: startT, instrument: src.instrument ?? "stock" };
    api
      .indicators({ ...q, ema: periods, rsi_period: prefs.rsiPeriod, atr_period: prefs.atrPeriod })
      .then((r) => !cancelled && (setIndicators(r), setTechError(null)))
      .catch((e) => !cancelled && (setIndicators(null), setTechError(e instanceof Error ? e.message : String(e))));
    return () => {
      cancelled = true;
    };
  }, [src?.symbol, src?.timeframe, src?.asof, src?.scanId, src?.instrument, startT, periods, prefs.rsiPeriod, prefs.atrPeriod]);
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
    if (!src || startT === undefined || !prefs.showRsLine) {
      setRsLine(null);
      return;
    }
    let cancelled = false;
    api
      .rsline({ symbol: src.symbol, instrument, timeframe: src.timeframe, asof: src.asof, scan_id: instrument === "stock" ? src.scanId : null, start_t: startT })
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
      const blob = new Blob([JSON.stringify({ format: "pas-drawings-v1", exported_at: new Date().toISOString(), drawings: exportAllDrawings() }, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      download("chart-drawings.json", url);
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    onImportDrawings: (file: File) => {
      file
        .text()
        .then((txt) => {
          const parsed = JSON.parse(txt);
          const n = importDrawings(parsed?.drawings ?? parsed);
          if (key) setDrawingsState(loadDrawings(key));
          flash(`Imported ${n} drawings.`);
        })
        .catch(() => flash("Import failed: not a drawings backup file."));
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
    selectedDrawing,
    setSelectedDrawing,
    indicators,
    tech,
    techError,
    rsLine,
    stage,
    extraPanes,
    stageOverlay,
    onReady,
    fitKey,
    actions,
    message,
  };
}
