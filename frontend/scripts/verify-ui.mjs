// UI verification against a running app (backend :8031 + frontend :5191).
// Drives the locally installed Microsoft Edge (or Chrome) through playwright-core.
//
//   node scripts/verify-ui.mjs [screenshotDir] [baseUrl]
//
// Checks: dashboard loading, pattern filters, candle rendering, volume pane,
// pattern overlays, createSeriesMarkers markers, timeframe switching, resize,
// multiple detection selection and chart disposal on unmount.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const outDir = process.argv[2] || "ui-screenshots";
const base = process.argv[3] || "http://127.0.0.1:5191";
mkdirSync(outDir, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` - ${detail}` : ""}`);
};
const dbg = (page) => page.evaluate(() => ({ ...window.__pasChart }));

async function launch() {
  for (const opt of [...(process.env.PW_EXECUTABLE ? [{ executablePath: process.env.PW_EXECUTABLE }] : []), { channel: "msedge" }, { channel: "chrome" }]) {
    try {
      return await chromium.launch({ ...opt, headless: true });
    } catch {
      /* try next */
    }
  }
  throw new Error("No Edge/Chrome installation found for playwright-core");
}

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
page.on("response", (r) => r.status() >= 400 && consoleErrors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}${new URL(r.url()).search}`));

try {
  console.log("Dashboard");
  await page.goto(`${base}/?u=all&tf=1d`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="result-row"]', { timeout: 30000 });
  const rows = await page.locator('[data-testid="result-row"]').count();
  check("dashboard loads results", rows > 0, `${rows} rows on page 1`);
  check("title and NSE indicator", (await page.textContent('[data-testid="app-title"]'))?.includes("Indian Price Action Screener") && (await page.isVisible('[data-testid="nse-indicator"]')));
  const total = await page.textContent('[data-testid="total-patterns"]');
  check("market overview totals", Number(total) > 0, `total=${total}`);
  check("disclaimer shown", await page.isVisible('[data-testid="disclaimer"]'));
  await page.screenshot({ path: join(outDir, "dashboard.png"), fullPage: false });

  // Pattern filter (server-side) - pick the first pattern present in the table.
  const firstPattern = await page.locator('[data-testid="result-row"]').first().getAttribute("data-pattern");
  await page.selectOption('[data-testid="filter-pattern"]', firstPattern);
  await page.waitForFunction((p) => {
    const r = [...document.querySelectorAll('[data-testid="result-row"]')];
    return r.length > 0 && r.every((x) => x.getAttribute("data-pattern") === p);
  }, firstPattern, { timeout: 15000 });
  const filtered = await page.locator('[data-testid="result-row"]').evaluateAll((r) => r.map((x) => x.getAttribute("data-pattern")));
  check("pattern filter applied", filtered.every((p) => p === firstPattern), `${filtered.length} rows of ${firstPattern}`);
  await page.fill('[data-testid="filter-search"]', "zzzz-no-such-symbol");
  await page.waitForTimeout(300);
  check("search filter applied", (await page.locator('[data-testid="result-row"]').count()) === 0);
  await page.fill('[data-testid="filter-search"]', "");
  await page.selectOption('[data-testid="filter-pattern"]', "");
  await page.waitForTimeout(800);

  console.log("Pattern Explorer");
  const row = page.locator('[data-testid="result-row"]').first();
  const symbol = await row.getAttribute("data-symbol");
  const pattern = await row.getAttribute("data-pattern");
  await row.click();
  await page.waitForSelector('[data-testid="chart-preview"]');
  await page.click('[data-testid="preview-open-page"]'); // the row opens the preview; "Open page" -> the chart page
  await page.waitForURL(/\/explorer\//);
  await page.waitForFunction(() => window.__pasChart && window.__pasChart.candles > 0 && window.__pasChart.lineSeries > 0, null, { timeout: 30000 });
  await page.waitForTimeout(500);
  const url = new URL(page.url());
  check("row click opens the correct chart", url.pathname.endsWith(`/${symbol}`) && (await page.textContent('[data-testid="explorer-symbol"]')) === symbol, `${symbol} ${url.search}`);
  let d = await dbg(page);
  check("candles rendered", d.candles > 100, `${d.candles} candles, tf=${d.timeframe}`);
  check("volume pane rendered", d.volumePane && d.volumeBars > 0, `${d.volumeBars} volume bars`);
  check("pattern overlay rendered", d.lineSeries >= 2 && d.overlayShapes >= 1, `${d.lineSeries} line series, ${d.overlayShapes} primitive shapes`);
  // Line patterns carry touch markers, reversals peak/trough pivots, flags pole markers.
  const patternMarkers = ["touch_upper", "touch_lower", "peak", "trough", "pole_start", "pole_end"].reduce((n, r) => n + (d.markerRoles[r] ?? 0), 0);
  check("markers via createSeriesMarkers", d.markers > 0 && patternMarkers > 0, JSON.stringify(d.markerRoles));
  check("canvas present", (await page.locator('[data-testid="pattern-chart"] canvas').count()) > 0);

  // Geometry alignment: every non-projected geometry point sits on a returned candle timestamp.
  const align = await page.evaluate(async ({ symbol, tf, pid }) => {
    const r = await fetch(`/api/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}&pattern_id=${pid}`).then((x) => x.json());
    const times = new Map(r.candles.map((c) => [c.i, c.t]));
    let bad = 0, n = 0;
    for (const det of r.detections)
      for (const el of det.geometry.elements) {
        const pts = el.points ?? [...(el.upper ?? []), ...(el.lower ?? [])];
        for (const p of pts.length ? pts : [el]) {
          if (p.projected) continue;
          n += 1;
          if (times.get(p.i) !== p.t) bad += 1;
        }
      }
    return { n, bad };
  }, { symbol, tf: url.searchParams.get("tf"), pid: url.searchParams.get("pattern_id") });
  check("geometry aligns with candle timestamps", align.bad === 0 && align.n > 0, `${align.n} points checked`);
  await page.screenshot({ path: join(outDir, `explorer-${symbol}-${pattern}.png`) });

  // Toggles
  await page.click('[data-testid="toggle-volume"]');
  await page.waitForTimeout(300);
  d = await dbg(page);
  check("volume toggle hides the pane", !d.volumePane);
  await page.click('[data-testid="toggle-volume"]');
  await page.click('[data-testid="toggle-overlay"]');
  await page.waitForTimeout(300);
  d = await dbg(page);
  check("overlay toggle removes geometry", d.lineSeries === 0 && d.overlayShapes === 0 && d.volumePane);
  await page.click('[data-testid="toggle-overlay"]');
  await page.click('[data-testid="toggle-swings"]');
  await page.waitForTimeout(300);
  d = await dbg(page);
  check("swing toggle adds swing markers", (d.markerRoles.swing_high ?? 0) + (d.markerRoles.swing_low ?? 0) > 0, JSON.stringify(d.markerRoles));
  await page.click('[data-testid="toggle-swings"]');
  const shapesBefore = (await dbg(page)).overlayShapes;
  await page.click('[data-testid="toggle-sr"]');
  await page.waitForTimeout(300);
  d = await dbg(page);
  const srRows = await page.locator('[data-testid="sr-table"] tbody tr').count();
  check("S/R zones toggle draws zones", (d.markerRoles.sr_zone ?? 0) > 0 && d.overlayShapes > shapesBefore && srRows === d.markerRoles.sr_zone, `${d.markerRoles.sr_zone ?? 0} zones drawn, ${srRows} table rows`);
  await page.click('[data-testid="toggle-sr"]');
  await page.click('[data-testid="reset-zoom"]');
  await page.click('[data-testid="go-latest"]');

  // Multiple detection selection (on this or another symbol with >= 2 detections).
  let items = await page.locator('[data-testid="detection-item"]').count();
  if (items < 2) {
    const multi = await page.evaluate(async () => {
      const r = await fetch("/api/scan?universe=all&timeframe=1d").then((x) => x.json());
      const counts = {};
      for (const m of r.matches) counts[m.symbol] = (counts[m.symbol] ?? 0) + 1;
      return Object.entries(counts).find(([, c]) => c >= 2)?.[0] ?? null;
    });
    if (multi) {
      await page.goto(`${base}/explorer/${multi}?tf=1d`, { waitUntil: "networkidle" });
      await page.waitForFunction(() => window.__pasChart && window.__pasChart.lineSeries > 0, null, { timeout: 30000 });
      items = await page.locator('[data-testid="detection-item"]').count();
    }
  }
  if (items >= 2) {
    const before = (await dbg(page)).detectionId;
    const second = page.locator('[data-testid="detection-item"]').nth(1);
    const target = await second.getAttribute("data-pattern-id");
    await second.click();
    await page.waitForFunction((t) => window.__pasChart.detectionId === t, target, { timeout: 10000 });
    check("multiple detection selection", (await dbg(page)).detectionId === target && target !== before, `${items} detections`);
  } else {
    check("multiple detection selection", false, "no symbol with >= 2 detections found in the 1D scan");
  }

  // P2 geometry: a flag/pennant draws its pole; a reversal draws neckline and pivots.
  for (const [family, need] of [
    [["bullish_flag", "bearish_flag", "bullish_pennant", "bearish_pennant"], ["pole_start", "pole_end"]],
    [["double_top", "double_bottom", "head_shoulders", "inverse_head_shoulders"], ["peak", "trough"]],
  ]) {
    const m = await page.evaluate(async (pats) => {
      const r = await fetch(`/api/scan?universe=all&timeframe=1d&pattern=${pats.join(",")}`).then((x) => x.json());
      return r.matches[0] ?? null;
    }, family);
    if (!m) {
      check(`P2 geometry (${family[0]}...)`, false, "no detection of this family in the 1D scan");
      continue;
    }
    await page.goto(`${base}/explorer/${encodeURIComponent(m.symbol)}?tf=1d&pattern_id=${m.pattern_id}`, { waitUntil: "networkidle" });
    await page.waitForFunction((id) => window.__pasChart && window.__pasChart.detectionId === id && window.__pasChart.lineSeries > 0, m.pattern_id, { timeout: 30000 });
    d = await dbg(page);
    check(`P2 geometry ${m.pattern}`, need.every((r) => (d.markerRoles[r] ?? 0) > 0), `${m.symbol}: ${JSON.stringify(d.markerRoles)}, ${d.lineSeries} lines`);
    await page.screenshot({ path: join(outDir, `explorer-${m.pattern}-${m.symbol}.png`) });
  }

  // 1.3.0 viewport fix: a converging formation whose apex projects ahead stays in view
  // after EMAs / sub panes / overlays are added (they used to shift the range left).
  const conv = await page.evaluate(async () => {
    const r = await fetch("/api/scan?universe=all&timeframe=1d&pattern=falling_wedge,rising_wedge,ascending_triangle,descending_triangle,symmetrical_triangle").then((x) => x.json());
    return r.matches[0] ?? null;
  });
  if (conv) {
    await page.goto(`${base}/explorer/${encodeURIComponent(conv.symbol)}?tf=1d&pattern_id=${conv.pattern_id}`, { waitUntil: "networkidle" });
    await page.waitForFunction((id) => window.__pasChart && window.__pasChart.detectionId === id && window.__pasChart.lineSeries > 0, conv.pattern_id, { timeout: 30000 });
    await page.waitForTimeout(2000);
    d = await dbg(page);
    const geo = await page.evaluate(async ([sym, pid]) => {
      const r = await fetch(`/api/chart?symbol=${encodeURIComponent(sym)}&timeframe=1d&pattern_id=${pid}`).then((x) => x.json());
      const g = r.detections.find((x) => x.pattern_id === pid).geometry;
      return { s: g.formation.start.t, e: g.formation.end.t };
    }, [conv.symbol, conv.pattern_id]);
    check("converging formation stays in view after series load", d.visibleFrom !== null && d.visibleFrom <= geo.s && d.visibleTo >= geo.e, `${conv.pattern} ${conv.symbol}: view ${d.visibleFrom}..${d.visibleTo}, formation ${geo.s}..${geo.e}`);
  } else {
    check("converging formation stays in view after series load", false, "no triangle / wedge in the 1D scan");
  }

  // Full history: Fit reaches the first cached candle (minBarSpacing allows ~8k bars),
  // and later series updates do not snap the view back; Log toggles the price axis.
  await page.click('[data-testid="fit-content"]');
  await page.waitForTimeout(1500);
  d = await dbg(page);
  const day = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 10) : String(t));
  check("Fit shows the whole history", d.visibleFrom !== null && d.visibleFrom <= d.firstCandleTime + 7 * 86400, `${d.candles} candles, view from ${day(d.visibleFrom)}, first candle ${day(d.firstCandleTime)}`);
  await page.click('[data-testid="toggle-log"]');
  await page.waitForTimeout(300);
  const logOn = (await dbg(page)).logScale;
  await page.click('[data-testid="toggle-log"]');
  await page.waitForTimeout(300);
  check("Log price scale toggles", logOn === true && (await dbg(page)).logScale === false);

  // Timeframe switching: new candles + new detections, never 1D coordinates on 1H.
  const created = (await dbg(page)).created;
  await page.click('[data-testid="explorer-tf-1h"]');
  await page.waitForFunction(() => window.__pasChart.timeframe === "1h" && window.__pasChart.candles > 0, null, { timeout: 30000 });
  await page.waitForTimeout(800);
  d = await dbg(page);
  const span = (d.lastCandleTime - d.firstCandleTime) / Math.max(1, d.candles - 1);
  check("timeframe switch reloads candles", new URL(page.url()).searchParams.get("tf") === "1h" && span < 86400, `avg bar spacing ${(span / 3600).toFixed(2)} h over ${d.candles} candles`);
  check("chart instance reused across timeframe switch", d.created === created, `created=${d.created}`);
  await page.screenshot({ path: join(outDir, "explorer-1h.png") });

  // Resize: autoSize follows the container.
  const w1 = (await dbg(page)).width;
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.waitForTimeout(600);
  const w2 = (await dbg(page)).width;
  check("chart resizes with its container", w1 > 0 && w2 > 0 && w2 < w1, `${w1}px -> ${w2}px`);

  // Unmount: leaving the explorer disposes every chart instance.
  await page.click('[data-testid="back"]');
  await page.waitForSelector('[data-testid="dashboard"]');
  await page.waitForTimeout(300);
  d = await dbg(page);
  check("chart disposed on unmount", d.created > 0 && d.created === d.disposed, `created=${d.created} disposed=${d.disposed}`);
  check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} catch (e) {
  check("run completed", false, String(e));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\nUI verification: ${results.length - failed.length} passed, ${failed.length} failed. Screenshots: ${outDir}`);
process.exit(failed.length ? 1 : 0);
