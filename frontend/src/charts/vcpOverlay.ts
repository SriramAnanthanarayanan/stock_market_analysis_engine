import type { IpoGeometry, VcpGeometry } from "../api/advanced";
import type { OverlayShape } from "./OverlayPrimitive";
import type { ChartMarker, VolumeHighlight } from "./PatternChart";

// Draws the backend VCP geometry (schema vcp-2.0, docs/vcp-methodology.md) as a
// contraction structure: each contraction Tn is a swing leg from its high to its
// low (labelled with its depth), recovery legs join them into a zig-zag, the final
// contraction is highlighted, the pivot is a line from the final contraction's high,
// the final contraction's volume bars are tinted (dry-up) and a breakout candle's
// volume is marked. No box is drawn. Nothing is detected here: every point is a
// (candle time, price) from /api/vcp/symbol.

export const VCP_COLORS = {
  leg: "#E3A33B",
  recovery: "rgba(227,163,59,0.45)",
  final: "#19C3DA",
  swingHigh: "#F5C542",
  swingLow: "#E3A33B",
  pivot: "#F5C542",
  breakout: "#95EB45",
  failed: "#8B9BA5",
  dryUp: "rgba(127,155,216,0.85)",
  breakoutVolume: "rgba(149,235,69,0.95)",
};

export function vcpShapes(g: VcpGeometry, lastClose?: number | null): OverlayShape[] {
  const shapes: OverlayShape[] = [];
  const cs = g.contractions;
  cs.forEach((c, k) => {
    const fin = c.final ?? k === cs.length - 1;
    // contraction leg: high -> low
    shapes.push({ kind: "segment", t0: c.t_high, p0: c.p_high, t1: c.t_low, p1: c.p_low, color: fin ? VCP_COLORS.final : VCP_COLORS.leg, width: fin ? 3 : 2 });
    // recovery leg: low -> next contraction's high (the zig-zag)
    const nxt = cs[k + 1];
    if (nxt) shapes.push({ kind: "segment", t0: c.t_low, p0: c.p_low, t1: nxt.t_high, p1: nxt.p_high, color: VCP_COLORS.recovery, width: 1, dashed: true });
    // swing points + depth labels
    shapes.push({ kind: "point", t: c.t_high, p: c.p_high, color: VCP_COLORS.swingHigh, radius: 3 });
    const vol = fin && g.dry_up?.volume_ratio != null ? ` · vol ${g.dry_up.volume_ratio.toFixed(2)}x 50D` : "";
    shapes.push({ kind: "point", t: c.t_low, p: c.p_low, color: fin ? VCP_COLORS.final : VCP_COLORS.swingLow, radius: 3, labelBelow: true, label: `${c.label} ${c.depth_pct.toFixed(1)}%${fin ? " final" : ""}${c.provisional ? " *" : ""}${vol}` });
  });
  const dist = lastClose ? ((g.pivot.p - lastClose) / g.pivot.p) * 100 : null;
  shapes.push({
    kind: "segment",
    t0: g.pivot.t,
    p0: g.pivot.p,
    t1: g.breakout ? g.breakout.t : g.pivot.t_end,
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

/** Volume bars to tint: the final contraction (dry-up) and the breakout candle. */
export function vcpVolume(g: VcpGeometry): VolumeHighlight[] {
  const out: VolumeHighlight[] = [];
  if (g.dry_up) out.push({ t0: g.dry_up.t_start, t1: g.dry_up.t_end, color: VCP_COLORS.dryUp });
  if (g.breakout) out.push({ t0: g.breakout.t, t1: g.breakout.t, color: VCP_COLORS.breakoutVolume });
  return out;
}

// ------------------------------------------------------------------ IPO base (docs/ipo-base.md)
export const IPO_COLORS = { listing: "#19C3DA", discovery: "rgba(127,155,216,0.10)", discoveryEdge: "rgba(127,155,216,0.45)", base: "rgba(245,197,66,0.07)", baseEdge: "rgba(245,197,66,0.5)", pivot: "#F5C542", touch: "#F5C542", breakout: "#95EB45", failed: "#8B9BA5" };

export function ipoShapes(g: IpoGeometry): OverlayShape[] {
  const shapes: OverlayShape[] = [];
  shapes.push({ kind: "vline", t: g.listing.t, color: IPO_COLORS.listing }); // labelled by a candle marker (ipoMarkers)
  const d = g.discovery;
  shapes.push({ kind: "zone", t0: d.t_start, t1: d.t_end, p0: d.low, p1: d.high, fill: IPO_COLORS.discovery, stroke: IPO_COLORS.discoveryEdge });
  shapes.push({ kind: "point", t: d.t_end, p: d.low, color: IPO_COLORS.discoveryEdge, radius: 1, labelBelow: true, label: `<- price discovery (first ${d.bars} sessions)` });
  if (g.base) {
    const b = g.base;
    shapes.push({ kind: "zone", t0: b.t_start, t1: b.t_end, p0: b.low, p1: b.high, fill: IPO_COLORS.base, stroke: IPO_COLORS.baseEdge, label: `IPO base ${b.depth_pct.toFixed(1)}% deep · ${b.length} sessions`, labelPos: "inside" });
    for (const t of b.touches) shapes.push({ kind: "point", t: t.t, p: t.p, color: IPO_COLORS.touch, radius: 3 });
  }
  if (g.pivot) shapes.push({ kind: "segment", t0: g.pivot.t, p0: g.pivot.p, t1: g.breakout ? g.breakout.t : g.pivot.t_end, p1: g.pivot.p, color: IPO_COLORS.pivot, dashed: true, width: 2, label: `IPO pivot ${g.pivot.p.toFixed(2)}` });
  return shapes;
}

export function ipoMarkers(g: IpoGeometry): ChartMarker[] {
  const out: ChartMarker[] = [{ t: g.listing.t, position: "below", shape: "circle", color: IPO_COLORS.listing, text: `Listed ${g.listing.date}${g.listing.source === "first_candle" ? " (first candle)" : ""}` }];
  if (g.breakout) out.push({ t: g.breakout.t, position: "below", shape: "arrowUp", color: g.breakout.volume_ok ? IPO_COLORS.breakout : IPO_COLORS.pivot, text: g.breakout.volume_ok ? `IPO base breakout (vol ${g.breakout.volume_ratio?.toFixed(1)}x)` : "Pivot cross (low vol)" });
  if (g.failure) out.push({ t: g.failure.t, position: "above", shape: "square", color: IPO_COLORS.failed, text: "IPO breakout failed" });
  return out;
}

export function ipoVolume(g: IpoGeometry): VolumeHighlight[] {
  return g.breakout ? [{ t0: g.breakout.t, t1: g.breakout.t, color: VCP_COLORS.breakoutVolume }] : [];
}
