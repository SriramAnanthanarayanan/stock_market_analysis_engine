import { useRef, useState } from "react";
import type { DrawStyle, DrawTool } from "../charts/useDrawingTools";
import type { Drawing } from "../charts/drawings";
import type { SaveState } from "../lib/drawingStore";
import { DEFAULT_EMAS, DEFAULT_PREFS, newId, type ChartPrefs } from "../lib/prefs";
import { drawingUser, setDrawingUser } from "../api/advanced";
import { DrawingSettingsPanel } from "./DrawingSettingsPanel";

const TOOLS: { id: DrawTool; label: string; title: string }[] = [
  { id: "none", label: "Pan", title: "Pan / select drawings (drag a drawing to move it, drag a handle to move one anchor)" },
  { id: "crosshair", label: "Inspect", title: "Crosshair inspection: price and time under the cursor" },
  { id: "hline", label: "H-line", title: "Horizontal line (one click)" },
  { id: "trend", label: "Trend", title: "Trendline (click start, click end)" },
  { id: "ray", label: "Ray", title: "Ray extending to the right (click start, click direction)" },
  { id: "rect", label: "Zone", title: "Rectangle / support or resistance zone (click two corners)" },
  { id: "channel", label: "Channel", title: "Parallel channel (click the two points of the first line, then a point on the parallel line)" },
  { id: "vline", label: "V-line", title: "Vertical line (one click)" },
  { id: "fib_retracement", label: "Fib", title: "Fibonacci retracement: click the start (P1) and end (P2) of the move; 0 at P2, 1 at P1" },
  { id: "fib_extension", label: "Fib ext", title: "Trend-based Fibonacci extension: click P1, P2 (the move) and P3 (end of the retracement); 1.0 = the P1-P2 move projected from P3" },
  { id: "text", label: "Text", title: "Annotation: click where the note goes, then edit its text in Settings" },
];

const SAVE_LABEL: Record<SaveState, string> = { loading: "Loading drawings...", saved: "All drawings saved", saving: "Saving...", unsaved: "Unsaved changes", error: "Save failed - retrying", offline: "Drawings offline" };

export interface DrawingStoreApi {
  state: SaveState;
  message: string | null;
  all: Drawing[];
  patch: (id: string, change: Partial<Drawing>) => void;
  add: (d: Drawing) => void;
  saveNow: () => Promise<void>;
}

interface Props {
  prefs: ChartPrefs;
  setPrefs: (p: ChartPrefs) => void;
  tool: DrawTool;
  setTool: (t: DrawTool) => void;
  drawStyle: DrawStyle;
  setDrawStyle: (s: DrawStyle) => void;
  drawings: Drawing[];
  setDrawings: (d: Drawing[]) => void;
  store?: DrawingStoreApi;
  timeframe?: string;
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const sel = p.drawings.find((d) => d.drawing_id === p.selectedDrawing) ?? null;
  const style: DrawStyle = sel ? { color: sel.color, width: Math.max(1, Math.min(4, sel.line_width)) as DrawStyle["width"], line_style: sel.line_style } : p.drawStyle;
  const applyStyle = (patch: Partial<DrawStyle>) => {
    if (sel) {
      const change: Partial<Drawing> = {};
      if (patch.color !== undefined) change.color = patch.color;
      if (patch.width !== undefined) change.line_width = patch.width;
      if (patch.line_style !== undefined) change.line_style = patch.line_style;
      p.setDrawings(p.drawings.map((d) => (d.drawing_id === sel.drawing_id ? { ...d, ...change } : d)));
    } else p.setDrawStyle({ ...p.drawStyle, ...patch });
  };
  const hidden = (p.store?.all ?? []).filter((d) => !d.visible && (d.timeframes === null || !p.timeframe || d.timeframes.includes(p.timeframe)));
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
        <button className={chip(!!prefs.showIpo)} onClick={() => set({ showIpo: !prefs.showIpo })} title={p.dailyOnlyNote ? "The IPO base is detected on daily candles: switch to 1D to see it" : "IPO base of the latest daily scan: listing date, price discovery, base, pivot, breakout"} data-testid="toggle-ipo">
          IPO base
        </button>
        <button className={chip(!!prefs.showMultiYear)} onClick={() => set({ showMultiYear: !prefs.showMultiYear })} title="Multi-year breakouts of the stock's history: the resistance line from the base start and the breakout candle (daily detector; shown on 1D and longer timeframes)" data-testid="toggle-multiyear">
          Multi-year
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
        <input type="color" className="h-7 w-8 bg-transparent" value={style.color.startsWith("#") ? style.color.slice(0, 7) : "#19C3DA"} onChange={(e) => applyStyle({ color: e.target.value })} title={sel ? "Colour of the selected drawing" : "Colour for new drawings"} data-testid="draw-color" />
        <select className="ctl h-7" value={style.width} onChange={(e) => applyStyle({ width: Number(e.target.value) as DrawStyle["width"] })} title="Line width" data-testid="draw-width">
          {[1, 2, 3, 4].map((w) => (
            <option key={w} value={w}>
              {w}px
            </option>
          ))}
        </select>
        <select className="ctl h-7" value={style.line_style} onChange={(e) => applyStyle({ line_style: e.target.value as DrawStyle["line_style"] })} title="Line style" data-testid="draw-line-style">
          <option value="solid">Solid</option>
          <option value="dashed">Dashed</option>
          <option value="dotted">Dotted</option>
        </select>
        <button className={chip(settingsOpen && !!sel)} disabled={!sel || !p.store} onClick={() => setSettingsOpen((x) => !x)} title="Settings of the selected drawing (levels, labels, anchors, timeframes, templates)" data-testid="draw-settings">
          Settings
        </button>
        <button
          className="chip h-7"
          disabled={!sel || !p.store}
          onClick={() => {
            if (!sel || !p.store) return;
            const copy: Drawing = { ...sel, drawing_id: newId(), version: undefined, created_at: undefined, updated_at: undefined, anchors: sel.anchors.map((a) => ({ ...a, p: a.p * 1.01 })) };
            p.store.add(copy);
            p.setSelectedDrawing(copy.drawing_id);
          }}
          title="Duplicate the selected drawing (offset 1 % in price)"
          data-testid="draw-duplicate"
        >
          Duplicate
        </button>
        <button className="chip h-7" disabled={!sel || !p.store} onClick={() => sel && p.store && (p.store.patch(sel.drawing_id, { visible: false }), p.setSelectedDrawing(null))} title="Hide the selected drawing (kept; show it again from the hidden list)" data-testid="draw-hide-one">
          Hide
        </button>
        {hidden.length > 0 && p.store && (
          <button className="chip h-7" onClick={() => hidden.forEach((d) => p.store!.patch(d.drawing_id, { visible: true }))} title={hidden.map((d) => d.drawing_type).join(", ")} data-testid="draw-show-hidden">
            Show {hidden.length} hidden
          </button>
        )}
        <button className="chip h-7" disabled={!sel} onClick={() => sel && (p.setDrawings(p.drawings.filter((d) => d.drawing_id !== sel.drawing_id)), p.setSelectedDrawing(null))} data-testid="draw-delete">
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
        {p.store && (
          <>
            <button className="btn h-7" onClick={() => void p.store!.saveNow()} disabled={p.store.state === "saving"} title="Drawings save automatically; this saves pending changes now" data-testid="draw-save">
              Save
            </button>
            <span className={`text-[11px] ${p.store.state === "saved" ? "text-muted" : p.store.state === "error" || p.store.state === "offline" ? "text-down" : "text-forming"}`} data-testid="draw-save-state" data-state={p.store.state}>
              {SAVE_LABEL[p.store.state]}
            </span>
            {p.store.message && <span className="text-[11px] text-forming" data-testid="draw-store-message">{p.store.message}</span>}
          </>
        )}
        <span className="mx-1 h-5 w-px bg-line" />
        <button className="btn h-7" onClick={p.onFit} title="Zoom out to the whole history" data-testid="fit-content">
          Fit
        </button>
        <span className="inline-flex overflow-hidden rounded border border-line" title="Chart type: candlesticks or a line of closing prices (same candles, indicators, overlays and drawings)">
          <button className={`h-7 px-2 text-[11px] ${(prefs.chartType ?? "candle") === "candle" ? "bg-accent/20 text-accent" : "text-muted"}`} onClick={() => set({ chartType: "candle" })} data-testid="chart-type-candle">
            Candles
          </button>
          <button className={`h-7 px-2 text-[11px] ${prefs.chartType === "line" ? "bg-accent/20 text-accent" : "text-muted"}`} onClick={() => set({ chartType: "line" })} data-testid="chart-type-line">
            Line
          </button>
        </span>
        <button className={chip(prefs.logScale)} onClick={() => set({ logScale: !prefs.logScale })} title="Logarithmic price scale: equal % moves look equal across decades" data-testid="toggle-log">
          Log
        </button>
        <button
          className={chip(!!prefs.indicatorsIncludePartial)}
          onClick={() => set({ indicatorsIncludePartial: !prefs.indicatorsIncludePartial })}
          title="1W / multi-month: also compute EMA / RSI / ATR on the running (incomplete) candle; its values are provisional. Detection never uses it."
          data-testid="toggle-ind-partial"
        >
          Ind. incl. incomplete
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
        <button className="btn h-7" onClick={p.onExportDrawings} title="Backup: download every drawing (all symbols) and templates as JSON" data-testid="export-drawings">
          Export drawings
        </button>
        <button className="btn h-7" onClick={() => fileRef.current?.click()} data-testid="import-drawings">
          Import
        </button>
        <label className="flex items-center gap-1 text-[11px] text-muted" title="Drawings are stored per user + symbol (+ the timeframe they were drawn on). 'local' is the single local user.">
          User
          <input
            className="ctl h-7 w-20"
            defaultValue={drawingUser()}
            onBlur={(e) => {
              if (e.target.value.trim() !== drawingUser()) {
                setDrawingUser(e.target.value);
                window.location.reload();
              }
            }}
            data-testid="draw-user"
          />
        </label>
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
      {settingsOpen && sel && p.store && <DrawingSettingsPanel d={sel} timeframe={p.timeframe ?? "1d"} onPatch={(change) => p.store!.patch(sel.drawing_id, change)} onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
