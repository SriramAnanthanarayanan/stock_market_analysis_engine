// E2 end-to-end UI verification against the running app (backend :8031, frontend :5191).
//   node scripts/verify-e2.mjs [screenshotDir] [baseUrl]
// Needs the index registry and official index history (startup refresh or POST /api/indices/refresh).
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const outDir = process.argv[2] || "ui-screenshots";
const base = process.argv[3] || "http://127.0.0.1:5191";
mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${detail}` : ""}`);
};
process.on("unhandledRejection", (e) => {
  if (String(e).includes("has been closed")) return;
  console.error(e);
  process.exitCode = 1;
});

let browser;
for (const channel of ["msedge", "chrome"]) {
  try {
    browser = await chromium.launch({ channel, headless: true });
    break;
  } catch {
    /* next */
  }
}
const context = await browser.newContext({ viewport: { width: 1700, height: 1100 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("response", (r) => r.status() >= 500 && errors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));
const api = (path) => page.evaluate(async (p) => (await fetch(p)).json(), path);
const dbg = () => page.evaluate(() => ({ ...window.__pasChart }));
const waitDbg = (fn, ms = 20000) => page.waitForFunction(fn, null, { timeout: ms });

try {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  });
  check("navigation: RRG and Index Analysis", (await page.isVisible('[data-testid="nav-rrg"]')) && (await page.isVisible('[data-testid="nav-indices"]')));

  console.log("Index Analysis");
  await page.click('[data-testid="nav-indices"]');
  await page.waitForSelector('[data-testid="index-table-row"]', { timeout: 60000 });
  const status = await page.textContent('[data-testid="ia-status"]');
  check("index universe from the official catalogue", /1[0-9]{2} indices in the official catalogue/.test(status) && /\d+ with verified data/.test(status), status.replace(/\s+/g, " ").slice(0, 140));
  const ov = await api("/api/indices/overview");
  const rowsAll = ov.items.filter((r) => r.analysis_enabled && r.latest_close !== undefined && r.latest_close !== null).length;
  const cats = new Set(ov.items.map((r) => r.category));
  check("broad, sector, thematic and factor categories", ["broad", "sector", "thematic", "strategy"].every((c) => cats.has(c)), [...cats].join(","));
  await page.selectOption('[data-testid="ia-category"]', "strategy");
  await page.selectOption('[data-testid="ia-subcategory"]', "momentum");
  await page.waitForTimeout(300);
  const mom = await page.locator('[data-testid="index-table-row"]').allTextContents();
  check("category + subcategory filter (factor / momentum)", mom.length >= 2 && mom.every((t) => t.includes("Momentum")), `${mom.length} momentum indices`);
  await page.selectOption('[data-testid="ia-subcategory"]', "");
  await page.selectOption('[data-testid="ia-category"]', "");
  await page.selectOption('[data-testid="ia-rs"]', "out");
  await page.waitForTimeout(300);
  const outRows = await page.locator('[data-testid="index-table-row"]').count();
  await page.selectOption('[data-testid="ia-rs"]', "");
  await page.selectOption('[data-testid="ia-trend"]', "above");
  await page.waitForTimeout(300);
  const aboveTxt = await page.locator('[data-testid="index-table-row"]').allTextContents();
  check("relative-strength and trend filters", outRows > 0 && outRows < rowsAll && aboveTxt.length > 0, `${outRows} outperforming, ${aboveTxt.length} above EMA 200 (of ${rowsAll})`);
  await page.selectOption('[data-testid="ia-trend"]', "");
  const d1 = ov.items.find((r) => r.code === "NIFTY IT");
  await page.click('[data-testid="ia-tf-1w"]');
  await page.waitForTimeout(2500);
  const w1 = (await api("/api/indices/overview?timeframe=1w")).items.find((r) => r.code === "NIFTY IT");
  check("timeframe switch (1D / 1W indicators)", d1.rsi !== w1.rsi && JSON.stringify(d1.returns) === JSON.stringify(w1.returns), `RSI 1D ${d1.rsi} vs 1W ${w1.rsi}; returns stay daily`);
  await page.screenshot({ path: join(outDir, "e2-index-analysis.png") });

  console.log("Index Explorer");
  await page.fill('[data-testid="ia-search"]', "NIFTY IT");
  await page.waitForTimeout(300);
  await page.locator('[data-testid="index-table-row"]', { hasText: "NIFTY IT" }).first().click();
  await page.waitForURL(/\/index\//);
  await waitDbg(() => window.__pasChart && window.__pasChart.candles > 100, 30000);
  check("index chart from official data", (await dbg()).candles > 100 && (await page.textContent('[data-testid="data-source"]')).includes("official"), `${(await dbg()).candles} candles`);
  await page.click('[data-testid="ema-toggle-50"]');
  await page.click('[data-testid="toggle-rsi"]');
  await waitDbg(() => window.__pasChart.emaSeries.includes(50) && window.__pasChart.rsiPane);
  check("indicators on an index chart", true, JSON.stringify((await dbg()).emaSeries));
  check("index registry + official reference check", (await page.textContent('[data-testid="index-registry"]')).includes("verified"));
  await page.waitForSelector('[data-testid="index-rrg"]');
  check("RRG position on the index page", (await page.textContent('[data-testid="index-rrg"]')).includes("Quadrant"));
  check("performance vs benchmark", (await page.textContent('[data-testid="index-performance"]')).includes("RS vs benchmark"));
  await page.screenshot({ path: join(outDir, "e2-index-explorer.png") });
  const reg = (await api("/api/indices")).items;
  const noIntraday = reg.find((r) => r.analysis_enabled && r.data_status === "ok" && r.intraday_status !== "verified");
  if (noIntraday) {
    await page.goto(`${base}/index/${encodeURIComponent(noIntraday.code)}?tf=1h`, { waitUntil: "networkidle" });
    await page.waitForSelector('[data-testid="index-chart-unavailable"]');
    check("no manufactured intraday data", (await page.textContent('[data-testid="index-chart-unavailable"]')).includes("intraday"), noIntraday.code);
  }
  const withIntraday = reg.find((r) => r.intraday_status === "verified" && r.analysis_enabled);
  if (withIntraday) {
    await page.goto(`${base}/index/${encodeURIComponent(withIntraday.code)}?tf=1h`, { waitUntil: "networkidle" });
    await waitDbg(() => window.__pasChart && window.__pasChart.timeframe === "1h" && window.__pasChart.candles > 50, 60000);
    check("verified intraday index chart (1H)", (await page.textContent('[data-testid="data-source"]')).includes("verified"), `${withIntraday.code} via ${withIntraday.intraday_ticker}`);
  } else check("verified intraday index chart (1H)", false, "no verified intraday mapping");

  console.log("Relative Rotation Graph");
  await page.goto(`${base}/rrg`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="rrg-head"]', { timeout: 60000 });
  const heads = await page.locator('[data-testid="rrg-head"]').count();
  const legend = await page.locator('[data-testid="rrg-toggle"]').count();
  check("RRG renders sector indices with trails", heads > 5 && heads === Math.min(legend, 20), `${heads} indices drawn`);
  const meta500 = await page.textContent('[data-testid="rrg-meta"]');
  check("RRG-style method and data basis stated", meta500.includes("NIFTY 500") && meta500.includes("price-return") && (await page.textContent('[data-testid="rrg-page"]')).includes("not the proprietary JdK"));
  const pts12 = await page.locator('[data-testid="rrg-point"]').first().locator("circle").count();
  await page.click('[data-testid="rrg-trail-4"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="rrg-point"]')?.querySelectorAll("circle").length === 4, null, { timeout: 20000 });
  check("trail length 12 -> 4 weeks", pts12 === 12, `${pts12} -> 4 points`);
  await page.selectOption('[data-testid="rrg-benchmark"]', "NIFTY 50");
  await page.waitForFunction(() => document.querySelector('[data-testid="rrg-meta"]')?.textContent?.includes("NIFTY 50 "), null, { timeout: 20000 });
  check("benchmark switch (NIFTY 50)", (await page.textContent('[data-testid="rrg-meta"]')).includes("Benchmark NIFTY 50"));
  await page.click('[data-testid="rrg-cat-strategy"]');
  await page.fill('[data-testid="rrg-max"]', "60");
  await page.waitForTimeout(500);
  const heads2 = await page.locator('[data-testid="rrg-head"]').count();
  check("category filter adds factor indices", heads2 > heads, `${heads} -> ${heads2}`);
  await page.fill('[data-testid="rrg-search"]', "momentum");
  await page.waitForTimeout(300);
  const momHeads = await page.locator('[data-testid="rrg-point"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-code")));
  const names = Object.fromEntries((await api("/api/indices")).items.map((r) => [r.code, r.name]));
  check("search index (official names)", momHeads.length > 0 && momHeads.every((c) => /MOMENTUM/i.test(names[c] ?? "")), momHeads.map((c) => names[c]).join(" | "));
  await page.locator('[data-testid="rrg-toggle"]').first().uncheck();
  await page.waitForTimeout(200);
  check("hide an index", (await page.locator('[data-testid="rrg-head"]').count()) === momHeads.length - 1);
  await page.fill('[data-testid="rrg-search"]', "");
  const head = page.locator('[data-testid="rrg-head"]').first();
  await head.hover();
  await page.waitForSelector('[data-testid="rrg-tooltip"]');
  check("hover tooltip", /RS-Ratio .* RS-Momentum/.test(await page.textContent('[data-testid="rrg-tooltip"]')));
  await page.screenshot({ path: join(outDir, "e2-rrg.png") });
  const lastWeekNow = await page.textContent('[data-testid="rrg-meta"]');
  await page.fill('[data-testid="rrg-asof"]', "2026-06-30");
  await page.waitForFunction((prev) => document.querySelector('[data-testid="rrg-meta"]')?.textContent !== prev, lastWeekNow, { timeout: 20000 });
  check("historical as-of RRG", (await page.textContent('[data-testid="rrg-meta"]')).includes("Jun 2026"), (await page.textContent('[data-testid="rrg-meta"]')).slice(0, 80));
  await page.fill('[data-testid="rrg-asof"]', "");
  await page.waitForTimeout(1500);
  const code = await page.locator('[data-testid="rrg-point"]').first().getAttribute("data-code");
  await page.locator('[data-testid="rrg-head"]').first().click();
  await page.waitForURL(/\/index\//);
  check("click an RRG point opens its index", decodeURIComponent(page.url()).includes(`/index/${code}`), code);

  console.log("Common symbol search");
  await page.fill('[data-testid="symbol-search"]', "NIFTY BANK");
  await page.waitForSelector('[data-testid="symbol-results"] button');
  const first = await page.locator('[data-testid="symbol-results"] button').first().textContent();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/index\/NIFTY%20BANK|\/index\/NIFTY BANK/);
  check("symbol search finds indices", first.includes("NIFTY BANK"), first);
  check("no page errors / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("run completed", false, String(e));
  await page.screenshot({ path: join(outDir, "e2-failure.png") }).catch(() => null);
} finally {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  }).catch(() => null);
  await context.close().catch(() => null);
  await browser.close().catch(() => null);
}
const failed = results.filter((r) => !r.ok);
console.log(`\nE2 UI verification: ${results.length - failed.length} passed, ${failed.length} failed. Screenshots: ${outDir}`);
process.exit(failed.length ? 1 : 0);
