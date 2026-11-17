// Keyboard stepping through a results table from the chart preview
// (docs/chart-preview.md): Space opens the next row's chart, Shift+Space the
// previous one, in the order the table shows (its sort, filters and page). At
// the end of a page the table's own pager moves to the next page.
//
// Tables mark themselves with data-chart-nav-table (+ the row offset of the page
// and the total row count); every SymbolButton carries data-chart-nav. Stepping
// clicks the neighbouring symbol button, so each row opens exactly as a mouse
// click would (its own timeframe, pattern and scanner context).
import { useEffect, useRef } from "react";
import { chartAnchor, setChartAnchor } from "./openChart";

const TABLE = "[data-chart-nav-table]";
const ITEM = "[data-chart-nav]";

export interface NavPosition {
  table: HTMLElement;
  items: HTMLElement[];
  index: number;
  /** 1-based row of the open chart in the whole (sorted, filtered) table. */
  row: number;
  total: number;
}

/** Where the open chart sits in its results table (null: not opened from one). */
export function locateChart(symbol: string): NavPosition | null {
  let a = chartAnchor();
  if (!a || !a.isConnected || a.dataset.symbol !== symbol || !a.closest(TABLE)) {
    // Opened another way (detail panel, row click) or the table re-rendered: first row of this symbol.
    a = Array.from(document.querySelectorAll<HTMLElement>(`${TABLE} ${ITEM}`)).find((el) => el.dataset.symbol === symbol) ?? null;
    setChartAnchor(a);
  }
  if (!a) return null;
  const table = a.closest<HTMLElement>(TABLE)!;
  const items = Array.from(table.querySelectorAll<HTMLElement>(ITEM));
  const index = items.indexOf(a);
  const tr = a.closest("tr");
  const rowInPage = tr?.parentElement ? Array.from(tr.parentElement.children).indexOf(tr) : index;
  const offset = Number(table.dataset.chartNavOffset ?? 0) || 0;
  const total = Number(table.dataset.chartNavTotal ?? items.length) || items.length;
  return { table, items, index, row: offset + rowInPage + 1, total };
}

/** Opens the next (dir 1) or previous (dir -1) row's chart. Returns a note when
 *  there is nothing to step to. */
export function stepChart(symbol: string, dir: 1 | -1): string | null {
  const pos = locateChart(symbol);
  if (!pos) return "Space steps through a results table: open a chart from a table row";
  const j = pos.index + dir;
  if (j >= 0 && j < pos.items.length) {
    pos.items[j].click();
    return null;
  }
  const pager = pos.table.querySelector<HTMLButtonElement>(`[data-chart-nav-page="${dir > 0 ? "next" : "prev"}"]`);
  if (!pager || pager.disabled) return dir > 0 ? "Last stock in this list" : "First stock in this list";
  pager.click();
  const table = pos.table;
  window.setTimeout(() => {
    const items = table.querySelectorAll<HTMLElement>(ITEM);
    (dir > 0 ? items[0] : items[items.length - 1])?.click();
  }, 60);
  return null;
}

/** Highlights the open chart's row and scrolls it into view (null: clear). */
export function markChartRow(pos: NavPosition | null) {
  for (const el of Array.from(document.querySelectorAll("[data-chart-current]"))) el.removeAttribute("data-chart-current");
  if (!pos) return;
  const a = pos.items[pos.index];
  const row = a?.closest("tr") ?? a;
  if (!row) return;
  row.setAttribute("data-chart-current", "");
  row.scrollIntoView({ block: "nearest" });
}

/** True while the user types (inputs, text notes): shortcuts stay off. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName);
}

/** A single-key chart shortcut (no Ctrl / Alt / Meta), off while typing. */
export function useChartKey(key: string, handler: (ev: KeyboardEvent) => void, enabled = true) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key.toLowerCase() !== key || ev.ctrlKey || ev.altKey || ev.metaKey || isTyping(ev.target)) return;
      ref.current(ev);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, enabled]);
}
