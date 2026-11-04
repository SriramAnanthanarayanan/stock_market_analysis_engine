// E3 end-to-end UI verification (Stage Analysis + RS Line) against the running app.
//   node scripts/verify-e3.mjs [screenshotDir] [baseUrl]
// Needs a completed 1W scan (stock stages) and the official index data (index stages, benchmark).
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
const waitDbg = (fn, ms = 30000) => page.waitForFunction(fn, null, { timeout: ms });

try {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  });
  console.log("Stage Analysis page");
  await page.click('[data-testid="nav-stage"]');
  await page.waitForSelector('[data-testid="stage-table-row"]', { timeout: 60000 });
  const listing = await api("/api/stage?universe=nifty500");
  const cardStage1 = Number((await page.textContent('[data-testid="stage-card-stage1"]')).match(/\d+/)[0]);
  check("summary counts (Stage 1 / Early Stage 2 / Stage 2 / unqualified)", cardStage1 === listing.summary.stage1 && (await page.isVisible('[data-testid="stage-card-early_stage2"]')), JSON.stringify(listing.summary));
  const firstRow = await page.locator('[data-testid="stage-table-row"]').first().textContent();
  check("ranked watchlist", firstRow.startsWith("1") && listing.items[0].rank === 1, firstRow.slice(0, 60));
  await page.click('[data-testid="stage-card-stage1"]');
  await page.waitForTimeout(800);
  const s1 = await page.locator('[data-testid="stage-table-row"]').allTextContents();
  check("filter by stage", s1.length === cardStage1 && s1.every((t) => t.includes("Stage 1 - Watchlist")), `${s1.length} rows`);
  await page.selectOption('[data-testid="stage-rs"]', "above_zero");
  await page.waitForTimeout(800);
  const rsRows = (await api("/api/stage?universe=nifty500&stage=stage1&rs=above_zero")).items;
  check("filter by RS condition", rsRows.every((r) => r.metrics.rs_zero_line === "above") && (await page.locator('[data-testid="stage-table-row"]').count()) === rsRows.length, `${rsRows.length} rows`);
  await page.locator('[data-testid="stage-table-row"]').first().click();
  await page.waitForSelector('[data-testid="stage-detail"]');
  const det = await page.textContent('[data-testid="stage-detail"]');
  check("explanation with gate results", /PASS/.test(det) && /FAIL/.test(det) && det.includes("Base Range") && det.includes("Testing Ema"), det.slice(0, 80));
  check("assumptions visibly flagged", (await page.locator('[data-testid="stage-detail"] [data-testid="assumption-tag"]').count()) >= 1 && (await page.locator('[data-testid="stage-assumptions"] li').count()) >= 5);
  await page.screenshot({ path: join(outDir, "e3-stage-page.png") });
  const sym = rsRows[0]?.symbol;
  await page.click('[data-testid="stage-open-chart"]');
  await page.waitForURL(/tf=1w&show=stage,rs/);
  await waitDbg(() => window.__pasChart && window.__pasChart.timeframe === "1w" && window.__pasChart.rsLinePane && window.__pasChart.overlayLines === 1);
  const d = await dbg();
  check("weekly chart with Stage overlays (30W EMA, base lines) and RS Line pane", d.rsLinePane && d.overlayLines === 1 && d.rsLinePoints > 50, `${sym}: RS points ${d.rsLinePoints}, panes ${d.panes}`);
  const panel = await page.textContent('[data-testid="stage-label"]');
  check("stage panel matches the watchlist", panel.includes("Stage 1"), panel);
  const rsw = await api(`/api/rsline?symbol=${sym}&timeframe=1w`);
  check("RS Line weekly = RS 52 vs NIFTY 50 (source setup)", rsw.length === 52 && rsw.benchmark === "NIFTY 50" && rsw.reference.label.startsWith("RS-52"), rsw.reference.label);
  await page.screenshot({ path: join(outDir, "e3-stage-chart.png") });
  await page.click('[data-testid="explorer-tf-1d"]');
  await waitDbg(() => window.__pasChart.timeframe === "1d" && window.__pasChart.rsLinePane);
  const rsd = await api(`/api/rsline?symbol=${sym}&timeframe=1d`);
  check("RS Line daily uses 63 bars", rsd.length === 63 && (await dbg()).overlayLines === 0, "Stage overlay only on 1W");

  console.log("Indices");
  await page.goto(`${base}/stage?inst=index`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="stage-table-row"]', { timeout: 60000 });
  const idx = await api("/api/stage?instrument=index");
  check("index Stage Analysis", idx.summary.total > 100 && (await page.locator('[data-testid="stage-table-row"]').count()) > 0, JSON.stringify(idx.summary));
  const ov = await api("/api/indices/overview");
  const withStage = ov.items.filter((r) => r.stage);
  check("Index Analysis shows the stage column", withStage.length > 50 && withStage.every((r) => idx.items.find((x) => x.symbol === r.code)?.stage === r.stage), `${withStage.length} indices`);
  const code = idx.items.find((r) => r.stage !== "insufficient_history")?.symbol;
  await page.goto(`${base}/index/${encodeURIComponent(code)}?tf=1w&show=stage,rs`, { waitUntil: "networkidle" });
  await waitDbg(() => window.__pasChart && window.__pasChart.rsLinePane && window.__pasChart.overlayLines === 1);
  check("index weekly chart with Stage overlays and RS Line", (await page.textContent('[data-testid="stage-panel"]')).length > 50, code);
  await page.screenshot({ path: join(outDir, "e3-index-stage.png") });
  check("no page errors / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("run completed", false, String(e));
  await page.screenshot({ path: join(outDir, "e3-failure.png") }).catch(() => null);
} finally {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith("pas.")) localStorage.removeItem(k);
  }).catch(() => null);
  await context.close().catch(() => null);
  await browser.close().catch(() => null);
}
const failed = results.filter((r) => !r.ok);
console.log(`\nE3 UI verification: ${results.length - failed.length} passed, ${failed.length} failed. Screenshots: ${outDir}`);
process.exit(failed.length ? 1 : 0);
