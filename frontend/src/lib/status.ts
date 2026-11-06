import type { StatusSlug, Timeframe, Universe } from "../api/types";

// One colour per lifecycle status, used identically in table badges, chart
// markers and lifecycle history. Colour never carries meaning alone: every
// badge also prints its label.
export const STATUS_META: Record<StatusSlug, { label: string; color: string; order: number }> = {
  broke_above: { label: "Broke Above", color: "#95EB45", order: 1 },
  broke_below: { label: "Broke Below", color: "#F2643C", order: 2 },
  near_upper: { label: "Near Upper Boundary", color: "#19C3DA", order: 3 },
  near_lower: { label: "Near Lower Boundary", color: "#19C3DA", order: 4 },
  validated: { label: "Validated", color: "#36C2A0", order: 5 },
  forming: { label: "Forming", color: "#E3A33B", order: 6 },
  developing: { label: "Developing", color: "#7F9BD8", order: 7 },
  invalidated: { label: "Invalidated", color: "#6F7E87", order: 8 },
};

export const STATUS_ORDER = (Object.keys(STATUS_META) as StatusSlug[]).sort((a, b) => STATUS_META[a].order - STATUS_META[b].order);

export const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: "1h", label: "1H" },
  { value: "4h", label: "4H" },
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
];

export const UNIVERSES: { value: Universe; label: string }[] = [
  { value: "nifty500", label: "NIFTY 500" },
  { value: "fno", label: "F&O" },
  { value: "non_fno", label: "Non-F&O" },
  { value: "all", label: "All" },
];

export const CATEGORY_LABELS: Record<string, string> = {
  channels_ranges: "Channels & ranges",
  triangles_wedges: "Triangles & wedges",
  continuation: "Continuation",
  reversal: "Reversal",
};


// Chart palette (dark surface). Candles use the up/down accents at reduced
// saturation so pattern geometry stays the visual focus.
export const CHART_COLORS = {
  background: "#052232",
  text: "#8B9BA5",
  grid: "rgba(25,195,218,0.07)",
  border: "rgba(25,195,218,0.25)",
  candleUp: "#3FBF9A",
  lineClose: "#19C3DA", // line chart mode (closes)
  candleDown: "#E8684A",
  volumeUp: "rgba(63,191,154,0.35)",
  volumeDown: "rgba(232,104,74,0.35)",
  boundary: "#19C3DA",
  projection: "rgba(25,195,218,0.75)",
  band: "rgba(25,195,218,0.09)",
  touch: "#F5D76E",
  swing: "rgba(230,237,240,0.55)",
  apex: "#E6EDF0",
  neckline: "#C792EA",
  pole: "#E3A33B",
  pivot: "#E6EDF0",
  provisional: "#E3A33B",
  supportFill: "rgba(63,191,154,0.10)",
  supportEdge: "rgba(63,191,154,0.55)",
  resistanceFill: "rgba(232,104,74,0.10)",
  resistanceEdge: "rgba(232,104,74,0.55)",
  breakUp: "#95EB45",
  breakDown: "#F2643C",
  invalid: "#8B9BA5",
};
