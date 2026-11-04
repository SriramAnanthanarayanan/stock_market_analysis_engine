import type { ReactNode } from "react";
import type { FnoStatus, StatusSlug } from "../api/types";
import { STATUS_META } from "../lib/status";

export function StatusBadge({ status, reason }: { status: StatusSlug; reason?: string | null }) {
  const meta = STATUS_META[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium"
      style={{ color: meta.color, backgroundColor: `${meta.color}1f`, border: `1px solid ${meta.color}55` }}
      title={reason ? `${meta.label} (${reason})` : meta.label}
      data-status={status}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: meta.color }} />
      {meta.label}
    </span>
  );
}

export function FnoBadge({ value }: { value: FnoStatus }) {
  const cls =
    value === "YES" ? "text-accent border-accent/50" : value === "NO" ? "text-muted border-line" : "text-forming border-forming/50";
  return (
    <span className={`inline-block rounded border px-1.5 text-[10px] font-semibold ${cls}`} title={`F&O membership: ${value}`}>
      {value === "YES" ? "F&O" : value === "NO" ? "Non-F&O" : "F&O ?"}
    </span>
  );
}

export function QualityCell({ score, label }: { score: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-2" title={`Pattern quality ${score.toFixed(1)} (${label}) - a geometric fit score, not a probability`}>
      <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-page">
        <span className="absolute inset-y-0 left-0 rounded-full bg-accent/80" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </span>
      <span className="num text-ink">{score.toFixed(0)}</span>
      <span className="text-[10px] text-muted">{label}</span>
    </span>
  );
}

export function Panel({ title, children, right, className = "" }: { title?: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-3 py-2">
          <h2 className="label">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Disclaimer() {
  return (
    <p className="mx-auto max-w-5xl px-4 py-6 text-center text-[11px] leading-relaxed text-muted" data-testid="disclaimer">
      Market data may be delayed, incomplete or adjusted by the provider. Pattern detections are rule-based chart analyses, not
      investment advice. Verify the underlying data and make independent decisions.
    </p>
  );
}

export function KV({ k, v, title }: { k: string; v: ReactNode; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-xs" title={title}>
      <span className="text-muted">{k}</span>
      <span className="num text-right text-ink">{v}</span>
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return <div className="rounded-md border border-down/50 bg-down/10 px-3 py-2 text-sm text-down">{message}</div>;
}
