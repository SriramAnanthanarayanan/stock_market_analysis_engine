import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ChartDetection, Label, LabelIn, LabelPoint, LinePoint, PatternInfo, Timeframe, Verdict } from "../api/types";
import type { OverlayShape } from "../charts/OverlayPrimitive";
import { fmtBarTime, fmtNum } from "../lib/format";
import { ErrorNote, Panel } from "./common";

type Mode = "none" | "start" | "end" | "upper" | "lower" | "swing_high" | "swing_low";

interface Draft {
  label_id?: string;
  pattern: string;
  verdict: Verdict;
  dataset: "" | "tuning" | "validation";
  notes: string;
  start?: number;
  end?: number;
  upper: LinePoint[];
  lower: LinePoint[];
  swings: LabelPoint[];
  detection_id: string | null;
}

const EMPTY = (pattern: string): Draft => ({ pattern, verdict: "valid", dataset: "", notes: "", upper: [], lower: [], swings: [], detection_id: null });
const COLORS = { window: "#E3A33B", line: "#FF79C6", swing: "#FF79C6" };
const VERDICTS: { value: Verdict; label: string }[] = [
  { value: "valid", label: "Valid pattern" },
  { value: "invalid", label: "Invalid pattern" },
  { value: "near_miss", label: "Near miss" },
  { value: "false_detection", label: "False detection" },
];

const toT = (iso: string) => Math.round(new Date(iso).getTime() / 1000);

function fromDetection(d: ChartDetection): Draft {
  const g = d.geometry;
  const line = (id: string) => g.elements.find((e) => e.type === "line" && e.id === id);
  const pts = (id: string) => {
    const el = line(id);
    return el && el.type === "line" ? el.points.filter((p) => !p.projected).map((p) => ({ t: p.t, p: p.p })) : [];
  };
  const top = g.lifecycle_model?.kind === "neckline_down";
  const bottom = g.lifecycle_model?.kind === "neckline_up";
  const swings: LabelPoint[] = g.elements
    .filter((e) => e.type === "marker" && ["touch_upper", "touch_lower", "peak", "trough"].includes(e.role))
    .map((e) => (e.type === "marker" ? { t: e.t, p: e.p, kind: e.role === "touch_upper" || e.role === "peak" ? "high" : "low" } : null))
    .filter((x): x is LabelPoint => x !== null)
    .slice(0, 50);
  return {
    ...EMPTY(d.pattern),
    start: g.formation.start.t,
    end: g.formation.end.t,
    upper: bottom ? pts("neckline") : top ? [] : pts("upper"),
    lower: top ? pts("neckline") : bottom ? [] : pts("lower"),
    swings,
    detection_id: d.pattern_id,
  };
}

export function labelShapes(d: { start?: number; end?: number; upper: LinePoint[]; lower: LinePoint[]; swings: LabelPoint[] }): OverlayShape[] {
  const out: OverlayShape[] = [];
  if (d.start) out.push({ kind: "vline", t: d.start, color: COLORS.window, label: "label start" });
  if (d.end) out.push({ kind: "vline", t: d.end, color: COLORS.window, label: "label end" });
  for (const ln of [d.upper, d.lower]) if (ln.length === 2) out.push({ kind: "segment", t0: ln[0].t, p0: ln[0].p, t1: ln[1].t, p1: ln[1].p, color: COLORS.line });
  for (const p of d.swings) out.push({ kind: "point", t: p.t, p: p.p, color: COLORS.swing, radius: 3 });
  return out;
}

export function labelToShapes(l: Label): OverlayShape[] {
  return labelShapes({ start: toT(l.formation_start), end: toT(l.formation_end), upper: l.upper_line ?? [], lower: l.lower_line ?? [], swings: l.swing_points ?? [] });
}

interface Props {
  symbol: string;
  timeframe: Timeframe;
  asof: string;
  detection: ChartDetection | null;
  patterns: PatternInfo[];
  click: { t: number; price: number; n: number } | null;
  onShapes: (shapes: OverlayShape[]) => void;
  onCaptureChange: (capturing: boolean) => void;
}

export function LabelPanel({ symbol, timeframe, asof, detection, patterns, click, onShapes, onCaptureChange }: Props) {
  const [draft, setDraft] = useState<Draft>(() => EMPTY(detection?.pattern ?? "falling_channel"));
  const [mode, setMode] = useState<Mode>("none");
  const [labels, setLabels] = useState<Label[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const reload = () => api.labels({ symbol, timeframe }).then(setLabels).catch(() => setLabels([]));
  useEffect(() => {
    void reload();
  }, [symbol, timeframe]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => onCaptureChange(mode !== "none"), [mode, onCaptureChange]);
  const shapes = useMemo(() => labelShapes(draft), [draft]);
  useEffect(() => onShapes(shapes), [shapes, onShapes]);

  useEffect(() => {
    if (!click || mode === "none") return;
    setDraft((d) => {
      const pt = { t: click.t, p: Math.round(click.price * 100) / 100 };
      if (mode === "start") return { ...d, start: click.t };
      if (mode === "end") return { ...d, end: click.t };
      if (mode === "upper") return { ...d, upper: [...d.upper.slice(-1), pt].slice(-2) };
      if (mode === "lower") return { ...d, lower: [...d.lower.slice(-1), pt].slice(-2) };
      const kind: LabelPoint["kind"] = mode === "swing_high" ? "high" : "low";
      return { ...d, swings: [...d.swings, { ...pt, kind }].slice(-50) };
    });
    if (mode === "start" || mode === "end") setMode("none");
  }, [click]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setError(null);
    setSaved(null);
    if (!draft.start || !draft.end) {
      setError("Set the formation start and end by clicking candles.");
      return;
    }
    const body: LabelIn = {
      symbol,
      timeframe,
      pattern: draft.pattern,
      verdict: draft.verdict,
      asof,
      formation_start_t: Math.min(draft.start, draft.end),
      formation_end_t: Math.max(draft.start, draft.end),
      upper_line: draft.upper.length === 2 ? [...draft.upper].sort((a, b) => a.t - b.t) : null,
      lower_line: draft.lower.length === 2 ? [...draft.lower].sort((a, b) => a.t - b.t) : null,
      swing_points: draft.swings.length ? draft.swings : null,
      dataset: draft.dataset || null,
      detection_id: draft.detection_id,
      notes: draft.notes || null,
    };
    try {
      const out = draft.label_id ? await api.updateLabel(draft.label_id, body) : await api.createLabel(body);
      setSaved(`Saved label ${out.label_id} (${out.dataset} set).`);
      setDraft(EMPTY(draft.pattern));
      setMode("none");
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const edit = (l: Label) => {
    setDraft({
      label_id: l.label_id,
      pattern: l.pattern,
      verdict: l.verdict,
      dataset: l.dataset,
      notes: l.notes ?? "",
      start: toT(l.formation_start),
      end: toT(l.formation_end),
      upper: l.upper_line ?? [],
      lower: l.lower_line ?? [],
      swings: l.swing_points ?? [],
      detection_id: l.detection_id,
    });
  };

  const remove = async (l: Label) => {
    try {
      await api.deleteLabel(l.label_id);
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const capture = (m: Mode, text: string) => (
    <button className={`chip ${mode === m ? "chip-on" : ""}`} onClick={() => setMode(mode === m ? "none" : m)} data-testid={`label-mode-${m}`}>
      {text}
    </button>
  );

  return (
    <Panel title={draft.label_id ? "Edit label" : "Label this chart"} right={<span className="text-[10px] text-muted">as of {asof}</span>}>
      <div className="space-y-2 text-xs" data-testid="label-panel">
        <p className="text-[10px] text-muted">
          Ground truth is yours: label what you see, not what the detector says. Click a capture button, then click candles on the chart.
        </p>
        <div className="grid grid-cols-2 gap-2">
          <select className="ctl" value={draft.pattern} onChange={(e) => setDraft({ ...draft, pattern: e.target.value })} data-testid="label-pattern">
            {patterns.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name}
              </option>
            ))}
          </select>
          <select className="ctl" value={draft.verdict} onChange={(e) => setDraft({ ...draft, verdict: e.target.value as Verdict })} data-testid="label-verdict">
            {VERDICTS.map((v) => (
              <option key={v.value} value={v.value}>
                {v.label}
              </option>
            ))}
          </select>
          <select className="ctl" value={draft.dataset} onChange={(e) => setDraft({ ...draft, dataset: e.target.value as Draft["dataset"] })} title="Default: deterministic split by stock (about 1 in 5 stocks -> validation)">
            <option value="">Dataset: default split</option>
            <option value="tuning">Tuning set</option>
            <option value="validation">Validation set</option>
          </select>
          {detection && (
            <button className="btn h-8" onClick={() => setDraft({ ...fromDetection(detection), verdict: draft.verdict })} title="Copy the selected detection's window, boundaries and touches as a starting point" data-testid="label-from-detection">
              Start from detection
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {capture("start", `Start ${draft.start ? "✓" : ""}`)}
          {capture("end", `End ${draft.end ? "✓" : ""}`)}
          {capture("upper", `Upper line ${draft.upper.length}/2`)}
          {capture("lower", `Lower line ${draft.lower.length}/2`)}
          {capture("swing_high", "+ swing high")}
          {capture("swing_low", "+ swing low")}
          <button className="chip" onClick={() => setDraft({ ...EMPTY(draft.pattern), verdict: draft.verdict })}>
            Clear
          </button>
        </div>
        <div className="text-[11px] text-muted" data-testid="label-window">
          Window: {draft.start ? fmtBarTime(draft.start, timeframe) : "-"} to {draft.end ? fmtBarTime(draft.end, timeframe) : "-"} - {draft.swings.length} swing points
        </div>
        <textarea className="ctl h-14 w-full py-1" placeholder="Notes" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} maxLength={2000} />
        <div className="flex gap-2">
          <button className="btn-primary" onClick={save} data-testid="label-save">
            {draft.label_id ? "Update label" : "Save label"}
          </button>
          {draft.label_id && (
            <button className="btn" onClick={() => setDraft(EMPTY(draft.pattern))}>
              Cancel edit
            </button>
          )}
        </div>
        {error && <ErrorNote message={error} />}
        {saved && <div className="text-validated">{saved}</div>}
        <div className="border-t border-line pt-2">
          <div className="label mb-1">Labels on {symbol} {timeframe.toUpperCase()} ({labels.length})</div>
          <ul className="max-h-48 space-y-1 overflow-y-auto" data-testid="label-list">
            {labels.map((l) => (
              <li key={l.label_id} className="flex items-center justify-between gap-2">
                <button className="text-left hover:text-ink" onClick={() => onShapes(labelToShapes(l))} title={l.notes ?? ""}>
                  <span className="text-ink">{patterns.find((p) => p.slug === l.pattern)?.name ?? l.pattern}</span>
                  <span className="ml-1 text-muted">
                    {l.verdict} - {l.dataset} - {fmtBarTime(l.formation_end, l.timeframe)} (as of {l.asof})
                  </span>
                </button>
                <span className="flex gap-1">
                  <button className="chip h-6" onClick={() => edit(l)}>
                    Edit
                  </button>
                  <button className="chip h-6" onClick={() => remove(l)}>
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
          {draft.upper.length + draft.lower.length > 0 && (
            <div className="mt-1 text-[10px] text-muted">
              Upper {draft.upper.map((p) => fmtNum(p.p)).join(" -> ") || "-"}; lower {draft.lower.map((p) => fmtNum(p.p)).join(" -> ") || "-"}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}
