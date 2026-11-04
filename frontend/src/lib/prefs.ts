// Personal chart preferences and drawings (E1). Stored in this browser's
// localStorage only - never mixed with detector configuration, never sent to
// the backend. Every access is guarded: private windows or blocked storage
// fall back to defaults. Drawings can be exported/imported as JSON (backup).

export interface EmaPref {
  period: number;
  color: string;
  width: 1 | 2 | 3;
  visible: boolean;
}

export interface ChartPrefs {
  v: 1;
  emas: EmaPref[];
  showVolume: boolean;
  showRsi: boolean;
  rsiPeriod: number;
  rsiOverbought: number;
  rsiOversold: number;
  showAtr: boolean;
  atrPeriod: number;
  showMarubozu: boolean;
  showDailyLevels: boolean;
  showBreakouts: boolean;
  breakoutConfirm: 1 | 2;
  showDivergence: boolean;
  showDrawings: boolean;
  showRsLine: boolean;
  showStage: boolean;
  showVcp: boolean; // VCP geometry from the latest daily scan (daily charts)
  logScale: boolean; // logarithmic price axis (long histories)
}

export const DEFAULT_EMAS: EmaPref[] = [
  { period: 10, color: "#F5C542", width: 1, visible: false },
  { period: 20, color: "#4FC3F7", width: 1, visible: false },
  { period: 30, color: "#BA68C8", width: 1, visible: false },
  { period: 50, color: "#FF8A65", width: 2, visible: false },
  { period: 100, color: "#81C784", width: 1, visible: false },
  { period: 200, color: "#E57373", width: 2, visible: false },
];

// Clean defaults: candles + volume only; everything else is opt-in.
export const DEFAULT_PREFS: ChartPrefs = {
  v: 1,
  emas: DEFAULT_EMAS,
  showVolume: true,
  showRsi: false,
  rsiPeriod: 14,
  rsiOverbought: 70,
  rsiOversold: 30,
  showAtr: false,
  atrPeriod: 14,
  showMarubozu: false,
  showDailyLevels: false,
  showBreakouts: false,
  breakoutConfirm: 1,
  showDivergence: false,
  showDrawings: true,
  showRsLine: false,
  showStage: false,
  showVcp: false,
  logScale: false,
};

const PREFS_KEY = "pas.chartPrefs.v1";
const LAYOUT_KEY = "pas.chartLayout.v1";
const DRAW_PREFIX = "pas.drawings.v1:";

function read<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function sanitize(p: Partial<ChartPrefs> | null): ChartPrefs {
  if (!p || p.v !== 1) return { ...DEFAULT_PREFS, emas: DEFAULT_EMAS.map((e) => ({ ...e })) };
  const emas = Array.isArray(p.emas) && p.emas.length ? p.emas.filter((e) => Number.isInteger(e.period) && e.period >= 1 && e.period <= 400).slice(0, 10) : DEFAULT_EMAS;
  return { ...DEFAULT_PREFS, ...p, emas: emas.map((e) => ({ ...e })) };
}

export function loadPrefs(): ChartPrefs {
  return sanitize(read<Partial<ChartPrefs>>(PREFS_KEY));
}

export function savePrefs(p: ChartPrefs): void {
  write(PREFS_KEY, p);
}

export interface SavedLayout {
  prefs: ChartPrefs;
  paneStretch: number[];
  savedAt: string;
}

export function saveLayout(prefs: ChartPrefs, paneStretch: number[]): boolean {
  return write(LAYOUT_KEY, { prefs, paneStretch, savedAt: new Date().toISOString() } satisfies SavedLayout);
}

export function loadLayout(): SavedLayout | null {
  const l = read<SavedLayout>(LAYOUT_KEY);
  return l ? { ...l, prefs: sanitize(l.prefs) } : null;
}

// ---------------------------------------------------------------- drawings
export type DrawingType = "hline" | "vline" | "trend" | "ray" | "rect" | "fib";

export interface Anchor {
  t: number; // bar start time, epoch seconds (UTC); may lie beyond the last candle
  p: number; // price
}

export interface Drawing {
  id: string;
  type: DrawingType;
  points: Anchor[];
  color: string;
  width: 1 | 2 | 3;
  dashed: boolean;
}

export const drawingKey = (chartId: string, symbol: string, timeframe: string) => `${DRAW_PREFIX}${chartId}:${symbol.toUpperCase()}:${timeframe}`;

export function loadDrawings(key: string): Drawing[] {
  const d = read<Drawing[]>(key);
  return Array.isArray(d) ? d.filter((x) => x && typeof x.id === "string" && Array.isArray(x.points)) : [];
}

export function saveDrawings(key: string, drawings: Drawing[]): boolean {
  try {
    if (!drawings.length) {
      window.localStorage.removeItem(key);
      return true;
    }
  } catch {
    return false;
  }
  return write(key, drawings);
}

/** Every stored drawing set (all symbols/timeframes) for a JSON backup file. */
export function exportAllDrawings(): Record<string, Drawing[]> {
  const out: Record<string, Drawing[]> = {};
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(DRAW_PREFIX)) out[k] = loadDrawings(k);
    }
  } catch {
    /* storage unavailable */
  }
  return out;
}

/** Merge a backup file; drawings with an existing id are replaced. Returns the number imported. */
export function importDrawings(data: unknown): number {
  if (!data || typeof data !== "object") return 0;
  let n = 0;
  for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
    if (!k.startsWith(DRAW_PREFIX) || !Array.isArray(v)) continue;
    const current = new Map(loadDrawings(k).map((d) => [d.id, d]));
    for (const d of v as Drawing[]) {
      if (d && typeof d.id === "string" && Array.isArray(d.points)) {
        current.set(d.id, d);
        n += 1;
      }
    }
    saveDrawings(k, [...current.values()]);
  }
  return n;
}

export const newId = () => `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
