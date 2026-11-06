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
  UTCTimestamp,
} from "lightweight-charts";

// One reusable rendering layer for every filled/custom shape (formation bands,
// S/R zones, apex points). Pattern-specific drawing code is not allowed here:
// shapes come straight from the backend geometry payload.

// ``logical`` (bar position on the chart) is the fallback when a timestamp is
// not on the time scale (e.g. a projected bar beyond the last candle).
export interface PolygonShape {
  kind: "polygon";
  points: { t: number; p: number; logical?: number }[];
  fill: string;
  stroke?: string;
}
export interface ZoneShape {
  kind: "zone";
  t0: number;
  t1: number;
  p0: number;
  p1: number;
  fill: string;
  stroke?: string;
  label?: string;
  /** "right" (default): label right of the zone; "inside": top-left inside it. */
  labelPos?: "right" | "inside";
}
export interface PointShape {
  kind: "point";
  t: number;
  p: number;
  logical?: number;
  color: string;
  radius: number;
  label?: string;
  /** Draw the label under the point (swing lows) instead of above it. */
  labelBelow?: boolean;
}
export interface SegmentShape {
  kind: "segment";
  t0: number;
  p0: number;
  t1: number;
  p1: number;
  color: string;
  dashed?: boolean;
  width?: number;
  label?: string;
}
export interface VLineShape {
  kind: "vline";
  t: number;
  color: string;
  label?: string;
}
export type OverlayShape = PolygonShape | ZoneShape | PointShape | SegmentShape | VLineShape;

type Mapper = { x: (t: number, logical?: number) => number | null; y: (p: number) => number | null };

class BackgroundRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: OverlayPrimitive) {}
  draw(): void {}
  drawBackground(target: CanvasRenderingTarget2D): void {
    const m = this.source.mapper();
    if (!m) return;
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      for (const s of this.source.shapes) {
        if (s.kind === "polygon") {
          const pts = s.points.map((pt) => [m.x(pt.t, pt.logical), m.y(pt.p)] as const);
          if (pts.some(([x, y]) => x === null || y === null)) continue;
          ctx.beginPath();
          pts.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x!, y!) : ctx.lineTo(x!, y!)));
          ctx.closePath();
          ctx.fillStyle = s.fill;
          ctx.fill();
          if (s.stroke) {
            ctx.strokeStyle = s.stroke;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        } else if (s.kind === "zone") {
          const x0 = m.x(s.t0);
          const x1 = m.x(s.t1);
          const y0 = m.y(s.p1);
          const y1 = m.y(s.p0);
          if (x0 === null || x1 === null || y0 === null || y1 === null) continue;
          ctx.fillStyle = s.fill;
          ctx.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
          if (s.stroke) {
            ctx.strokeStyle = s.stroke;
            ctx.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
          }
        }
      }
    });
  }
}

class TopRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: OverlayPrimitive) {}
  draw(target: CanvasRenderingTarget2D): void {
    const m = this.source.mapper();
    if (!m) return;
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.font = "11px Inter, Segoe UI, sans-serif";
      for (const s of this.source.shapes) {
        if (s.kind === "segment") {
          const x0 = m.x(s.t0);
          const x1 = m.x(s.t1);
          const y0 = m.y(s.p0);
          const y1 = m.y(s.p1);
          if (x0 === null || x1 === null || y0 === null || y1 === null) continue;
          ctx.save();
          ctx.strokeStyle = s.color;
          ctx.lineWidth = s.width ?? 2;
          ctx.setLineDash(s.dashed ? [6, 4] : []);
          ctx.beginPath();
          ctx.moveTo(x0, y0);
          ctx.lineTo(x1, y1);
          ctx.stroke();
          ctx.restore();
          if (s.label) {
            ctx.fillStyle = s.color;
            ctx.fillText(s.label, Math.min(x0, x1) + 4, Math.min(y0, y1) - 4);
          }
          continue;
        }
        if (s.kind === "vline") {
          const x = m.x(s.t);
          if (x === null) continue;
          ctx.save();
          ctx.strokeStyle = s.color;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, ctx.canvas.height);
          ctx.stroke();
          ctx.restore();
          if (s.label) {
            ctx.fillStyle = s.color;
            ctx.fillText(s.label, x + 4, 14);
          }
          continue;
        }
        if (s.kind === "zone" && s.label) {
          const x0 = m.x(s.t0);
          const x1 = m.x(s.t1);
          const y = m.y(s.p1);
          if (x1 !== null && y !== null) {
            ctx.fillStyle = s.stroke ?? "#8B9BA5";
            if (s.labelPos === "inside" && x0 !== null) ctx.fillText(s.label, Math.min(x0, x1) + 3, y + 11);
            else ctx.fillText(s.label, x1 + 4, y + 10);
          }
          continue;
        }
        if (s.kind !== "point") continue;
        const x = m.x(s.t, s.logical);
        const y = m.y(s.p);
        if (x === null || y === null) continue;
        ctx.beginPath();
        ctx.arc(x, y, s.radius, 0, Math.PI * 2);
        ctx.fillStyle = "#052232";
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = s.color;
        ctx.stroke();
        if (s.label) {
          ctx.fillStyle = s.color;
          ctx.fillText(s.label, x + s.radius + 4, s.labelBelow ? y + s.radius + 12 : y - s.radius - 2);
        }
      }
    });
  }
}

class View implements IPrimitivePaneView {
  constructor(
    private readonly r: IPrimitivePaneRenderer,
    private readonly z: PrimitivePaneViewZOrder,
  ) {}
  zOrder(): PrimitivePaneViewZOrder {
    return this.z;
  }
  renderer(): IPrimitivePaneRenderer {
    return this.r;
  }
}

export class OverlayPrimitive implements ISeriesPrimitive<Time> {
  shapes: OverlayShape[] = [];
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views: View[];

  constructor() {
    this.views = [new View(new BackgroundRenderer(this), "bottom"), new View(new TopRenderer(this), "top")];
  }

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

  setShapes(shapes: OverlayShape[]): void {
    this.shapes = shapes;
    this.requestUpdate?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  mapper(): Mapper | null {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return null;
    const ts = chart.timeScale();
    return {
      x: (t, logical) => ts.timeToCoordinate(t as UTCTimestamp) ?? (logical === undefined ? null : ts.logicalToCoordinate(logical as Logical)),
      y: (p) => series.priceToCoordinate(p),
    };
  }
}
