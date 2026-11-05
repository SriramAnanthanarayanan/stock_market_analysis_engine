// E1 end-to-end UI verification against the running app (backend :8031, frontend :5191).
//   node scripts/verify-e1.mjs [screenshotDir] [baseUrl]
// Needs completed 1D and 1H scans with technical analysis (any scan since E1).
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const outDir = process.argv[2] || "ui-screenshots";
const base = process.argv[3] || "http://127.0.0.1:5191";
// Live-data defaults; override for other data sets (e.g. the synthetic demo: E1_SYMBOL=RWALK E1_MIN_UNIVERSE=5).
const SYM = process.env.E1_SYMBOL || "ITC";
const MIN_UNIVERSE = Number(process.env.E1_MIN_UNIVERSE || 300);
mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${detail}` : ""}`);
};

// Playwright may reject an internal navigation promise while the browser closes.
process.on("unhandledRejection", (e) => {
  if (String(e).includes("has been closed")) return;
  console.error(e);
  process.exitCode = 1;
});

let browser;
for (const opt of [...(process.env.PW_EXECUTABLE ? [{ executablePath: process.env.PW_EXECUTABLE }] : []), { channel: "msedge" }, { channel: "chrome" }]) {
  try {
    browser = await chromium.launch({ ...opt, headless: true });
    break;
  } catch {
    /* next */
  }
}
const context = await browser.newContext({ viewport: { width: 1700, height: 1050 }, acceptDownloads: true });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(m.text()));
page.on("response", (r) => r.status() >= 500 && errors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));
page.on("dialog", (d) => d.accept());
const api = (path) => page.evaluate(async (p) => (await fetch(p)).json(), path);
const dbg = () => page.evaluate(() => ({ ...window.__pasChart }));
const waitDbg = (fn, ms = 20000) => page.waitForFunction(fn, null, { timeout: ms });
const chartBox = async () => page.locator('[data-testid="pattern-chart"]').boundingBox();
// Drawings live in the database per symbol (docs/chart-drawing-persistence.md).
const drawSym = () => decodeURIComponent(new URL(page.url()).pathname.split("/").pop());
const stored = async () => (await page.evaluate(async (s) => (await fetch(`/api/drawings?symbol=${encodeURIComponent(s)}`)).json(), drawSym())).items;
const autosaved = async () => {
  await page.waitForTimeout(250);
  await page.waitForFunction(() => document.querySelector('[data-testid="draw-save-state"]')?.getAttribute("data-state") === "saved", null, { timeout: 15000 }).catch(() => null);
};

try {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  });

  console.log("Navigation and scanners");
  const navs = ["nav-scanner", "nav-candlesticks", "nav-breakouts", "nav-indicators", "nav-history", "nav-evaluation"];
  const present = await Promise.all(navs.map((n) => page.isVisible(`[data-testid="${n}"]`)));
  check("left navigation lists the scanners", present.every(Boolean), navs.filter((_, i) => !present[i]).join(","));
  await page.click('[data-testid="nav-candlesticks"]');
  await page.waitForSelector('[data-testid="candlestick-scanner"]');
  await page.selectOption('[data-testid="age-select"]', "4");
  await page.waitForTimeout(1200);
  const candleRows = await page.locator('[data-testid="candle-table-row"]').count();
  check("candlestick scanner lists Marubozu candles", candleRows > 0, `${candleRows} rows (last 5 candles)`);
  const firstCandle = candleRows ? (await page.locator('[data-testid="candle-table-row"]').first().textContent()) : "";
  await page.click('[data-testid="nav-breakouts"]');
  await page.waitForSelector('[data-testid="breakout-scanner"]');
  await page.selectOption('[data-testid="age-select"]', "4");
  await page.waitForTimeout(1200);
  const brRows = await page.locator('[data-testid="breakout-table-row"]').count();
  const brCount = Number((await page.textContent('[data-testid="result-count"]')).split(" ")[0]);
  check("breakout scanner lists 10-day breakouts", brRows > 0, `${brCount} results`);
  await page.selectOption('[data-testid="confirm-select"]', "2");
  await page.waitForFunction((n) => Number(document.querySelector('[data-testid="result-count"]').textContent.split(" ")[0]) !== n, brCount, { timeout: 10000 }).catch(() => null);
  const brCount2 = Number((await page.textContent('[data-testid="result-count"]')).split(" ")[0]);
  const statuses = (await page.locator('[data-testid="breakout-table-row"]').allTextContents()).every((t) => t.includes("(2-close)"));
  check("breakout confirmation mode switch (2-close)", brCount2 !== brCount && statuses, `1-close ${brCount} vs 2-close ${brCount2}`);
  await page.click('[data-testid="nav-indicators"]');
  await page.waitForSelector('[data-testid="indicator-scanner"]');
  await page.waitForTimeout(1200);
  const count = async () => Number((await page.textContent('[data-testid="result-count"]')).split(" ")[0]);
  const indAll = await count();
  await page.selectOption('[data-testid="align-select"]', "bullish_full");
  await page.waitForFunction((n) => Number(document.querySelector('[data-testid="result-count"]').textContent.split(" ")[0]) !== n, indAll, { timeout: 10000 }).catch(() => null);
  const indBull = await count();
  const rowsTxt = await page.locator('[data-testid="indicator-table-row"]').allTextContents();
  check("indicator scanner filters EMA alignment", indAll >= MIN_UNIVERSE && indBull < indAll && rowsTxt.every((t) => t.includes("Bullish full")), `${indAll} symbols -> ${indBull} bullish full`);
  await page.screenshot({ path: join(outDir, "e1-indicator-scanner.png") });

  console.log("Symbol search");
  await page.fill('[data-testid="symbol-search"]', SYM);
  await page.waitForSelector('[data-testid="symbol-results"] button');
  await page.keyboard.press("Enter");
  await page.waitForURL(new RegExp(`/explorer/${SYM}`));
  await waitDbg(() => window.__pasChart && window.__pasChart.candles > 100);
  check("symbol search opens the chart", (await page.textContent('[data-testid="explorer-symbol"]')) === SYM);

  console.log("Indicators on the chart");
  check("clean defaults: no EMA, RSI or ATR", (await dbg()).emaSeries.length === 0 && !(await dbg()).rsiPane && !(await dbg()).atrPane);
  for (const p of [20, 50, 200]) await page.click(`[data-testid="ema-toggle-${p}"]`);
  await waitDbg(() => window.__pasChart.emaSeries.length === 3);
  let d = await dbg();
  check("multiple EMA overlays", JSON.stringify(d.emaSeries) === "[20,50,200]", JSON.stringify(d.emaSeries));
  const chartData = await api(`/api/chart?symbol=${SYM}&timeframe=1d`);
  const ind = await api(`/api/indicators?symbol=${SYM}&timeframe=1d&start_t=${chartData.candles[0].t}${chartData.scan_id ? `&scan_id=${chartData.scan_id}` : ""}`);
  const legend = await page.textContent('[data-testid="indicator-legend"]');
  const want = Number(ind.latest.ema["50"]).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  check("chart EMA values equal backend values", legend.includes(`EMA50 ${want}`) || legend.includes(`EMA50 ${Number(ind.latest.ema["50"]).toFixed(2)}`), `legend "${legend}" vs backend EMA50 ${ind.latest.ema["50"]}`);
  check("indicator window equals chart candles", ind.times.length === chartData.candles.length && ind.times[0] === chartData.candles[0].t, `${ind.times.length} vs ${chartData.candles.length}`);
  await page.click('[data-testid="ema-toggle-50"]');
  await waitDbg(() => window.__pasChart.emaSeries.length === 2);
  check("disable an individual EMA", JSON.stringify((await dbg()).emaSeries) === "[20,200]");
  await page.click('[data-testid="toggle-rsi"]');
  await waitDbg(() => window.__pasChart.rsiPane);
  await page.click('[data-testid="toggle-atr"]');
  await waitDbg(() => window.__pasChart.atrPane);
  d = await dbg();
  check("RSI and ATR panes", d.rsiPane && d.atrPane && d.panes === 4 && d.rsiLevels === 5, `panes=${d.panes} rsiLevels=${d.rsiLevels}`);
  await page.click('[data-testid="indicators-menu"]');
  await page.fill('[data-testid="ema-period-0"]', "9");
  await page.check('[data-testid="ema-visible-0"]');
  await waitDbg(() => window.__pasChart.emaSeries.includes(9));
  check("change an EMA period (backend recomputes)", (await dbg()).emaSeries.includes(9), JSON.stringify((await dbg()).emaSeries));
  await page.click('[data-testid="ema-reset"]');
  await waitDbg(() => window.__pasChart.emaSeries.length === 0);
  check("restore EMA defaults", (await dbg()).emaSeries.length === 0);
  await page.keyboard.press("Escape");
  await page.click('[data-testid="indicators-menu"]');
  await page.screenshot({ path: join(outDir, "e1-panes.png") });

  console.log("Overlays from scanner rows");
  const divs = await api("/api/scan/indicators?timeframe=1d&divergence=bullish&divergence_status=confirmed&max_age=20");
  const dsym = divs.items[0]?.symbol;
  if (dsym) {
    await page.goto(`${base}/explorer/${dsym}?tf=1d&show=divergence`, { waitUntil: "networkidle" });
    await waitDbg(() => window.__pasChart.divergenceSegments > 0);
    d = await dbg();
    check("RSI divergence lines and markers", d.divergenceSegments > 0 && d.rsiPane && Object.keys(d.techMarkers).some((k) => k.startsWith("divergence_")), `${dsym}: ${d.divergenceSegments} segments, ${JSON.stringify(d.techMarkers)}`);
    check("divergence details panel", (await page.locator('[data-testid="divergence-list"] li').count()) > 0);
    await page.screenshot({ path: join(outDir, "e1-divergence.png") });
  } else check("RSI divergence lines and markers", false, "no confirmed bullish divergence in the latest 1D scan");

  await page.goto(`${base}/candlesticks?age=4`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="candle-table-row"]');
  await page.locator('[data-testid="candle-table-row"]').first().click();
  await page.waitForSelector('[data-testid="chart-preview"]');
  await page.click('[data-testid="preview-open-page"]'); // the row opens the preview; "Open page" -> the chart page
  await page.waitForURL(/show=marubozu/);
  await waitDbg(() => Object.keys(window.__pasChart.techMarkers).some((k) => k.endsWith("marubozu")));
  d = await dbg();
  check("Marubozu markers and metrics", (await page.locator('[data-testid="marubozu-list"] li').count()) > 0, `${firstCandle?.slice(0, 30)} -> ${JSON.stringify(d.techMarkers)}`);

  await page.goto(`${base}/breakouts?age=4`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="breakout-table-row"]');
  await page.locator('[data-testid="breakout-table-row"]').first().click();
  await page.waitForSelector('[data-testid="chart-preview"]');
  await page.click('[data-testid="preview-open-page"]'); // the row opens the preview; "Open page" -> the chart page
  await page.waitForURL(/show=levels,breakouts/);
  await waitDbg(() => window.__pasChart.dailyLevels === 2 && Object.keys(window.__pasChart.techMarkers).some((k) => k.includes("10d")));
  d = await dbg();
  check("daily breakout markers and 10D levels on 1D", d.dailyLevels === 2, JSON.stringify(d.techMarkers));
  const sym1d = new URL(page.url()).pathname.split("/").pop();
  await page.screenshot({ path: join(outDir, "e1-breakout.png") });
  await page.click('[data-testid="explorer-tf-1h"]');
  await waitDbg(() => window.__pasChart.timeframe === "1h" && window.__pasChart.dailyLevels === 2);
  const lv1h = await api(`/api/technical?symbol=${sym1d}&timeframe=1h`);
  const lv1d = await api(`/api/technical?symbol=${sym1d}&timeframe=1d`);
  check(
    "1H chart shows daily-derived 10D levels (not 10 hourly bars)",
    lv1h.daily.levels["10"].high === lv1d.daily.levels["10"].high && (await page.textContent('[data-testid="daily-levels"]')).includes("completed daily candles"),
    `1H ${lv1h.daily.levels["10"].high} = 1D ${lv1d.daily.levels["10"].high}`,
  );
  await page.screenshot({ path: join(outDir, "e1-1h-levels.png") });

  console.log("Drawing tools");
  await page.evaluate(() => localStorage.removeItem("pas.chartPrefs.v1")); // default panes: candles + volume
  await page.goto(`${base}/explorer/${process.env.E1_DRAW_SYMBOL || SYM}?tf=1d`, { waitUntil: "networkidle" });
  await waitDbg(() => window.__pasChart.candles > 100);
  let box = await chartBox();
  const at = (fx, fy) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
  const click = async (fx, fy) => {
    const p = at(fx, fy);
    await page.mouse.click(p.x, p.y);
  };
  // Start from a clean symbol: this script writes drawings to the database and removes them again.
  for (const d of await stored()) await page.evaluate(async ([id, v]) => fetch(`/api/drawings/${id}?version=${v}`, { method: "DELETE" }), [d.drawing_id, d.version]);
  await page.reload({ waitUntil: "networkidle" });
  await waitDbg(() => window.__pasChart.candles > 100 && window.__pasChart.drawings === 0);
  box = await chartBox();
  await page.click('[data-testid="tool-hline"]');
  await click(0.5, 0.2);
  await page.click('[data-testid="tool-trend"]');
  await click(0.3, 0.45);
  await click(0.55, 0.3);
  await page.click('[data-testid="tool-rect"]');
  await click(0.6, 0.5);
  await click(0.7, 0.4);
  await page.click('[data-testid="tool-fib_retracement"]');
  await click(0.2, 0.55);
  await click(0.4, 0.25);
  await page.click('[data-testid="tool-ray"]');
  await click(0.1, 0.5);
  await click(0.2, 0.45);
  await page.click('[data-testid="tool-vline"]');
  await click(0.45, 0.3);
  await waitDbg(() => window.__pasChart.drawings === 6);
  check("create hline, trendline, zone, fib, ray, vline", (await dbg()).drawings === 6, `${(await dbg()).drawings} drawings`);
  await autosaved();
  let saved = await stored();
  check("drawings saved to the database, anchored to time and price", saved.length === 6 && saved.every((s) => s.anchors.every((p) => Number.isFinite(p.t) && p.t > 1e9 && Number.isFinite(p.p))), saved.map((s) => s.drawing_type).join(","));
  await page.screenshot({ path: join(outDir, "e1-drawings.png") });
  // Select the horizontal line by clicking it, then drag it down.
  await page.click('[data-testid="tool-none"]');
  const hl = saved.find((s) => s.drawing_type === "hline");
  await click(0.85, 0.2);
  await waitDbg(() => window.__pasChart.selectedDrawing !== null);
  check("select an existing drawing", (await dbg()).selectedDrawing === hl.drawing_id, `${(await dbg()).selectedDrawing}`);
  const p0 = at(0.85, 0.2);
  await page.mouse.move(p0.x, p0.y);
  await page.mouse.down();
  await page.mouse.move(p0.x, p0.y + 40, { steps: 6 });
  await page.mouse.up();
  await autosaved();
  saved = await stored();
  const moved = saved.find((s) => s.drawing_id === hl.drawing_id);
  check("move a drawing (price anchor changes, saved)", moved.anchors[0].p < hl.anchors[0].p && moved.version > hl.version, `${hl.anchors[0].p.toFixed(2)} -> ${moved.anchors[0].p.toFixed(2)}`);
  // Resize the trendline by dragging its end handle.
  const tr = saved.find((s) => s.drawing_type === "trend");
  const e = at(0.55, 0.3);
  await page.mouse.click(box.x + box.width * 0.425, box.y + box.height * 0.375); // middle of the trendline
  await page.waitForTimeout(150);
  const selTrend = (await dbg()).selectedDrawing === tr.drawing_id;
  await page.mouse.move(e.x, e.y);
  await page.mouse.down();
  await page.mouse.move(e.x + 60, e.y - 30, { steps: 6 });
  await page.mouse.up();
  await autosaved();
  saved = await stored();
  const tr2 = saved.find((s) => s.drawing_id === tr.drawing_id);
  check("resize a drawing (end handle)", selTrend && tr2.anchors[1].t > tr.anchors[1].t && tr2.anchors[0].t === tr.anchors[0].t, `end ${tr.anchors[1].t} -> ${tr2.anchors[1].t}`);
  await page.selectOption('[data-testid="draw-width"]', "3");
  await autosaved();
  saved = await stored();
  check("change drawing style", saved.find((s) => s.drawing_id === tr.drawing_id).line_width === 3);
  await page.click('[data-testid="draw-delete"]');
  await waitDbg(() => window.__pasChart.drawings === 5);
  await autosaved();
  check("delete a drawing", (await dbg()).drawings === 5 && (await stored()).length === 5);
  await page.reload({ waitUntil: "networkidle" });
  await waitDbg(() => window.__pasChart.candles > 100 && window.__pasChart.drawings === 5);
  check("drawings persist after a refresh (database)", (await dbg()).drawings === 5);
  await page.click('[data-testid="explorer-tf-1w"]');
  await waitDbg(() => window.__pasChart.timeframe === "1w");
  await page.waitForTimeout(800);
  check("drawings belong to the symbol: shown on 1W too (default: every timeframe)", (await dbg()).drawings === 5);
  await page.click('[data-testid="explorer-tf-1d"]');
  await waitDbg(() => window.__pasChart.timeframe === "1d" && window.__pasChart.drawings === 5);
  const dl = page.waitForEvent("download");
  await page.click('[data-testid="export-drawings"]');
  const file = await dl;
  check("export drawings backup (JSON)", (await file.suggestedFilename()) === "chart-drawings.json");
  await page.click('[data-testid="draw-hide"]');
  await page.waitForTimeout(150);
  check("hide drawings", (await page.textContent('[data-testid="draw-hide"]')).includes("Show"));
  await page.click('[data-testid="draw-hide"]');
  await page.click('[data-testid="draw-clear"]'); // confirm dialog accepted
  await waitDbg(() => window.__pasChart.drawings === 0);
  await autosaved();
  check("clear all drawings with confirmation", (await dbg()).drawings === 0 && (await stored()).length === 0);
  const backup = await file.path();
  await page.setInputFiles('[data-testid="import-file"]', backup);
  await waitDbg(() => window.__pasChart.drawings === 5);
  check("import drawings backup", (await dbg()).drawings === 5);
  for (const d of await stored()) await page.evaluate(async ([id, v]) => fetch(`/api/drawings/${id}?version=${v}`, { method: "DELETE" }), [d.drawing_id, d.version]);
  await page.reload({ waitUntil: "networkidle" });
  await waitDbg(() => window.__pasChart.candles > 100);
  await page.click('[data-testid="tool-crosshair"]');
  const c = at(0.5, 0.3);
  await page.mouse.move(c.x, c.y);
  await page.mouse.move(c.x + 5, c.y + 5);
  await page.waitForSelector('[data-testid="crosshair-readout"]', { timeout: 3000 }).catch(() => null);
  check("crosshair inspection readout", await page.isVisible('[data-testid="crosshair-readout"]'));
  await page.click('[data-testid="tool-none"]');

  console.log("Layout, image, cleanup");
  await page.click('[data-testid="ema-toggle-200"]');
  await page.click('[data-testid="toggle-rsi"]');
  await waitDbg(() => window.__pasChart.rsiPane && window.__pasChart.emaSeries.includes(200));
  await page.click('[data-testid="save-layout"]');
  await page.click('[data-testid="toggle-rsi"]');
  await page.click('[data-testid="ema-toggle-200"]');
  await waitDbg(() => !window.__pasChart.rsiPane);
  await page.click('[data-testid="restore-layout"]');
  await waitDbg(() => window.__pasChart.rsiPane && window.__pasChart.emaSeries.includes(200));
  check("save / restore chart layout", true);
  const img = page.waitForEvent("download");
  await page.click('[data-testid="export-image"]');
  check("export chart image", (await (await img).suggestedFilename()).endsWith(".png"));
  await page.click('[data-testid="fit-content"]');
  await page.setViewportSize({ width: 1300, height: 900 });
  await page.waitForTimeout(400);
  box = await chartBox();
  check("chart resizes with panes", box.width < 1300 && (await dbg()).panes >= 3, `${Math.round(box.width)}px, ${(await dbg()).panes} panes`);
  const before = await dbg();
  await page.click('[data-testid="nav-indicators"]');
  await page.waitForSelector('[data-testid="indicator-scanner"]');
  const after = await dbg();
  check("chart disposed on navigation", after.disposed === before.disposed + 1, `created=${after.created} disposed=${after.disposed}`);
  check("no page errors / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("run completed", false, String(e));
  await page.screenshot({ path: join(outDir, "e1-failure.png") }).catch(() => null);
} finally {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  }).catch(() => null);
  await context.close().catch(() => null);
  await browser.close().catch(() => null);
}
const failed = results.filter((r) => !r.ok);
console.log(`\nE1 UI verification: ${results.length - failed.length} passed, ${failed.length} failed. Screenshots: ${outDir}`);
process.exit(failed.length ? 1 : 0);
