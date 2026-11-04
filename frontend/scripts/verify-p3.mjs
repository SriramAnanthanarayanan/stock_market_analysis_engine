// P3 end-to-end UI verification against the running app (backend :8031, frontend :5191).
//   node scripts/verify-p3.mjs [screenshotDir] [baseUrl]
// Walks the P3 acceptance path: open a chart, label it (from a detection and by
// clicking candles), save and export labels, run an evaluation (precision/recall
// by pattern and timeframe), compare two configurations, run a sweep, and open
// the pattern history.
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
for (const channel of ["msedge", "chrome"]) {
  try {
    browser = await chromium.launch({ channel, headless: true });
    break;
  } catch {
    /* next */
  }
}
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("response", (r) => {
  const u = new URL(r.url());
  if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${u.pathname}`);
});
const api = (path, init) => page.evaluate(async ([p, i]) => (await fetch(p, i)).json(), [path, init]);
const labelCount = async () => (await api("/api/labels")).length;
const waitForLabels = async (n, ms = 15000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if ((await labelCount()) >= n) return true;
    await page.waitForTimeout(200);
  }
  return false;
};

try {
  await page.goto(`${base}/?u=all&tf=1d`, { waitUntil: "networkidle" });
  const before = (await api("/api/labels")).length;

  console.log("Label a chart");
  await page.waitForSelector('[data-testid="result-row"]', { timeout: 30000 });
  await page.locator('[data-testid="result-row"]').first().click();
  await page.waitForFunction(() => window.__pasChart && window.__pasChart.lineSeries > 0, null, { timeout: 30000 });
  await page.click('[data-testid="toggle-label"]');
  await page.waitForSelector('[data-testid="label-panel"]');
  await page.click('[data-testid="label-from-detection"]');
  await page.selectOption('[data-testid="label-verdict"]', "valid");
  await page.click('[data-testid="label-save"]');
  await waitForLabels(before + 1);
  const afterOne = await labelCount();
  check("label saved from a detection", afterOne === before + 1, `${before} -> ${afterOne} labels`);

  // Manual labelling by clicking candles on the chart.
  const box = await page.locator('[data-testid="pattern-chart"]').boundingBox();
  const at = async (fx) => page.mouse.click(box.x + box.width * fx, box.y + box.height * 0.4);
  await page.click('[data-testid="label-mode-start"]');
  await at(0.35);
  await page.click('[data-testid="label-mode-end"]');
  await at(0.6);
  await page.click('[data-testid="label-mode-upper"]');
  await at(0.36);
  await at(0.59);
  const windowText = await page.textContent('[data-testid="label-window"]');
  check("start/end captured by clicking candles", windowText && !windowText.includes("Window: - to -"), windowText?.trim());
  await page.selectOption('[data-testid="label-verdict"]', "near_miss");
  await page.click('[data-testid="label-save"]');
  await waitForLabels(afterOne + 1);
  const afterTwo = await labelCount();
  check("manually drawn label saved", afterTwo === afterOne + 1, `${afterOne} -> ${afterTwo} labels`);
  await page.screenshot({ path: join(outDir, "p3-labelling.png") });

  const csv = await page.evaluate(async () => (await fetch("/api/labels/export?format=csv")).text());
  check("labels export (CSV)", csv.split("\n").filter(Boolean).length === afterTwo + 1, `${csv.split("\n").filter(Boolean).length - 1} rows`);

  console.log("Evaluation");
  await page.click('[data-testid="nav-evaluation"]');
  await page.waitForSelector('[data-testid="run-evaluation"]');
  await page.click('[data-testid="run-evaluation"]');
  await page.waitForSelector('[data-testid="metrics-pattern-tf"] tbody tr', { timeout: 60000 });
  const rows = await page.locator('[data-testid="metrics-pattern-tf"] tbody tr').count();
  const firstRow = await page.locator('[data-testid="metrics-pattern-tf"] tbody tr').first().textContent();
  check("precision/recall by pattern and timeframe", rows >= 1, `${rows} rows; ${firstRow?.trim()}`);
  await page.click('[data-testid="run-compare"]');
  await page.waitForSelector('[data-testid="compare-table"] tbody tr', { timeout: 60000 });
  check("compare two configurations", (await page.locator('[data-testid="compare-table"] tbody tr').count()) >= 1);
  await page.click('[data-testid="run-sweep"]');
  await page.waitForSelector('[data-testid="sweep-table"] tbody tr', { timeout: 90000 });
  check("parameter sweep (5 values)", (await page.locator('[data-testid="sweep-table"] tbody tr').count()) === 5);
  const runs = await api("/api/evaluation/runs");
  check("evaluation runs persisted", runs.length >= 3 && runs.slice(0, 3).map((r) => r.kind).sort().join(",") === "compare,evaluate,sweep", runs.slice(0, 3).map((r) => r.kind).join(","));
  await page.screenshot({ path: join(outDir, "p3-evaluation.png"), fullPage: true });

  console.log("History");
  await page.click('[data-testid="nav-history"]');
  await page.waitForSelector('[data-testid="tracks-table"]');
  await page.waitForTimeout(1500);
  const trackRows = await page.locator('[data-testid="track-row"]').count();
  check("pattern history lists tracks", trackRows > 0, `${trackRows} tracks`);
  if (trackRows) {
    await page.locator('[data-testid="track-row"]').first().click();
    await page.waitForSelector('[data-testid="track-detail"]');
    const evs = await page.locator('[data-testid="track-detail"] ol li').count();
    check("track detail with lifecycle events", evs >= 1, `${evs} events`);
  }
  check("outcome analytics panel", await page.isVisible('[data-testid="outcome-summary"]'));
  await page.screenshot({ path: join(outDir, "p3-history.png"), fullPage: true });
  check("no page errors / server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("run completed", false, String(e));
} finally {
  await browser.close();
}
const failed = results.filter((r) => !r.ok);
console.log(`\nP3 UI verification: ${results.length - failed.length} passed, ${failed.length} failed. Screenshots: ${outDir}`);
process.exit(failed.length ? 1 : 0);
