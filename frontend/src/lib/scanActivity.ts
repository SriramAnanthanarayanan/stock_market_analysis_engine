import { useEffect, useRef, useSyncExternalStore } from "react";
import { api } from "../api/client";
import type { ScanSummary } from "../api/types";

/**
 * Background scan activity, shared by every page through one poller.
 *
 * The backend downloads new candles by itself (startup scans, the after-close job,
 * the interval scheduler). Pages use this to show that an update is running and to
 * reload when a scan of their timeframe has finished, so the user never has to press
 * "Run scan" just to see fresh data.
 */
export interface ScanActivity {
  active: ScanSummary[];
  /** startup timeframes not handled yet (the running one included) */
  startupPending: string[];
  /** a completed daily/weekly scan is still writing its confluence snapshot */
  postprocessing: boolean;
  ready: boolean;
}

const BUSY_MS = 2000;
const IDLE_MS = 15000;

let snapshot: ScanActivity = { active: [], startupPending: [], postprocessing: false, ready: false };
const listeners = new Set<() => void>();
const finishedListeners = new Set<(scan: ScanSummary) => void>();
let seenFinished: Set<string> | null = null;
let timer: number | undefined;
let polling = false;

function busy(): boolean {
  return snapshot.active.length > 0 || snapshot.startupPending.length > 0 || snapshot.postprocessing;
}

async function poll(): Promise<void> {
  if (polling) return;
  polling = true;
  try {
    const r = await api.scanActive();
    const finished = r.recent_finished ?? [];
    let fresh: ScanSummary[] = [];
    if (seenFinished === null) {
      seenFinished = new Set(finished.map((s) => s.scan_id)); // already shown by the pages
    } else {
      fresh = finished.filter((s) => !seenFinished!.has(s.scan_id)).reverse(); // oldest first
      fresh.forEach((s) => seenFinished!.add(s.scan_id));
    }
    snapshot = {
      active: r.active ?? [],
      startupPending: r.startup_pending ?? [],
      postprocessing: (r.postprocessing ?? []).length > 0,
      ready: true,
    };
    listeners.forEach((l) => l());
    for (const s of fresh) finishedListeners.forEach((l) => l(s));
  } catch {
    /* backend restarting or unreachable: keep the last state and retry */
  } finally {
    polling = false;
    schedule();
  }
}

function schedule(): void {
  window.clearTimeout(timer);
  timer = undefined;
  if (listeners.size === 0 && finishedListeners.size === 0) return;
  timer = window.setTimeout(() => void poll(), busy() ? BUSY_MS : IDLE_MS);
}

function ensurePolling(): void {
  if (timer === undefined && !polling) void poll();
}

function stopIfUnused(): void {
  if (listeners.size === 0 && finishedListeners.size === 0) {
    window.clearTimeout(timer);
    timer = undefined;
  }
}

/** Poll right away (e.g. after a page queued a scan) instead of waiting for the idle interval. */
export function pollScanActivityNow(): void {
  window.clearTimeout(timer);
  timer = undefined;
  void poll();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  ensurePolling();
  return () => {
    listeners.delete(l);
    stopIfUnused();
  };
}

export function useScanActivity(): ScanActivity {
  return useSyncExternalStore(subscribe, () => snapshot);
}

/**
 * Calls `onFinished` when a scan finishes whose results this page shows: same
 * timeframe (null = any) and the same as-of date (none = live).
 */
export function useOnScanFinished(timeframe: string | null, asof: string | null | undefined, onFinished: (scan: ScanSummary) => void): void {
  const cb = useRef(onFinished);
  cb.current = onFinished;
  useEffect(() => {
    const l = (s: ScanSummary) => {
      if (timeframe && s.timeframe !== timeframe) return;
      if ((s.asof ?? "") !== (asof ?? "")) return;
      cb.current(s);
    };
    finishedListeners.add(l);
    ensurePolling();
    return () => {
      finishedListeners.delete(l);
      stopIfUnused();
    };
  }, [timeframe, asof]);
}
