// UI verification of the improvement task (line chart, incomplete higher-timeframe
// candles, VCP contraction structure, IPO base, 10-day about-to-break-out, confluence
// additions, the shared preview from every scanner, persistent drawings by user incl.
// text annotations, alignment across candle <-> line, zoom, refresh, timeframe).
//   node scripts/verify-improvements.mjs [screenshotDir] [baseUrl]
//   node scripts/verify-improvements.mjs [screenshotDir] [baseUrl] --after-restart
// Needs backend + frontend with completed 1W and 1D scans (the synthetic demo:
// backend/scripts/synthetic_demo.py has VCP4T, SETUP10, IPONEW, IPOBASE, MULTI10, MULTI2). It WRITES
// test drawings for VCP4T (local user) and one for user "verify-b"; the restart run removes them.
// Browser: Edge / Chrome channel, or PW_EXECUTABLE=/path/to/chromium.
import { chromium } from "playwright-core";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const afterRestart = process.argv.includes("--after-restart");
const outDir = args[0] || "ui-screenshots";
const base = args[1] || "http://127.0.0.1:5191";
const SYM = process.env.IMP_SYMBOL || "VCP4T";
const ASOF = process.env.IMP_ASOF || "2026-10-28"; // a mid-week date: the week / month are still running
mkdirSync(outDir, { recursive: true });
const idsFile = join(outDir, "verify-improvements-drawings.json");
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
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && !/Failed to load resource|409 \(Conflict\)/.test(m.text()) && errors.push(m.text()));
page.on("response", (r) => r.status() >= 500 && errors.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));
page.on("dialog", (d) => d.accept());
const api = (path, init) =>
  page.evaluate(async ([p, i]) => {
    const r = await fetch(p, i ? { ...i, headers: { "Content-Type": "application/json", ...(i.headers || {}) } } : undefined);
    return { status: r.status, body: r.status === 204 ? null : await r.json().catch(() => null) };
  }, [path, init ?? null]);
const dbg = () => page.evaluate(() => ({ ...window.__pasChart }));
const px = () => page.evaluate(() => window.__pasChart.anchorPixels());
const chartReady = () => page.waitForFunction(() => window.__pasChart && window.__pasChart.candles > 0, null, { timeout: 30000 });
const settle = (ms = 900) => page.waitForTimeout(ms);
const rows = (t) => page.locator(`[data-testid="${t}-row"]`);
const samePx = (a, b, tol = 0.75) =>
  Object.keys(a).length > 0 &&
  Object.keys(a).every((k) => b[k] && a[k].every((pt, i) => pt.every((v, j) => v !== null && b[k][i][j] !== null && Math.abs(v - b[k][i][j]) <= tol)));
async function previewFrom(testid, symbol) {
  const sel = symbol ? `[data-testid="${testid}-row"] [data-testid="symbol-link"][data-symbol="${symbol}"]` : `[data-testid="${testid}-row"] [data-testid="symbol-link"]`;
  const link = page.locator(sel).first();
  const sym = await link.getAttribute("data-symbol");
  const urlBefore = page.url();
  const before = (await dbg()).created;
  await link.click();
  await page.waitForSelector('[data-testid="chart-preview"]', { timeout: 15000 });
  await page.waitForFunction((b) => window.__pasChart.created > b && window.__pasChart.candles > 0, before, { timeout: 30000 });
  await settle(500);
  return { sym, sameUrl: page.url() === urlBefore };
}
async function closePreview() {
  await page.click('[data-testid="preview-close"]');
  await page.waitForSelector('[data-testid="chart-preview"]', { state: "detached", timeout: 5000 }).catch(() => null);
}

try {
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  if (afterRestart) {
    console.log("Drawings after an application restart");
    const saved = JSON.parse(readFileSync(idsFile, "utf8"));
    const list = (await api(`/api/drawings?symbol=${SYM}`)).body?.items ?? [];
    const ids = new Set(list.map((d) => d.drawing_id));
    check("all drawings survive the restart (database)", saved.ids.every((id) => ids.has(id)), `${saved.ids.length} expected, ${ids.size} found`);
    const note = list.find((d) => d.drawing_type === "text");
    check("text annotation kept with its text and creating timeframe", note && note.style.label === "Verify note" && note.created_timeframe === "1d");
    await page.goto(`${base}/explorer/${SYM}?tf=1d`, { waitUntil: "networkidle" });
    await chartReady();
    await page.click('[data-testid="chart-type-line"]');
    await page.waitForFunction(() => window.__pasChart.chartType === "line", null, { timeout: 10000 }).catch(() => null);
    await page.waitForFunction((n) => window.__pasChart.drawings >= n, saved.ids.length, { timeout: 15000 }).catch(() => null);
    const d = await dbg();
    check("line chart shows them after the restart", d.chartType === "line" && d.drawings >= saved.ids.length, `${d.drawings} drawings, ${d.chartType}`);
    await page.screenshot({ path: join(outDir, "imp-after-restart.png") });
    for (const it of list.filter((x) => saved.ids.includes(x.drawing_id))) await api(`/api/drawings/${it.drawing_id}?version=${it.version}`, { method: "DELETE" });
    const b = (await api(`/api/drawings?symbol=${SYM}`, { headers: { "X-PAS-User": "verify-b" } })).body?.items ?? [];
    for (const it of b) await api(`/api/drawings/${it.drawing_id}?version=${it.version}`, { method: "DELETE", headers: { "X-PAS-User": "verify-b" } });
    console.log("  [INFO] cleanup - verification drawings removed");
  } else {
    // remove this script's own drawings of an earlier run (never anything else)
    let prevIds = [];
    try {
      prevIds = JSON.parse(readFileSync(idsFile, "utf8")).ids ?? [];
    } catch {
      /* first run */
    }
    for (const user of [null, "verify-b"]) {
      const h = user ? { headers: { "X-PAS-User": user } } : {};
      const items = (await api(`/api/drawings?symbol=${SYM}`, user ? { method: "GET", ...h } : undefined)).body?.items ?? [];
      for (const it of items.filter((x) => x.drawing_id.startsWith("imp") || prevIds.includes(x.drawing_id) || (x.drawing_type === "text" && x.style?.label === "Verify note")))
        await api(`/api/drawings/${it.drawing_id}?version=${it.version}`, { method: "DELETE", ...h });
    }
    // ------------------------------------------------------------ A. line chart + drawing alignment
    console.log("Line chart and drawing alignment");
    await page.goto(`${base}/explorer/${SYM}?tf=1d`, { waitUntil: "networkidle" });
    await chartReady();
    let d = await dbg();
    check("candlestick mode by default", d.chartType === "candle" && d.linePoints === 0);
    const chart = (await api(`/api/chart?symbol=${SYM}&timeframe=1d`)).body;
    const lastC = chart.candles[chart.candles.length - 1];
    const t0 = chart.candles[chart.candles.length - 60].t;
    const t1 = chart.candles[chart.candles.length - 20].t;
    const mk = (id, type, anchors, extra = {}) => ({ drawing_id: id, instrument: "stock", symbol: SYM, drawing_type: type, anchors, color: "#F5C542", line_style: "solid", line_width: 2, style: {}, settings: null, visible: true, timeframes: null, created_timeframe: "1d", ...extra });
    const lo = Math.min(...chart.candles.slice(-60).map((c) => c.l));
    const hi = Math.max(...chart.candles.slice(-60).map((c) => c.h));
    const bodies = [
      mk("impHline01", "hline", [{ t: t0, p: hi }]),
      mk("impTrend01", "trend", [{ t: t0, p: lo }, { t: t1, p: hi }]),
      mk("impZone001", "rect", [{ t: t0, p: lo }, { t: t1, p: (lo + hi) / 2 }]),
      mk("impChan001", "channel", [{ t: t0, p: lo }, { t: t1, p: hi }, { t: t0, p: lo * 1.05 }]),
      mk("impFibR001", "fib_retracement", [{ t: t0, p: lo }, { t: t1, p: hi }], { settings: {} }),
      mk("impFibE001", "fib_extension", [{ t: t0, p: lo }, { t: t1, p: hi }, { t: lastC.t, p: (lo + hi) / 2 }], { settings: {} }),
    ];
    const created = [];
    for (const b of bodies) {
      const r = await api("/api/drawings", { method: "POST", body: JSON.stringify(b) });
      if (r.status === 201) created.push(b.drawing_id);
    }
    check("zone, trendline, channel, Fibonacci retracement / extension saved", created.length === bodies.length, `${created.length}/${bodies.length}`);
    await page.reload({ waitUntil: "networkidle" });
    await chartReady();
    await page.waitForFunction((n) => window.__pasChart.drawings >= n, bodies.length, { timeout: 15000 }).catch(() => null);
    // annotation through the UI tool
    const box = await page.locator('[data-testid="pattern-chart"]').first().boundingBox();
    await page.click('[data-testid="tool-text"]');
    await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.3);
    await page.waitForFunction(() => document.querySelector('[data-testid="draw-save-state"]')?.getAttribute("data-state") === "saved", null, { timeout: 15000 }).catch(() => null);
    await settle(400);
    let list = (await api(`/api/drawings?symbol=${SYM}`)).body.items;
    const note = list.find((x) => x.drawing_type === "text");
    if (note) {
      const { version: _v, created_at: _c, updated_at: _u, origin: _o, owner: _w, ...body } = note;
      const r = await api(`/api/drawings/${note.drawing_id}`, { method: "PUT", body: JSON.stringify({ ...body, style: { ...note.style, label: "Verify note" }, base_version: note.version }) });
      check("text annotation created with the tool, saved with its timeframe", r.status === 200 && note.created_timeframe === "1d", note.drawing_id);
      created.push(note.drawing_id);
    } else check("text annotation created with the tool, saved with its timeframe", false, "no text drawing");
    await page.reload({ waitUntil: "networkidle" });
    await chartReady();
    await page.waitForFunction((n) => window.__pasChart.drawings >= n, created.length, { timeout: 15000 }).catch(() => null);
    await settle(600);
    const pxCandle = await px();
    await page.click('[data-testid="chart-type-line"]');
    await page.waitForFunction(() => window.__pasChart.chartType === "line", null, { timeout: 10000 });
    await settle(500);
    d = await dbg();
    check("line chart of closes: same timestamps and prices as the candles", d.linePoints === d.candles && d.lineLast?.t === lastC.t && Math.abs(d.lineLast.v - lastC.c) < 1e-9 && d.lineFirst?.t === d.firstCandleTime, `${d.linePoints} points, last ${d.lineLast?.v} vs close ${lastC.c}`);
    const pxLine = await px();
    check("drawings stay aligned: candle -> line", samePx(pxCandle, pxLine), `${Object.keys(pxLine).length} drawings`);
    check("volume, overlays and drawings kept in line mode", d.volumePane && d.drawings >= created.length);
    await page.screenshot({ path: join(outDir, "imp-line-chart-drawings.png") });
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.wheel(0, -400);
    await settle(500);
    const pxZoom = await px();
    const listZoom = (await api(`/api/drawings?symbol=${SYM}`)).body.items;
    check("zoom moves pixels, never the anchors (time, price)", !samePx(pxLine, pxZoom, 0.1) && listZoom.every((x) => JSON.stringify(x.anchors) === JSON.stringify(list.find((y) => y.drawing_id === x.drawing_id)?.anchors ?? x.anchors)));
    await page.click('[data-testid="reset-zoom"]');
    await settle(500);
    await page.click('[data-testid="chart-type-candle"]');
    await page.waitForFunction(() => window.__pasChart.chartType === "candle", null, { timeout: 10000 });
    await settle(400);
    check("drawings stay aligned: line -> candle (after zoom + reset)", samePx(pxCandle, await px()));
    await page.click('[data-testid="chart-type-line"]');
    await page.reload({ waitUntil: "networkidle" });
    await chartReady();
    await page.waitForFunction((n) => window.__pasChart.drawings >= n, created.length, { timeout: 15000 }).catch(() => null);
    await settle(600);
    d = await dbg();
    check("refresh: line mode and drawings restored, same positions", d.chartType === "line" && d.drawings >= created.length && samePx(pxCandle, await px()), `${d.drawings} drawings`);
    await page.click('[data-testid="explorer-tf-1w"]');
    await page.waitForFunction(() => window.__pasChart.timeframe === "1w" && window.__pasChart.candles > 0, null, { timeout: 20000 });
    await page.waitForFunction((n) => window.__pasChart.drawings >= n, created.length, { timeout: 15000 }).catch(() => null);
    await settle(500);
    const pxW = await px();
    check("timeframe switch: the same drawings on 1W (mapped by time)", (await dbg()).drawings >= created.length && Object.values(pxW).every((pts) => pts.every(([x, y]) => x !== null && y !== null)));
    await page.screenshot({ path: join(outDir, "imp-weekly-line-drawings.png") });
    await page.click('[data-testid="chart-type-candle"]');
    // another user sees none of them; their own drawing is invisible to the local user
    const other = await api("/api/drawings", { method: "POST", body: JSON.stringify(mk("impUserB01", "hline", [{ t: t0, p: lo }])), headers: { "X-PAS-User": "verify-b" } });
    const localIds = (await api(`/api/drawings?symbol=${SYM}`)).body.items.map((x) => x.drawing_id);
    const bIds = (await api(`/api/drawings?symbol=${SYM}`, { headers: { "X-PAS-User": "verify-b" } })).body.items.map((x) => x.drawing_id);
    check("drawings are stored per user + symbol (+ creating timeframe)", other.status === 201 && !localIds.includes("impUserB01") && bIds.length === 1 && bIds[0] === "impUserB01");
    writeFileSync(idsFile, JSON.stringify({ symbol: SYM, ids: created }));

    // ------------------------------------------------------------ B. incomplete higher-timeframe candles
    console.log("Incomplete (running) higher-timeframe candles");
    for (const tf of ["1w", "1mo", "3mo", "6mo", "12mo"]) {
      await page.goto(`${base}/explorer/RWALK?tf=${tf}&asof=${ASOF}`, { waitUntil: "networkidle" });
      await chartReady();
      await page.waitForFunction(() => window.__pasChart.partialMarker === true, null, { timeout: 15000 }).catch(() => null);
      const c = (await api(`/api/chart?symbol=RWALK&timeframe=${tf}&asof=${ASOF}`)).body;
      d = await dbg();
      check(`${tf}: running candle drawn, grey, labelled INCOMPLETE`, d.partialCandle && d.partialMarker && d.partialT === c.partial_candle?.t && c.partial_candle?.is_complete === false && d.lastCandleTime < d.partialT, `${c.partial_candle?.sessions} sessions`);
      if (tf === "1w" || tf === "1mo") await page.screenshot({ path: join(outDir, `imp-partial-${tf}.png`) });
    }
    const daily = (await api(`/api/chart?symbol=RWALK&timeframe=1d&asof=${ASOF}`)).body;
    const wk = (await api(`/api/chart?symbol=RWALK&timeframe=1w&asof=${ASOF}`)).body;
    const mo = (await api(`/api/chart?symbol=RWALK&timeframe=1mo&asof=${ASOF}`)).body;
    const dl = daily.candles[daily.candles.length - 1];
    check("latest price / time consistent across 1D / 1W / 1M", wk.partial_candle.c === dl.c && mo.partial_candle.c === dl.c && Date.parse(wk.partial_candle.last_session) / 1000 === dl.t);
    await page.goto(`${base}/explorer/RWALK?tf=1w&asof=${ASOF}`, { waitUntil: "networkidle" });
    await chartReady();
    await page.click('[data-testid="chart-type-line"]');
    await page.waitForFunction(() => window.__pasChart.chartType === "line", null, { timeout: 10000 });
    d = await dbg();
    check("line mode includes the running period as the last (grey) point", d.linePoints === d.candles + 1 && d.lineLast.t === d.partialT);
    await page.click('[data-testid="chart-type-candle"]');

    // ------------------------------------------------------------ C. VCP contraction structure
    console.log("VCP contraction structure");
    await page.click('[data-testid="nav-vcp"]');
    await page.waitForSelector('[data-testid="vcp-table-row"]', { timeout: 15000 });
    const pv = await previewFrom("vcp-table", SYM);
    await page.waitForFunction(() => (window.__pasChart.overlayKinds.point ?? 0) >= 8, null, { timeout: 15000 }).catch(() => null);
    d = await dbg();
    const k = d.overlayKinds;
    check("VCP drawn as T1..T4 legs + swing points + pivot, not a box", (k.segment ?? 0) >= 8 && (k.point ?? 0) >= 8 && !(k.zone ?? 0), JSON.stringify(k));
    check("final contraction volume dry-up highlighted", d.volumeHighlights >= 1);
    const ctx = await page.textContent('[data-testid="preview-context"]');
    check("preview lists T1 > T2 > T3 > T4 and the pivot", /T1 \d+% > T2 \d+% > T3 \d+% > T4 \d+%/.test(ctx) && ctx.includes("pivot"), pv.sym);
    await page.screenshot({ path: join(outDir, "imp-vcp-structure.png") });
    await closePreview();
    await rows("vcp-table").filter({ hasText: SYM }).first().click();
    await page.waitForSelector('[data-testid="vcp-contractions"]', { timeout: 10000 });
    const finalRows = await page.locator('[data-testid="vcp-contractions"] tbody tr[data-final="1"]').count();
    check("VCP detail: per-contraction metrics with the final contraction marked", (await page.locator('[data-testid="vcp-contractions"] tbody tr').count()) === 4 && finalRows === 1);
    await page.screenshot({ path: join(outDir, "imp-vcp-detail.png") });

    // ------------------------------------------------------------ D. IPO base
    console.log("IPO Base Breakout");
    await page.click('[data-testid="nav-ipo"]');
    await page.waitForSelector('[data-testid="ipo-table-row"]', { timeout: 15000 });
    const st = await page.locator('[data-testid="ipo-table-row"] [data-ipo-status]').evaluateAll((els) => els.map((e) => e.getAttribute("data-ipo-status")));
    check("IPO base scanner lists the post-listing bases", st.includes("breakout") && st.includes("near_pivot"), st.join(","));
    await rows("ipo-table").filter({ hasText: "IPONEW" }).first().click();
    await page.waitForSelector('[data-testid="ipo-panel"]');
    check("IPO detail: listing, discovery, base, pivot, breakout volume, why", (await page.textContent('[data-testid="ipo-panel"]')).includes("Discovery period") && (await page.locator('[data-testid="ipo-explanation"] li').count()) > 3);
    await page.screenshot({ path: join(outDir, "imp-ipo-scanner.png") });
    const ip = await previewFrom("ipo-table", "IPONEW");
    await page.waitForFunction(() => (window.__pasChart.overlayKinds.vline ?? 0) >= 1, null, { timeout: 15000 }).catch(() => null);
    d = await dbg();
    check("IPO chart: listing date, discovery period, base, pivot, breakout + volume", (d.overlayKinds.vline ?? 0) >= 1 && (d.overlayKinds.zone ?? 0) >= 2 && (d.techMarkers.extra_marker ?? 0) >= 2 && d.volumeHighlights >= 1, `${ip.sym} ${JSON.stringify(d.overlayKinds)}`);
    await page.screenshot({ path: join(outDir, "imp-ipo-chart.png") });
    await closePreview();

    // ------------------------------------------------------------ E. 10-day about to break out
    console.log("10-day: about to break out");
    await page.click('[data-testid="nav-setups"]');
    await page.waitForSelector('[data-testid="setup-table-row"]', { timeout: 15000 });
    const states = await page.locator('[data-testid="setup-table-row"] [data-state]').evaluateAll((els) => els.map((e) => e.getAttribute("data-state")));
    check("stocks approaching the 10-day high before the breakout", states.length > 0 && states.every((s) => ["approaching", "very_near", "touched"].includes(s)) && states.includes("very_near"), states.join(","));
    const head = await page.textContent('[data-testid="setup-table"] thead');
    check("columns: 10D high, distance %, distance ATR, EMA20/50, volume ratio, RS, stage, setup quality", ["10D high", "Distance %", "Distance ATR", "EMA20 / 50", "Vol ratio", "RS", "Stage", "Setup quality"].every((c) => head.includes(c)));
    const sp = await previewFrom("setup-table", "SETUP10");
    await page.waitForFunction(() => (window.__pasChart.overlayKinds.segment ?? 0) >= 1, null, { timeout: 15000 }).catch(() => null);
    check("setup preview draws the 10-day level", ((await dbg()).overlayKinds.segment ?? 0) >= 1 && (await page.textContent('[data-testid="preview-context"]')).includes("Very Near"), sp.sym);
    await page.screenshot({ path: join(outDir, "imp-setup-preview.png") });
    await closePreview();

    // ------------------------------------------------------------ F. confluence
    console.log("Confluence additions");
    await page.click('[data-testid="nav-confluence"]');
    await page.waitForSelector('[data-testid="confluence-table-row"]', { timeout: 15000 });
    const okScreens = (await page.isVisible('[data-testid="cf-screen-about_to_breakout"]')) && (await page.isVisible('[data-testid="cf-screen-ipo_base"]'));
    check("screens: 10D about to break out, IPO base", okScreens);
    const cfHead = await page.textContent('[data-testid="confluence-table"] thead');
    check("IPO base column", cfHead.includes("IPO base"));
    await page.click('[data-testid="cf-screen-ipo_base"]');
    await settle();
    await rows("confluence-table").filter({ hasText: "IPONEW" }).first().click();
    await page.waitForSelector('[data-testid="confluence-breakdown"]');
    const srcs = await page.locator('[data-testid="breakdown-evidence"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-source")));
    check("IPO base evidence in the breakdown", srcs.includes("ipo_base"), [...new Set(srcs)].join(","));
    await page.screenshot({ path: join(outDir, "imp-confluence-ipo.png") });
    await page.click('[data-testid="cf-screen-all"]');
    await settle();

    // ------------------------------------------------------------ F2. multi-year breakouts
    console.log("Multi-year breakouts");
    await page.click('[data-testid="nav-multiyear"]');
    await page.waitForSelector('[data-testid="multiyear-table-row"]', { timeout: 15000 });
    const yrs = await rows("multiyear-table").filter({ hasText: "MULTI10" }).first().locator("[data-years]").getAttribute("data-years");
    check("multi-year: MULTI10 closes above a ~10-year resistance (default 5+ years)", Number(yrs) >= 9.5 && Number(yrs) <= 10.5, `${yrs} years`);
    const setYears = async (v) => {
      await page.fill('[data-testid="my-years"]', v);
      await page.press('[data-testid="my-years"]', "Enter");
      await page.waitForFunction((x) => location.search.includes(`years=${x}`), v, { timeout: 5000 }).catch(() => null);
      await settle();
    };
    await setYears("12");
    const at12 = await rows("multiyear-table").filter({ hasText: "MULTI10" }).count();
    await setYears("7.5");
    const at75 = await rows("multiyear-table").filter({ hasText: "MULTI10" }).count();
    check("multi-year: any number of years (12+ hides it, 7.5+ shows it)", at12 === 0 && at75 === 1, `12+: ${at12}, 7.5+: ${at75}`);
    const mp = await previewFrom("multiyear-table", "MULTI10");
    await page.waitForFunction(() => (window.__pasChart.overlayKinds.segment ?? 0) >= 1 && (window.__pasChart.techMarkers.extra_marker ?? 0) >= 1, null, { timeout: 15000 }).catch(() => null);
    const md = await dbg();
    check(
      "multi-year preview: weekly chart, resistance line from the base start + breakout marker",
      md.timeframe === "1w" && (md.overlayKinds.segment ?? 0) >= 1 && (md.techMarkers.extra_marker ?? 0) >= 1 && (await page.textContent('[data-testid="preview-context"]')).includes("years below it"),
      `${mp.sym} ${md.timeframe} ${JSON.stringify(md.overlayKinds)}`,
    );
    await page.screenshot({ path: join(outDir, "imp-multiyear-preview.png") });
    await closePreview();
    await page.click('[data-testid="my-tab-history"]');
    await page.waitForSelector('[data-testid="multiyear-summary"]', { timeout: 15000 });
    await page.waitForSelector('[data-testid="multiyear-history-table-row"]', { timeout: 15000 }).catch(() => null);
    const sumRows = await page.locator('[data-testid="multiyear-summary"] tbody tr').count();
    const histRows = await rows("multiyear-history-table").filter({ hasText: "MULTI2" }).count();
    const cav = (await page.textContent('[data-testid="multiyear-caveats"]')) ?? "";
    check("multi-year history: forward returns by horizon, past breakouts, caveats", sumRows === 6 && histRows >= 1 && cav.includes("Survivorship"), `${sumRows} horizons, MULTI2 rows ${histRows}`);
    await page.screenshot({ path: join(outDir, "imp-multiyear-history.png") });
    await page.goto(`${base}/explorer/MULTI10?tf=1mo`, { waitUntil: "networkidle" });
    await chartReady();
    await page.click('[data-testid="toggle-multiyear"]');
    await page.waitForFunction(() => (window.__pasChart.overlayKinds.segment ?? 0) >= 1 && (window.__pasChart.techMarkers.extra_marker ?? 0) >= 1, null, { timeout: 15000 }).catch(() => null);
    const td = await dbg();
    check("chart terminal: Multi-year chip on the monthly chart (marker on the running month)", (td.overlayKinds.segment ?? 0) >= 1 && (td.techMarkers.extra_marker ?? 0) >= 1, JSON.stringify(td.overlayKinds));
    await page.screenshot({ path: join(outDir, "imp-multiyear-terminal.png") });
    await page.click('[data-testid="toggle-multiyear"]'); // leave the saved preferences as they were
    await page.goto(`${base}/confluence`, { waitUntil: "networkidle" });
    await page.waitForSelector('[data-testid="confluence-table-row"]', { timeout: 15000 });
    await page.click('[data-testid="cf-screen-multiyear_breakout"]');
    await settle();
    const cfMy = await rows("confluence-table").filter({ hasText: "MULTI10" }).count();
    const cfYears = cfMy ? await rows("confluence-table").filter({ hasText: "MULTI10" }).first().locator("[data-multiyear]").count() : 0;
    check("confluence: 'Multi-year Breakout' screen lists it with its years", cfMy === 1 && cfYears === 1);
    await page.click('[data-testid="cf-screen-all"]');
    await settle();

    // ------------------------------------------------------------ G. the same preview from every scanner
    console.log("Every scanner opens the shared preview from the symbol");
    for (const [nav, testid] of [["nav-scanner", null], ["nav-vcp", "vcp-table"], ["nav-ipo", "ipo-table"], ["nav-fresh-breakouts", "fresh-table"], ["nav-setups", "setup-table"], ["nav-volume", null], ["nav-candlesticks", null], ["nav-confluence", "confluence-table"], ["nav-multiyear", "multiyear-table"]]) {
      await page.click(`[data-testid="${nav}"]`);
      await settle(1200);
      const link = page.locator('main [data-testid="symbol-link"]').first();
      if (!(await link.count())) {
        check(`${nav}: symbol click opens the preview`, false, "no rows");
        continue;
      }
      const url = page.url();
      await link.click();
      const opened = await page.waitForSelector('[data-testid="chart-preview"]', { timeout: 15000 }).then(() => true).catch(() => false);
      check(`${nav}: symbol click opens the preview (no navigation)`, opened && page.url() === url, testid ?? "");
      await closePreview();
    }
  }
  check("no console or server errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} catch (e) {
  check("script ran to completion", false, String(e).slice(0, 300));
  await page.screenshot({ path: join(outDir, "imp-error.png") }).catch(() => null);
} finally {
  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) process.exitCode = 1;
}
