import { useRef, useState } from "react";
import type { DrawStyle, DrawTool } from "../charts/useDrawingTools";
import { DEFAULT_EMAS, DEFAULT_PREFS, type ChartPrefs, type Drawing } from "../lib/prefs";

const TOOLS: { id: DrawTool; label: string; title: string }[] = [
  { id: "none", label: "Pan", title: "Pan / select drawings (drag a drawing to move it, drag a handle to resize)" },
  { id: "crosshair", label: "Inspect", title: "Crosshair inspection: price and time under the cursor" },
  { id: "hline", label: "H-line", title: "Horizontal line (one click)" },
  { id: "trend", label: "Trend", title: "Trendline (click start, click end)" },
  { id: "ray", label: "Ray", title: "Ray extending to the right (click start, click direction)" },
  { id: "rect", label: "Zone", title: "Rectangle / price zone (click two corners)" },
  { id: "vline", label: "V-line", title: "Vertical line (one click)" },
  { id: "fib", label: "Fib", title: "Fibonacci retracement (click start, click end; 1 = start, 0 = end)" },
];

interface Props {
  prefs: ChartPrefs;
  setPrefs: (p: ChartPrefs) => void;
  tool: DrawTool;
  setTool: (t: DrawTool) => void;
  drawStyle: DrawStyle;
  setDrawStyle: (s: DrawStyle) => void;
  drawings: Drawing[];
  setDrawings: (d: Drawing[]) => void;
  selectedDrawing: string | null;
  setSelectedDrawing: (id: string | null) => void;
  onFit: () => void;
  onReset: () => void;
  onLatest: () => void;
  onSaveLayout: () => void;
  onRestoreLayout: () => void;
  onScreenshot: () => void;
  onExportDrawings: () => void;
  onImportDrawings: (file: File) => void;
  dailyOnlyNote?: boolean;
}

export function ChartTerminalBar(p: Props) {
  const [open, setOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const { prefs, setPrefs } = p;
  const set = (patch: Partial<ChartPrefs>) => setPrefs({ ...prefs, ...patch });
  const setEma = (i: number, patch: Partial<ChartPrefs["emas"][number]>) => setPrefs({ ...prefs, emas: prefs.emas.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
  const sel = p.drawings.find((d) => d.id === p.selectedDrawing) ?? null;
  const style: DrawStyle = sel ? { color: sel.color, width: sel.width, dashed: sel.dashed } : p.drawStyle;
  const applyStyle = (patch: Partial<DrawStyle>) => {
    if (sel) p.setDrawings(p.drawings.map((d) => (d.id === sel.id ? { ...d, ...patch } : d)));
    else p.setDrawStyle({ ...p.drawStyle, ...patch });
  };
  const chip = (on: boolean) => `chip h-7 ${on ? "chip-on" : ""}`;

  return (
    <div className="flex flex-col gap-1.5" data-testid="terminal-bar">
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="relative">
          <button className={chip(open)} onClick={() => setOpen((x) => !x)} data-testid="indicators-menu">
            Indicators &#9662;
          </button>
          {open && (
            <div className="absolute left-0 top-8 z-30 w-[420px] rounded-md border border-line bg-panel p-3 text-xs shadow-xl" data-testid="indicator-settings">
              <div className="mb-1 flex items-center justify-between">
                <span className="label">EMA overlays (backend-computed, TradingView ta.ema seed)</span>
                <button className="text-accent hover:underline" onClick={() => set({ emas: DEFAULT_EMAS.map((e) => ({ ...e })) })} data-testid="ema-reset">
                  Restore defaults
                </button>
              </div>
              {prefs.emas.map((e, i) => (
                <div key={i} className="mb-1 flex items-center gap-2">
                  <input type="checkbox" checked={e.visible} onChange={(ev) => setEma(i, { visible: ev.target.checked })} data-testid={`ema-visible-${i}`} />
                  <span className="w-8 text-muted">EMA</span>
                  <input
                    className="ctl h-7 w-16"
                    type="number"
                    min={1}
                    max={400}
                    value={e.period}
                    onChange={(ev) => {
                      const v = Math.round(Number(ev.target.value));
                      if (v >= 1 && v <= 400) setEma(i, { period: v });
                    }}
                    data-testid={`ema-period-${i}`}
                  />
                  <input type="color" value={e.color} onChange={(ev) => setEma(i, { color: ev.target.value })} className="h-6 w-8 bg-transparent" title="Line colour" />
                  <select className="ctl h-7" value={e.width} onChange={(ev) => setEma(i, { width: Number(ev.target.value) as 1 | 2 | 3 })} title="Line width">
                    <option value={1}>1px</option>
                    <option value={2}>2px</option>
                    <option value={3}>3px</option>
                  </select>
                </div>
              ))}
              <div className="mt-2 grid grid-cols-2 gap-2 border-t border-line pt-2">
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={prefs.showRsi} onChange={(e) => set({ showRsi: e.target.checked })} /> RSI pane
                </label>
                <span className="flex items-center gap-1 text-muted">
                  period
                  <input className="ctl h-7 w-14" type="number" min={2} max={100} value={prefs.rsiPeriod} onChange={(e) => set({ rsiPeriod: Math.max(2, Math.min(100, Math.round(Number(e.target.value)) || 14)) })} data-testid="rsi-period" />
                </span>
                <span className="flex items-center gap-1 text-muted">
                  OB
                  <input className="ctl h-7 w-14" type="number" min={50} max={100} value={prefs.rsiOverbought} onChange={(e) => set({ rsiOverbought: Number(e.target.value) })} />
                  OS
                  <input className="ctl h-7 w-14" type="number" min={0} max={50} value={prefs.rsiOversold} onChange={(e) => set({ rsiOversold: Number(e.target.value) })} />
                </span>
                <span />
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={prefs.showAtr} onChange={(e) => set({ showAtr: e.target.checked })} /> ATR pane
                </label>
                <span className="flex items-center gap-1 text-muted">
                  period
                  <input className="ctl h-7 w-14" type="number" min={2} max={100} value={prefs.atrPeriod} onChange={(e) => set({ atrPeriod: Math.max(2, Math.min(100, Math.round(Number(e.target.value)) || 14)) })} />
                </span>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={prefs.showBreakouts} onChange={(e) => set({ showBreakouts: e.target.checked })} /> Breakout markers (1D)
                </label>
                <select className="ctl h-7" value={prefs.breakoutConfirm} onChange={(e) => set({ breakoutConfirm: Number(e.target.value) as 1 | 2 })} title="Confirmation mode" data-testid="breakout-confirm">
                  <option value={1}>1-close confirmation</option>
                  <option value={2}>2-close confirmation</option>
                </select>
              </div>
              <div className="mt-2 flex justify-between border-t border-line pt-2">
                <button className="text-accent hover:underline" onClick={() => setPrefs({ ...DEFAULT_PREFS, emas: DEFAULT_EMAS.map((e) => ({ ...e })) })}>
                  Restore all chart defaults
                </button>
                <button className="btn h-7" onClick={() => setOpen(false)}>
                  Close
                </button>
              </div>
            </div>
          )}
        </div>
        {prefs.emas.map((e, i) => (
          <button key={i} className={chip(e.visible)} style={e.visible ? { borderColor: e.color, color: e.color } : undefined} onClick={() => setEma(i, { visible: !e.visible })} data-testid={`ema-toggle-${e.period}`}>
            EMA {e.period}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-line" />
        <button className={chip(prefs.showRsi)} onClick={() => set({ showRsi: !prefs.showRsi })} data-testid="toggle-rsi">
          RSI
        </button>
        <button className={chip(prefs.showAtr)} onClick={() => set({ showAtr: !prefs.showAtr })} data-testid="toggle-atr">
          ATR
        </button>
        <button className={chip(prefs.showDivergence)} onClick={() => set({ showDivergence: !prefs.showDivergence })} title="Regular RSI divergence on confirmed swing points" data-testid="toggle-divergence">
          RSI divergence
        </button>
        <button className={chip(prefs.showMarubozu)} onClick={() => set({ showMarubozu: !prefs.showMarubozu })} data-testid="toggle-marubozu">
          Marubozu
        </button>
        <button className={chip(prefs.showDailyLevels)} onClick={() => set({ showDailyLevels: !prefs.showDailyLevels })} title="Previous 10 completed daily candles' high / low (daily-derived on 1H/4H)" data-testid="toggle-daily-levels">
          10D levels
        </button>
        <button className={chip(prefs.showRsLine)} onClick={() => set({ showRsLine: !prefs.showRsLine })} title="RS Line of rs_line.txt vs NIFTY 50 (52 on 1W, 63 on 1D, 123 on 1H/4H)" data-testid="toggle-rsline">
          RS Line
        </button>
        <button className={chip(prefs.showStage)} onClick={() => set({ showStage: !prefs.showStage })} title="Stage Analysis overlay on the weekly chart: 30W EMA and base resistance / support" data-testid="toggle-stage">
          Stage (1W)
        </button>
        <button className={chip(prefs.showBreakouts)} onClick={() => set({ showBreakouts: !prefs.showBreakouts })} title={p.dailyOnlyNote ? "Breakout markers are drawn on the daily chart" : "10-day breakout / breakdown markers"} data-testid="toggle-breakouts">
          Breakouts
        </button>
        <button className={chip(prefs.showVcp)} onClick={() => set({ showVcp: !prefs.showVcp })} title={p.dailyOnlyNote ? "VCP is detected on daily candles: switch to 1D to see it" : "Volatility Contraction Pattern of the latest daily scan: base, T1/T2/... contractions, pivot, breakout"} data-testid="toggle-vcp">
          VCP (1D)
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex gap-1" role="group" aria-label="Drawing tools">
          {TOOLS.map((t) => (
            <button key={t.id} className={chip(p.tool === t.id)} title={t.title} onClick={() => p.setTool(t.id)} data-testid={`tool-${t.id}`}>
              {t.label}
            </button>
          ))}
        </div>
        <input type="color" className="h-7 w-8 bg-transparent" value={style.color} onChange={(e) => applyStyle({ color: e.target.value })} title={sel ? "Colour of the selected drawing" : "Colour for new drawings"} data-testid="draw-color" />
        <select className="ctl h-7" value={style.width} onChange={(e) => applyStyle({ width: Number(e.target.value) as 1 | 2 | 3 })} title="Line width" data-testid="draw-width">
          <option value={1}>1px</option>
          <option value={2}>2px</option>
          <option value={3}>3px</option>
        </select>
        <button className={chip(style.dashed)} onClick={() => applyStyle({ dashed: !style.dashed })} data-testid="draw-dashed">
          Dashed
        </button>
        <button className="chip h-7" disabled={!sel} onClick={() => sel && (p.setDrawings(p.drawings.filter((d) => d.id !== sel.id)), p.setSelectedDrawing(null))} data-testid="draw-delete">
          Delete
        </button>
        <button className={chip(!prefs.showDrawings)} onClick={() => set({ showDrawings: !prefs.showDrawings })} data-testid="draw-hide">
          {prefs.showDrawings ? "Hide drawings" : "Show drawings"}
        </button>
        <button
          className="chip h-7"
          disabled={!p.drawings.length}
          onClick={() => {
            if (window.confirm(`Delete all ${p.drawings.length} drawings on this chart? This cannot be undone (export first to keep a backup).`)) {
              p.setDrawings([]);
              p.setSelectedDrawing(null);
            }
          }}
          data-testid="draw-clear"
        >
          Clear all
        </button>
        <span className="text-[11px] text-muted" data-testid="drawing-count">
          {p.drawings.length} drawing{p.drawings.length === 1 ? "" : "s"}
        </span>
        <span className="mx-1 h-5 w-px bg-line" />
        <button className="btn h-7" onClick={p.onFit} title="Zoom out to the whole history" data-testid="fit-content">
          Fit
        </button>
        <button className={chip(prefs.logScale)} onClick={() => set({ logScale: !prefs.logScale })} title="Logarithmic price scale: equal % moves look equal across decades" data-testid="toggle-log">
          Log
        </button>
        <button className="btn h-7" onClick={p.onReset} data-testid="reset-zoom">
          Reset zoom
        </button>
        <button className="btn h-7" onClick={p.onLatest} data-testid="go-latest">
          Latest candle
        </button>
        <button className="btn h-7" onClick={p.onSaveLayout} title="Save indicators, panes and their heights" data-testid="save-layout">
          Save layout
        </button>
        <button className="btn h-7" onClick={p.onRestoreLayout} data-testid="restore-layout">
          Restore layout
        </button>
        <button className="btn h-7" onClick={p.onScreenshot} data-testid="export-image">
          PNG
        </button>
        <button className="btn h-7" onClick={p.onExportDrawings} title="Download all drawings (every symbol/timeframe) as JSON" data-testid="export-drawings">
          Export drawings
        </button>
        <button className="btn h-7" onClick={() => fileRef.current?.click()} data-testid="import-drawings">
          Import
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) p.onImportDrawings(f);
            e.target.value = "";
          }}
          data-testid="import-file"
        />
      </div>
    </div>
  );
}
