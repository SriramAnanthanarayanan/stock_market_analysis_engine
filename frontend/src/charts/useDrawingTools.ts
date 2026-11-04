import { useEffect, useRef } from "react";
import type { IChartApi } from "lightweight-charts";
import { ANCHOR_COUNT, hitTest, translateDrawing, type Anchor, type Drawing, type DrawingPrimitive, type DrawingType } from "./drawings";

export type DrawTool = "none" | "crosshair" | DrawingType;

export interface DrawStyle {
  color: string;
  width: 1 | 2 | 3 | 4;
  line_style: "solid" | "dashed" | "dotted";
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
  /** Builds a new drawing (id, symbol, defaults) from the tool and its anchors. */
  makeDrawing?: (type: DrawingType, anchors: Anchor[], style: DrawStyle) => Drawing;
  onChange?: (d: Drawing[]) => void;
  onSelect?: (id: string | null) => void;
  onToolDone?: () => void;
}

type Drag = { id: string; handle: number | null; start: Anchor; startLogical: number; original: Drawing; current: Drawing };

/** Mouse interaction for manual drawings: create (one, two or three clicks),
 *  select, move, move one anchor (handles), Esc to cancel, Delete to remove.
 *  Events that hit a drawing are captured before Lightweight Charts sees them,
 *  so dragging a drawing never pans the chart. Esc / Delete that were handled
 *  here call preventDefault so an enclosing preview does not also close. */
export function useDrawingTools(a: Args): void {
  const ref = useRef(a);
  ref.current = a;
  const pending = useRef<{ type: DrawingType; anchors: Anchor[] } | null>(null);
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
    const preview = (anchors: Anchor[]) => {
      const st = ref.current;
      const p = pending.current;
      if (!p || !st.makeDrawing || !anchors.length) return;
      // Pad with the cursor point so 3-anchor tools can preview after the first click.
      const need = ANCHOR_COUNT[p.type];
      const full = anchors.length >= need ? anchors.slice(0, need) : [...anchors, ...Array(need - anchors.length).fill(anchors[anchors.length - 1])];
      const d = st.makeDrawing(p.type, full, st.style);
      prim()?.update({ preview: d });
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
      if (tool !== "none" && tool !== "crosshair" && st.makeDrawing) {
        consumed.current = true;
        stop(ev);
        const need = ANCHOR_COUNT[tool];
        const cur = pending.current && pending.current.type === tool ? pending.current : { type: tool, anchors: [] };
        const anchors = [...cur.anchors, anchor];
        if (anchors.length >= need) {
          pending.current = null;
          p.update({ preview: null });
          const d = st.makeDrawing(tool, anchors.slice(0, need), st.style);
          st.onChange?.([...st.drawings, d]);
          st.onSelect?.(d.drawing_id);
          st.onToolDone?.();
          return;
        }
        pending.current = { type: tool, anchors };
        preview([...anchors, anchor]);
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
      const original = st.drawings.find((d) => d.drawing_id === hit.id)!;
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
        if (anchor) preview([...pending.current.anchors, anchor]);
        return;
      }
      const dr = drag.current;
      if (!dr) return;
      const anchor = p.anchorAt(x, y);
      const logical = ref.current.chart.current?.timeScale().coordinateToLogical(x);
      if (!anchor || logical === null || logical === undefined) return;
      const o = dr.original;
      let next: Drawing;
      if (dr.handle !== null) {
        next = {
          ...o,
          anchors: o.anchors.map((q, i) => {
            if (i !== dr.handle) return q;
            if (o.drawing_type === "hline") return { t: q.t, p: anchor.p };
            if (o.drawing_type === "vline") return { t: anchor.t, p: q.p };
            return anchor;
          }),
        };
      } else {
        next = translateDrawing(o, p.timeMap, Number(logical) - dr.startLogical, anchor.p - dr.start.p);
      }
      dr.current = next;
      p.update({ drawings: ref.current.drawings.map((d) => (d.drawing_id === dr.id ? next : d)) });
    };

    const onUp = () => {
      const dr = drag.current;
      if (!dr) return;
      drag.current = null;
      setScroll(true);
      const st = ref.current;
      if (dr.current !== dr.original) st.onChange?.(st.drawings.map((d) => (d.drawing_id === dr.id ? dr.current : d)));
    };

    // Lightweight Charts listens to mouse events: swallow the compatibility
    // mousedown of a pointer press we already handled.
    const onMouseDown = (ev: MouseEvent) => {
      if (consumed.current) stop(ev);
    };

    const onKey = (ev: KeyboardEvent) => {
      const target = ev.target as HTMLElement | null;
      if (target && (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName) || target.isContentEditable)) return;
      const st = ref.current;
      if (ev.key === "Escape") {
        const busy = !!pending.current || !!st.selectedId || (st.tool !== "none" && st.tool !== "crosshair");
        if (pending.current) {
          pending.current = null;
          prim()?.update({ preview: null });
        }
        if (busy) ev.preventDefault();
        st.onSelect?.(null);
        st.onToolDone?.();
      } else if ((ev.key === "Delete" || ev.key === "Backspace") && st.selectedId) {
        ev.preventDefault();
        st.onChange?.(st.drawings.filter((d) => d.drawing_id !== st.selectedId));
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
