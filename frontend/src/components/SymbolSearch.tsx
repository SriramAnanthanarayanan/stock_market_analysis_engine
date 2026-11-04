import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

type Item = { symbol: string; name: string | null; kind: "stock" | "index"; fno_status?: string; category?: string };

/** Common symbol search for NSE stocks (and supported indices once registered). */
export function SymbolSearch() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!q.trim()) {
      setItems([]);
      return;
    }
    const id = window.setTimeout(() => {
      api
        .symbols(q.trim(), 12)
        .then((r) => {
          setItems(r.items);
          setHi(0);
        })
        .catch(() => setItems([]));
    }, 150);
    return () => window.clearTimeout(id);
  }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const go = (it: Item) => {
    setOpen(false);
    setQ("");
    navigate(it.kind === "index" ? `/index/${encodeURIComponent(it.symbol)}?tf=1d` : `/explorer/${encodeURIComponent(it.symbol)}?tf=1d`);
  };
  return (
    <div className="relative" ref={box}>
      <input
        className="ctl h-8 w-64"
        placeholder="Search symbol or company..."
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setHi((h) => Math.min(h + 1, items.length - 1));
          else if (e.key === "ArrowUp") setHi((h) => Math.max(h - 1, 0));
          else if (e.key === "Enter" && items[hi]) go(items[hi]);
          else if (e.key === "Escape") setOpen(false);
        }}
        data-testid="symbol-search"
      />
      {open && items.length > 0 && (
        <ul className="absolute right-0 top-9 z-40 max-h-80 w-80 overflow-y-auto rounded-md border border-line bg-panel text-xs shadow-xl" data-testid="symbol-results">
          {items.map((it, i) => (
            <li key={`${it.kind}:${it.symbol}`}>
              <button className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-panel2 ${i === hi ? "bg-panel2" : ""}`} onMouseDown={(e) => e.preventDefault()} onClick={() => go(it)}>
                <span>
                  <span className="font-medium text-ink">{it.symbol}</span>
                  <span className="ml-2 text-muted">{it.name}</span>
                </span>
                <span className="text-[10px] uppercase text-muted">{it.kind === "index" ? it.category ?? "index" : it.fno_status === "YES" ? "F&O" : "stock"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
