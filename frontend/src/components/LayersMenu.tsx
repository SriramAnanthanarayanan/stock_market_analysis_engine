import { useEffect, useRef, useState } from "react";
import { activeLayerCount, LAYERS, layerOn, setLayer } from "../lib/chartLayers";
import type { ChartPrefs } from "../lib/prefs";

const GROUPS: { id: "chart" | "signals" | "panes"; label: string }[] = [
  { id: "chart", label: "Chart" },
  { id: "signals", label: "Signals and patterns" },
  { id: "panes", label: "Panes" },
];

/** "Layers" menu: every overlay of the chart in one place, with a checkbox each.
 *  Choices are saved in this browser (lib/prefs.ts); a layer switched off here
 *  stays off, also when a scanner row would switch it on (lib/chartLayers.ts). */
export function LayersMenu({
  prefs,
  setPrefs,
  clean,
  onToggleClean,
  forced,
  missing,
}: {
  prefs: ChartPrefs;
  setPrefs: (p: ChartPrefs) => void;
  clean: boolean;
  onToggleClean: () => void;
  /** Layers the scanner row switched on for this chart. */
  forced?: Set<string>;
  /** Layers with nothing to draw on this chart (shown greyed). */
  missing?: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  const n = activeLayerCount(prefs);
  const hidden = new Set(prefs.hiddenByUser ?? []);

  return (
    <div className="relative" ref={ref}>
      <button className={`chip h-6 ${open ? "chip-on" : ""}`} onClick={() => setOpen((x) => !x)} title="Show or hide every overlay of the chart (saved in this browser)" data-testid="layers-menu">
        Layers {n > 0 ? n : ""} ▾
      </button>
      {open && (
        <div className="absolute left-0 top-7 z-30 w-[440px] max-w-[90vw] rounded-md border border-line bg-panel p-3 text-xs shadow-xl" data-testid="layers-panel">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            {GROUPS.map((g) => (
              <div key={g.id} className={g.id === "signals" ? "row-span-2" : ""}>
                <div className="label mb-1">{g.label}</div>
                {LAYERS.filter((l) => l.group === g.id).map((l) => {
                  const on = layerOn(prefs, l.key);
                  const none = missing?.has(l.key);
                  return (
                    <label key={l.key} className={`flex items-center gap-2 py-0.5 ${none ? "text-muted/60" : "text-ink"}`} title={l.title}>
                      <input type="checkbox" checked={on} onChange={(e) => setPrefs(setLayer(prefs, l.key, e.target.checked))} data-testid={`layer-${l.key}`} />
                      <span>{l.label}</span>
                      {on && forced?.has(l.key) && <span className="rounded border border-line px-1 text-[10px] text-muted">from this list</span>}
                      {!on && forced?.has(l.key) && hidden.has(l.key) && <span className="text-[10px] text-muted">hidden by you</span>}
                      {none && <span className="text-[10px]">(none here)</span>}
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2">
            <button className={`chip h-6 shrink-0 whitespace-nowrap ${clean ? "chip-on" : ""}`} onClick={onToggleClean} title="Hide every overlay at once (H); press again to bring them back. Volume and your drawings stay." data-testid="layers-clean">
              {clean ? "Show all again (H)" : "Hide all (H)"}
            </button>
            <span className="text-[11px] text-muted">Saved in this browser. A layer you switch off stays off, also when a scanner list would show it.</span>
          </div>
        </div>
      )}
    </div>
  );
}
