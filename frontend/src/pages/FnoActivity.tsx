import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fnoApi, type FnoActivity as Activity, type FnoRow, type FnoStatus } from "../api/fno";
import { ErrorNote, Panel } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { fmtAgo, fmtNum, fmtPct, fmtVolume } from "../lib/format";
import { SymbolButton, useOpenChart, type PreviewContext } from "../lib/openChart";

const SIGNAL_CLS: Record<string, string> = {
  "Strong Long Buildup": "font-semibold text-validated",
  "Long Buildup": "text-validated",
  "Strong Short Buildup": "font-semibold text-down",
  "Short Buildup": "text-down",
  "Short Covering": "text-validated/80",
  "Long Unwinding": "text-forming",
  Neutral: "text-muted",
};

const sign = (v: number | null | undefined, d = 2) => (v === null || v === undefined || !Number.isFinite(v) ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}`);

function oiChange(r: FnoRow): string {
  return r.oi_change_infinite ? "∞ (prev OI 0)" : fmtPct(r.oi_change_pct, 2);
}

/** Every context metric as label / value lines (the same block the original Telegram report printed). */
function contextLines(r: FnoRow): { k: string; v: string }[] {
  const c = r.context;
  const out: { k: string; v: string }[] = [
    { k: "Signal", v: `${r.signal} (${r.instrument === "FUTIDX" ? "index" : "stock"} futures, expiry ${r.expiry})` },
    { k: "Futures close", v: `${fmtNum(r.close_prev)} -> ${fmtNum(r.close_today)} (${fmtPct(r.price_change_pct, 2)})` },
    { k: "Front-month OI", v: `${fmtVolume(r.oi_prev)} -> ${fmtVolume(r.oi_today)} (${oiChange(r)})` },
  ];
  if (c.spot !== undefined) out.push({ k: "Spot / basis", v: `${fmtNum(c.spot)} / ${sign(c.basis)} (${sign(c.basis_pct)}%)` });
  if (c.basis_avg !== undefined) out.push({ k: "Basis 5-session avg", v: `${sign(c.basis_avg)}${c.basis_trend ? ` [${c.basis_trend}]` : ""}` });
  if (c.volume_ratio !== undefined || c.delivery_pct !== undefined) out.push({ k: "Volume ratio / delivery", v: `${c.volume_ratio !== undefined ? `${fmtNum(c.volume_ratio)}x` : "-"} / ${c.delivery_pct !== undefined ? `${fmtNum(c.delivery_pct, 1)}%` : "-"}` });
  if (c.delivery_surge_pct !== undefined) out.push({ k: "Delivery surge vs 20", v: fmtPct(c.delivery_surge_pct, 1) });
  if (c.volume_rank_pct !== undefined) out.push({ k: "Volume rank (1 year)", v: `${fmtNum(c.volume_rank_pct, 0)}th percentile` });
  if (c.high_10d !== undefined) out.push({ k: "10-session range", v: `${fmtNum(c.low_10d)} - ${fmtNum(c.high_10d)} (${fmtPct(c.dist_high_10d_pct, 1)} H / ${fmtPct(c.dist_low_10d_pct, 1)} L)` });
  if (c.high_20d !== undefined) out.push({ k: "20-session range", v: `${fmtNum(c.low_20d)} - ${fmtNum(c.high_20d)} (${fmtPct(c.dist_high_20d_pct, 1)} H / ${fmtPct(c.dist_low_20d_pct, 1)} L)` });
  if (c.pcr !== undefined) out.push({ k: `PCR${c.option_expiry ? ` (${c.option_expiry})` : ""}`, v: fmtNum(c.pcr) });
  if (c.max_call_strike !== undefined) out.push({ k: "Call wall", v: `${fmtNum(c.max_call_strike, 0)} CE (${fmtVolume(c.max_call_oi)})` });
  if (c.max_put_strike !== undefined) out.push({ k: "Put wall", v: `${fmtNum(c.max_put_strike, 0)} PE (${fmtVolume(c.max_put_oi)})` });
  if (c.oi_all_expiries !== undefined) out.push({ k: "OI all expiries", v: `${fmtVolume(c.oi_all_expiries)}${c.oi_change_5d_pct !== undefined ? `, 5 sessions ${fmtPct(c.oi_change_5d_pct, 1)}` : ""}${c.oi_change_20d_pct !== undefined ? `, 20 sessions ${fmtPct(c.oi_change_20d_pct, 1)}` : ""}` });
  if (c.oi_build_streak !== undefined) out.push({ k: "OI build-up streak", v: `${c.oi_build_streak} session${c.oi_build_streak === 1 ? "" : "s"}` });
  if (c.atm_iv !== undefined) out.push({ k: `ATM IV (approx.${c.iv_expiry ? `, ${c.iv_expiry}` : ""})`, v: `${fmtNum(c.atm_iv, 1)}%${c.iv_rank !== undefined ? `, rank ${fmtNum(c.iv_rank, 0)}` : ""}${c.iv_percentile !== undefined ? `, pct ${fmtNum(c.iv_percentile, 0)}` : ""}` });
  for (const f of r.flags) out.push({ k: "Flag", v: f.text });
  return out;
}

function previewContext(r: FnoRow, date: string): PreviewContext {
  const c = r.context;
  const long = r.side === "long";
  const levels: PreviewContext["levels"] = [];
  if (c.max_call_strike !== undefined) levels.push({ price: c.max_call_strike, label: `Call wall ${fmtNum(c.max_call_strike, 0)}`, color: "#F2643C" });
  if (c.max_put_strike !== undefined) levels.push({ price: c.max_put_strike, label: `Put wall ${fmtNum(c.max_put_strike, 0)}`, color: "#95EB45" });
  return {
    title: `${r.signal} on ${date}`,
    lines: contextLines(r),
    levels,
    markers: [{ t: r.t, label: r.signal.replace(" Buildup", ""), color: long ? "#95EB45" : r.side === "short" ? "#F2643C" : "#E3A33B", position: long ? "below" : "above" }],
  };
}

function Flags({ r }: { r: FnoRow }) {
  if (!r.flags.length) return null;
  return (
    <span className="cursor-help rounded border border-forming/60 px-1 text-[10px] font-semibold text-forming" title={r.flags.map((f) => f.text).join("\n\n")} data-testid="fno-flag">
      {r.flags.some((f) => f.code === "lot_size_changed") ? "CORP. ACTION?" : "NOTE"}
    </span>
  );
}

export function FnoActivity() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const date = params.get("d") ?? "";
  const [act, setAct] = useState<Activity | null>(null);
  const [status, setStatus] = useState<FnoStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wlInput, setWlInput] = useState("");
  const [wlError, setWlError] = useState<string | null>(null);

  const load = useCallback(() => {
    fnoApi
      .activity(date || null)
      .then((a) => (setAct(a), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [date]);
  const loadStatus = useCallback(() => {
    fnoApi
      .status()
      .then(setStatus)
      .catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  useEffect(loadStatus, [loadStatus]);

  const running = status?.job.state === "running";
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      fnoApi.status().then((st) => {
        setStatus(st);
        if (st.job.state !== "running") load();
      });
    }, 3000);
    return () => window.clearInterval(id);
  }, [running, load]);

  const tradeDate = act?.trade_date ?? "";
  const open = useCallback(
    (r: FnoRow) => {
      if (!r.chart) return;
      openChart({ symbol: r.chart.symbol, timeframe: "1d", instrument: r.chart.instrument, context: previewContext(r, tradeDate) });
    },
    [openChart, tradeDate],
  );

  const update = (body: { sessions?: number; recompute?: boolean } = {}) =>
    fnoApi
      .refresh(body)
      .then(() => loadStatus())
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

  const saveWatchlist = (symbols: string[]) =>
    fnoApi
      .setWatchlist(symbols)
      .then(() => (setWlError(null), setWlInput(""), load()))
      .catch((e) => setWlError(e instanceof Error ? e.message : String(e)));

  const addSymbols = () => {
    const add = wlInput
      .split(/[\s,;]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (add.length) saveWatchlist([...(act?.watchlist_symbols ?? []), ...add]);
  };

  const columns = useCallback(
    (kind: "signal" | "watchlist"): Column<FnoRow>[] => [
      ...(kind === "signal" ? [{ key: "rank", label: "#", align: "right" as const, render: (r: FnoRow) => <span className="text-muted">{r.rank}</span>, sort: (r: FnoRow) => r.rank, title: "Order of the original report: Strong first, then by OI change (highest first)" }] : []),
      { key: "symbol", label: "Symbol", render: (r) => (r.chart ? <SymbolButton symbol={r.symbol} onOpen={() => open(r)} /> : <span className="font-semibold">{r.symbol}</span>), sort: (r) => r.symbol },
      { key: "company", label: "Company", render: (r) => <span className="block max-w-[170px] truncate text-muted" title={r.company ?? ""}>{r.company ?? (r.instrument === "FUTIDX" ? "Index futures" : "")}</span>, sort: (r) => r.company ?? "" },
      {
        key: "signal",
        label: kind === "signal" ? "Signal" : "Status",
        render: (r) => (
          <span className="flex items-center gap-1.5 whitespace-nowrap">
            <span className={SIGNAL_CLS[r.signal] ?? ""}>{r.signal}</span>
            <Flags r={r} />
          </span>
        ),
        sort: (r) => r.signal,
      },
      { key: "price", label: "Fut. price", align: "right", render: (r) => <span className={(r.price_change_pct ?? 0) >= 0 ? "text-validated" : "text-down"}>{fmtPct(r.price_change_pct, 2)}</span>, sort: (r) => r.price_change_pct, title: "Front-month futures close vs the previous session (selection input)" },
      { key: "oi", label: "Fut. OI", align: "right", render: (r) => oiChange(r), sort: (r) => (r.oi_change_infinite ? Number.MAX_VALUE : r.oi_change_pct), title: "Front-month futures open interest vs the previous session (selection input)" },
      { key: "close", label: "Fut. close", align: "right", render: (r) => fmtNum(r.close_today), sort: (r) => r.close_today },
      { key: "spot", label: "Basis %", align: "right", render: (r) => (r.context.basis_pct !== undefined ? `${sign(r.context.basis_pct)}%` : "-"), sort: (r) => r.context.basis_pct ?? null, title: "(futures - spot) / spot; trend vs the same contract's 5-session average in the preview" },
      { key: "vol", label: "Vol x", align: "right", render: (r) => (r.context.volume_ratio !== undefined ? `${fmtNum(r.context.volume_ratio)}x` : "-"), sort: (r) => r.context.volume_ratio ?? null, title: "Cash-market traded quantity / mean of the previous 20 sessions" },
      { key: "deliv", label: "Deliv %", align: "right", render: (r) => fmtNum(r.context.delivery_pct, 1), sort: (r) => r.context.delivery_pct ?? null, title: "Delivery % of the cash market (NSE full bhavcopy); surge vs 20 sessions in the preview" },
      { key: "h20", label: "vs 20D high", align: "right", render: (r) => fmtPct(r.context.dist_high_20d_pct, 1), sort: (r) => r.context.dist_high_20d_pct ?? null, title: "Spot close vs the highest high of the previous 20 sessions" },
      { key: "pcr", label: "PCR", align: "right", render: (r) => fmtNum(r.context.pcr), sort: (r) => r.context.pcr ?? null, title: "Put OI / call OI of the nearest option expiry" },
      { key: "walls", label: "Put / call wall", align: "right", render: (r) => (r.context.max_put_strike !== undefined || r.context.max_call_strike !== undefined ? `${fmtNum(r.context.max_put_strike, 0)} / ${fmtNum(r.context.max_call_strike, 0)}` : "-"), title: "Strikes with the highest put / call OI (nearest expiry)" },
      { key: "oi5", label: "OI 5S", align: "right", render: (r) => fmtPct(r.context.oi_change_5d_pct, 1), sort: (r) => r.context.oi_change_5d_pct ?? null, title: "Combined OI of all expiries vs 5 sessions ago (rollover-safe)" },
      { key: "streak", label: "OI streak", align: "right", render: (r) => (r.context.oi_build_streak ?? "-").toString(), sort: (r) => r.context.oi_build_streak ?? null, title: "Consecutive sessions of rising combined OI" },
      { key: "iv", label: "ATM IV", align: "right", render: (r) => (r.context.atm_iv !== undefined ? `${fmtNum(r.context.atm_iv, 1)}%${r.context.iv_rank !== undefined ? ` (R${fmtNum(r.context.iv_rank, 0)})` : ""}` : "-"), sort: (r) => r.context.atm_iv ?? null, title: "Approximate ATM implied volatility (Brenner-Subrahmanyam from settlement prices); R = IV rank over 1 year" },    ],
    [open],
  );

  const signalCols = useMemo(() => columns("signal"), [columns]);
  const watchCols = useMemo(() => columns("watchlist"), [columns]);
  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of act?.signals ?? []) m[r.signal] = (m[r.signal] ?? 0) + 1;
    return m;
  }, [act]);

  const st = status?.sessions;
  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="fno-activity">
      <section className="panel flex flex-wrap items-end gap-x-4 gap-y-2 px-3 py-2.5">
        <div className="min-w-[260px] flex-1">
          <h1 className="text-sm font-semibold text-ink">F&amp;O OI Buildup</h1>
          <p className="text-[11px] text-muted">
            Your futures price + open-interest buildup rules (ported unchanged from the Bhav copy screener) on official NSE end-of-day bhavcopy data. Screening output, not trade recommendations; the rules are not validated.
          </p>
        </div>
        <label className={`flex flex-col gap-0.5 text-[11px] text-muted ${act?.dates.length ? "" : "hidden"}`}>
          Session
          <select className="ctl" value={date || tradeDate} onChange={(e) => setParams(e.target.value && e.target.value !== act?.dates[0]?.trade_date ? { d: e.target.value } : {}, { replace: true })} data-testid="fno-date">
            {(act?.dates ?? []).map((d) => (
              <option key={d.trade_date} value={d.trade_date}>
                {d.trade_date} ({d.signals} signal{d.signals === 1 ? "" : "s"})
              </option>
            ))}
          </select>
        </label>
        <div className="text-[11px] text-muted" data-testid="fno-data-status">
          {st ? (
            <>
              NSE sessions stored: <span className="text-ink">{st.stored}</span>
              {st.first ? ` (${st.first} to ${st.last})` : ""}
              <br />
              {running ? (
                <span className="text-accent">
                  Updating: {status?.job.phase} {status?.job.progress ?? ""}
                </span>
              ) : status?.job.state === "error" ? (
                <span className="text-down">Last update failed: {status.job.error}</span>
              ) : status?.job.finished_at ? (
                `Last update ${fmtAgo(status.job.finished_at)}`
              ) : (
                "Updates every weekday evening after NSE publishes the files"
              )}
            </>
          ) : (
            "-"
          )}
        </div>
        <button type="button" className="btn-primary" disabled={running} onClick={() => update()} data-testid="fno-refresh" title="Download missing NSE sessions and compute their signals">
          {running ? "Updating..." : "Update now"}
        </button>
      </section>

      {error && <ErrorNote message={error} />}

      {act && act.trade_date === null && (
        <Panel title="No F&O data yet">
          <p className="text-xs text-muted">
            The first update downloads about one year of NSE F&amp;O, equity and index bhavcopies ({status?.backfill_sessions ?? 260} sessions) in the background. It starts with the app and takes about 20 minutes; progress is shown above.
          </p>
        </Panel>
      )}

      {act?.trade_date && (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted" data-testid="fno-summary">
            <span>
              Comparing <span className="text-ink">{act.trade_date}</span> with <span className="text-ink">{act.prev_date}</span>
            </span>
            <span>
              Front month <span className="text-ink">{act.front_expiry?.latest ?? "-"}</span>
            </span>
            {Object.entries(counts).map(([k, v]) => (
              <span key={k} className={SIGNAL_CLS[k]}>
                {k}: {v}
              </span>
            ))}
          </div>
          {act.notices.map((n) => (
            <div key={n.code} className={`rounded-md border px-3 py-1.5 text-xs ${n.level === "warn" ? "border-forming/50 bg-forming/10 text-forming" : "border-line text-muted"}`} data-testid={`fno-notice-${n.code}`}>
              {n.text}
            </div>
          ))}

          <Panel title={`Strategy signals (${act.signals.length})`}>
            <DataTable rows={act.signals} columns={signalCols} testid="fno-signals" onRow={open} empty="No futures met the buildup rules on this session." pageSize={100} />
          </Panel>

          <Panel title={`Existing positions (${act.watchlist_symbols.length})`}>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              {act.watchlist_symbols.map((s) => (
                <span key={s} className="flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-xs" data-testid="fno-watch-chip">
                  {s}
                  <button type="button" className="text-muted hover:text-down" title={`Remove ${s}`} onClick={() => saveWatchlist(act.watchlist_symbols.filter((x) => x !== s))}>
                    ×
                  </button>
                </span>
              ))}
              <input
                className="ctl w-56"
                placeholder="Add F&O symbols, e.g. TCS, NIFTY"
                value={wlInput}
                onChange={(e) => setWlInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addSymbols()}
                data-testid="fno-watch-input"
              />
              <button type="button" className="btn" onClick={addSymbols} data-testid="fno-watch-add">
                Add
              </button>
            </div>
            {wlError && <ErrorNote message={wlError} />}
            {!!act.watchlist_missing?.length && <div className="mb-2 text-[11px] text-muted">No front-month futures match on both sessions for: {act.watchlist_missing.join(", ")}</div>}
            {act.watchlist_symbols.length > 0 && <DataTable rows={act.watchlist} columns={watchCols} testid="fno-watchlist" onRow={open} empty="No watchlist futures on this session." />}
          </Panel>
        </>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title={`Rules (FnoConfig ${act?.config.version ?? ""})`}>
          <table className="w-full text-xs" data-testid="fno-rules">
            <thead className="text-muted">
              <tr>
                <th className="py-1 text-left font-normal">Signal</th>
                <th className="py-1 text-right font-normal">Front-month OI change</th>
                <th className="py-1 text-right font-normal">Stock futures price</th>
                <th className="py-1 text-right font-normal">Index futures price</th>
              </tr>
            </thead>
            <tbody>
              {(act?.rules ?? []).map((r) => (
                <tr key={r.signal} className="border-t border-line">
                  <td className={`py-1 ${SIGNAL_CLS[r.signal]}`}>{r.signal}</td>
                  <td className="num py-1 text-right">{r.oi}</td>
                  <td className="num py-1 text-right">{r.stock}</td>
                  <td className="num py-1 text-right">{r.index}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted">
            <li>Both sessions are cut to their own nearest futures expiry, then the same contract is compared (original rule). On the first session after an expiry the front months differ, so that session has no signals.</li>
            <li>Prices and OI are NSE&apos;s raw values: a bonus or split shows up as a large price fall with an OI jump (flagged as CORP. ACTION? when the lot size changed).</li>
            <li>Existing positions use the same comparison and show the quadrant: Long Buildup, Short Buildup, Long Unwinding, Short Covering.</li>
            <li>The context columns are informational only and never change which futures are selected.</li>
          </ul>
        </Panel>
        <Panel title="Data sources (NSE archives, free, end of day)">
          <ul className="space-y-1 text-[11px] text-muted" data-testid="fno-sources">
            {(status?.sources ?? []).map((s) => (
              <li key={s.name}>
                <span className="text-ink">{s.name}</span> ({s.from}): {s.use}
              </li>
            ))}
          </ul>
          {!!st?.missing.length && <div className="mt-2 text-[11px] text-muted">No NSE F&amp;O file (holiday or not published): {st.missing.join(", ")}</div>}
          {!!st?.partial.length && <div className="mt-1 text-[11px] text-muted">Sessions with a missing equity / index file: {st.partial.map((p) => p.trade_date).join(", ")}</div>}
        </Panel>
      </div>
    </div>
  );
}
