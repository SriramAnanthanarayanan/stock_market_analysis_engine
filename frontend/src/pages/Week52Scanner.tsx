import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { advApi, type FreshBreakout, type OfficialCheck, type Week52Result, type Week52State } from "../api/advanced";
import type { Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtDateTime, fmtNum } from "../lib/format";
import { SymbolButton, useOpenChart } from "../lib/openChart";

function Official({ o }: { o?: OfficialCheck }) {
  if (!o || o.status === "unavailable") return <span className="text-muted" title={o?.note ?? "No official NSE 52-week report stored for this date"}>n/a</span>;
  return (
    <span className={o.status === "match" ? "text-validated" : "text-forming"} title={`NSE adjusted high ${fmtNum(o.nse_adjusted_high ?? null)} / low ${fmtNum(o.nse_adjusted_low ?? null)} (report ${o.report_date})`}>
      {o.status}
    </span>
  );
}

export function Week52Scanner() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "all";
  const asof = params.get("asof") ?? "";
  const view = (params.get("view") ?? "events") as "events" | "touch" | "state";
  const direction = params.get("dir") ?? "both";
  const confirm = params.get("confirm") ?? "1";
  const maxAge = params.get("age") ?? "0";
  const nearPct = params.get("near") ?? "";
  const insufficient = params.get("insuff") === "1";
  const fno = params.get("fno") ?? "";
  const [res, setRes] = useState<Week52Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    advApi
      .week52({ universe, asof: asof || null, view, direction, confirm, max_age: maxAge, near_pct: nearPct || null, include_insufficient: insufficient || null, fno: fno || null })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, asof, view, direction, confirm, maxAge, nearPct, insufficient, fno]);
  useEffect(load, [load, tick]);

  const openEvent = (r: FreshBreakout) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      context: {
        title: r.direction === "bullish" ? "52-week high breakout" : "52-week low breakdown",
        lines: [
          { k: "Candle", v: fmtBarTime(r.time, "1d") },
          { k: r.direction === "bullish" ? "Previous 52-week high" : "Previous 52-week low", v: fmtNum(r.level) },
          { k: "Close", v: fmtNum(r.close) },
          { k: "Breakout %", v: `${fmtNum(r.breakout_pct, 2)}%` },
          { k: "ATR multiple", v: fmtNum(r.distance_atr, 2) },
          { k: "Volume ratio", v: fmtNum(r.volume_ratio, 2) },
          { k: "Basis", v: "split/bonus-adjusted OHLC" },
        ],
        levels: [{ price: r.level, label: `52W ${r.direction === "bullish" ? "high" : "low"} ${fmtNum(r.level)}`, color: r.direction === "bullish" ? "#95EB45" : "#F2643C" }],
        markers: [{ t: r.t, label: r.direction === "bullish" ? "52W high" : "52W low", color: r.direction === "bullish" ? "#95EB45" : "#F2643C", position: r.direction === "bullish" ? "below" : "above" }],
      },
    });
  const openState = (r: Week52State) =>
    openChart({
      symbol: r.symbol,
      timeframe: "1d",
      asof: asof || null,
      context: {
        title: r.status === "ok" ? "52-week range" : "52-week range (INSUFFICIENT_HISTORY)",
        lines: [
          { k: "52-week high", v: r.high_52w !== undefined ? `${fmtNum(r.high_52w)} (${fmtBarTime(r.high_52w_time ?? null, "1d")})` : "-" },
          { k: "52-week low", v: r.low_52w !== undefined ? `${fmtNum(r.low_52w)} (${fmtBarTime(r.low_52w_time ?? null, "1d")})` : "-" },
          { k: "Close", v: fmtNum(r.close) },
          { k: "From high / low", v: `${fmtNum(r.distance_from_high_pct ?? null, 2)}% / ${fmtNum(r.distance_from_low_pct ?? null, 2)}%` },
        ],
        levels: r.status === "ok" ? [{ price: r.high_52w!, label: "52W high", color: "#95EB45" }, { price: r.low_52w!, label: "52W low", color: "#F2643C" }] : [],
      },
    });

  const eventCols: Column<FreshBreakout>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => openEvent(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "type", label: "Type", render: (r) => <span className={r.direction === "bullish" ? "text-validated" : "text-down"}>{r.direction === "bullish" ? "52W high breakout" : "52W low breakdown"}{view === "touch" ? " (touch)" : ""}</span>, sort: (r) => r.direction },
    { key: "hl", label: "52W high / low", align: "right", render: (r) => fmtNum(r.level), sort: (r) => r.level, title: "Previous 252 completed sessions (current excluded)" },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "dist", label: "Distance", align: "right", render: (r) => `${fmtNum(r.direction === "bullish" ? r.state?.distance_from_high_pct ?? null : r.state?.distance_from_low_pct ?? null, 2)}%`, title: "Latest close vs the 52-week level of the latest candle" },
    { key: "pct", label: "Breakout %", align: "right", render: (r) => (view === "touch" ? "-" : `${fmtNum(r.breakout_pct, 2)}%`), sort: (r) => Math.abs(r.breakout_pct ?? 0) },
    { key: "atr", label: "ATR multiple", align: "right", render: (r) => fmtNum(r.distance_atr ?? null, 2), sort: (r) => (r.distance_atr == null ? null : Math.abs(r.distance_atr)) },
    { key: "vr", label: "Vol ratio", align: "right", render: (r) => fmtNum(r.volume_ratio ?? null, 2), sort: (r) => r.volume_ratio ?? null },
    { key: "basis", label: "Adjustment basis", render: () => <span className="text-muted" title="Provider OHLC adjusted for splits / bonus issues; the close is on the same basis. Actual unadjusted prices are not available from this provider.">split/bonus adj.</span> },
    { key: "official", label: "NSE official", render: (r) => <Official o={r.official} /> },
    { key: "conf", label: view === "touch" ? "Close outcome" : "Confirmed at", render: (r) => (view === "touch" ? <span className="text-forming">{(r.close_status ?? "").replace("_", " ")} (provisional)</span> : fmtDateTime(r.confirmed_at)), sort: (r) => r.t },
    { key: "age", label: "Sessions since", align: "right", render: (r) => r.age_bars, sort: (r) => r.age_bars },
  ];
  const stateCols: Column<Week52State>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <SymbolButton symbol={r.symbol} onOpen={() => openState(r)} />, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "status", label: "Status", render: (r) => (r.status === "ok" ? <span className="text-ink">ok</span> : <span className="text-forming" title={r.note}>INSUFFICIENT_HISTORY</span>), sort: (r) => r.status },
    { key: "high", label: "52W high", align: "right", render: (r) => (r.status === "ok" ? fmtNum(r.high_52w ?? null) : <span className="text-muted" title="Available-period high, not a 52-week level">({fmtNum(r.available_high ?? null)})</span>), sort: (r) => r.high_52w ?? null },
    { key: "low", label: "52W low", align: "right", render: (r) => (r.status === "ok" ? fmtNum(r.low_52w ?? null) : <span className="text-muted">({fmtNum(r.available_low ?? null)})</span>), sort: (r) => r.low_52w ?? null },
    { key: "close", label: "Close", align: "right", render: (r) => fmtNum(r.close), sort: (r) => r.close },
    { key: "dh", label: "From 52W high", align: "right", render: (r) => (r.distance_from_high_pct === undefined ? "-" : `${r.distance_from_high_pct.toFixed(2)}%`), sort: (r) => r.distance_from_high_pct ?? null },
    { key: "dl", label: "From 52W low", align: "right", render: (r) => (r.distance_from_low_pct === undefined ? "-" : `${r.distance_from_low_pct.toFixed(2)}%`), sort: (r) => r.distance_from_low_pct ?? null },
    { key: "flags", label: "Today", render: (r) => [r.close_above_high && "close > 52W high", r.close_below_low && "close < 52W low", r.touch_high && !r.close_above_high && "touched high", r.touch_low && !r.close_below_low && "touched low"].filter(Boolean).join(", ") || "-" },
    { key: "sessions", label: "Sessions", align: "right", render: (r) => r.available_sessions, sort: (r) => r.available_sessions, title: "Completed sessions before the latest candle" },
    { key: "official", label: "NSE official", render: (r) => <Official o={r.official} /> },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="week52-scanner">
      <TechScanHeader
        title="52-Week Breakouts"
        subtitle="Close beyond the previous 252 completed sessions' high / low (current excluded). Levels and closes on the same split/bonus-adjusted basis; fewer than 252 sessions = INSUFFICIENT_HISTORY."
        universe={universe}
        timeframe={null}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="View">
          <select className="ctl" value={view} onChange={(e) => set({ view: e.target.value === "events" ? "" : e.target.value })} data-testid="view-select">
            <option value="events">Fresh breakouts (completed close)</option>
            <option value="touch">Intraday touches (provisional)</option>
            <option value="state">52-week range of every stock</option>
          </select>
        </Field>
        <Field label="Type">
          <select className="ctl" value={direction} onChange={(e) => set({ dir: e.target.value === "both" ? "" : e.target.value })} data-testid="dir-select">
            <option value="both">Highs + lows</option>
            <option value="bullish">52-week highs</option>
            <option value="bearish">52-week lows</option>
          </select>
        </Field>
        {view === "events" && (
          <Field label="Confirmation">
            <select className="ctl" value={confirm} onChange={(e) => set({ confirm: e.target.value === "1" ? "" : e.target.value })} data-testid="confirm-select">
              <option value="1">One completed close</option>
              <option value="2">Two consecutive closes</option>
            </select>
          </Field>
        )}
        {view !== "state" ? (
          <Field label="Sessions since">
            <select className="ctl" value={maxAge} onChange={(e) => set({ age: e.target.value === "0" ? "" : e.target.value })} data-testid="age-select">
              <option value="0">Today</option>
              {[2, 5, 10, 20].map((n) => (
                <option key={n} value={n}>
                  Within {n} sessions
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <>
            <Field label="Within % of level">
              <input className="ctl w-20" type="number" min={0} step={0.5} value={nearPct} onChange={(e) => set({ near: e.target.value })} data-testid="near-pct" />
            </Field>
            <label className="flex items-center gap-1 text-xs text-muted">
              <input type="checkbox" checked={insufficient} onChange={(e) => set({ insuff: e.target.checked ? "1" : "" })} data-testid="insufficient-toggle" /> Include insufficient history
            </label>
          </>
        )}
        <Field label="F&O">
          <select className="ctl" value={fno} onChange={(e) => set({ fno: e.target.value })} data-testid="fno-select">
            <option value="">All</option>
            <option value="YES">F&amp;O only</option>
            <option value="NO">Non-F&amp;O</option>
          </select>
        </Field>
        <span className="text-xs text-muted" data-testid="result-count">
          {res?.count ?? 0} results{res?.insufficient_history ? ` · ${res.insufficient_history} with insufficient history` : ""}
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.note && <div className="text-[11px] text-muted">{res.note}{res.official_report_date ? ` Official NSE report: ${res.official_report_date}.` : " No official NSE 52-week report stored for this date."}</div>}
      {view === "state" ? (
        <DataTable rows={(res?.items ?? []) as Week52State[]} columns={stateCols} testid="week52-table" onRow={openState} empty="No stock matches these filters." initialSort={{ key: "dh", desc: true }} />
      ) : (
        <DataTable rows={(res?.items ?? []) as FreshBreakout[]} columns={eventCols} testid="week52-table" onRow={openEvent} empty="No 52-week breakouts match these filters on the latest daily scan." initialSort={{ key: "atr", desc: true }} />
      )}
    </div>
  );
}
