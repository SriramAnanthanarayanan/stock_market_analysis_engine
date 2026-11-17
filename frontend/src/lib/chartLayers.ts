// Chart layers: every thing a chart can draw on top of the candles, how the
// user hides it and how that choice is remembered (docs/chart-preview.md).
//
// - A scanner row may switch layers on for its chart ("?show=breakouts,..."):
//   that is a per-chart override, never written to the saved preferences.
// - When the user switches a layer off, its key goes into `hiddenByUser`, so a
//   scanner row no longer switches it back on. Switching it on again removes it.
// - "Hide all" switches every overlay off and keeps a snapshot of the saved
//   layers; "Show all" brings that snapshot back. Volume and the user's own
//   drawings are not overlays and stay as they are.
import type { ChartPrefs } from "./prefs";

type Flag = "showPattern" | "showContext" | "showLabels" | "showVolume" | "showDrawings" | "showRsi" | "showAtr" | "showMarubozu" | "showDailyLevels" | "showBreakouts" | "showDivergence" | "showRsLine" | "showStage" | "showVcp" | "showIpo" | "showMultiYear";

export interface LayerDef {
  key: string;
  label: string;
  flag: Flag;
  group: "chart" | "signals" | "panes";
  title: string;
}

export const LAYERS: LayerDef[] = [
  { key: "pattern", label: "Pattern", flag: "showPattern", group: "chart", title: "Geometry of the detected pattern (lines, pivots, breakout arrow)" },
  { key: "context", label: "Scanner signal", flag: "showContext", group: "chart", title: "Levels and markers of the scanner row you opened (breakout level, pivot, breakout candle)" },
  { key: "labels", label: "Text labels", flag: "showLabels", group: "chart", title: "Text on overlays and markers (10D high, T1 32%, Pivot, ...). Lines and arrows stay; your own drawings keep their text." },
  { key: "volume", label: "Volume", flag: "showVolume", group: "chart", title: "Volume pane" },
  { key: "drawings", label: "My drawings", flag: "showDrawings", group: "chart", title: "Your own drawings (they stay saved when hidden)" },
  { key: "breakouts", label: "10D breakouts", flag: "showBreakouts", group: "signals", title: "10-day breakout / breakdown markers (daily chart)" },
  { key: "levels", label: "10D levels", flag: "showDailyLevels", group: "signals", title: "Previous 10 completed daily candles' high / low" },
  { key: "marubozu", label: "Marubozu", flag: "showMarubozu", group: "signals", title: "Marubozu candle markers (M)" },
  { key: "divergence", label: "RSI divergence", flag: "showDivergence", group: "signals", title: "Regular RSI divergence on confirmed swing points" },
  { key: "vcp", label: "VCP", flag: "showVcp", group: "signals", title: "VCP structure of the latest daily scan (daily chart)" },
  { key: "ipo", label: "IPO base", flag: "showIpo", group: "signals", title: "IPO base of the latest daily scan (daily chart)" },
  { key: "multiyear", label: "Multi-year", flag: "showMultiYear", group: "signals", title: "Multi-year breakouts (1D and longer timeframes)" },
  { key: "stage", label: "Stage (1W)", flag: "showStage", group: "signals", title: "Stage Analysis: 30W EMA and base resistance / support (weekly chart)" },
  { key: "rsi", label: "RSI pane", flag: "showRsi", group: "panes", title: "RSI pane" },
  { key: "atr", label: "ATR pane", flag: "showAtr", group: "panes", title: "ATR pane" },
  { key: "rs", label: "RS Line pane", flag: "showRsLine", group: "panes", title: "RS Line vs NIFTY 50" },
];

const BY_KEY = new Map(LAYERS.map((l) => [l.key, l]));

/** Layers "Hide all" switches off (and "Show all" brings back). */
const CLEAN_KEYS = LAYERS.filter((l) => !["labels", "volume", "drawings"].includes(l.key)).map((l) => l.key);

/** What a scanner's `show` token switches on for its chart. */
const SHOW_TOKENS: Record<string, string[]> = {
  rsi: ["rsi"],
  divergence: ["rsi", "divergence"],
  atr: ["atr"],
  marubozu: ["marubozu"],
  levels: ["levels"],
  breakouts: ["breakouts"],
  rs: ["rs"],
  stage: ["stage"],
  vcp: ["vcp"],
  ipo: ["ipo"],
  multiyear: ["multiyear"],
  ema: ["ema:20", "ema:50", "ema:200"],
};

/** Layer keys a scanner can switch on: only these are remembered as hidden. */
const FORCEABLE = new Set(Object.values(SHOW_TOKENS).flat());

export type LayerSnapshot = Record<string, boolean>;

const emaPeriod = (key: string): number | null => (key.startsWith("ema:") ? Number(key.slice(4)) : null);

export function layerOn(p: ChartPrefs, key: string): boolean {
  const period = emaPeriod(key);
  if (period !== null) return p.emas.some((e) => e.period === period && e.visible);
  const def = BY_KEY.get(key);
  return def ? p[def.flag] !== false : false;
}

export function setLayer(p: ChartPrefs, key: string, on: boolean): ChartPrefs {
  const period = emaPeriod(key);
  if (period !== null) return { ...p, emas: p.emas.map((e) => (e.period === period ? { ...e, visible: on } : e)) };
  const def = BY_KEY.get(key);
  return def ? { ...p, [def.flag]: on } : p;
}

const emaKeys = (p: ChartPrefs) => p.emas.map((e) => `ema:${e.period}`);

/** Scanner `show` tokens -> layer keys. */
export function forcedLayers(show: string | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const tok of (show ?? "").split(",")) for (const k of SHOW_TOKENS[tok.trim()] ?? []) out.add(k);
  return out;
}

/** The prefs a chart draws with: the saved prefs plus the scanner's layers
 *  (except those the user hid, and none while "Hide all" is on). */
export function effectivePrefs(saved: ChartPrefs, forced: Set<string>): ChartPrefs {
  if (saved.cleanSnapshot || !forced.size) return saved;
  const hidden = new Set(saved.hiddenByUser ?? []);
  let p = saved;
  for (const k of forced) if (!hidden.has(k) && !layerOn(p, k)) p = setLayer(p, k, true);
  return p;
}

/** Prefs to save after the user changed the chart from `before` (what was drawn)
 *  to `next`: scanner layers that are still on keep their saved value (the
 *  override is not saved), a layer switched off is remembered as hidden, a layer
 *  switched on is no longer hidden. A caller that replaces `hiddenByUser` itself
 *  (restore defaults / layout) sets it as given. */
export function prefsToSave(next: ChartPrefs, before: ChartPrefs, saved: ChartPrefs, forced: Set<string>): ChartPrefs {
  const explicit = next.hiddenByUser !== before.hiddenByUser;
  const hidden = new Set(explicit ? next.hiddenByUser ?? [] : saved.hiddenByUser ?? []);
  const overriding = !saved.cleanSnapshot;
  let out: ChartPrefs = { ...next, emas: next.emas.map((e) => ({ ...e })) };
  const keys = new Set([...LAYERS.map((l) => l.key), ...emaKeys(next), ...emaKeys(before)]);
  for (const k of keys) {
    const was = layerOn(before, k);
    const now = layerOn(next, k);
    if (!explicit && FORCEABLE.has(k)) {
      if (was && !now) hidden.add(k);
      if (!was && now) hidden.delete(k);
    }
    if (overriding && forced.has(k) && was && now) out = setLayer(out, k, layerOn(saved, k));
  }
  return { ...out, hiddenByUser: [...hidden].sort() };
}

/** "Hide all": every overlay off, the saved layers kept to bring back. */
export function hideAll(saved: ChartPrefs): ChartPrefs {
  const snap: LayerSnapshot = {};
  let p = saved;
  for (const k of [...CLEAN_KEYS, ...emaKeys(saved)]) {
    snap[k] = layerOn(saved, k);
    p = setLayer(p, k, false);
  }
  return { ...p, cleanSnapshot: snap };
}

/** "Show all": the layers from before "Hide all". */
export function showAll(saved: ChartPrefs): ChartPrefs {
  let p: ChartPrefs = { ...saved, cleanSnapshot: null };
  for (const [k, on] of Object.entries(saved.cleanSnapshot ?? {})) p = setLayer(p, k, on);
  return p;
}

/** Overlays currently drawn (EMAs included), for the "Layers" button. */
export function activeLayerCount(p: ChartPrefs): number {
  return LAYERS.filter((l) => !["labels", "volume", "drawings"].includes(l.key) && layerOn(p, l.key)).length + p.emas.filter((e) => e.visible).length;
}
