import type { Candle, PartialCandle } from "../api/types";

/** Line chart mode: one point per candle at the candle's start time with its close
 *  (same timestamps and prices as the candlesticks); the running 1W / multi-month
 *  period, when present, is the last point and is flagged so it can be drawn grey. */
export function toLineData(candles: Candle[], partial: PartialCandle | null): { t: number; v: number; partial: boolean }[] {
  const out = candles.map((c) => ({ t: c.t, v: c.c, partial: false }));
  if (partial && (!candles.length || partial.t > candles[candles.length - 1].t)) out.push({ t: partial.t, v: partial.c, partial: true });
  return out;
}
