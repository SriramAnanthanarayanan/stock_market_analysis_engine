// UI verification of chart-preview keyboard stepping and chart layers
// (docs/chart-preview.md "Keyboard" and "Layers"):
//   Space / Shift+Space step through the results table (its order, across pages),
//   a timeframe picked in the preview is kept, the open row is highlighted;
//   Layers / Labels / Hide all (H) declutter the chart and are saved in the browser,
//   a scanner layer the user hid stays hidden on the next chart.
//   node scripts/verify-chart-nav.mjs [screenshotDir] [baseUrl]
// Needs backend + frontend and a completed 1D scan with a Confluence snapshot
// (NAV_PAGE=/vcp etc. for another table). Read-only: writes nothing to the DB.
// Browser: Edge / Chrome channel, or PW_EXECUTABLE=/path/to/chromium.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const outDir = args[0] || "ui-screenshots";
const base = args[1] || "http://127.0.0.1:5191";
const PAGE = process.env.NAV_PAGE || "/confluence";
mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${detail}` : ""}`);
};

let browser;
for (const opt of [...(process.env.PW_EXECUTABLE ? [{ executablePath: process.env.PW_EXECUTABLE }] : []), { channel: "msedge" }, { channel: "chrome" }]) {
  try {
    browser = await chromium.launch({ ...opt, headless: true });
    break;
  } catch {
    /* next */
  }
}
if (!browser) throw new Error("no browser: install Edge/Chrome or set PW_EXECUTABLE");
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(m.text()));
page.on("response", (r) => r.status() >= 500 && errors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));

const PREVIEW = '[data-testid="chart-preview"]';
const previewSymbol = () => page.getAttribute(PREVIEW, "data-symbol");
const navPos = () => page.innerText('[data-testid="preview-nav-pos"]').catch(() => null);
const rowSymbols = () => page.$$eval("[data-chart-nav-table] [data-chart-nav]", (els) => els.map((e) => e.dataset.symbol));
const currentRow = () => page.$eval("tr[data-chart-current] [data-chart-nav]", (e) => e.dataset.symbol).catch(() => null);
const prefs = () => page.evaluate(() => JSON.parse(localStorage.getItem("pas.chartPrefs.v1") || "{}"));
const dbg = () => page.evaluate(() => ({ ...window.__pasChart }));
async function settled(symbol) {
  await page.waitForFunction((s) => document.querySelector('[data-testid="chart-preview"]')?.getAttribute("data-symbol") === s, symbol, { timeout: 20000 });
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="chart-preview"]');
    return el && !el.innerText.includes("Loading...") && window.__pasChart?.candles > 0;
  }, null, { timeout: 30000 });
  await page.waitForTimeout(1500); // indicator / technical / overlay requests
}

try {
  await page.goto(`${base}${PAGE}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("[data-chart-nav-table] [data-chart-nav]", { timeout: 30000 });
  await page.waitForTimeout(1000);
  const syms = await rowSymbols();
  const total = Number(await page.getAttribute("[data-chart-nav-table]", "data-chart-nav-total"));
  check("table marks its rows for keyboard stepping", syms.length >= 3 && total >= syms.length, `${syms.length} on the page, ${total} rows`);

  // ---- Space / Shift+Space
  await page.locator("[data-chart-nav-table] [data-chart-nav]").first().click();
  await settled(syms[0]);
  check("preview shows the row position", (await navPos()) === `1 / ${total}`, await navPos());
  check("open row is highlighted", (await currentRow()) === syms[0], await currentRow());
  await page.screenshot({ path: join(outDir, "nav-1-first.png") });
  await page.keyboard.press("Space");
  await settled(syms[1]);
  check("Space opens the next stock (focused symbol button not re-clicked)", (await previewSymbol()) === syms[1], `${syms[0]} -> ${await previewSymbol()}`);
  check("position and highlight follow", (await navPos()) === `2 / ${total}` && (await currentRow()) === syms[1], `${await navPos()} ${await currentRow()}`);
  await page.keyboard.press("Space");
  await settled(syms[2]);
  await page.keyboard.press("Shift+Space");
  await settled(syms[1]);
  check("Shift+Space opens the previous stock", (await previewSymbol()) === syms[1]);
  await page.click('[data-testid="preview-next"]');
  await settled(syms[2]);
  check("▶ button steps too", (await previewSymbol()) === syms[2]);
  await page.click('[data-testid="preview-prev"]');
  await settled(syms[1]);

  // ---- a timeframe picked in the preview is kept while stepping
  await page.click('[data-testid="preview-tf-1w"]');
  await settled(syms[1]);
  await page.keyboard.press("Space");
  await settled(syms[2]);
  const onWeekly = await page.$eval('[data-testid="preview-tf-1w"]', (e) => e.className.includes("chip-on"));
  check("picked timeframe (1W) kept on the next stock", onWeekly && (await dbg()).timeframe === "1w", (await dbg()).timeframe);
  await page.click('[data-testid="preview-tf-1d"]');
  await settled(syms[2]);

  // ---- Layers: a scanner layer the user hides stays hidden, overrides are not saved
  const forcedBefore = await prefs();
  check("scanner overrides are not saved", !forcedBefore.showBreakouts && !forcedBefore.showMarubozu, JSON.stringify({ b: forcedBefore.showBreakouts, m: forcedBefore.showMarubozu }));
  await page.click('[data-testid="layers-menu"]');
  await page.waitForSelector('[data-testid="layers-panel"]');
  const brk = page.locator('[data-testid="layer-breakouts"]');
  const forcedOn = await brk.isChecked();
  await page.screenshot({ path: join(outDir, "nav-2-layers.png") });
  if (forcedOn) {
    const m0 = (await dbg()).markers;
    await brk.uncheck();
    await page.waitForTimeout(800);
    const m1 = (await dbg()).markers;
    const p1 = await prefs();
    check("hiding a scanner layer removes it and is remembered", m1 <= m0 && (p1.hiddenByUser ?? []).includes("breakouts"), `markers ${m0} -> ${m1}, hidden ${JSON.stringify(p1.hiddenByUser)}`);
    await page.click('[data-testid="layers-menu"]'); // close the menu
    await page.keyboard.press("Space");
    await settled(syms[3] ?? syms[0]);
    await page.click('[data-testid="layers-menu"]');
    check("hidden scanner layer stays hidden on the next stock", !(await page.locator('[data-testid="layer-breakouts"]').isChecked()));
    await page.locator('[data-testid="layer-breakouts"]').check();
    await page.waitForTimeout(500);
    const p2 = await prefs();
    check("switching it on again forgets the hide", !(p2.hiddenByUser ?? []).includes("breakouts"), JSON.stringify(p2.hiddenByUser));
    await page.locator('[data-testid="layer-breakouts"]').uncheck(); // leave the test browser clean
    await page.locator('[data-testid="layer-breakouts"]').check();
    await page.click('[data-testid="layers-menu"]');
  } else {
    check("scanner layer present (skipped: this list does not switch breakouts on)", true);
    await page.click('[data-testid="layers-menu"]');
  }
  const symNow = await previewSymbol();

  // ---- Labels
  await page.click('[data-testid="preview-labels"]');
  await page.waitForTimeout(800);
  check("Labels off: text removed, saved", (await dbg()).labels === false && (await prefs()).showLabels === false);
  await page.screenshot({ path: join(outDir, "nav-3-labels-off.png") });
  await page.click('[data-testid="preview-labels"]');
  await page.waitForTimeout(500);
  check("Labels on again", (await dbg()).labels === true);

  // ---- Hide all (H)
  const before = await dbg();
  await page.keyboard.press("h");
  await page.waitForTimeout(1000);
  const cleanDbg = await dbg();
  const cleanText = await page.innerText('[data-testid="preview-clean"]');
  check("H hides every overlay", cleanText.includes("Show all") && cleanDbg.lineSeries === 0 && cleanDbg.emaSeries.length === 0 && (await prefs()).cleanSnapshot, `lines ${before.lineSeries} -> ${cleanDbg.lineSeries}, markers ${before.markers} -> ${cleanDbg.markers}`);
  await page.screenshot({ path: join(outDir, "nav-4-hide-all.png") });
  await page.keyboard.press("Space");
  await page.waitForFunction((s) => document.querySelector('[data-testid="chart-preview"]')?.getAttribute("data-symbol") !== s, symNow, { timeout: 20000 });
  await settled(await previewSymbol());
  check("Hide all stays on for the next stock", (await page.innerText('[data-testid="preview-clean"]')).includes("Show all"));
  await page.keyboard.press("h");
  await page.waitForTimeout(1000);
  check("H again brings the overlays back", (await page.innerText('[data-testid="preview-clean"]')).includes("Hide all") && !(await prefs()).cleanSnapshot);

  // ---- across a page boundary
  const pager = page.locator('[data-chart-nav-page="next"]');
  if ((await pager.count()) && !(await pager.isDisabled())) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    const onPage = await rowSymbols();
    await page.locator("[data-chart-nav-table] [data-chart-nav]").last().click();
    await settled(onPage[onPage.length - 1]);
    const lastPos = await navPos();
    await page.keyboard.press("Space");
    await page.waitForFunction((s) => document.querySelector('[data-testid="chart-preview"]')?.getAttribute("data-symbol") !== s, onPage[onPage.length - 1], { timeout: 20000 });
    await settled(await previewSymbol());
    const nextPos = await navPos();
    check("Space at the end of a page opens the first row of the next page", Number(nextPos?.split(" / ")[0]) === Number(lastPos?.split(" / ")[0]) + 1, `${lastPos} -> ${nextPos} (${await previewSymbol()})`);
    await page.keyboard.press("Shift+Space");
    await settled(onPage[onPage.length - 1]);
    check("Shift+Space goes back to the previous page", (await navPos()) === lastPos, `${await navPos()}`);
  } else check("page boundary (skipped: one page only)", true);

  // ---- Esc closes, highlight cleared
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  check("Esc closes the preview and clears the highlight", (await page.locator(PREVIEW).count()) === 0 && (await currentRow()) === null);
} finally {
  check("no console / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) process.exitCode = 1;
