import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConflictError, drawingsApi } from "../api/advanced";
import type { Drawing } from "../charts/drawings";
import { exportAllDrawings, hasLegacyDrawings, markLegacyMigrated } from "./prefs";

// Database-backed drawings for one instrument/symbol (docs/chart-drawing-persistence.md).
//
// * The chart edits the drawings visible on its timeframe; the store diffs the
//   result against the server copies and saves only what changed (create /
//   update / delete) after a short debounce (autosave) or on "Save".
// * Every update / delete sends the version it was based on. If another tab
//   saved first the server answers 409 with its newer copy: the store keeps the
//   newer copy (never overwrites it with stale state) and reports the conflict.
// * Tabs showing the same symbol are told to reload after a save
//   (BroadcastChannel), and every tab reloads on focus when it has no pending edits.
// * Pending edits are flushed with keepalive requests when the page is hidden or
//   closed, and when the chart switches to another symbol.

export type SaveState = "loading" | "saved" | "saving" | "unsaved" | "error" | "offline";
type Op = "create" | "update" | "delete";

const DEBOUNCE_MS = 700;
const channel: BroadcastChannel | null = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("pas-drawings") : null;
const TAB_ID = Math.random().toString(36).slice(2);

let migration: Promise<void> | null = null;
/** One-time import of drawings that older versions kept only in this browser. The
 *  localStorage keys are left in place (nothing is deleted). */
export function migrateLegacyOnce(): Promise<void> {
  if (migration) return migration;
  migration = (async () => {
    if (!hasLegacyDrawings()) return;
    try {
      await drawingsApi.import({ format: "pas-drawings-v1", drawings: exportAllDrawings() });
      markLegacyMigrated();
    } catch {
      migration = null; // retry on the next chart open
    }
  })();
  return migration;
}

function strip(d: Drawing) {
  const { version: _v, created_at: _c, updated_at: _u, origin: _o, ...rest } = d;
  void _v;
  void _c;
  void _u;
  void _o;
  return rest;
}

const same = (a: Drawing, b: Drawing) => JSON.stringify(strip(a)) === JSON.stringify(strip(b));

export const visibleOn = (d: Drawing, timeframe: string) => d.timeframes === null || d.timeframes === undefined || d.timeframes.includes(timeframe);

export function useDrawingStore(instrument: "stock" | "index", symbol: string | null, timeframe: string) {
  const [all, setAll] = useState<Drawing[]>([]);
  const [state, setState] = useState<SaveState>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const server = useRef<Map<string, Drawing>>(new Map()); // last saved copy per id
  const pending = useRef<Map<string, Op>>(new Map());
  const local = useRef<Drawing[]>([]);
  const timer = useRef<number | null>(null);
  const key = symbol ? `${instrument}:${symbol}` : null;
  const keyRef = useRef(key);
  keyRef.current = key;

  const apply = (items: Drawing[]) => {
    local.current = items;
    setAll(items);
  };

  const reload = useCallback(async () => {
    if (!symbol) return;
    try {
      await migrateLegacyOnce();
      const r = await drawingsApi.list(instrument, symbol);
      if (keyRef.current !== `${instrument}:${symbol}`) return;
      server.current = new Map(r.items.map((d) => [d.drawing_id, d]));
      pending.current.clear();
      apply(r.items);
      setState("saved");
    } catch (e) {
      setState("offline");
      setMessage(`Drawings could not be loaded: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [instrument, symbol]);

  const flush = useCallback(
    async (keepalive = false) => {
      if (timer.current) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
      if (!pending.current.size) return;
      setState("saving");
      const ops = [...pending.current.entries()];
      pending.current.clear();
      let conflict: string | null = null;
      let failed = false;
      for (const [id, op] of ops) {
        const cur = local.current.find((d) => d.drawing_id === id);
        const base = server.current.get(id);
        try {
          if (op === "delete") {
            if (base?.version) await drawingsApi.remove(id, base.version, keepalive);
            server.current.delete(id);
          } else if (!base) {
            if (!cur) continue;
            const saved = await drawingsApi.create(strip(cur), keepalive);
            server.current.set(id, saved);
            local.current = local.current.map((d) => (d.drawing_id === id ? saved : d));
          } else if (cur) {
            const saved = await drawingsApi.update(strip(cur), base.version ?? 1, keepalive);
            server.current.set(id, saved);
            local.current = local.current.map((d) => (d.drawing_id === id ? saved : d));
          }
        } catch (e) {
          if (e instanceof ConflictError) {
            // Another window saved a newer version: keep it, drop the stale local edit.
            conflict = e.message;
            if (e.current) {
              server.current.set(id, e.current);
              const exists = local.current.some((d) => d.drawing_id === id);
              local.current = exists ? local.current.map((d) => (d.drawing_id === id ? e.current! : d)) : [...local.current, e.current];
            }
          } else if ((e as { status?: number }).status === 404) {
            server.current.delete(id);
            local.current = local.current.filter((d) => d.drawing_id !== id);
            conflict = "A drawing was deleted in another window.";
          } else {
            failed = true;
            pending.current.set(id, op); // retried by the next save
          }
        }
      }
      setAll(local.current);
      if (failed) {
        setState("error");
        setMessage("Some drawings could not be saved (server unreachable?) - they stay pending and are retried.");
      } else {
        setState(pending.current.size ? "unsaved" : "saved");
        setMessage(conflict ? `${conflict} The newer version was kept.` : null);
        channel?.postMessage({ key: keyRef.current, from: TAB_ID });
      }
    },
    [],
  );

  const schedule = useCallback(() => {
    setState("unsaved");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), DEBOUNCE_MS);
  }, [flush]);

  /** The chart's edited list of the drawings it showed (``shown``) -> diff -> save. */
  const commit = useCallback(
    (shown: Drawing[], edited: Drawing[]) => {
      const before = new Map(shown.map((d) => [d.drawing_id, d]));
      const after = new Map(edited.map((d) => [d.drawing_id, d]));
      let next = [...local.current];
      for (const [id, d] of after) {
        const prev = before.get(id);
        if (!prev) {
          next.push(d);
          pending.current.set(id, server.current.has(id) ? "update" : "create");
        } else if (!same(prev, d)) {
          next = next.map((x) => (x.drawing_id === id ? d : x));
          pending.current.set(id, server.current.has(id) ? "update" : "create");
        }
      }
      for (const id of before.keys()) {
        if (!after.has(id)) {
          next = next.filter((x) => x.drawing_id !== id);
          if (server.current.has(id)) pending.current.set(id, "delete");
          else pending.current.delete(id);
        }
      }
      apply(next);
      schedule();
    },
    [schedule],
  );

  /** Patch one drawing (style, settings, visibility, timeframes, anchors). */
  const patch = useCallback(
    (id: string, change: Partial<Drawing>) => {
      const cur = local.current.find((d) => d.drawing_id === id);
      if (!cur) return;
      commit([cur], [{ ...cur, ...change }]);
    },
    [commit],
  );

  const removeAll = useCallback(
    (ids: string[]) => {
      const shown = local.current.filter((d) => ids.includes(d.drawing_id));
      commit(shown, []);
    },
    [commit],
  );

  const add = useCallback((d: Drawing) => commit([], [d]), [commit]);

  // load on symbol change; flush the previous symbol first
  useEffect(() => {
    setState("loading");
    apply([]);
    server.current = new Map();
    void reload();
    return () => {
      void flush(true);
    };
  }, [reload, flush]);

  // other tabs / windows
  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      if (ev.data?.key === keyRef.current && ev.data?.from !== TAB_ID && !pending.current.size) void reload();
    };
    const onFocus = () => {
      if (!pending.current.size) void reload();
    };
    const onHide = () => {
      if (document.visibilityState === "hidden") void flush(true);
    };
    channel?.addEventListener("message", onMsg);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      channel?.removeEventListener("message", onMsg);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
    };
  }, [reload, flush]);

  const onTimeframe = useMemo(() => all.filter((d) => visibleOn(d, timeframe)), [all, timeframe]);
  return { all, onTimeframe, state, message, setMessage, commit, patch, add, removeAll, saveNow: () => flush(), reload };
}
