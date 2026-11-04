import type { VcpGeometry } from "../api/advanced";
import type { OverlayShape } from "./OverlayPrimitive";
import type { ChartMarker } from "./PatternChart";

// Draws the backend VCP geometry (docs/vcp-methodology.md). Nothing is detected
// here: every point is a (candle time, price) from /api/vcp/symbol.

export const VCP_COLORS = {
  base: "rgba(25,195,218,0.05)",
  baseEdge: "rgba(25,195,218,0.35)",
  contraction: ["rgba(227,163,59,0.13)", "rgba(199,146,234,0.13)"],
  contractionEdge: ["rgba(227,163,59,0.65)", "rgba(199,146,234,0.65)"],
  leg: "#E3A33B",
  pivot: "#F5C542",
  breakout: "#95EB45",
  failed: "#8B9BA5",
};

export function vcpShapes(g: VcpGeometry, lastClose?: number | null): OverlayShape[] {
  const shapes: OverlayShape[] = [];
  shapes.push({ kind: "zone", t0: g.base.t_start, t1: g.base.t_end, p0: g.base.p_low, p1: g.base.p_high, fill: VCP_COLORS.base, stroke: VCP_COLORS.baseEdge });
  g.contractions.forEach((c, k) => {
    const vol = c.volume_vs_avg50 === null ? "" : ` · vol ${c.volume_vs_avg50.toFixed(2)}x`;
    shapes.push({ kind: "zone", t0: c.t_high, t1: c.t_low, p0: c.p_low, p1: c.p_high, fill: VCP_COLORS.contraction[k % 2], stroke: VCP_COLORS.contractionEdge[k % 2], label: `${c.label} -${c.depth_pct.toFixed(1)}%${vol}${c.provisional ? " *" : ""}`, labelPos: "inside" });
    shapes.push({ kind: "segment", t0: c.t_high, p0: c.p_high, t1: c.t_low, p1: c.p_low, color: VCP_COLORS.leg, width: 2 });
  });
  const dist = lastClose ? ((g.pivot.p - lastClose) / g.pivot.p) * 100 : null;
  shapes.push({
    kind: "segment",
    t0: g.pivot.t,
    p0: g.pivot.p,
    t1: g.pivot.t_end,
    p1: g.pivot.p,
    color: VCP_COLORS.pivot,
    dashed: true,
    width: 2,
    label: `Pivot ${g.pivot.p.toFixed(2)}${dist === null ? "" : dist >= 0 ? ` (${dist.toFixed(2)}% above close)` : ` (close ${(-dist).toFixed(2)}% above)`}`,
  });
  return shapes;
}

export function vcpMarkers(g: VcpGeometry): ChartMarker[] {
  const out: ChartMarker[] = [];
  if (g.breakout) out.push({ t: g.breakout.t, position: "below", shape: "arrowUp", color: g.breakout.volume_ok ? VCP_COLORS.breakout : VCP_COLORS.pivot, text: g.breakout.volume_ok ? `VCP breakout (vol ${g.breakout.volume_ratio?.toFixed(1)}x)` : "Pivot cross (low vol)" });
  if (g.event && g.event.status !== "breakout") out.push({ t: g.event.t, position: "above", shape: "square", color: VCP_COLORS.failed, text: g.event.status === "failed_breakout" ? "VCP failed" : "VCP invalidated" });
  return out;
}
