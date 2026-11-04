import { useEffect, useState } from "react";
import { drawingsApi, type DrawingTemplate } from "../api/advanced";
import { DRAWING_LABELS, fibSettingsOf, isFib, type Anchor, type Drawing } from "../charts/drawings";
import { LEVEL_COLORS, defaultLevels, logAllowed, type FibLevelCfg, type FibSettings } from "../charts/fib";
import { newId } from "../lib/prefs";
import { CHART_TIMEFRAMES } from "../lib/timeframes";

const IST_OFFSET = 5.5 * 3600;

/** epoch seconds -> "YYYY-MM-DDTHH:MM" in IST (for datetime-local inputs). */
export function toIstInput(t: number): string {
  return new Date((t + IST_OFFSET) * 1000).toISOString().slice(0, 16);
}
export function fromIstInput(v: string): number | null {
  const ms = Date.parse(`${v}:00Z`);
  return Number.isFinite(ms) ? Math.round(ms / 1000 - IST_OFFSET) : null;
}

interface Props {
  d: Drawing;
  timeframe: string;
  onPatch: (change: Partial<Drawing>) => void;
  onClose: () => void;
}

/** Settings of the selected drawing: style, visibility by timeframe, anchors and
 *  (Fibonacci) levels, calculation mode, labels and templates. */
export function DrawingSettingsPanel({ d, timeframe, onPatch, onClose }: Props) {
  const fib = isFib(d.drawing_type);
  const st = fib ? fibSettingsOf(d) : null;
  const setFib = (patch: Partial<FibSettings>) => onPatch({ settings: { ...(d.settings ?? {}), ...st, ...patch } });
  const levels: FibLevelCfg[] = st?.levels ?? [];
  const setLevels = (lv: FibLevelCfg[]) => setFib({ levels: lv });
  const [templates, setTemplates] = useState<DrawingTemplate[]>([]);
  const [tplName, setTplName] = useState("");
  const [tplScope, setTplScope] = useState<"global" | "symbol">("global");
  const [newRatio, setNewRatio] = useState("");
  useEffect(() => {
    if (!fib) return;
    drawingsApi
      .templates(d.drawing_type, d.symbol)
      .then((r) => setTemplates(r.items))
      .catch(() => setTemplates([]));
  }, [fib, d.drawing_type, d.symbol]);
  const tfSel = d.timeframes;
  const toggleTf = (tf: string) => {
    const cur = new Set(tfSel ?? CHART_TIMEFRAMES.map((x) => x.value));
    if (cur.has(tf)) cur.delete(tf);
    else cur.add(tf);
    const list = CHART_TIMEFRAMES.map((x) => x.value).filter((x) => cur.has(x));
    onPatch({ timeframes: list.length === CHART_TIMEFRAMES.length ? null : list.length ? list : [timeframe] });
  };
  const setAnchor = (i: number, a: Partial<Anchor>) => onPatch({ anchors: d.anchors.map((x, j) => (j === i ? { ...x, ...a } : x)) });
  const style = d.style ?? {};
  const applyTemplate = (t: DrawingTemplate) => {
    const s = { ...(t.settings as Record<string, unknown>) };
    const line: Partial<Drawing> = {};
    if (typeof s.color === "string") line.color = s.color as string;
    if (typeof s.line_style === "string") line.line_style = s.line_style as Drawing["line_style"];
    if (typeof s.line_width === "number") line.line_width = s.line_width as number;
    delete s.color;
    delete s.line_style;
    delete s.line_width;
    delete s.style;
    onPatch({ ...line, settings: { ...st, ...(s as FibSettings), template_id: t.template_id } });
  };
  const saveTemplate = () => {
    if (!tplName.trim() || !st) return;
    const { template_id: _tid, ...rest } = st;
    void _tid;
    drawingsApi
      .saveTemplate({ template_id: newId(), name: tplName.trim(), drawing_type: d.drawing_type, scope: tplScope, symbol: tplScope === "symbol" ? d.symbol : null, settings: { ...rest, color: d.color, line_style: d.line_style, line_width: d.line_width } })
      .then((t) => (setTemplates((x) => [...x, t]), setTplName("")))
      .catch(() => undefined);
  };
  const logOk = logAllowed(d.anchors.map((a) => a.p));

  return (
    <div className="rounded-md border border-line bg-panel p-3 text-xs shadow-xl" data-testid="drawing-settings">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">{DRAWING_LABELS[d.drawing_type]} settings</span>
        <button className="chip h-6" onClick={onClose} data-testid="drawing-settings-close">
          Close
        </button>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <section>
          <div className="label mb-1">Style</div>
          <div className="flex flex-wrap items-center gap-2">
            <input type="color" value={d.color.startsWith("#") ? d.color.slice(0, 7) : "#19C3DA"} onChange={(e) => onPatch({ color: e.target.value })} className="h-6 w-8 bg-transparent" title="Line colour" data-testid="ds-color" />
            <select className="ctl h-7" value={d.line_style} onChange={(e) => onPatch({ line_style: e.target.value as Drawing["line_style"] })} data-testid="ds-line-style">
              <option value="solid">Solid</option>
              <option value="dashed">Dashed</option>
              <option value="dotted">Dotted</option>
            </select>
            <select className="ctl h-7" value={d.line_width} onChange={(e) => onPatch({ line_width: Number(e.target.value) })} data-testid="ds-width">
              {[1, 2, 3, 4].map((w) => (
                <option key={w} value={w}>
                  {w}px
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-muted" title="Line transparency">
              opacity
              <input type="range" min={0.1} max={1} step={0.05} value={style.opacity ?? 1} onChange={(e) => onPatch({ style: { ...style, opacity: Number(e.target.value) } })} data-testid="ds-opacity" />
            </label>
          </div>
          {!fib && (
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <input className="ctl h-7 w-40" placeholder="Label text" value={style.label ?? ""} onChange={(e) => onPatch({ style: { ...style, label: e.target.value } })} data-testid="ds-label" />
              {d.drawing_type === "trend" && (
                <>
                  <label className="flex items-center gap-1 text-muted">
                    <input type="checkbox" checked={!!style.extend_left} onChange={(e) => onPatch({ style: { ...style, extend_left: e.target.checked } })} /> extend left
                  </label>
                  <label className="flex items-center gap-1 text-muted">
                    <input type="checkbox" checked={!!style.extend_right} onChange={(e) => onPatch({ style: { ...style, extend_right: e.target.checked } })} /> extend right
                  </label>
                </>
              )}
              {(d.drawing_type === "rect" || d.drawing_type === "channel") && (
                <label className="flex items-center gap-1 text-muted">
                  fill
                  <input type="range" min={0} max={0.5} step={0.02} value={style.fill_opacity ?? (d.drawing_type === "rect" ? 0.12 : 0.08)} onChange={(e) => onPatch({ style: { ...style, fill_opacity: Number(e.target.value) } })} />
                </label>
              )}
            </div>
          )}
          <div className="label mb-1 mt-2">Visibility</div>
          <div className="flex flex-wrap items-center gap-1">
            <label className="mr-2 flex items-center gap-1 text-muted">
              <input type="checkbox" checked={d.visible} onChange={(e) => onPatch({ visible: e.target.checked })} data-testid="ds-visible" /> visible
            </label>
            <button className={`chip h-6 ${tfSel === null ? "chip-on" : ""}`} onClick={() => onPatch({ timeframes: null })} data-testid="ds-tf-all">
              All timeframes
            </button>
            <button className={`chip h-6 ${tfSel?.length === 1 && tfSel[0] === timeframe ? "chip-on" : ""}`} onClick={() => onPatch({ timeframes: [timeframe] })} data-testid="ds-tf-this">
              Only {timeframe}
            </button>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {CHART_TIMEFRAMES.map((t) => (
              <button key={t.value} className={`chip h-6 ${tfSel === null || tfSel.includes(t.value) ? "chip-on" : ""}`} onClick={() => toggleTf(t.value)} data-testid={`ds-tf-${t.value}`}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="label mb-1 mt-2">Anchors (IST, price)</div>
          {d.anchors.map((a, i) => (
            <div key={i} className="mb-1 flex items-center gap-1">
              <span className="w-6 text-muted">P{i + 1}</span>
              {d.drawing_type !== "hline" && (
                <input
                  type="datetime-local"
                  className="ctl h-7"
                  value={toIstInput(a.t)}
                  onChange={(e) => {
                    const t = fromIstInput(e.target.value);
                    if (t !== null) setAnchor(i, { t });
                  }}
                  data-testid={`ds-anchor-time-${i}`}
                />
              )}
              {d.drawing_type !== "vline" && (
                <input
                  type="number"
                  step="any"
                  className="ctl h-7 w-28"
                  value={Number(a.p.toFixed(4))}
                  onChange={(e) => {
                    const p = Number(e.target.value);
                    if (Number.isFinite(p)) setAnchor(i, { p });
                  }}
                  data-testid={`ds-anchor-price-${i}`}
                />
              )}
            </div>
          ))}
        </section>
        {fib && st && (
          <section data-testid="fib-settings">
            <div className="label mb-1">Fibonacci</div>
            <div className="flex flex-wrap items-center gap-2">
              <select className="ctl h-7" value={st.mode ?? "linear"} onChange={(e) => setFib({ mode: e.target.value as "linear" | "log" })} data-testid="fib-mode">
                <option value="linear">Linear</option>
                <option value="log" disabled={!logOk}>
                  Logarithmic{logOk ? "" : " (needs positive prices)"}
                </option>
              </select>
              {(
                [
                  ["reverse", "Reverse"],
                  ["extend_left", "Extend left"],
                  ["extend_right", "Extend right"],
                  ["show_trendline", "Trend line"],
                  ["show_prices", "Prices"],
                  ["show_ratios", "Ratios"],
                  ["percent_labels", "Percent"],
                  ["show_background", "Background"],
                  ["one_color", "One colour"],
                ] as [keyof FibSettings, string][]
              ).map(([k, label]) => (
                <label key={k} className="flex items-center gap-1 text-muted">
                  <input type="checkbox" checked={!!st[k]} onChange={(e) => setFib({ [k]: e.target.checked } as Partial<FibSettings>)} data-testid={`fib-${k}`} />
                  {label}
                </label>
              ))}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-muted">
              labels
              <select className="ctl h-7" value={st.label_position ?? "left"} onChange={(e) => setFib({ label_position: e.target.value as "left" | "right" })} data-testid="fib-label-pos">
                <option value="left">left</option>
                <option value="right">right</option>
              </select>
              font
              <input className="ctl h-7 w-14" type="number" min={8} max={24} value={st.font_size ?? 11} onChange={(e) => setFib({ font_size: Math.max(8, Math.min(24, Number(e.target.value) || 11)) })} data-testid="fib-font" />
              fill
              <input type="range" min={0} max={0.4} step={0.02} value={st.background_opacity ?? 0.06} onChange={(e) => setFib({ background_opacity: Number(e.target.value) })} data-testid="fib-bg-opacity" />
            </div>
            <div className="mt-2 max-h-56 overflow-y-auto">
              <table className="w-full" data-testid="fib-levels">
                <tbody>
                  {levels.map((lv, i) => (
                    <tr key={i} className="border-t border-line/40">
                      <td>
                        <input type="checkbox" checked={lv.visible !== false} onChange={(e) => setLevels(levels.map((x, j) => (j === i ? { ...x, visible: e.target.checked } : x)))} data-testid={`fib-level-visible-${i}`} />
                      </td>
                      <td>
                        <input className="ctl h-6 w-20" type="number" step="any" value={lv.ratio} onChange={(e) => Number.isFinite(Number(e.target.value)) && setLevels(levels.map((x, j) => (j === i ? { ...x, ratio: Number(e.target.value) } : x)))} data-testid={`fib-level-ratio-${i}`} />
                      </td>
                      <td>
                        <input type="color" className="h-5 w-7 bg-transparent" value={(lv.color ?? LEVEL_COLORS[String(lv.ratio)] ?? d.color).slice(0, 7)} onChange={(e) => setLevels(levels.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))} />
                      </td>
                      <td>
                        <input className="ctl h-6 w-24" placeholder="text" value={lv.label ?? ""} onChange={(e) => setLevels(levels.map((x, j) => (j === i ? { ...x, label: e.target.value || null } : x)))} />
                      </td>
                      <td className="whitespace-nowrap text-right">
                        <button className="px-1 text-muted hover:text-ink" disabled={i === 0} onClick={() => setLevels(levels.map((x, j) => (j === i - 1 ? levels[i] : j === i ? levels[i - 1] : x)))} title="Move up">
                          ▴
                        </button>
                        <button className="px-1 text-muted hover:text-ink" disabled={i === levels.length - 1} onClick={() => setLevels(levels.map((x, j) => (j === i + 1 ? levels[i] : j === i ? levels[i + 1] : x)))} title="Move down">
                          ▾
                        </button>
                        <button className="px-1 text-down" onClick={() => setLevels(levels.filter((_, j) => j !== i))} title="Remove level" data-testid={`fib-level-remove-${i}`}>
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-1 flex items-center gap-1">
              <input className="ctl h-7 w-24" type="number" step="any" placeholder="ratio e.g. 0.707" value={newRatio} onChange={(e) => setNewRatio(e.target.value)} data-testid="fib-new-ratio" />
              <button
                className="chip h-7"
                onClick={() => {
                  const r = Number(newRatio);
                  if (newRatio !== "" && Number.isFinite(r)) {
                    setLevels([...levels, { ratio: r, visible: true }]);
                    setNewRatio("");
                  }
                }}
                data-testid="fib-add-level"
              >
                Add level
              </button>
              <button className="chip h-7" onClick={() => setLevels(defaultLevels(d.drawing_type as "fib_retracement" | "fib_extension"))} data-testid="fib-reset-levels">
                Default levels
              </button>
              <span className="text-[10px] text-muted">{levels.filter((l) => l.visible !== false).length} shown (max 24 drawn)</span>
            </div>
            <div className="label mb-1 mt-2">Templates</div>
            <div className="flex flex-wrap items-center gap-1">
              {templates.map((t) => (
                <span key={t.template_id} className="flex items-center gap-0.5">
                  <button className={`chip h-6 ${st.template_id === t.template_id ? "chip-on" : ""}`} onClick={() => applyTemplate(t)} title={`${t.scope === "symbol" ? `${t.symbol} only` : "all symbols"}`} data-testid="fib-template">
                    {t.name}
                  </button>
                  <button className="text-[10px] text-muted hover:text-down" onClick={() => drawingsApi.deleteTemplate(t.template_id).then(() => setTemplates((x) => x.filter((y) => y.template_id !== t.template_id)))} title="Delete template">
                    ✕
                  </button>
                </span>
              ))}
              <input className="ctl h-7 w-32" placeholder="Template name" value={tplName} onChange={(e) => setTplName(e.target.value)} data-testid="fib-template-name" />
              <select className="ctl h-7" value={tplScope} onChange={(e) => setTplScope(e.target.value as "global" | "symbol")}>
                <option value="global">All symbols</option>
                <option value="symbol">This symbol</option>
              </select>
              <button className="chip h-7" onClick={saveTemplate} disabled={!tplName.trim()} data-testid="fib-template-save">
                Save template
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
