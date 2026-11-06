// Line chart mode (improvement task): the line uses the candles' own timestamps
// and closes; the running (incomplete) period is appended last and flagged; the
// drawing time mapper is built from the same times, so drawings cannot move when
// switching candle <-> line.
import { test } from "node:test";
import assert from "node:assert/strict";

const line = await import(globalThis.__pasLoad("charts/lineData"));
const dr = await import(globalThis.__pasLoad("charts/drawings"));

const candles = [
  { t: 1000, i: 0, o: 10, h: 12, l: 9, c: 11, v: 5 },
  { t: 2000, i: 1, o: 11, h: 13, l: 10, c: 12.5, v: 6 },
  { t: 3000, i: 2, o: 12.5, h: 14, l: 12, c: 13, v: null },
];

test("same timestamps and close prices as the candles", () => {
  const pts = line.toLineData(candles, null);
  assert.deepEqual(pts.map((p) => p.t), candles.map((c) => c.t));
  assert.deepEqual(pts.map((p) => p.v), candles.map((c) => c.c));
  assert.ok(pts.every((p) => p.partial === false));
});

test("the running period is the last, flagged point; never duplicated", () => {
  const partial = { t: 4000, o: 13, h: 15, l: 12.5, c: 14.2, v: 3, sessions: 2, last_session: null, period_end: null };
  const pts = line.toLineData(candles, partial);
  assert.equal(pts.length, 4);
  assert.deepEqual(pts[3], { t: 4000, v: 14.2, partial: true });
  assert.equal(line.toLineData(candles, { ...partial, t: 3000 }).length, 3); // already a complete candle
  assert.deepEqual(line.toLineData([], partial), [{ t: 4000, v: 14.2, partial: true }]);
});

test("drawing anchors map to the same chart position in both modes", () => {
  const times = candles.map((c) => c.t);
  const lineTimes = line.toLineData(candles, null).map((p) => p.t);
  const a = new dr.TimeMapper(times);
  const b = new dr.TimeMapper(lineTimes);
  for (const t of [500, 1000, 1500, 2999, 3000, 5000]) assert.equal(a.logicalOf(t), b.logicalOf(t));
});
