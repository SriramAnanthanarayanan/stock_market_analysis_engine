// UI verification of the F&O OI Buildup page (/fno, docs/fno-activity.md).
//   node scripts/verify-fno.mjs [screenshotDir] [baseUrl]
// Needs backend + frontend and at least two stored NSE sessions (the startup
// backfill). It edits the F&O watchlist and restores the original list at the end.
// Browser: Edge / Chrome channel, or PW_EXECUTABLE=/path/to/chromium.
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
const api = (path, init) =>
  page.evaluate(async ([p, i]) => {
    const r = await fetch(p, i ? { ...i, headers: { "Content-Type": "application/json" } } : undefined);
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [path, init ?? null]);

let originalWatch = null;
try {
  await page.goto(`${base}/`, { waitUntil: "domcontentloaded" });
  await page.click('[data-testid="nav-fno"]');
  await page.waitForSelector('[data-testid="fno-activity"]', { timeout: 15000 });
  check("nav link opens the F&O page", page.url().endsWith("/fno"));

  const st = (await api("/api/fno/status")).body;
  check("NSE sessions stored", st?.sessions?.stored >= 2, `${st?.sessions?.stored} (${st?.sessions?.first} to ${st?.sessions?.last}), job ${st?.job?.state}`);
  const act = (await api("/api/fno/activity")).body;
  originalWatch = act.watchlist_symbols;
  check("a session with a run exists", !!act.trade_date, `${act.trade_date} vs ${act.prev_date}`);

  const rules = await page.locator('[data-testid="fno-rules"] tbody tr').allInnerTexts();
  const flat = rules.join(" | ").replace(/\s+/g, " ");
  check("rules table shows the original thresholds", rules.length === 4 && /Strong Long Buildup > 10% > \+2% > \+1%/.test(flat) && /Short Buildup > 7% < -1.5% < -1%/.test(flat), flat);

  await page.waitForSelector('[data-testid="fno-signals"]', { timeout: 15000 });
  const uiRows = page.locator('[data-testid="fno-signals-row"]');
  const n = await uiRows.count();
  check("signals table = API signals", n === Math.min(act.signals.length, 100), `${n} rows, API ${act.signals.length}`);
  if (n) {
    const first = await uiRows.first().locator('[data-testid="symbol-link"]').getAttribute("data-symbol");
    check("original order (rank 1 first)", first === act.signals[0].symbol, `${first}`);
  }
  for (const nt of act.notices) check(`notice shown: ${nt.code}`, (await page.locator(`[data-testid="fno-notice-${nt.code}"]`).count()) === 1);
  await page.screenshot({ path: join(outDir, "fno-page.png"), fullPage: true });

  if (n) {
    const r0 = act.signals[0];
    await uiRows.first().locator('[data-testid="symbol-link"]').click();
    await page.waitForFunction(() => window.__pasChart && window.__pasChart.candles > 0, null, { timeout: 30000 }).catch(() => null);
    await page.waitForTimeout(1200);
    const txt = (await page.locator("body").innerText()).toLowerCase(); // panel titles are upper-cased by CSS
    check("preview opens with the signal context", txt.includes(`${r0.signal} on ${act.trade_date}`.toLowerCase()) && txt.includes("front-month oi"), r0.symbol);
    await page.screenshot({ path: join(outDir, "fno-preview.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
  }

  const roll = [];
  for (const d of act.dates.slice(0, 80)) {
    if (d.signals === 0 && d.notices > 0) {
      const a = (await api(`/api/fno/activity?date=${d.trade_date}`)).body;
      if (a.notices.some((x) => x.code === "rollover")) {
        roll.push(d.trade_date);
        break;
      }
    }
  }
  if (roll.length) {
    await page.selectOption('[data-testid="fno-date"]', roll[0]);
    await page.waitForSelector('[data-testid="fno-notice-rollover"]', { timeout: 15000 });
    check("rollover session shows the notice and no signals", (await page.locator('[data-testid="fno-signals-row"]').count()) === 0, roll[0]);
    await page.screenshot({ path: join(outDir, "fno-rollover.png"), fullPage: true });
    await page.selectOption('[data-testid="fno-date"]', act.dates[0].trade_date);
    await page.waitForTimeout(800);
  } else {
    check("rollover session in the stored history", false, "none found in the last 80 runs");
  }

  const known = (await api("/api/fno/watchlist")).body.known;
  const add = ["NIFTY", known.find((s) => s !== "NIFTY" && !originalWatch.includes(s))].filter((s) => !originalWatch.includes(s));
  await page.fill('[data-testid="fno-watch-input"]', add.join(", ").toLowerCase());
  await page.click('[data-testid="fno-watch-add"]');
  await page.waitForTimeout(1500);
  const chips = await page.locator('[data-testid="fno-watch-chip"]').allInnerTexts();
  check("watchlist add (lower case accepted)", add.every((s) => chips.some((c) => c.startsWith(s))), chips.join(" "));
  const wl = (await api("/api/fno/activity")).body;
  check("watchlist rows use the quadrant status", wl.watchlist.every((r) => ["Long Buildup", "Short Buildup", "Long Unwinding", "Short Covering", "Neutral"].includes(r.signal)), wl.watchlist.map((r) => `${r.symbol}:${r.signal}`).join(", "));
  await page.fill('[data-testid="fno-watch-input"]', "NOTASYMBOL");
  await page.click('[data-testid="fno-watch-add"]');
  await page.waitForTimeout(800);
  check("unknown symbol rejected", (await page.locator("text=not F&O underlyings").count()) > 0);
  await page.screenshot({ path: join(outDir, "fno-watchlist.png"), fullPage: true });
} finally {
  if (originalWatch) await api("/api/fno/watchlist", { method: "PUT", body: JSON.stringify({ symbols: originalWatch }) });
  check("no console / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) process.exitCode = 1;
