import { useMemo, useState } from "react";
import type { RRGItem } from "../api/types";

export const RRG_PALETTE = ["#4FC3F7", "#F5C542", "#FF8A65", "#81C784", "#BA68C8", "#E57373", "#4DB6AC", "#FFD54F", "#9575CD", "#F06292", "#AED581", "#64B5F6", "#FFB74D", "#A1887F", "#90A4AE", "#DCE775", "#4DD0E1", "#FF8A80", "#B39DDB", "#80CBC4"];

const QUAD = {
  leading: { fill: "rgba(63,191,154,0.08)", text: "#3FBF9A", label: "Leading" },
  weakening: { fill: "rgba(245,197,66,0.08)", text: "#F5C542", label: "Weakening" },
  lagging: { fill: "rgba(232,104,74,0.08)", text: "#E8684A", label: "Lagging" },
  improving: { fill: "rgba(79,195,247,0.08)", text: "#4FC3F7", label: "Improving" },
};

interface Props {
  items: RRGItem[];
  color: (code: string) => string;
  center: number;
  onSelect?: (code: string) => void;
  height?: number;
  compact?: boolean;
}

/** Relative Rotation Graph: x = RS-Ratio, y = RS-Momentum, 100/100 centre.
 *  Each trail runs oldest -> newest; the arrow head marks the latest week. */
export function RRGChart({ items, color, center, onSelect, height = 620, compact = false }: Props) {
  const [hover, setHover] = useState<{ code: string; i: number } | null>(null);
  const W = 1000;
  const H = (height / 620) * 620;
  const pad = { l: 56, r: 24, t: 20, b: 44 };
  const ext = useMemo(() => {
    const xs = items.flatMap((it) => it.trail.map((p) => p.ratio));
    const ys = items.flatMap((it) => it.trail.map((p) => p.momentum));
    const span = (vals: number[]) => {
      const lo = Math.min(center, ...vals);
      const hi = Math.max(center, ...vals);
      const d = Math.max(Math.abs(hi - center), Math.abs(center - lo), 1) * 1.12;
      return [center - d, center + d] as const;
    };
    return { x: span(xs.length ? xs : [center - 2, center + 2]), y: span(ys.length ? ys : [center - 2, center + 2]) };
  }, [items, center]);
  const sx = (v: number) => pad.l + ((v - ext.x[0]) / (ext.x[1] - ext.x[0])) * (W - pad.l - pad.r);
  const sy = (v: number) => H - pad.b - ((v - ext.y[0]) / (ext.y[1] - ext.y[0])) * (H - pad.t - pad.b);
  const cx = sx(center);
  const cy = sy(center);
  const ticks = (lo: number, hi: number) => {
    const step = (hi - lo) / 6;
    const mag = Math.pow(10, Math.floor(Math.log10(step)));
    const st = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= step) ?? step;
    const out: number[] = [];
    for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) out.push(Number(v.toFixed(6)));
    return out;
  };
  const hov = hover ? items.find((i) => i.code === hover.code) : null;
  const hp = hov && hover ? hov.trail[hover.i] : null;
  return (
    <div className="relative w-full" data-testid="rrg-chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: compact ? 320 : undefined }} role="img" aria-label="Relative Rotation Graph">
        <defs>
          {items.map((it) => (
            <marker key={it.code} id={`arr-${it.code.replace(/\W/g, "_")}`} viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={color(it.code)} />
            </marker>
          ))}
        </defs>
        <rect x={cx} y={pad.t} width={W - pad.r - cx} height={cy - pad.t} fill={QUAD.leading.fill} />
        <rect x={cx} y={cy} width={W - pad.r - cx} height={H - pad.b - cy} fill={QUAD.weakening.fill} />
        <rect x={pad.l} y={cy} width={cx - pad.l} height={H - pad.b - cy} fill={QUAD.lagging.fill} />
        <rect x={pad.l} y={pad.t} width={cx - pad.l} height={cy - pad.t} fill={QUAD.improving.fill} />
        <text x={W - pad.r - 8} y={pad.t + 18} textAnchor="end" fill={QUAD.leading.text} fontSize="15" fontWeight="600">Leading</text>
        <text x={W - pad.r - 8} y={H - pad.b - 10} textAnchor="end" fill={QUAD.weakening.text} fontSize="15" fontWeight="600">Weakening</text>
        <text x={pad.l + 8} y={H - pad.b - 10} fill={QUAD.lagging.text} fontSize="15" fontWeight="600">Lagging</text>
        <text x={pad.l + 8} y={pad.t + 18} fill={QUAD.improving.text} fontSize="15" fontWeight="600">Improving</text>
        {ticks(ext.x[0], ext.x[1]).map((v) => (
          <g key={`x${v}`}>
            <line x1={sx(v)} x2={sx(v)} y1={pad.t} y2={H - pad.b} stroke="rgba(25,195,218,0.07)" />
            <text x={sx(v)} y={H - pad.b + 16} textAnchor="middle" fill="#8B9BA5" fontSize="11">{v}</text>
          </g>
        ))}
        {ticks(ext.y[0], ext.y[1]).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} stroke="rgba(25,195,218,0.07)" />
            <text x={pad.l - 6} y={sy(v) + 4} textAnchor="end" fill="#8B9BA5" fontSize="11">{v}</text>
          </g>
        ))}
        <line x1={cx} x2={cx} y1={pad.t} y2={H - pad.b} stroke="rgba(230,237,240,0.35)" />
        <line x1={pad.l} x2={W - pad.r} y1={cy} y2={cy} stroke="rgba(230,237,240,0.35)" />
        <text x={(W + pad.l) / 2} y={H - 6} textAnchor="middle" fill="#8B9BA5" fontSize="12">RS-Ratio (relative strength)</text>
        <text x={14} y={(H - pad.b + pad.t) / 2} textAnchor="middle" fill="#8B9BA5" fontSize="12" transform={`rotate(-90 14 ${(H - pad.b + pad.t) / 2})`}>RS-Momentum</text>
        {items.map((it) => {
          const c = color(it.code);
          const pts = it.trail.map((p) => `${sx(p.ratio)},${sy(p.momentum)}`).join(" ");
          const last = it.trail[it.trail.length - 1];
          if (!last) return null;
          const dim = hover && hover.code !== it.code;
          return (
            <g key={it.code} opacity={dim ? 0.25 : 1} data-testid="rrg-point" data-code={it.code}>
              <polyline points={pts} fill="none" stroke={c} strokeWidth={2} strokeOpacity={0.8} markerEnd={`url(#arr-${it.code.replace(/\W/g, "_")})`} />
              {it.trail.map((p, i) => (
                <circle
                  key={p.t}
                  cx={sx(p.ratio)}
                  cy={sy(p.momentum)}
                  r={i === it.trail.length - 1 ? 6 : 2.5}
                  fill={i === it.trail.length - 1 ? c : "#052232"}
                  stroke={c}
                  strokeWidth={1.5}
                  opacity={0.35 + (0.65 * (i + 1)) / it.trail.length}
                  className="cursor-pointer"
                  onMouseEnter={() => setHover({ code: it.code, i })}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => onSelect?.(it.code)}
                  data-testid={i === it.trail.length - 1 ? "rrg-head" : undefined}
                />
              ))}
              {!compact && (
                <text x={sx(last.ratio) + 9} y={sy(last.momentum) - 8} fill={c} fontSize="11" className="pointer-events-none">
                  {it.code.replace(/^NIFTY\s?/, "")}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hov && hp && (
        <div className="pointer-events-none absolute left-3 top-3 rounded border border-line bg-page/95 px-3 py-2 text-xs" data-testid="rrg-tooltip">
          <div className="font-medium" style={{ color: color(hov.code) }}>
            {hov.name}
          </div>
          <div className="text-muted">Week of {new Date(hp.t * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" })}</div>
          <div className="num">
            RS-Ratio {hp.ratio.toFixed(2)} · RS-Momentum {hp.momentum.toFixed(2)}
          </div>
          {hover?.i === hov.trail.length - 1 && hov.latest && (
            <div>
              {QUAD[hov.latest.quadrant as keyof typeof QUAD]?.label} · moving {hov.latest.direction}
              {hov.latest.previous_quadrant && hov.latest.previous_quadrant !== hov.latest.quadrant ? ` (from ${hov.latest.previous_quadrant})` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
