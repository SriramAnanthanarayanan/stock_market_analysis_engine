// Fibonacci retracement and trend-based extension (docs/fibonacci-tools.md).
// Pure functions on prices - never on pixels - so levels are identical at any
// zoom, pane size or timeframe. Mirrors TradingView's documented anchors:
//
//   Retracement (two anchors P1 -> P2):   Level(r)     = P2 + (P1 - P2) x r
//     r = 0 is at the second anchor, r = 1 at the first; r > 1 extends beyond P1.
//   Extension (three anchors P1, P2, P3): Extension(r) = P3 + (P2 - P1) x r
//     r = 1 projects a move equal to P1 -> P2 from the retracement end P3.
//   Logarithmic mode interpolates in log-price space:
//     log L(r) = log P2 + (log P1 - log P2) x r      log E(r) = log P3 + (log P2 - log P1) x r
//   (only when every anchor price is positive).
//   Reverse: retracement swaps the anchor interpretation (0 at P1, 1 at P2);
//   extension projects the P1 -> P2 move in the opposite direction from P3.

export const FIB_RETRACEMENT_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236];
export const FIB_EXTENSION_LEVELS = [0, 0.382, 0.618, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236];
/** Levels drawn at most (extra custom levels stay stored and editable, the first 24 visible are drawn). */
export const MAX_DISPLAYED_LEVELS = 24;

export type FibMode = "linear" | "log";

export interface FibLevelCfg {
  ratio: number;
  visible: boolean;
  color?: string | null;
  label?: string | null;
}

export interface FibSettings {
  levels?: FibLevelCfg[];
  mode?: FibMode;
  reverse?: boolean;
  extend_left?: boolean;
  extend_right?: boolean;
  show_trendline?: boolean;
  show_prices?: boolean;
  show_ratios?: boolean;
  show_background?: boolean;
  background_opacity?: number;
  label_position?: "left" | "right";
  font_size?: number;
  one_color?: boolean;
  percent_labels?: boolean;
  template_id?: string;
}

// TradingView-like default palette per ratio (any level can be recoloured).
export const LEVEL_COLORS: Record<string, string> = {
  "0": "#8B9BA5",
  "0.236": "#F2643C",
  "0.382": "#95EB45",
  "0.5": "#36C2A0",
  "0.618": "#19C3DA",
  "0.786": "#7F9BD8",
  "1": "#8B9BA5",
  "1.272": "#E3A33B",
  "1.414": "#C792EA",
  "1.618": "#19C3DA",
  "2": "#F2643C",
  "2.618": "#C792EA",
  "3.618": "#F5C542",
  "4.236": "#E8684A",
};

export function defaultLevels(kind: "fib_retracement" | "fib_extension"): FibLevelCfg[] {
  return (kind === "fib_extension" ? FIB_EXTENSION_LEVELS : FIB_RETRACEMENT_LEVELS).map((ratio) => ({ ratio, visible: true }));
}

export function defaultFibSettings(kind: "fib_retracement" | "fib_extension"): FibSettings {
  return {
    levels: defaultLevels(kind),
    mode: "linear",
    reverse: false,
    extend_left: false,
    extend_right: false,
    show_trendline: true,
    show_prices: true,
    show_ratios: true,
    show_background: true,
    background_opacity: 0.06,
    label_position: "left",
    font_size: 11,
    one_color: false,
    percent_labels: false,
  };
}

export function logAllowed(prices: number[]): boolean {
  return prices.every((p) => Number.isFinite(p) && p > 0);
}

export function retracementPrice(p1: number, p2: number, r: number, mode: FibMode = "linear", reverse = false): number {
  const a = reverse ? p2 : p1;
  const b = reverse ? p1 : p2;
  if (mode === "log") {
    if (!logAllowed([a, b])) return Number.NaN;
    return Math.exp(Math.log(b) + (Math.log(a) - Math.log(b)) * r);
  }
  return b + (a - b) * r;
}

export function extensionPrice(p1: number, p2: number, p3: number, r: number, mode: FibMode = "linear", reverse = false): number {
  const dir = reverse ? -1 : 1;
  if (mode === "log") {
    if (!logAllowed([p1, p2, p3])) return Number.NaN;
    return Math.exp(Math.log(p3) + (Math.log(p2) - Math.log(p1)) * r * dir);
  }
  return p3 + (p2 - p1) * r * dir;
}

export interface FibLevelOut {
  ratio: number;
  price: number;
  color: string | null;
  label: string | null;
}

/** Visible levels (sorted by ratio, at most MAX_DISPLAYED_LEVELS) with their prices. */
export function fibLevels(kind: "fib_retracement" | "fib_extension", prices: number[], st: FibSettings): FibLevelOut[] {
  const mode: FibMode = st.mode === "log" && logAllowed(prices) ? "log" : "linear";
  const levels = (st.levels && st.levels.length ? st.levels : defaultLevels(kind)).filter((l) => l.visible !== false && Number.isFinite(l.ratio));
  const sorted = [...levels].sort((x, y) => x.ratio - y.ratio).slice(0, MAX_DISPLAYED_LEVELS);
  return sorted.map((l) => ({
    ratio: l.ratio,
    price: kind === "fib_extension" ? extensionPrice(prices[0], prices[1], prices[2], l.ratio, mode, !!st.reverse) : retracementPrice(prices[0], prices[1], l.ratio, mode, !!st.reverse),
    color: l.color ?? null,
    label: l.label ?? null,
  }));
}

export function ratioText(r: number, percent: boolean): string {
  if (percent) return `${(r * 100).toFixed(r * 100 === Math.round(r * 100) ? 0 : 1)}%`;
  return String(Number(r.toFixed(3)));
}
