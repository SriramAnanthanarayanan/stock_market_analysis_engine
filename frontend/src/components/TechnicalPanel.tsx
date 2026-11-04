import type { DataSourceInfo, IndicatorPayload, TechnicalPayload, Timeframe } from "../api/types";
import { fmtBarTime, fmtNum, humanize } from "../lib/format";
import { KV, Panel } from "./common";

const ALIGN_LABEL: Record<string, string> = {
  bullish_full: "Bullish full (10>20>30>50>100>200)",
  bearish_full: "Bearish full (10<20<30<50<100<200)",
  bullish_partial: "Bullish partial (20>50>200)",
  bearish_partial: "Bearish partial (20<50<200)",
  mixed: "Mixed",
  not_available: "Not available (insufficient history)",
};

export function DataSourcePanel({ ds }: { ds: DataSourceInfo | null | undefined }) {
  if (!ds) return null;
  const mismatch = ds.scan_price_basis && ds.scan_price_basis !== ds.price_basis;
  return (
    <Panel title="Data source">
      <div data-testid="data-source">
        <KV k="Provider" v={ds.provider} />
        <KV k="Price basis" v={ds.price_basis_label} title={ds.note} />
        <KV k="Adjusted close" v={ds.adjusted_close_available ? "available (returns only)" : "not available"} />
        {mismatch && <div className="mt-1 text-[11px] text-forming">Scan computed on {ds.scan_price_basis}; re-run the scan.</div>}
        <p className="mt-1 text-[10px] text-muted">{ds.note}</p>
      </div>
    </Panel>
  );
}

export function TechnicalPanel({ tech, ind, timeframe, error }: { tech: TechnicalPayload | null; ind: IndicatorPayload | null; timeframe: Timeframe; error?: string | null }) {
  if (error) return <Panel title="Technical indicators"><div className="text-xs text-down">{error}</div></Panel>;
  if (!tech) return null;
  const s = tech.snapshot;
  const maru = tech.marubozu.slice(-5).reverse();
  const divs = tech.divergences.filter((d) => d.status !== "superseded").slice(-4).reverse();
  const brk = tech.daily.breakouts.slice(-4).reverse();
  return (
    <Panel title="Technical indicators" right={<span className="text-[10px] text-muted">v{tech.config_version}</span>}>
      <div className="text-xs" data-testid="technical-panel">
        <KV k="EMA alignment" v={ALIGN_LABEL[s.alignment] ?? s.alignment} />
        <KV k="Close vs EMA 20 / 50 / 200" v={["20", "50", "200"].map((p) => s.price_vs_ema[p] ?? "n/a").join(" / ")} />
        <div className="my-1 grid grid-cols-3 gap-x-2 text-[11px]">
          {Object.entries(s.ema).map(([p, v]) => (
            <span key={p} className="num text-muted">
              EMA{p} <span className="text-ink">{fmtNum(v)}</span>
            </span>
          ))}
        </div>
        <KV k={`RSI(${ind?.params.rsi_period ?? 14})`} v={s.rsi === null ? "n/a" : `${s.rsi.toFixed(1)} (${s.rsi_state})`} />
        <KV k="ATR(14) / % of close" v={s.atr === null ? "n/a" : `${fmtNum(s.atr)} / ${fmtNum(s.atr_pct, 2)}%`} />
        <KV k="Last candle range / ATR" v={fmtNum(s.range_atr, 2)} />
        {tech.ema_crosses.length > 0 && (
          <div className="mt-1">
            <div className="label">EMA crossovers in view</div>
            {tech.ema_crosses.slice(-4).reverse().map((c) => (
              <div key={`${c.kind}${c.t}`} className={c.direction === "bullish" ? "text-validated" : "text-down"}>
                EMA {c.fast}/{c.slow} {c.direction} - {fmtBarTime(c.time, timeframe)}
              </div>
            ))}
          </div>
        )}
        <div className="mt-2 border-t border-line pt-2">
          <div className="label">Daily {Object.keys(tech.daily.levels).join("/")}-day levels {timeframe === "1d" ? "" : "(daily-derived)"}</div>
          {Object.values(tech.daily.levels).map((lv) =>
            lv ? (
              <div key={lv.lookback_days} className="num" data-testid="daily-levels">
                High <span className="text-validated">{fmtNum(lv.high)}</span> / Low <span className="text-down">{fmtNum(lv.low)}</span>
                <div className="text-[10px] text-muted">
                  previous {lv.lookback_days} completed daily candles {fmtBarTime(lv.reference_start, "1d")} - {fmtBarTime(lv.reference_end, "1d")}
                </div>
              </div>
            ) : (
              <div key="none" className="text-muted">Insufficient daily history.</div>
            ),
          )}
          {tech.daily.status !== "ok" && <div className="text-forming">{humanize(tech.daily.status)}</div>}
          {timeframe === "1d" && brk.length > 0 && (
            <ul className="mt-1 space-y-0.5" data-testid="breakout-list">
              {brk.map((b) => (
                <li key={`${b.kind}${b.t}`} className={b.status === "failed" ? "text-muted" : b.direction === "bullish" ? "text-validated" : "text-down"}>
                  {b.lookback_days}D {b.direction === "bullish" ? "high breakout" : "low breakdown"} ({b.status}, {b.confirm_closes}-close) {fmtBarTime(b.time, "1d")}: close {fmtNum(b.close)} vs {fmtNum(b.level)} ({b.breakout_pct.toFixed(2)}%, {fmtNum(b.distance_atr, 2)} ATR, vol {fmtNum(b.volume_ratio, 2)}x)
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="mt-2 border-t border-line pt-2">
          <div className="label">Marubozu candles in view ({tech.marubozu.length})</div>
          {maru.length === 0 && <div className="text-muted">None.</div>}
          <ul className="space-y-1" data-testid="marubozu-list">
            {maru.map((m) => (
              <li key={m.t} className="text-[11px]">
                <span className={m.direction === "bullish" ? "text-validated" : "text-down"}>{humanize(m.kind)}</span> <span className="text-muted">{fmtBarTime(m.time, timeframe)}</span>
                <div className="num text-muted">
                  body {m.body_pct.toFixed(1)}% · wicks {m.upper_wick_pct.toFixed(1)}% / {m.lower_wick_pct.toFixed(1)}% · range {fmtNum(m.range)} ({fmtNum(m.range_atr, 2)} ATR) · vol {fmtNum(m.volume_ratio, 2)}x
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="mt-2 border-t border-line pt-2">
          <div className="label">RSI divergence (regular, confirmed pivots)</div>
          {divs.length === 0 && <div className="text-muted">None in view.</div>}
          <ul className="space-y-1" data-testid="divergence-list">
            {divs.map((d) => (
              <li key={`${d.direction}${d.p2.t}`} className="text-[11px]">
                <span className={d.direction === "bullish" ? "text-validated" : "text-down"}>
                  {d.direction} ({d.status})
                </span>{" "}
                <span className="text-muted">
                  P1 {fmtBarTime(d.p1.time, timeframe)} -&gt; P2 {fmtBarTime(d.p2.time, timeframe)}
                  {d.confirmed_time ? `, confirmed ${fmtBarTime(d.confirmed_time, timeframe)}` : ", awaiting confirmation"}
                </span>
                <div className="num text-muted">
                  price {fmtNum(d.p1.price)} -&gt; {fmtNum(d.p2.price)} ({d.price_diff_atr.toFixed(2)} ATR) · RSI {d.p1.rsi.toFixed(1)} -&gt; {d.p2.rsi.toFixed(1)} ({d.rsi_diff.toFixed(1)})
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="mt-2 text-[10px] text-muted">Indicators describe price behaviour; they are not signals or recommendations. All values are computed by the backend on completed candles.</p>
      </div>
    </Panel>
  );
}
