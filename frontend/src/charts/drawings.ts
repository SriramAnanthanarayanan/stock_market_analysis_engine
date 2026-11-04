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
import type { Anchor, Drawing } from "../lib/prefs";

// Manual drawings (E1). Kept apart from backend pattern geometry: own
// primitive, own storage, never sent to the detector. Every point is anchored
// to (bar time, price); pixels are derived on each render, so drawings stay put
// while zooming, panning, resizing or switching panes.

export const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const HIT_PX = 6;
const HANDLE_PX = 5;

/** Bar time <-> chart logical index for the series' data (candles + whitespace
 *  bars). Times between bars interpolate; beyond the data they extrapolate with
 *  the median bar spacing so drawings may extend into the future. */
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

export function fibPrice(d: Drawing, level: number): number {
  const [a, b] = d.points;
  return b.p + (a.p - b.p) * level; // level 0 = second point, 1 = first point
}

function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
}

/** Ray end: extend a -> b to the right edge of the pane. */
function rayEnd(a: Pt, b: Pt, width: number): Pt {
  if (b.x === a.x) return b;
  const k = (b.y - a.y) / (b.x - a.x);
  const x = b.x > a.x ? width : 0;
  return { x, y: a.y + k * (x - a.x) };
}

function pts(d: Drawing, m: PixelMapper): (Pt | null)[] {
  return d.points.map((a) => {
    const x = m.x(a.t);
    const y = m.y(a.p);
    return x === null || y === null ? null : { x, y };
  });
}

export type Hit = { id: string; handle: number | null };

export function hitTest(drawings: Drawing[], m: PixelMapper, p: Pt, selectedId: string | null): Hit | null {
  // Handles of the selected drawing first, then bodies (top-most = last drawn).
  const sel = drawings.find((d) => d.id === selectedId);
  if (sel) {
    const ps = pts(sel, m);
    for (let i = 0; i < ps.length; i++) {
      const q = ps[i];
      if (!q) continue;
      const hx = sel.type === "hline" ? m.width - 40 : q.x;
      const hy = sel.type === "vline" ? 30 : q.y;
      if (Math.hypot(p.x - hx, p.y - hy) <= HANDLE_PX + 3) return { id: sel.id, handle: i };
    }
  }
  for (let k = drawings.length - 1; k >= 0; k--) {
    const d = drawings[k];
    const ps = pts(d, m);
    if (ps.some((q) => q === null)) continue;
    const [a, b] = ps as Pt[];
    let dist = Infinity;
    if (d.type === "hline") dist = Math.abs(p.y - a.y);
    else if (d.type === "vline") dist = Math.abs(p.x - a.x);
    else if (d.type === "trend") dist = distToSegment(p, a, b);
    else if (d.type === "ray") dist = distToSegment(p, a, rayEnd(a, b, m.width));
    else if (d.type === "rect") {
      const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
      const inside = p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1;
      dist = inside ? 0 : Math.min(distToSegment(p, { x: x0, y: y0 }, { x: x1, y: y0 }), distToSegment(p, { x: x1, y: y0 }, { x: x1, y: y1 }), distToSegment(p, { x: x0, y: y1 }, { x: x1, y: y1 }), distToSegment(p, { x: x0, y: y0 }, { x: x0, y: y1 }));
    } else if (d.type === "fib") {
      const x0 = Math.min(a.x, b.x);
      for (const lv of FIB_LEVELS) {
        const y = m.y(fibPrice(d, lv));
        if (y !== null && p.x >= x0 - HIT_PX) dist = Math.min(dist, Math.abs(p.y - y));
      }
      dist = Math.min(dist, distToSegment(p, a, b));
    }
    if (dist <= HIT_PX) return { id: d.id, handle: null };
  }
  return null;
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
        const ps = pts(d, { ...m, width: W });
        if (ps.some((q) => q === null)) continue;
        const [a, b] = ps as Pt[];
        ctx.save();
        ctx.strokeStyle = d.color;
        ctx.fillStyle = d.color;
        ctx.lineWidth = d.width;
        ctx.setLineDash(d.dashed ? [6, 4] : []);
        ctx.beginPath();
        if (d.type === "hline") {
          ctx.moveTo(0, a.y);
          ctx.lineTo(W, a.y);
          ctx.stroke();
          ctx.fillText(d.points[0].p.toFixed(2), W - 60, a.y - 4);
        } else if (d.type === "vline") {
          ctx.moveTo(a.x, 0);
          ctx.lineTo(a.x, mediaSize.height);
          ctx.stroke();
        } else if (d.type === "trend") {
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        } else if (d.type === "ray") {
          const e = rayEnd(a, b, W);
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(e.x, e.y);
          ctx.stroke();
        } else if (d.type === "rect") {
          ctx.globalAlpha = 0.12;
          ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
          ctx.globalAlpha = 1;
          ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        } else if (d.type === "fib") {
          const x0 = Math.min(a.x, b.x);
          ctx.setLineDash([]);
          for (const lv of FIB_LEVELS) {
            const price = fibPrice(d, lv);
            const y = m.y(price);
            if (y === null) continue;
            ctx.globalAlpha = lv === 0 || lv === 1 ? 1 : 0.75;
            ctx.beginPath();
            ctx.moveTo(x0, y);
            ctx.lineTo(W, y);
            ctx.stroke();
            ctx.fillText(`${lv} (${price.toFixed(2)})`, x0 + 4, y - 3);
          }
          ctx.globalAlpha = 0.5;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
        ctx.restore();
        if (d.id === this.src.selectedId) {
          for (const [i, q] of ps.entries()) {
            if (!q) continue;
            const hx = d.type === "hline" ? W - 40 : q.x;
            const hy = d.type === "vline" ? 30 : q.y;
            ctx.beginPath();
            ctx.arc(hx, hy, HANDLE_PX, 0, Math.PI * 2);
            ctx.fillStyle = "#052232";
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.strokeStyle = "#19C3DA";
            ctx.stroke();
            void i;
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
