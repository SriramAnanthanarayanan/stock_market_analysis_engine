// Lightweight counters read by the UI verification script (scripts/verify-ui.mjs)
// to prove chart creation/disposal and what was rendered. No effect on behaviour.

export interface ChartDebugInfo {
  created: number;
  disposed: number;
  candles: number;
  logScale: boolean;
  volumeBars: number;
  volumePane: boolean;
  lineSeries: number;
  markers: number;
  markerRoles: Record<string, number>;
  overlayShapes: number;
  detectionId: string | null;
  timeframe: string | null;
  firstCandleTime: number | null;
  lastCandleTime: number | null;
  width: number;
  height: number;
  // E1
  emaSeries: number[];
  panes: number;
  rsiPane: boolean;
  atrPane: boolean;
  rsLinePane: boolean;
  rsiLevels: number;
  techMarkers: Record<string, number>;
  divergenceSegments: number;
  dailyLevels: number;
  drawings: number;
  selectedDrawing: string | null;
  drawingTool: string;
  overlayLines: number;
  rsLinePoints: number;
  visibleFrom: number | null;
  visibleTo: number | null;
  partialCandle: boolean;
  partialMarker: boolean;
  partialT: number | null;
  // improvement task: chart type, line data, drawings' pixel positions, volume highlights
  chartType: "candle" | "line";
  linePoints: number;
  lineFirst: { t: number; v: number } | null;
  lineLast: { t: number; v: number } | null;
  volumeHighlights: number;
  overlayKinds: Record<string, number>;
  /** Pixel position of every drawing anchor (id -> [x, y][]) through the drawing layer's mapper. */
  anchorPixels: () => Record<string, (number | null)[][]>;
}

export const chartDebug: ChartDebugInfo = {
  created: 0,
  disposed: 0,
  candles: 0,
  logScale: false,
  volumeBars: 0,
  volumePane: false,
  lineSeries: 0,
  markers: 0,
  markerRoles: {},
  overlayShapes: 0,
  detectionId: null,
  timeframe: null,
  firstCandleTime: null,
  lastCandleTime: null,
  width: 0,
  height: 0,
  emaSeries: [],
  panes: 1,
  rsiPane: false,
  atrPane: false,
  rsLinePane: false,
  rsiLevels: 0,
  techMarkers: {},
  divergenceSegments: 0,
  dailyLevels: 0,
  drawings: 0,
  selectedDrawing: null,
  drawingTool: "none",
  overlayLines: 0,
  rsLinePoints: 0,
  visibleFrom: null,
  visibleTo: null,
  partialCandle: false,
  partialMarker: false,
  partialT: null,
  chartType: "candle",
  linePoints: 0,
  lineFirst: null,
  lineLast: null,
  volumeHighlights: 0,
  overlayKinds: {},
  anchorPixels: () => ({}),
};

declare global {
  interface Window {
    __pasChart?: ChartDebugInfo;
  }
}

if (typeof window !== "undefined") window.__pasChart = chartDebug;
