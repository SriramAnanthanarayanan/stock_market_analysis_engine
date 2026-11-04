import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import { LEVEL_COLORS, defaultFibSettings, fibLevels, ratioText, type FibSettings } from "./fib";

// Manual drawings: their own primitive, never mixed with backend pattern
// geometry. Every point is anchored to (bar start time in epoch seconds, price);
// pixels are derived on each render, so drawings stay put while zooming,
// panning, resizing, switching panes or timeframes. Persistence:
// lib/drawingStore.ts (database, docs/chart-drawing-persistence.md).

export type DrawingType = "hline" | "vline" | "trend" | "ray" | "rect" | "channel" | "fib_retracement" | "fib_extension";
export const ANCHOR_COUNT: Record<DrawingType, number> = { hline: 1, vline: 1, trend: 2, ray: 2, rect: 2, channel: 3, fib_retracement: 2, fib_extension: 3 };
export const DRAWING_LABELS: Record<DrawingType, string> = {
  hline: "Horizontal line",
  vline: "Vertical line",
  trend: "Trend line",
  ray: "Ray",
  rect: "Zone / rectangle",
  channel: "Parallel channel",
  fib_retracement: "Fib retracement",
  fib_extension: "Trend-based Fib extension",
};

export interface Anchor {
  t: number; // bar start time, epoch seconds (UTC instant); may lie beyond the last candle
  p: number; // price
}

export interface DrawingStyle {
  opacity?: number; // 0-1 line transparency
  extend_left?: boolean;
  extend_right?: boolean;
  label?: string;
  fill_opacity?: number; // zones / channels
}

export interface Drawing {
  drawing_id: string;
  instrument: "stock" | "index";
  symbol: string;
  drawing_type: DrawingType;
  anchors: Anchor[];
  color: string;
  line_style: "solid" | "dashed" | "dotted";
  line_width: number;
  style: DrawingStyle;
  settings: FibSettings | null;
  visible: boolean;
  timeframes: string[] | null; // null = every timeframe
  version?: number;
  created_at?: string;
  updated_at?: string;
  origin?: string | null;
}

export const isFib = (t: DrawingType) => t === "fib_retracement" || t === "fib_extension";
const HIT_PX = 6;
export const HANDLE_PX = 5;

/** Bar time <-> chart logical index for the series' data (candles + whitespace
 *  bars). Times between bars interpolate; beyond the data they extrapolate with
 *  the median bar spacing so drawings may extend into the future. A drawing made
 *  on one timeframe keeps its times on another: a daily anchor on a weekly chart
 *  lands proportionally inside its week. */
export class TimeMapper {
  readonly times: number[];
  readonly step: number;
  constructor(times: number[]) {
    this.times = times;
    const diffs: number[] = [];
    for (let i = Math.max(1, times.length - 30); i < times.length; i++) diffs.push(times[i] - times[i - 1]);
    diffs.sort((a, b) => a - b);
    this.step = diffs.length ? diffs[Math.floor(diffs.length / 2)] : 86400;
  }
  logicalOf(t: number): number {
    const a = this.times;
    const n = a.length;
    if (!n) return 0;
    if (t <= a[0]) return (t - a[0]) / this.step;
    if (t >= a[n - 1]) return n - 1 + (t - a[n - 1]) / this.step;
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (a[mid] <= t) lo = mid;
      else hi = mid;
    }
    return lo + (t - a[lo]) / (a[hi] - a[lo]);
  }
  /** Snaps to the nearest bar inside the data; extrapolates outside it. */
  timeOf(logical: number): number {
    const a = this.times;
    const n = a.length;
    if (!n) return 0;
    const i = Math.round(logical);
    if (i >= 0 && i < n) return a[i];
    if (i < 0) return a[0] + i * this.step;
    return a[n - 1] + (i - (n - 1)) * this.step;
  }
}

export interface PixelMapper {
  x: (t: number) => number | null;
  y: (p: number) => number | null;
  width: number;
  height: number;
}

type Pt = { x: number; y: number };

export function anchorPrices(d: Drawing): number[] {
  return d.anchors.map((a) => a.p);
}

export function fibSettingsOf(d: Drawing): FibSettings {
  const base = defaultFibSettings(d.drawing_type === "fib_extension" ? "fib_extension" : "fib_retracement");
  return { ...base, ...(d.settings ?? {}) };
}

function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
}

/** The segment a-b extended to the pane edges as requested. */
export function extendSegment(a: Pt, b: Pt, width: number, left: boolean, right: boolean): [Pt, Pt] {
  if (b.x === a.x) return [a, b];
  const k = (b.y - a.y) / (b.x - a.x);
  const [l, r] = a.x <= b.x ? [a, b] : [b, a];
  const l2 = left ? { x: 0, y: l.y + k * (0 - l.x) } : l;
  const r2 = right ? { x: width, y: r.y + k * (width - r.x) } : r;
  return [l2, r2];
}

function pts(d: Drawing, m: PixelMapper): (Pt | null)[] {
  return d.anchors.map((a) => {
    const x = m.x(a.t);
    const y = m.y(a.p);
    return x === null || y === null ? null : { x, y };
  });
}

/** Parallel channel: line through anchors 0-1 and its parallel through anchor 2. */
export function channelLines(ps: Pt[]): [Pt, Pt, Pt, Pt] {
  const [a, b, c] = ps;
  const k = b.x === a.x ? 0 : (b.y - a.y) / (b.x - a.x);
  const off = c.y - (a.y + k * (c.x - a.x));
  return [a, b, { x: a.x, y: a.y + off }, { x: b.x, y: b.y + off }];
}

function fibXRange(d: Drawing, ps: Pt[], width: number, st: FibSettings): [number, number] {
  let x0: number;
  let x1: number;
  if (d.drawing_type === "fib_extension") {
    const span = Math.max(Math.abs(ps[1].x - ps[0].x), 40);
    x0 = ps[2].x;
    x1 = ps[2].x + span;
  } else {
    x0 = Math.min(ps[0].x, ps[1].x);
    x1 = Math.max(ps[0].x, ps[1].x);
  }
  if (st.extend_left) x0 = 0;
  if (st.extend_right) x1 = width;
  return [x0, x1];
}

export type Hit = { id: string; handle: number | null };

export function handlePos(d: Drawing, q: Pt, width: number): Pt {
  return { x: d.drawing_type === "hline" ? width - 40 : q.x, y: d.drawing_type === "vline" ? 30 : q.y };
}

export function hitTest(drawings: Drawing[], m: PixelMapper, p: Pt, selectedId: string | null): Hit | null {
  const sel = drawings.find((d) => d.drawing_id === selectedId);
  if (sel) {
    const ps = pts(sel, m);
    for (let i = 0; i < ps.length; i++) {
      const q = ps[i];
      if (!q) continue;
      const h = handlePos(sel, q, m.width);
      if (Math.hypot(p.x - h.x, p.y - h.y) <= HANDLE_PX + 3) return { id: sel.drawing_id, handle: i };
    }
  }
  for (let k = drawings.length - 1; k >= 0; k--) {
    const d = drawings[k];
    if (!d.visible) continue;
    const ps = pts(d, m);
    if (ps.some((q) => q === null)) continue;
    const P = ps as Pt[];
    let dist = Infinity;
    const t = d.drawing_type;
    if (t === "hline") dist = Math.abs(p.y - P[0].y);
    else if (t === "vline") dist = Math.abs(p.x - P[0].x);
    else if (t === "trend") {
      const [a, b] = extendSegment(P[0], P[1], m.width, !!d.style?.extend_left, !!d.style?.extend_right);
      dist = distToSegment(p, a, b);
    } else if (t === "ray") {
      const [a, b] = extendSegment(P[0], P[1], m.width, false, true);
      dist = distToSegment(p, a, b);
    } else if (t === "rect") {
      const x0 = Math.min(P[0].x, P[1].x), x1 = Math.max(P[0].x, P[1].x), y0 = Math.min(P[0].y, P[1].y), y1 = Math.max(P[0].y, P[1].y);
      const inside = p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1;
      dist = inside ? 0 : Math.min(distToSegment(p, { x: x0, y: y0 }, { x: x1, y: y0 }), distToSegment(p, { x: x1, y: y0 }, { x: x1, y: y1 }), distToSegment(p, { x: x0, y: y1 }, { x: x1, y: y1 }), distToSegment(p, { x: x0, y: y0 }, { x: x0, y: y1 }));
    } else if (t === "channel") {
      const [a, b, c, e] = channelLines(P);
      dist = Math.min(distToSegment(p, a, b), distToSegment(p, c, e));
    } else if (isFib(t)) {
      const st = fibSettingsOf(d);
      const [x0, x1] = fibXRange(d, P, m.width, st);
      for (const lv of fibLevels(t as "fib_retracement" | "fib_extension", anchorPrices(d), st)) {
        const y = m.y(lv.price);
        if (y !== null && p.x >= x0 - HIT_PX && p.x <= x1 + HIT_PX) dist = Math.min(dist, Math.abs(p.y - y));
      }
      for (let i = 0; i + 1 < P.length; i++) dist = Math.min(dist, distToSegment(p, P[i], P[i + 1]));
    }
    if (dist <= HIT_PX) return { id: d.drawing_id, handle: null };
  }
  return null;
}

function dash(style: Drawing["line_style"]): number[] {
  return style === "dashed" ? [6, 4] : style === "dotted" ? [2, 3] : [];
}

function withAlpha(color: string, alpha: number): string {
  if (color.startsWith("#") && (color.length === 7 || color.length === 9)) {
    const r = parseInt(color.slice(1, 3), 16), g = parseInt(color.slice(3, 5), 16), b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  return color;
}

function drawFib(ctx: CanvasRenderingContext2D, d: Drawing, P: Pt[], m: PixelMapper, W: number): void {
  const t = d.drawing_type as "fib_retracement" | "fib_extension";
  const st = fibSettingsOf(d);
  const levels = fibLevels(t, anchorPrices(d), st);
  const [x0, x1] = fibXRange(d, P, W, st);
  const fs = st.font_size ?? 11;
  ctx.font = `${fs}px Inter, Segoe UI, sans-serif`;
  const colorOf = (lv: { ratio: number; color: string | null }) => (st.one_color ? d.color : lv.color ?? LEVEL_COLORS[String(lv.ratio)] ?? d.color);
  const ys = levels.map((lv) => m.y(lv.price));
  if (st.show_background !== false) {
    for (let i = 0; i + 1 < levels.length; i++) {
      const ya = ys[i], yb = ys[i + 1];
      if (ya === null || yb === null) continue;
      ctx.fillStyle = withAlpha(colorOf(levels[i + 1]), st.background_opacity ?? 0.06);
      ctx.fillRect(x0, Math.min(ya, yb), x1 - x0, Math.abs(yb - ya));
    }
  }
  ctx.setLineDash(dash(d.line_style));
  ctx.lineWidth = d.line_width;
  levels.forEach((lv, i) => {
    const y = ys[i];
    if (y === null || !Number.isFinite(lv.price)) return;
    const c = colorOf(lv);
    ctx.strokeStyle = c;
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    const parts: string[] = [];
    if (st.show_ratios !== false) parts.push(ratioText(lv.ratio, !!st.percent_labels));
    if (st.show_prices !== false) parts.push(`(${lv.price.toFixed(2)})`);
    if (lv.label) parts.push(lv.label);
    if (parts.length) {
      ctx.fillStyle = c;
      const text = parts.join(" ");
      const tx = st.label_position === "right" ? x1 - ctx.measureText(text).width - 4 : x0 + 4;
      ctx.fillText(text, tx, y - 3);
    }
  });
  if (st.show_trendline !== false) {
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = d.color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(P[0].x, P[0].y);
    for (let i = 1; i < P.length; i++) ctx.lineTo(P[i].x, P[i].y);
    ctx.stroke();
    ctx.restore();
  }
}

class DrawRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly src: DrawingPrimitive) {}
  draw(target: CanvasRenderingTarget2D): void {
    const m = this.src.mapper();
    if (!m || !this.src.visible) return;
    const all = this.src.preview ? [...this.src.drawings, this.src.preview] : this.src.drawings;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.font = "11px Inter, Segoe UI, sans-serif";
      const W = mediaSize.width;
      for (const d of all) {
        if (!d.visible) continue;
        const ps = pts(d, { ...m, width: W });
        if (ps.some((q) => q === null)) continue;
        const P = ps as Pt[];
        ctx.save();
        ctx.globalAlpha = d.style?.opacity ?? 1;
        ctx.strokeStyle = d.color;
        ctx.fillStyle = d.color;
        ctx.lineWidth = d.line_width;
        ctx.setLineDash(dash(d.line_style));
        ctx.beginPath();
        const t = d.drawing_type;
        if (t === "hline") {
          ctx.moveTo(0, P[0].y);
          ctx.lineTo(W, P[0].y);
          ctx.stroke();
          ctx.fillText(`${d.style?.label ? `${d.style.label} ` : ""}${d.anchors[0].p.toFixed(2)}`, W - 90, P[0].y - 4);
        } else if (t === "vline") {
          ctx.moveTo(P[0].x, 0);
          ctx.lineTo(P[0].x, mediaSize.height);
          ctx.stroke();
        } else if (t === "trend" || t === "ray") {
          const [a, b] = extendSegment(P[0], P[1], W, t === "trend" && !!d.style?.extend_left, t === "ray" || !!d.style?.extend_right);
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
          if (d.style?.label) ctx.fillText(d.style.label, P[1].x + 4, P[1].y - 4);
        } else if (t === "rect") {
          const x = Math.min(P[0].x, P[1].x), y = Math.min(P[0].y, P[1].y), w = Math.abs(P[1].x - P[0].x), h = Math.abs(P[1].y - P[0].y);
          ctx.fillStyle = withAlpha(d.color, d.style?.fill_opacity ?? 0.12);
          ctx.fillRect(x, y, w, h);
          ctx.strokeRect(x, y, w, h);
          if (d.style?.label) {
            ctx.fillStyle = d.color;
            ctx.fillText(d.style.label, x + 4, y + 12);
          }
        } else if (t === "channel") {
          const [a, b, c, e] = channelLines(P);
          ctx.fillStyle = withAlpha(d.color, d.style?.fill_opacity ?? 0.08);
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.lineTo(e.x, e.y);
          ctx.lineTo(c.x, c.y);
          ctx.closePath();
          ctx.fill();
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.moveTo(c.x, c.y);
          ctx.lineTo(e.x, e.y);
          ctx.stroke();
          ctx.save();
          ctx.globalAlpha *= 0.5;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo((a.x + c.x) / 2, (a.y + c.y) / 2);
          ctx.lineTo((b.x + e.x) / 2, (b.y + e.y) / 2);
          ctx.stroke();
          ctx.restore();
        } else if (isFib(t)) {
          drawFib(ctx, d, P, { ...m, width: W }, W);
        }
        ctx.restore();
        if (d.drawing_id === this.src.selectedId) {
          for (const q of P) {
            const h = handlePos(d, q, W);
            ctx.beginPath();
            ctx.arc(h.x, h.y, HANDLE_PX, 0, Math.PI * 2);
            ctx.fillStyle = "#052232";
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.strokeStyle = "#19C3DA";
            ctx.setLineDash([]);
            ctx.stroke();
          }
        }
      }
    });
  }
}

class DrawView implements IPrimitivePaneView {
  constructor(private readonly r: IPrimitivePaneRenderer) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "top";
  }
  renderer(): IPrimitivePaneRenderer {
    return this.r;
  }
}

export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  drawings: Drawing[] = [];
  preview: Drawing | null = null;
  selectedId: string | null = null;
  visible = true;
  timeMap = new TimeMapper([]);
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views = [new DrawView(new DrawRenderer(this))];

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }
  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }
  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }
  /** Only requests a repaint of this layer - the chart's series are untouched. */
  update(patch: Partial<Pick<DrawingPrimitive, "drawings" | "preview" | "selectedId" | "visible" | "timeMap">>): void {
    Object.assign(this, patch);
    this.requestUpdate?.();
  }
  mapper(): PixelMapper | null {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return null;
    const ts = chart.timeScale();
    const tm = this.timeMap;
    return {
      x: (t) => ts.logicalToCoordinate(tm.logicalOf(t) as Logical),
      y: (p) => series.priceToCoordinate(p),
      width: ts.width(),
      height: chart.panes()[0]?.getHeight() ?? 0,
    };
  }
  /** Pixel position on the main pane -> (bar time, price). */
  anchorAt(x: number, y: number): Anchor | null {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return null;
    const logical = chart.timeScale().coordinateToLogical(x);
    const price = series.coordinateToPrice(y);
    if (logical === null || price === null) return null;
    return { t: this.timeMap.timeOf(logical), p: Number(price) };
  }
}

/** Move a whole drawing by whole bars and a price offset (anchors stay (time, price)). */
export function translateDrawing(d: Drawing, tm: TimeMapper, dBars: number, dPrice: number): Drawing {
  return {
    ...d,
    anchors: d.anchors.map((q) => ({
      t: d.drawing_type === "hline" ? q.t : tm.timeOf(tm.logicalOf(q.t) + dBars),
      p: d.drawing_type === "vline" ? q.p : q.p + dPrice,
    })),
  };
}
