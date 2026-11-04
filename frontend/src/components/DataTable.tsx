import { useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  sort?: (row: T) => number | string | null;
  align?: "left" | "right";
  title?: string;
}

/** Sortable, paginated table for the technical scanners. */
export function DataTable<T>({ rows, columns, onRow, testid, empty, pageSize = 50, initialSort }: { rows: T[]; columns: Column<T>[]; onRow?: (r: T) => void; testid: string; empty: ReactNode; pageSize?: number; initialSort?: { key: string; desc: boolean } }) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key);
    if (!col?.sort || !sort) return rows;
    const val = col.sort;
    return [...rows].sort((a, b) => {
      const x = val(a);
      const y = val(b);
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      const r = x < y ? -1 : 1;
      return sort.desc ? -r : r;
    });
  }, [rows, columns, sort]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const cur = Math.min(page, pages - 1);
  const shown = sorted.slice(cur * pageSize, (cur + 1) * pageSize);
  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs" data-testid={testid}>
          <thead className="bg-panel2 text-muted">
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={`whitespace-nowrap px-2 py-2 font-normal ${c.align === "right" ? "text-right" : "text-left"} ${c.sort ? "cursor-pointer hover:text-ink" : ""}`}
                  title={c.title}
                  onClick={() => c.sort && setSort((s) => (s?.key === c.key ? { key: c.key, desc: !s.desc } : { key: c.key, desc: true }))}
                >
                  {c.label}
                  {sort?.key === c.key ? (sort.desc ? " ▾" : " ▴") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} className={`border-t border-line/50 ${onRow ? "cursor-pointer hover:bg-panel2" : ""}`} onClick={() => onRow?.(r)} data-testid={`${testid}-row`}>
                {columns.map((c) => (
                  <td key={c.key} className={`whitespace-nowrap px-2 py-1.5 ${c.align === "right" ? "num text-right" : ""}`}>
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <div className="px-3 py-6 text-center text-xs text-muted">{empty}</div>}
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-1.5 text-xs text-muted">
          <button className="chip h-6" disabled={cur === 0} onClick={() => setPage(cur - 1)}>
            Prev
          </button>
          <span>
            Page {cur + 1} / {pages} ({sorted.length} rows)
          </span>
          <button className="chip h-6" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}
