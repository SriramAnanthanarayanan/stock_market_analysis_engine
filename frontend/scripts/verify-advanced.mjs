// End-to-end UI verification of the advanced features against the running app
// (backend :8031 + frontend :5191, completed 1D and 1W scans with the advanced scanners).
//   node scripts/verify-advanced.mjs [screenshotDir] [baseUrl]
//   node scripts/verify-advanced.mjs [screenshotDir] [baseUrl] --after-restart
// The second form, run after restarting the backend, checks that the drawings the
// first run created are still there. It WRITES drawings and one template into the
// app's database and deletes what it can again (the restart check keeps two drawings
// on one symbol; delete them in the chart afterwards if you do not want them).
// Browser: Edge / Chrome channel, or PW_EXECUTABLE=/path/to/chromium.
import { chromium } from "playwright-core";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const afterRestart = process.argv.includes("--after-restart");
const outDir = args[0] || "ui-screenshots";
const base = args[1] || "http://127.0.0.1:5191";
mkdirSync(outDir, { recursive: true });
const idsFile = join(outDir, "verify-advanced-drawings.json");
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${detail}` : ""}`);
};
const info = (name, detail) => console.log(`  [INFO] ${name}${detail ? ` - ${detail}` : ""}`);

process.on("unhandledRejection", (e) => {
  if (String(e).includes("has been closed")) return;
  console.error(e);
  process.exitCode = 1;
});

let browser;
const launchers = [
  ...(process.env.PW_EXECUTABLE ? [{ executablePath: process.env.PW_EXECUTABLE }] : []),
  { channel: "msedge" },
  { channel: "chrome" },
];
for (const opt of launchers) {
  try {
    browser = await chromium.launch({ ...opt, headless: true });
    break;
  } catch {
    /* next */
  }
}
if (!browser) throw new Error("no browser: install Edge/Chrome or set PW_EXECUTABLE");
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
const errors = [];
const watch = (p) => {
  p.on("pageerror", (e) => errors.push(String(e)));
  p.on("console", (m) => m.type() === "error" && !/Failed to load resource|409 \(Conflict\)/.test(m.text()) && errors.push(m.text()));
  p.on("response", (r) => r.status() >= 500 && errors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));
  p.on("dialog", (d) => d.accept());
};
watch(page);
const api = (path, init) => page.evaluate(async ([p, i]) => {
  const r = await fetch(p, i ? { ...i, headers: { "Content-Type": "application/json" } } : undefined);
  return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
}, [path, init ?? null]);
const dbg = (p = page) => p.evaluate(() => ({ ...window.__pasChart }));
const settle = (ms = 900) => page.waitForTimeout(ms);
const rows = (testid) => page.locator(`[data-testid="${testid}-row"]`);
const chartReady = (p = page) => p.waitForFunction(() => window.__pasChart && window.__pasChart.candles > 0, null, { timeout: 30000 });
// Autosave runs after a short debounce: wait past it, then for the "saved" state.
const saved = async (p = page) => {
  await p.waitForTimeout(250);
  return p.waitForFunction(() => document.querySelector('[data-testid="draw-save-state"]')?.getAttribute("data-state") === "saved", null, { timeout: 15000 }).then(() => true).catch(() => false);
};

async function openPreviewFrom(testid) {
  const link = page.locator(`[data-testid="${testid}-row"] [data-testid="symbol-link"]`).first();
  const sym = await link.getAttribute("data-symbol");
  await link.click();
  await page.waitForSelector('[data-testid="chart-preview"]', { timeout: 15000 });
  await chartReady();
  return sym;
}
async function closePreview() {
  await page.click('[data-testid="preview-close"]');
  await page.waitForSelector('[data-testid="chart-preview"]', { state: "detached", timeout: 5000 }).catch(() => null);
}

async function chartBox(p = page) {
  return p.locator('[data-testid="pattern-chart"]').first().boundingBox();
}
async function clickChart(fx, fy, p = page) {
  const b = await chartBox(p);
  await p.mouse.click(b.x + b.width * fx, b.y + b.height * fy);
}

try {
  if (afterRestart) {
    console.log("Drawings after an application restart");
    const saved_ = JSON.parse(readFileSync(idsFile, "utf8"));
    await page.goto(`${base}/explorer/${encodeURIComponent(saved_.symbol)}?tf=1d`, { waitUntil: "networkidle" });
    await chartReady();
    await settle(1500);
    const list = await api(`/api/drawings?symbol=${encodeURIComponent(saved_.symbol)}`);
    const ids = new Set((list.body?.items ?? []).map((d) => d.drawing_id));
    check("drawings survive a backend restart (database)", saved_.ids.every((id) => ids.has(id)), `${saved_.ids.length} expected, ${ids.size} on ${saved_.symbol}`);
    check("chart shows them after the restart", (await dbg()).drawings >= saved_.ids.length, `chart drawings ${(await dbg()).drawings}`);
    await page.screenshot({ path: join(outDir, "adv-after-restart.png") });
    for (const id of saved_.ids) {
      const d = (list.body?.items ?? []).find((x) => x.drawing_id === id);
      if (d) await api(`/api/drawings/${id}?version=${d.version}`, { method: "DELETE" });
    }
    info("cleanup", "verification drawings removed");
  } else {
    await page.goto(`${base}/`, { waitUntil: "networkidle" });
    console.log("Navigation");
    const navs = ["nav-confluence", "nav-fresh-breakouts", "nav-52week", "nav-volume", "nav-vcp"];
    const present = await Promise.all(navs.map((n) => page.isVisible(`[data-testid="${n}"]`)));
    check("new scanners in the navigation", present.every(Boolean), navs.filter((_, i) => !present[i]).join(","));

    console.log("Confluence Dashboard");
    await page.click('[data-testid="nav-confluence"]');
    await page.waitForSelector('[data-testid="confluence-dashboard"]');
    await page.waitForSelector('[data-testid="confluence-table-row"]', { timeout: 15000 }).catch(() => null);
    const cfRows = await rows("confluence-table").count();
    const scores = (await page.locator('[data-testid="cf-score"]').allTextContents()).map(Number);
    check("dashboard ranks stocks by confluence score", cfRows > 0 && scores.every((x, i) => i === 0 || scores[i - 1] >= x), `${cfRows} rows, top ${scores[0]}`);
    const cfHead = await page.textContent('[data-testid="confluence-table"] thead');
    const cfCols = ["Symbol", "Company", "Close", "Score", "Band", "Coverage", "Signals", "VCP", "10D", "52W", "Marubozu", "Vol shock", "Pattern", "EMA alignment", "Stage", "RS / RRG", "Latest signal"];
    check("columns of spec 8.7 present", cfCols.every((c) => cfHead.includes(c)), cfCols.filter((c) => !cfHead.includes(c)).join(","));
    check("snapshot, config and band counts shown", (await page.textContent('[data-testid="cf-snapshot"]')).includes("confluence config v") && (await page.isVisible('[data-testid="cf-band-counts"]')));
    await page.screenshot({ path: join(outDir, "adv-confluence.png") });
    await page.click('[data-testid="cf-screen-breakout_marubozu_volume"]');
    await settle();
    const comboRows = await rows("confluence-table").count();
    const badges = await page.locator('[data-testid="confluence-table-row"] [data-testid="cf-combo-badge"]').count();
    check("screen: fresh breakout + Marubozu + volume shock", comboRows > 0 && badges === comboRows && (await page.isVisible('[data-testid="cf-screen-description"]')), `${comboRows} rows, ${badges} with the combination bonus`);
    if (comboRows) {
      await rows("confluence-table").first().click();
      await page.waitForSelector('[data-testid="confluence-breakdown"]');
      const cats = await page.locator('[data-testid^="breakdown-cat-"]').count();
      const uses = await page.locator('[data-testid="breakdown-evidence"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
      check("row click: evidence breakdown with five categories", cats === 5 && uses.includes("used"), `${cats} categories, ${uses.length} evidence rows`);
      check("capped / overlapping evidence is shown, not added", uses.some((u) => u === "bonus" || u === "overlapping"), [...new Set(uses)].join(","));
      check("same-session combination explained", (await page.textContent('[data-testid="breakdown-combo"]')).includes("same session"));
      await page.screenshot({ path: join(outDir, "adv-confluence-breakdown.png") });
      const urlCf = page.url();
      const cfSym = await openPreviewFrom("confluence-table");
      check("symbol click opens the chart preview with the confluence context", (await page.textContent('[data-testid="preview-context"]')).includes("Confluence"), cfSym);
      await page.screenshot({ path: join(outDir, "adv-confluence-preview.png") });
      await closePreview();
      check("dashboard filters kept after the preview", page.url() === urlCf && (await page.isVisible('[data-testid="confluence-breakdown"]')));
    }
    await page.click('[data-testid="cf-screen-all"]');
    await page.click('[data-testid="cf-dir-bearish"]');
    await settle();
    const bearRows = await rows("confluence-table").count();
    if (bearRows) {
      await rows("confluence-table").first().click();
      await page.waitForSelector('[data-testid="confluence-breakdown"]');
    }
    check("bearish confluence listed separately", bearRows === 0 || (await page.textContent('[data-testid="cf-detail"]')).includes("(bearish)"), `${bearRows} rows`);
    await page.click('[data-testid="cf-dir-bullish"]');
    await page.selectOption('[data-testid="cf-vcp"]', "breakout");
    await settle();
    const vcpLabels = await page.locator('[data-testid="confluence-table-row"] td:nth-child(9)').allTextContents();
    check("VCP status filter", vcpLabels.every((t) => /breakout/i.test(t)), vcpLabels.join(","));
    await page.selectOption('[data-testid="cf-vcp"]', "");
    await page.fill('[data-testid="cf-search"]', "rwal");
    await settle(400);
    check("search by symbol / company", (await rows("confluence-table").count()) <= 1);
    await page.fill('[data-testid="cf-search"]', "");
    const snapBefore = await page.textContent('[data-testid="cf-snapshot"]');
    await page.click('[data-testid="cf-recompute"]');
    await page.waitForFunction((b) => document.querySelector('[data-testid="cf-snapshot"]')?.textContent !== b, snapBefore, { timeout: 15000 }).catch(() => null);
    check("Recompute builds a new snapshot from the stored evidence", (await page.textContent('[data-testid="cf-snapshot"]')) !== snapBefore);

    console.log("Fresh 10-day breakouts");
    await page.click('[data-testid="nav-fresh-breakouts"]');
    await page.waitForSelector('[data-testid="fresh-breakout-scanner"]');
    await page.selectOption('[data-testid="age-select"]', "60");
    await settle();
    const freshRows = await rows("fresh-table").count();
    check("fresh breakout events listed (last 60 sessions)", freshRows > 0, `${freshRows} rows`);
    const header = await page.textContent('[data-testid="fresh-table"] thead');
    check("columns: level, strength, first crossing, sessions since, confirmation, event ID", ["Prev 10D", "Strength", "First crossing", "Sessions since", "Confirmation", "Event ID"].every((c) => header.includes(c)));
    await page.check('[data-testid="first-only"]');
    await settle();
    const firstRows = await rows("fresh-table").count();
    check("First Breakout in Lookback filter narrows the list", firstRows <= freshRows, `${firstRows} <= ${freshRows}`);
    await page.uncheck('[data-testid="first-only"]');
    await settle();
    await page.screenshot({ path: join(outDir, "adv-fresh-breakouts.png") });

    console.log("Chart preview keeps the page state");
    await page.evaluate(() => window.scrollTo(0, 120));
    const urlBefore = page.url();
    const scrollBefore = await page.evaluate(() => window.scrollY);
    const sym = await openPreviewFrom("fresh-table");
    check("symbol click opens the preview drawer", await page.isVisible('[data-testid="chart-preview"]'), sym);
    check("preview shows the signal that opened it", (await page.textContent('[data-testid="preview-context"]')).includes("Previous 10-day"), "");
    check("preview draws the breakout level and marker", (await dbg()).overlayShapes > 0 && ((await dbg()).techMarkers.extra_marker ?? 0) >= 1);
    await page.screenshot({ path: join(outDir, "adv-preview.png") });
    await page.click('[data-testid="preview-expand"]');
    await page.waitForSelector('[data-testid="chart-fullscreen"] [data-testid="explorer"]', { timeout: 15000 });
    await chartReady();
    check("Expand opens the full charting terminal", await page.isVisible('[data-testid="chart-fullscreen"] [data-testid="terminal-bar"]'));
    await page.screenshot({ path: join(outDir, "adv-fullscreen.png") });
    await page.keyboard.press("Escape");
    await page.waitForSelector('[data-testid="chart-fullscreen"]', { state: "detached", timeout: 5000 });
    check("Esc closes fullscreen back to the preview", await page.isVisible('[data-testid="chart-preview"]'));
    await page.keyboard.press("Escape");
    await page.waitForSelector('[data-testid="chart-preview"]', { state: "detached", timeout: 5000 });
    check("Esc closes the preview", !(await page.isVisible('[data-testid="chart-preview"]')));
    check("filters, URL and scroll position are unchanged", page.url() === urlBefore && (await page.inputValue('[data-testid="age-select"]')) === "60" && Math.abs((await page.evaluate(() => window.scrollY)) - scrollBefore) < 2, `scroll ${scrollBefore}`);

    console.log("Volume Shocker");
    await page.click('[data-testid="nav-volume"]');
    await page.waitForSelector('[data-testid="volume-shocker"]');
    await page.selectOption('[data-testid="age-select"]', "4");
    await page.uncheck('[data-testid="liq-toggle"]');
    await settle();
    const v2 = await rows("volume-table").count();
    await page.selectOption('[data-testid="rvol-select"]', "1.5");
    await settle();
    const v15 = await rows("volume-table").count();
    check("volume shocks listed; RVOL 1.5x >= 2x", v2 > 0 && v15 >= v2, `2x: ${v2}, 1.5x: ${v15}`);
    if (v15) {
      await openPreviewFrom("volume-table");
      check("volume-shock preview shows RVOL", (await page.textContent('[data-testid="preview-context"]')).includes("RVOL"));
      await closePreview();
    }
    await page.screenshot({ path: join(outDir, "adv-volume.png") });

    console.log("52-week breakouts");
    await page.click('[data-testid="nav-52week"]');
    await page.waitForSelector('[data-testid="week52-scanner"]');
    await page.selectOption('[data-testid="view-select"]', "state");
    await settle();
    const ok52 = await rows("week52-table").count();
    await page.check('[data-testid="insufficient-toggle"]');
    await settle();
    const all52 = await rows("week52-table").count();
    check("52-week range view; insufficient history only when asked", ok52 > 0 && all52 >= ok52, `${ok52} with 252 sessions, ${all52} incl. insufficient`);
    await page.screenshot({ path: join(outDir, "adv-52week.png") });

    console.log("VCP");
    await page.click('[data-testid="nav-vcp"]');
    await page.waitForSelector('[data-testid="vcp-scanner"]');
    await settle();
    const vcpRows = await rows("vcp-table").count();
    check("VCP scanner lists formations", vcpRows > 0, `${vcpRows} rows`);
    let vcpSymbol = null;
    if (vcpRows) {
      await rows("vcp-table").first().click();
      await page.waitForSelector('[data-testid="vcp-detail"] [data-testid="vcp-contractions"]', { timeout: 10000 });
      check("VCP detail explains contractions, score and gates", (await page.locator('[data-testid="vcp-contractions"] tbody tr').count()) >= 2 && (await page.isVisible('[data-testid="vcp-score"]')) && (await page.textContent('[data-testid="vcp-explanation"]')).includes("PASS"));
      await page.screenshot({ path: join(outDir, "adv-vcp-scanner.png") });
      vcpSymbol = await openPreviewFrom("vcp-table");
      await page.waitForFunction(() => (window.__pasChart.techMarkers.extra_marker ?? 0) >= 0 && window.__pasChart.overlayShapes >= 5, null, { timeout: 15000 }).catch(() => null);
      const d = await dbg();
      check("VCP chart: base, contractions and pivot drawn from stored geometry", d.overlayShapes >= 5, `${d.overlayShapes} shapes`);
      await page.screenshot({ path: join(outDir, "adv-vcp-preview.png") });

      console.log("Multi-month timeframes");
      for (const tf of ["1mo", "3mo", "6mo", "12mo"]) {
        await page.click(`[data-testid="preview-tf-${tf}"]`);
        await page.waitForFunction((t) => window.__pasChart.timeframe === t && window.__pasChart.candles > 0, tf, { timeout: 20000 }).catch(() => null);
        const d2 = await dbg();
        check(`${tf} candles in the preview`, d2.timeframe === tf && d2.candles > 0, `${d2.candles} candles, running period shown: ${d2.partialCandle}`);
      }
      await page.screenshot({ path: join(outDir, "adv-preview-12mo.png") });
      await page.click('[data-testid="preview-expand"]');
      await page.waitForSelector('[data-testid="multimonth-note"]', { timeout: 15000 });
      check("multi-month limitation note in the terminal", (await page.textContent('[data-testid="multimonth-note"]')).includes("do not run"));
      await page.keyboard.press("Escape");
      await page.waitForSelector('[data-testid="chart-fullscreen"]', { state: "detached" });
      await closePreview();
    }

    console.log("Every results table opens the preview");
    for (const [nav, testid, table] of [
      ["nav-scanner", "dashboard", "result"],
      ["nav-candlesticks", "candlestick-scanner", "candle-table"],
      ["nav-breakouts", "breakout-scanner", "breakout-table"],
      ["nav-indicators", "indicator-scanner", "indicator-table"],
      ["nav-stage", null, "stage-table"],
    ]) {
      await page.click(`[data-testid="${nav}"]`);
      if (testid) await page.waitForSelector(`[data-testid="${testid}"]`);
      await settle(1200);
      const sel = table === "result" ? '[data-testid="result-row"] [data-testid="symbol-link"]' : `[data-testid="${table}-row"] [data-testid="symbol-link"]`;
      const n = await page.locator(sel).count();
      if (!n) {
        info(`${nav}`, "no rows to open on this data");
        continue;
      }
      await page.locator(sel).first().click();
      const okp = await page.waitForSelector('[data-testid="chart-preview"]', { timeout: 15000 }).then(() => true).catch(() => false);
      await chartReady().catch(() => null);
      check(`${nav}: symbol click opens the preview (no navigation)`, okp && page.url().includes(nav === "nav-scanner" ? "/" : "") && !(await page.isVisible('[data-testid="explorer"]')));
      await closePreview();
    }

    console.log("Drawings: Fibonacci, persistence, timeframes, tabs, backup");
    const target = vcpSymbol || sym;
    await page.goto(`${base}/explorer/${encodeURIComponent(target)}?tf=1d`, { waitUntil: "networkidle" });
    await chartReady();
    await settle(1200);
    const beforeList = (await api(`/api/drawings?symbol=${target}`)).body.items;
    const before = beforeList.length;
    const beforeIds = new Set(beforeList.map((x) => x.drawing_id));
    // Clicks stay in the upper part of the chart: the price pane (sub panes sit below it).
    await page.click('[data-testid="tool-fib_retracement"]');
    await clickChart(0.55, 0.35);
    await clickChart(0.8, 0.08);
    check("Fib retracement created with two clicks", await saved(), "");
    let list = (await api(`/api/drawings?symbol=${target}`)).body.items;
    const fibR = list.find((x) => x.drawing_type === "fib_retracement" && !beforeIds.has(x.drawing_id));
    check("saved to the database with 14 default levels", list.length === before + 1 && fibR && fibR.settings.levels.length === 14, `${list.length - before} new`);
    await page.click('[data-testid="draw-settings"]');
    await page.waitForSelector('[data-testid="fib-settings"]');
    await page.selectOption('[data-testid="fib-mode"]', "log");
    await page.fill('[data-testid="fib-new-ratio"]', "0.707");
    await page.click('[data-testid="fib-add-level"]');
    await page.check('[data-testid="fib-percent_labels"]');
    await saved();
    list = (await api(`/api/drawings?symbol=${target}`)).body.items;
    const fibR2 = list.find((x) => x.drawing_id === fibR.drawing_id);
    check("settings saved: log mode, custom level, percent labels", fibR2.settings.mode === "log" && fibR2.settings.levels.some((l) => l.ratio === 0.707) && fibR2.settings.percent_labels === true && fibR2.version > 1, `version ${fibR2.version}`);
    const tplName = `verify ${Date.now()}`;
    await page.fill('[data-testid="fib-template-name"]', tplName);
    await page.click('[data-testid="fib-template-save"]');
    await settle(800);
    const tpls = (await api("/api/drawing-templates")).body.items;
    const tpl = tpls.find((t) => t.name === tplName);
    check("Fibonacci template saved", !!tpl);
    await page.click('[data-testid="ds-tf-this"]');
    await saved();
    await page.click('[data-testid="drawing-settings-close"]');
    await page.screenshot({ path: join(outDir, "adv-fib-retracement.png") });
    await page.click('[data-testid="tool-fib_extension"]');
    await clickChart(0.3, 0.38);
    await clickChart(0.45, 0.12);
    await clickChart(0.52, 0.25);
    await saved();
    list = (await api(`/api/drawings?symbol=${target}`)).body.items;
    const fibE = list.find((x) => x.drawing_type === "fib_extension" && !beforeIds.has(x.drawing_id));
    check("trend-based Fib extension created with three clicks", !!fibE && fibE.anchors.length === 3);
    await page.screenshot({ path: join(outDir, "adv-fib-extension.png") });
    const onDaily = (await dbg()).drawings;
    await page.click('[data-testid="explorer-tf-1w"]');
    await chartReady();
    await settle(1500);
    const onWeekly = (await dbg()).drawings;
    check("'only this timeframe' hides the retracement on 1W; the extension stays", onWeekly === onDaily - 1, `1D ${onDaily}, 1W ${onWeekly}`);
    await page.click('[data-testid="explorer-tf-1d"]');
    await chartReady();
    await page.reload({ waitUntil: "networkidle" });
    await chartReady();
    await settle(1500);
    check("drawings are back after a browser refresh", (await dbg()).drawings === onDaily, `${(await dbg()).drawings}`);

    // second tab: a save there reaches this tab; a stale edit here is rejected
    const page2 = await context.newPage();
    watch(page2);
    await page2.goto(`${base}/explorer/${encodeURIComponent(target)}?tf=1d`, { waitUntil: "networkidle" });
    await chartReady(page2);
    await page2.waitForTimeout(1200);
    await page2.click('[data-testid="tool-hline"]');
    await clickChart(0.5, 0.2, page2);
    await saved(page2);
    await page.waitForFunction((n) => window.__pasChart.drawings === n, onDaily + 1, { timeout: 10000 }).catch(() => null);
    check("a drawing saved in another tab appears here", (await dbg()).drawings === onDaily + 1, `${(await dbg()).drawings}`);
    // the h-line the second tab just drew (never another script's or the user's own h-line)
    const hl = (await api(`/api/drawings?symbol=${target}`)).body.items.find((x) => x.drawing_type === "hline" && !beforeIds.has(x.drawing_id));
    // the other tab changes the extension (version +1) without this tab knowing
    const cur = (await api(`/api/drawings?symbol=${target}`)).body.items.find((x) => x.drawing_id === fibE.drawing_id);
    const theirs = await page2.evaluate(async (d) => (await fetch(`/api/drawings/${d.drawing_id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...d, color: "#ff00ff", base_version: d.version, version: undefined, created_at: undefined, updated_at: undefined, origin: undefined }) })).status, cur);
    const stale = await api(`/api/drawings/${fibE.drawing_id}`, { method: "PUT", body: JSON.stringify({ ...cur, color: "#00ff00", base_version: cur.version, version: undefined, created_at: undefined, updated_at: undefined, origin: undefined }) });
    check("a stale edit from another tab is rejected (409), the newer version kept", theirs === 200 && stale.status === 409 && stale.body.detail.current.color === "#ff00ff");
    await page2.close();

    const [download] = await Promise.all([page.waitForEvent("download"), page.click('[data-testid="export-drawings"]')]);
    const file = join(outDir, "verify-drawings-backup.json");
    await download.saveAs(file);
    const backup = JSON.parse(readFileSync(file, "utf8"));
    check("export: database backup with drawings and templates", backup.format === "pas-drawings-v2" && backup.drawings.some((d) => d.drawing_id === fibR.drawing_id) && backup.templates.length >= 1);
    const hlNow = (await api(`/api/drawings?symbol=${target}`)).body.items.find((x) => x.drawing_id === hl.drawing_id);
    await api(`/api/drawings/${hl.drawing_id}?version=${hlNow.version}`, { method: "DELETE" });
    await page.setInputFiles('[data-testid="import-file"]', file);
    await page.waitForFunction(() => /Restore:/.test(document.querySelector('[data-testid="terminal-message"]')?.textContent ?? ""), null, { timeout: 10000 }).catch(() => null);
    const restored = (await api(`/api/drawings?symbol=${target}`)).body.items.some((x) => x.drawing_id === hl.drawing_id);
    check("import restores a deleted drawing from the backup", restored, (await page.textContent('[data-testid="terminal-message"]').catch(() => "")) ?? "");
    // delete with the keyboard: create an h-line (auto-selected), press Delete
    await page.click('[data-testid="tool-hline"]');
    await clickChart(0.4, 0.3);
    await saved();
    const withNew = (await api(`/api/drawings?symbol=${target}`)).body.count;
    await page.keyboard.press("Delete");
    await saved();
    await page.reload({ waitUntil: "networkidle" });
    await chartReady();
    check("Delete removes a drawing from the database (gone after refresh)", (await api(`/api/drawings?symbol=${target}`)).body.count === withNew - 1);
    // keep the two Fibonacci drawings for the restart check; remove the h-line
    const hl2 = (await api(`/api/drawings?symbol=${target}`)).body.items.find((x) => x.drawing_id === hl.drawing_id);
    if (hl2) await api(`/api/drawings/${hl2.drawing_id}?version=${hl2.version}`, { method: "DELETE" });
    if (tpl) await api(`/api/drawing-templates/${tpl.template_id}`, { method: "DELETE" });
    writeFileSync(idsFile, JSON.stringify({ symbol: target, ids: [fibR.drawing_id, fibE.drawing_id] }));
    info("restart check", `run again with --after-restart after restarting the backend (${idsFile})`);
  }
  check("no console or server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("script ran to completion", false, String(e).slice(0, 300));
  await page.screenshot({ path: join(outDir, "adv-error.png") }).catch(() => null);
} finally {
  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) process.exitCode = 1;
}
