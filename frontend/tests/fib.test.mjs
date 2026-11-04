// Fibonacci math and drawing coordinates (spec 15.6). Reference values are
// computed independently below (hand arithmetic), following TradingView's
// documented anchors: retracement 0 at the second point, 1 at the first;
// extension 1.0 = the P1->P2 move projected from P3.
import { test } from "node:test";
import assert from "node:assert/strict";

const fib = await import(globalThis.__pasLoad("charts/fib"));
const dr = await import(globalThis.__pasLoad("charts/drawings"));

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

test("uptrend retracement: low 100 -> high 200", () => {
  const L = (r) => fib.retracementPrice(100, 200, r);
  close(L(0), 200); // 0 % at the end of the move (P2)
  close(L(1), 100); // 100 % at the start (P1)
  close(L(0.236), 176.4);
  close(L(0.382), 161.8);
  close(L(0.5), 150);
  close(L(0.618), 138.2);
  close(L(1.618), 38.2);
});

test("downtrend retracement: high 200 -> low 100", () => {
  const L = (r) => fib.retracementPrice(200, 100, r);
  close(L(0), 100);
  close(L(1), 200);
  close(L(0.382), 138.2);
  close(L(0.618), 161.8);
  close(L(1.618), 261.8);
});

test("uptrend extension: 100 -> 200, retrace to 150", () => {
  const E = (r) => fib.extensionPrice(100, 200, 150, r);
  close(E(0), 150);
  close(E(0.618), 211.8);
  close(E(1), 250); // a move equal to P1->P2 projected from P3
  close(E(1.618), 311.8);
});

test("downtrend extension: 200 -> 100, retrace to 150", () => {
  const E = (r) => fib.extensionPrice(200, 100, 150, r);
  close(E(0), 150);
  close(E(0.618), 88.2);
  close(E(1), 50);
  close(E(1.618), -11.8);
});

test("reverse mode", () => {
  close(fib.retracementPrice(100, 200, 0, "linear", true), 100);
  close(fib.retracementPrice(100, 200, 0.618, "linear", true), 161.8);
  close(fib.extensionPrice(100, 200, 150, 1, "linear", true), 50);
});

test("logarithmic mode interpolates in log space", () => {
  close(fib.retracementPrice(100, 400, 0.5, "log"), 200, 1e-9); // geometric mean
  close(fib.retracementPrice(100, 400, 0, "log"), 400, 1e-9);
  close(fib.retracementPrice(100, 400, 1, "log"), 100, 1e-9);
  close(fib.extensionPrice(100, 200, 150, 1, "log"), 300, 1e-9); // same ratio as P1->P2
  close(fib.extensionPrice(100, 200, 150, 0.5, "log"), 150 * Math.SQRT2, 1e-9);
  assert.ok(Number.isNaN(fib.retracementPrice(-5, 100, 0.5, "log")));
  // log requested with a non-positive anchor -> linear levels (never invalid prices)
  const lv = fib.fibLevels("fib_retracement", [-5, 100], { mode: "log", levels: [{ ratio: 0.5, visible: true }] });
  close(lv[0].price, 47.5);
  // log differs from linear on a big move
  assert.ok(Math.abs(fib.retracementPrice(100, 400, 0.5, "log") - fib.retracementPrice(100, 400, 0.5)) > 40);
});

test("default and custom levels", () => {
  assert.deepEqual(fib.FIB_RETRACEMENT_LEVELS, [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236]);
  assert.deepEqual(fib.FIB_EXTENSION_LEVELS, [0, 0.382, 0.618, 1, 1.272, 1.414, 1.618, 2, 2.618, 3.618, 4.236]);
  const custom = fib.fibLevels("fib_retracement", [100, 200], { levels: [{ ratio: 5, visible: true }, { ratio: 0.7, visible: false }, { ratio: 0.3, visible: true, label: "mine" }] });
  assert.deepEqual(custom.map((l) => l.ratio), [0.3, 5]); // hidden level dropped, sorted
  assert.equal(custom[0].label, "mine");
  close(custom[1].price, 200 - 500);
  const many = fib.fibLevels("fib_retracement", [1, 2], { levels: Array.from({ length: 30 }, (_, i) => ({ ratio: i / 10, visible: true })) });
  assert.equal(many.length, fib.MAX_DISPLAYED_LEVELS);
  assert.equal(fib.ratioText(0.618, true), "61.8%");
  assert.equal(fib.ratioText(0.5, true), "50%");
  assert.equal(fib.ratioText(1.618, false), "1.618");
});

test("time axis: anchors are times, not bar numbers", () => {
  const day = 86400;
  const t0 = 1_760_000_000;
  const times = Array.from({ length: 10 }, (_, i) => t0 + i * day);
  const m1 = new dr.TimeMapper(times);
  const m2 = new dr.TimeMapper([...Array.from({ length: 5 }, (_, i) => t0 - (5 - i) * day), ...times]); // older history prepended
  const t = times[3];
  close(m2.logicalOf(t) - m1.logicalOf(t), 5); // bar position shifts ...
  assert.equal(m1.timeOf(m1.logicalOf(t)), t); // ... the anchor time does not
  assert.equal(m2.timeOf(m2.logicalOf(t)), t);
  close(m1.logicalOf(t0 + 2.5 * day), 2.5); // between bars: interpolated
  close(m1.logicalOf(times[9] + 3 * day), 12); // future: extrapolated with the bar spacing
  // a daily anchor on a weekly chart lands proportionally inside its week
  const weeks = Array.from({ length: 6 }, (_, i) => t0 + i * 7 * day);
  const mw = new dr.TimeMapper(weeks);
  close(mw.logicalOf(t0 + 7 * day + 2 * day), 1 + 2 / 7);
});

test("price axis and moves keep (time, price) anchors", () => {
  const day = 86400;
  const t0 = 1_760_000_000;
  const tm = new dr.TimeMapper(Array.from({ length: 20 }, (_, i) => t0 + i * day));
  const d = { drawing_id: "x1", drawing_type: "fib_retracement", anchors: [{ t: t0 + 2 * day, p: 100 }, { t: t0 + 8 * day, p: 200 }], visible: true };
  const moved = dr.translateDrawing(d, tm, 3, 12.5);
  assert.deepEqual(moved.anchors, [{ t: t0 + 5 * day, p: 112.5 }, { t: t0 + 11 * day, p: 212.5 }]);
  const h = dr.translateDrawing({ ...d, drawing_type: "hline", anchors: [{ t: t0, p: 50 }] }, tm, 4, 1);
  assert.deepEqual(h.anchors, [{ t: t0, p: 51 }]); // horizontal line: price only
  // levels follow the anchor prices exactly (moving an anchor moves the levels)
  const lv = fib.fibLevels("fib_retracement", moved.anchors.map((a) => a.p), {});
  close(lv.find((x) => x.ratio === 0.5).price, 162.5);
});

test("segment extension and parallel channel", () => {
  const [a, b] = dr.extendSegment({ x: 10, y: 10 }, { x: 20, y: 20 }, 100, true, true);
  assert.deepEqual([a, b], [{ x: 0, y: 0 }, { x: 100, y: 100 }]);
  const [p, q, r, s] = dr.channelLines([{ x: 0, y: 0 }, { x: 10, y: 10 }, { x: 5, y: 0 }]);
  assert.deepEqual([p, q], [{ x: 0, y: 0 }, { x: 10, y: 10 }]);
  assert.deepEqual([r, s], [{ x: 0, y: -5 }, { x: 10, y: 5 }]);
  assert.equal(dr.ANCHOR_COUNT.fib_extension, 3);
  assert.equal(dr.ANCHOR_COUNT.channel, 3);
});
