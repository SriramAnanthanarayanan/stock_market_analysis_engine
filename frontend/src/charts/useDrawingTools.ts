import { useEffect, useRef } from "react";
import type { IChartApi } from "lightweight-charts";
import type { Anchor, Drawing, DrawingType } from "../lib/prefs";
import { newId } from "../lib/prefs";
import { hitTest, type DrawingPrimitive } from "./drawings";

export type DrawTool = "none" | "crosshair" | DrawingType;
export const TWO_POINT: DrawingType[] = ["trend", "ray", "rect", "fib"];

export interface DrawStyle {
  color: string;
  width: 1 | 2 | 3;
  dashed: boolean;
}

interface Args {
  container: React.RefObject<HTMLDivElement>;
  chart: React.MutableRefObject<IChartApi | null>;
  primitive: React.MutableRefObject<DrawingPrimitive | null>;
  drawings: Drawing[];
  visible: boolean;
  tool: DrawTool;
  style: DrawStyle;
  selectedId: string | null;
  onChange?: (d: Drawing[]) => void;
  onSelect?: (id: string | null) => void;
  onToolDone?: () => void;
}

type Drag = { id: string; handle: number | null; start: Anchor; startLogical: number; original: Drawing; current: Drawing };

/** Mouse interaction for manual drawings: create (click / click-click), select,
 *  move, resize (handles), Esc to cancel, Delete to remove. Events that hit a
 *  drawing are captured before Lightweight Charts sees them, so dragging a
 *  drawing never pans the chart. */
export function useDrawingTools(a: Args): void {
  const ref = useRef(a);
  ref.current = a;
  const pending = useRef<Drawing | null>(null);
  const drag = useRef<Drag | null>(null);
  const consumed = useRef(false);

  useEffect(() => {
    const el = a.container.current;
    if (!el) return;

    const local = (ev: MouseEvent) => {
      const r = el.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };
    const inMainPane = (x: number, y: number) => {
      const chart = ref.current.chart.current;
      if (!chart) return false;
      return x >= 0 && x <= chart.timeScale().width() && y >= 0 && y <= (chart.panes()[0]?.getHeight() ?? 0);
    };
    const setScroll = (on: boolean) => ref.current.chart.current?.applyOptions({ handleScroll: { pressedMouseMove: on }, handleScale: { axisPressedMouseMove: on } });
    const prim = () => ref.current.primitive.current;
    const stop = (ev: Event) => {
      ev.stopPropagation();
      ev.preventDefault();
    };

    const onDown = (ev: PointerEvent) => {
      consumed.current = false;
      if (ev.button !== 0) return;
      const st = ref.current;
      const p = prim();
      if (!p || !st.visible) return;
      const { x, y } = local(ev);
      if (!inMainPane(x, y)) return;
      const anchor = p.anchorAt(x, y);
      if (!anchor) return;
      const tool = st.tool;
      if (tool !== "none" && tool !== "crosshair") {
        consumed.current = true;
        stop(ev);
        const base = { color: st.style.color, width: st.style.width, dashed: st.style.dashed };
        if (tool === "hline" || tool === "vline") {
          const d: Drawing = { id: newId(), type: tool, points: [anchor], ...base };
          st.onChange?.([...st.drawings, d]);
          st.onSelect?.(d.id);
          st.onToolDone?.();
          return;
        }
        if (!pending.current) {
          pending.current = { id: newId(), type: tool, points: [anchor, anchor], ...base };
          p.update({ preview: pending.current });
          return;
        }
        const d = { ...pending.current, points: [pending.current.points[0], anchor] };
        pending.current = null;
        p.update({ preview: null });
        st.onChange?.([...st.drawings, d]);
        st.onSelect?.(d.id);
        st.onToolDone?.();
        return;
      }
      const m = p.mapper();
      if (!m) return;
      const hit = hitTest(st.drawings, m, { x, y }, st.selectedId);
      if (!hit) {
        if (st.selectedId) st.onSelect?.(null);
        return;
      }
      consumed.current = true;
      stop(ev);
      st.onSelect?.(hit.id);
      const original = st.drawings.find((d) => d.id === hit.id)!;
      const logical = ref.current.chart.current?.timeScale().coordinateToLogical(x) ?? 0;
      drag.current = { id: hit.id, handle: hit.handle, start: anchor, startLogical: Number(logical), original, current: original };
      setScroll(false);
    };

    const onMove = (ev: PointerEvent) => {
      const p = prim();
      if (!p) return;
      const { x, y } = local(ev);
      if (pending.current) {
        const anchor = p.anchorAt(x, y);
        if (anchor) {
          pending.current = { ...pending.current, points: [pending.current.points[0], anchor] };
          p.update({ preview: pending.current });
        }
        return;
      }
      const dr = drag.current;
      if (!dr) return;
      const anchor = p.anchorAt(x, y);
      const logical = ref.current.chart.current?.timeScale().coordinateToLogical(x);
      if (!anchor || logical === null || logical === undefined) return;
      const o = dr.original;
      let points: Anchor[];
      if (dr.handle !== null) {
        points = o.points.map((q, i) => {
          if (i !== dr.handle) return q;
          if (o.type === "hline") return { t: q.t, p: anchor.p };
          if (o.type === "vline") return { t: anchor.t, p: q.p };
          return anchor;
        });
      } else {
        const dl = Number(logical) - dr.startLogical;
        const dp = anchor.p - dr.start.p;
        const tm = p.timeMap;
        points = o.points.map((q) => ({ t: o.type === "hline" ? q.t : tm.timeOf(tm.logicalOf(q.t) + dl), p: o.type === "vline" ? q.p : q.p + dp }));
      }
      dr.current = { ...o, points };
      p.update({ drawings: ref.current.drawings.map((d) => (d.id === dr.id ? dr.current : d)) });
    };

    const onUp = () => {
      const dr = drag.current;
      if (!dr) return;
      drag.current = null;
      setScroll(true);
      const st = ref.current;
      if (dr.current !== dr.original) st.onChange?.(st.drawings.map((d) => (d.id === dr.id ? dr.current : d)));
    };

    // Lightweight Charts listens to mouse events: swallow the compatibility
    // mousedown of a pointer press we already handled.
    const onMouseDown = (ev: MouseEvent) => {
      if (consumed.current) stop(ev);
    };

    const onKey = (ev: KeyboardEvent) => {
      const target = ev.target as HTMLElement | null;
      if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      const st = ref.current;
      if (ev.key === "Escape") {
        if (pending.current) {
          pending.current = null;
          prim()?.update({ preview: null });
        }
        st.onSelect?.(null);
        st.onToolDone?.();
      } else if ((ev.key === "Delete" || ev.key === "Backspace") && st.selectedId) {
        st.onChange?.(st.drawings.filter((d) => d.id !== st.selectedId));
        st.onSelect?.(null);
      }
    };

    el.addEventListener("pointerdown", onDown, true);
    el.addEventListener("mousedown", onMouseDown, true);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    return () => {
      el.removeEventListener("pointerdown", onDown, true);
      el.removeEventListener("mousedown", onMouseDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
    };
  }, [a.container]);

  // Switching tools cancels a half-drawn shape.
  useEffect(() => {
    if (pending.current && a.tool !== pending.current.type) {
      pending.current = null;
      a.primitive.current?.update({ preview: null });
    }
  }, [a.tool, a.primitive]);
}
