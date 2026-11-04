import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { CandleSignal, TechScanResult, Timeframe, Universe } from "../api/types";
import { ErrorNote, FnoBadge } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import { Field, TechScanHeader } from "../components/TechScanHeader";
import { fmtBarTime, fmtNum, fmtVolume } from "../lib/format";

export function CandlestickScanner() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const universe = (params.get("u") as Universe) || "all";
  const timeframe = (params.get("tf") as Timeframe) || "1d";
  const asof = params.get("asof") ?? "";
  const kind = params.get("kind") ?? "";
  const minAtr = params.get("min_atr") ?? "";
  const minVol = params.get("min_vol") ?? "";
  const maxAge = params.get("age") ?? "0";
  const [res, setRes] = useState<TechScanResult<CandleSignal> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k);
    setParams(next, { replace: true });
  };
  const load = useCallback(() => {
    api
      .scanCandlesticks({ universe, timeframe, asof: asof || null, kind: kind || null, min_range_atr: minAtr || null, min_volume_ratio: minVol || null, max_age: maxAge })
      .then((r) => (setRes(r), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [universe, timeframe, asof, kind, minAtr, minVol, maxAge]);
  useEffect(load, [load, tick]);

  const cols: Column<CandleSignal>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <span className="font-medium text-ink">{r.symbol}</span>, sort: (r) => r.symbol },
    { key: "company", label: "Company", render: (r) => <span className="text-muted">{r.company}</span>, sort: (r) => r.company ?? "" },
    { key: "fno", label: "F&O", render: (r) => <FnoBadge value={r.fno_status} /> },
    { key: "kind", label: "Pattern", render: (r) => <span className={r.direction === "bullish" ? "text-validated" : "text-down"}>{r.direction === "bullish" ? "Bullish Marubozu" : "Bearish Marubozu"}</span>, sort: (r) => r.kind },
    { key: "time", label: "Candle", render: (r) => fmtBarTime(r.time, r.timeframe), sort: (r) => r.t },
    { key: "body", label: "Body %", align: "right", render: (r) => r.body_pct.toFixed(1), sort: (r) => r.body_pct },
    { key: "wicks", label: "Upper / lower wick %", align: "right", render: (r) => `${r.upper_wick_pct.toFixed(1)} / ${r.lower_wick_pct.toFixed(1)}` },
    { key: "range", label: "Range", align: "right", render: (r) => fmtNum(r.range), sort: (r) => r.range },
    { key: "ratr", label: "Range / ATR", align: "right", render: (r) => fmtNum(r.range_atr, 2), sort: (r) => r.range_atr },
    { key: "vol", label: "Volume", align: "right", render: (r) => fmtVolume(r.volume), sort: (r) => r.volume },
    { key: "vr", label: "Vol ratio (20)", align: "right", render: (r) => fmtNum(r.volume_ratio, 2), sort: (r) => r.volume_ratio, title: "Candle volume / mean of the previous 20 completed candles (supporting information only)" },
    { key: "age", label: "Age", align: "right", render: (r) => `${r.age_bars} bars`, sort: (r) => r.age_bars },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="candlestick-scanner">
      <TechScanHeader
        title="Candlestick Scanner"
        subtitle="Marubozu: body >= 90 % of the range, each wick <= 5 % (raw OHLC, completed candles). Volume is supporting information only."
        universe={universe}
        timeframe={timeframe}
        asof={asof}
        onChange={(p) => set({ u: p.universe ?? universe, tf: p.timeframe ?? timeframe, asof: p.asof ?? asof })}
        scan={res?.scan ?? null}
        onScanDone={() => setTick((x) => x + 1)}
      >
        <Field label="Pattern">
          <select className="ctl" value={kind} onChange={(e) => set({ kind: e.target.value })} data-testid="kind-select">
            <option value="">Bullish + bearish</option>
            <option value="bullish_marubozu">Bullish Marubozu</option>
            <option value="bearish_marubozu">Bearish Marubozu</option>
          </select>
        </Field>
        <Field label="Min range (x ATR)">
          <input className="ctl w-24" type="number" min={0} step={0.1} value={minAtr} onChange={(e) => set({ min_atr: e.target.value })} data-testid="min-atr" />
        </Field>
        <Field label="Min relative volume">
          <input className="ctl w-24" type="number" min={0} step={0.1} value={minVol} onChange={(e) => set({ min_vol: e.target.value })} data-testid="min-vol" />
        </Field>
        <Field label="Candle age">
          <select className="ctl" value={maxAge} onChange={(e) => set({ age: e.target.value })} data-testid="age-select">
            <option value="0">Latest completed candle</option>
            {[1, 2, 4].map((n) => (
              <option key={n} value={n}>
                Within last {n + 1} candles
              </option>
            ))}
          </select>
        </Field>
        <span className="text-xs text-muted" data-testid="result-count">
          {res?.count ?? 0} candles
        </span>
      </TechScanHeader>
      {error && <ErrorNote message={error} />}
      {res?.note && <div className="text-[11px] text-muted">{res.note}</div>}
      <DataTable
        rows={res?.items ?? []}
        columns={cols}
        testid="candle-table"
        onRow={(r) => navigate(`/explorer/${r.symbol}?tf=${r.timeframe}&show=marubozu${asof ? `&asof=${asof}` : ""}`)}
        empty="No Marubozu candles match these filters on this scan."
        initialSort={{ key: "ratr", desc: true }}
      />
    </div>
  );
}
