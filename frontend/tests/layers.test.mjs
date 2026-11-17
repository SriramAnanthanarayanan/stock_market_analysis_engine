// Chart layers (lib/chartLayers.ts): a scanner row's overlays are a per-chart
// override that is never saved, a layer the user hides stays hidden (also from
// scanner rows), and "Hide all" / "Show all" is an exact round trip.
import { test } from "node:test";
import assert from "node:assert/strict";

const L = await import(globalThis.__pasLoad("lib/chartLayers"));
const P = await import(globalThis.__pasLoad("lib/prefs"));

const fresh = () => ({ ...P.DEFAULT_PREFS, emas: P.DEFAULT_EMAS.map((e) => ({ ...e })), hiddenByUser: [], cleanSnapshot: null });

test("scanner show tokens map to layers", () => {
  assert.deepEqual([...L.forcedLayers("breakouts,marubozu")].sort(), ["breakouts", "marubozu"]);
  assert.deepEqual([...L.forcedLayers("divergence")].sort(), ["divergence", "rsi"]);
  assert.deepEqual([...L.forcedLayers("ema")].sort(), ["ema:20", "ema:200", "ema:50"]);
  assert.equal(L.forcedLayers(null).size, 0);
});

test("a scanner override is drawn but not saved when another layer changes", () => {
  const saved = fresh();
  const forced = L.forcedLayers("breakouts,ema");
  const eff = L.effectivePrefs(saved, forced);
  assert.equal(eff.showBreakouts, true);
  assert.equal(L.layerOn(eff, "ema:50"), true);
  assert.equal(saved.showBreakouts, false);
  // The user switches the volume pane off: breakouts / EMAs stay unsaved.
  const next = L.prefsToSave({ ...eff, showVolume: false }, eff, saved, forced);
  assert.equal(next.showVolume, false);
  assert.equal(next.showBreakouts, false);
  assert.equal(L.layerOn(next, "ema:50"), false);
  assert.deepEqual(next.hiddenByUser, []);
});

test("hiding a scanner layer is remembered; switching it on again forgets it", () => {
  const saved = fresh();
  const forced = L.forcedLayers("breakouts");
  const eff = L.effectivePrefs(saved, forced);
  const hidden = L.prefsToSave(L.setLayer(eff, "breakouts", false), eff, saved, forced);
  assert.deepEqual(hidden.hiddenByUser, ["breakouts"]);
  // Next chart from the same scanner: stays hidden.
  const eff2 = L.effectivePrefs(hidden, forced);
  assert.equal(eff2.showBreakouts, false);
  // Switched on again: shown and no longer hidden.
  const shown = L.prefsToSave(L.setLayer(eff2, "breakouts", true), eff2, hidden, forced);
  assert.equal(shown.showBreakouts, true);
  assert.deepEqual(shown.hiddenByUser, []);
});

test("labels, pattern and scanner signal are plain saved switches", () => {
  const saved = fresh();
  const forced = new Set();
  const eff = L.effectivePrefs(saved, forced);
  const next = L.prefsToSave({ ...eff, showLabels: false, showPattern: false }, eff, saved, forced);
  assert.equal(next.showLabels, false);
  assert.equal(next.showPattern, false);
  assert.equal(L.layerOn(next, "labels"), false);
  assert.equal(L.layerOn(next, "context"), true);
  assert.deepEqual(next.hiddenByUser, []); // only scanner-switchable layers are remembered as hidden
});

test("hide all switches every overlay off, ignores scanner rows, and show all restores", () => {
  let saved = fresh();
  saved = { ...saved, showRsi: true, showVcp: true, showLabels: false, emas: saved.emas.map((e) => ({ ...e, visible: e.period === 50 })) };
  const clean = L.hideAll(saved);
  assert.ok(clean.cleanSnapshot);
  for (const k of ["rsi", "vcp", "pattern", "context", "ema:50"]) assert.equal(L.layerOn(clean, k), false, k);
  assert.equal(clean.showVolume, true); // volume and drawings stay
  assert.equal(clean.showDrawings, true);
  assert.equal(clean.showLabels, false); // labels keep their own setting
  const eff = L.effectivePrefs(clean, L.forcedLayers("breakouts,vcp"));
  assert.equal(eff.showBreakouts, false);
  assert.equal(eff.showVcp, false);
  assert.equal(L.activeLayerCount(eff), 0);
  const back = L.showAll(clean);
  assert.equal(back.cleanSnapshot, null);
  for (const k of ["rsi", "vcp", "pattern", "context", "ema:50"]) assert.equal(L.layerOn(back, k), true, k);
  assert.equal(L.layerOn(back, "ema:20"), false);
});

test("restoring defaults clears the hidden list", () => {
  const saved = { ...fresh(), hiddenByUser: ["breakouts"] };
  const forced = L.forcedLayers("breakouts");
  const eff = L.effectivePrefs(saved, forced);
  const next = L.prefsToSave({ ...P.DEFAULT_PREFS, emas: P.DEFAULT_EMAS.map((e) => ({ ...e })), hiddenByUser: [], cleanSnapshot: null }, eff, saved, forced);
  assert.deepEqual(next.hiddenByUser, []);
  assert.equal(L.effectivePrefs(next, forced).showBreakouts, true);
});
