import { createContext, useCallback, useContext, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { ChartTimeframe } from "./timeframes";

/** What a results table asks for when a symbol is clicked. ``context`` is the
 *  signal that made the user open the chart (shown in the preview). */
export interface ChartRequest {
  symbol: string;
  timeframe: ChartTimeframe;
  instrument?: "stock" | "index";
  patternId?: string | null;
  asof?: string | null;
  show?: string[];
  context?: PreviewContext | null;
}

export interface PreviewContext {
  title: string;
  lines: { k: string; v: string }[];
  /** Price levels to draw in the preview (e.g. a breakout level or a VCP pivot). */
  levels?: { price: number; label: string; color?: string; t0?: number }[];
  /** A marker at a candle (e.g. the breakout candle). */
  markers?: { t: number; label: string; color?: string; position?: "above" | "below" }[];
  /** Load the VCP overlay for this symbol. */
  vcp?: boolean;
  /** Candle time the preview should bring into view (e.g. the start of a reference window). */
  focusT?: number;
}

type Opener = (req: ChartRequest) => void;

const Ctx = createContext<Opener | null>(null);

export function ChartOpenerProvider({ value, children }: { value: Opener; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function explorerUrl(req: ChartRequest): string {
  const qs = new URLSearchParams({ tf: req.timeframe });
  if (req.patternId) qs.set("pattern_id", req.patternId);
  if (req.asof) qs.set("asof", req.asof);
  if (req.show?.length) qs.set("show", req.show.join(","));
  return req.instrument === "index" ? `/index/${encodeURIComponent(req.symbol)}?${qs.toString()}` : `/explorer/${encodeURIComponent(req.symbol)}?${qs.toString()}`;
}

/** Opens the shared chart preview (or, without a provider, the full chart page). */
export function useOpenChart(): Opener {
  const ctx = useContext(Ctx);
  const navigate = useNavigate();
  const location = useLocation();
  const fallback = useCallback<Opener>((req) => navigate(explorerUrl(req), { state: { from: `${location.pathname}${location.search}` } }), [navigate, location]);
  return ctx ?? fallback;
}

/** Clickable symbol for every results table: opens the chart preview without
 *  leaving the page (row clicks are not triggered twice). */
export function SymbolButton({ symbol, onOpen, testid = "symbol-link" }: { symbol: string; onOpen: () => void; testid?: string }) {
  return (
    <button
      type="button"
      className="font-semibold text-ink underline decoration-accent/40 decoration-dotted underline-offset-2 hover:text-accent"
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
      title={`Open the ${symbol} chart preview`}
      data-testid={testid}
      data-symbol={symbol}
    >
      {symbol}
    </button>
  );
}
