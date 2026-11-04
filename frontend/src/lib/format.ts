// All dates are rendered in Asia/Kolkata explicitly - never browser-local time.
// Inputs are either Unix seconds (true UTC instants) or ISO strings with an
// offset; both describe the same instant, so no manual shifting is ever done.

export const IST = "Asia/Kolkata";

const dateFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, day: "2-digit", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const timeFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false });
const dayMonthFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, day: "2-digit", month: "short" });
const monthFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, month: "short" });
const yearFmt = new Intl.DateTimeFormat("en-GB", { timeZone: IST, year: "numeric" });

export const isIntraday = (tf: string) => tf === "1h" || tf === "4h";

export function toDate(v: number | string): Date {
  return typeof v === "number" ? new Date(v * 1000) : new Date(v);
}

export function fmtDate(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "-";
  return dateFmt.format(toDate(v));
}

export function fmtDateTime(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "-";
  return `${dateTimeFmt.format(toDate(v))} IST`;
}

export function fmtBarTime(v: number | string | null | undefined, timeframe: string): string {
  return isIntraday(timeframe) ? fmtDateTime(v) : fmtDate(v);
}

export function tickLabel(t: number, kind: "year" | "month" | "day" | "time"): string {
  const d = toDate(t);
  if (kind === "year") return yearFmt.format(d);
  if (kind === "month") return monthFmt.format(d);
  if (kind === "day") return dayMonthFmt.format(d);
  return timeFmt.format(d);
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function fmtVolume(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  return v.toLocaleString("en-IN");
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
