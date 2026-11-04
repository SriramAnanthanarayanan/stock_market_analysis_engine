import type { Timeframe } from "../api/types";

/** Scan timeframes (1H-1W) plus the chart-only multi-month timeframes, which are
 *  aggregated by the backend from completed daily candles. "1mo" (not "1M") so a
 *  month is never confused with a minute. */
export type MultiMonth = "1mo" | "3mo" | "6mo" | "12mo";
export type ChartTimeframe = Timeframe | MultiMonth;

export const CHART_TIMEFRAMES: { value: ChartTimeframe; label: string; title: string }[] = [
  { value: "1h", label: "1H", title: "1 Hour" },
  { value: "4h", label: "4H", title: "4 Hours (09:15-13:15, 13:15-15:30)" },
  { value: "1d", label: "1D", title: "1 Day" },
  { value: "1w", label: "1W", title: "1 Week" },
  { value: "1mo", label: "1 Month", title: "Calendar month, aggregated from completed daily candles" },
  { value: "3mo", label: "3 Months", title: "Calendar quarter, aggregated from completed daily candles" },
  { value: "6mo", label: "6 Months", title: "January-June / July-December, aggregated from completed daily candles" },
  { value: "12mo", label: "12 Months", title: "Calendar year, aggregated from completed daily candles" },
];

export const isMultiMonth = (tf: string): tf is MultiMonth => tf === "1mo" || tf === "3mo" || tf === "6mo" || tf === "12mo";
export const isScanTimeframe = (tf: string): tf is Timeframe => tf === "1h" || tf === "4h" || tf === "1d" || tf === "1w";
export const tfLabel = (tf: string) => CHART_TIMEFRAMES.find((x) => x.value === tf)?.label ?? tf.toUpperCase();
