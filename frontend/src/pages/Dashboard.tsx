import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useOpenChart } from "../lib/openChart";
import { fmtBarTime } from "../lib/format";
import { api, exportUrl } from "../api/client";
import type { HealthResponse, Match, OverviewResponse, PatternInfo, ScanResponse, ScanSummary, Timeframe, Universe } from "../api/types";
import { ErrorNote } from "../components/common";
import { Overview } from "../components/Overview";
import { DEFAULT_FILTERS, FilterBar, ResultsTable, type Filters } from "../components/ResultsTable";
import { ScanControls } from "../components/ScanControls";

const ACTIVE = new Set(["queued", "running"]);

export function Dashboard() {
  const [params, setParams] = useSearchParams();
  const openChart = useOpenChart();
  const universe = (params.get("u") as Universe) || "nifty500";
  const timeframe = (params.get("tf") as Timeframe) || "1d";
  const asof = params.get("asof") || "";
  const filters: Filters = {
    pattern: params.get("pattern") || DEFAULT_FILTERS.pattern,
    category: params.get("category") || DEFAULT_FILTERS.category,
    status: params.get("status") || DEFAULT_FILTERS.status,
    fno: params.get("fno") || DEFAULT_FILTERS.fno,
    minQuality: Number(params.get("minq") || 0),
    search: params.get("q") || "",
    developing: params.get("dev") === "1",
  };

  // undefined = leave unchanged; "", false, 0 = back to default (removed from the URL).
  const update = (patch: Record<string, string | number | boolean | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) continue;
      if (v === "" || v === false || v === 0) next.delete(k);
      else next.set(k, v === true ? "1" : String(v));
    }
    setParams(next, { replace: true });
  };

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [patterns, setPatterns] = useState<PatternInfo[]>([]);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [tracked, setTracked] = useState<string | null>(null);
  const [active, setActive] = useState<ScanSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.scan({
        universe,
        timeframe,
        asof: asof || null,
        pattern: filters.pattern || null,
        category: filters.category || null,
        status: filters.status || null,
        min_quality: filters.minQuality || undefined,
        include_developing: filters.developing || undefined,
      });
      setScan(res);
      setActive(res.active_scan);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [universe, timeframe, asof, filters.pattern, filters.category, filters.status, filters.minQuality, filters.developing]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.patterns().then(setPatterns).catch(() => setPatterns([]));
  }, []);

  useEffect(() => {
    api.overview(universe, asof || null).then(setOverview).catch(() => setOverview(null));
    api.health().then(setHealth).catch(() => setHealth(null));
  }, [universe, asof, scan?.scan?.scan_id]);

  // Follow a running scan (started here, by the scheduler or at startup).
  const followId = tracked ?? scan?.active_scan?.scan_id ?? null;
  useEffect(() => {
    if (!followId) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const st = await api.scanStatus(followId);
        if (cancelled) return;
        setActive(st);
        if (!ACTIVE.has(st.state)) {
          window.clearInterval(timer);
          setTracked(null);
          setActive(null);
          void load();
        }
      } catch {
        /* transient; keep polling */
      }
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [followId, load]);

  const onRefresh = async () => {
    setRefreshing(true);
    try {
      const r = await api.refresh({ universe, timeframe, asof: asof || null });
      setTracked(r.scan_id);
      setActive(await api.scanStatus(r.scan_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  const rows = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return (scan?.matches ?? []).filter(
      (m) =>
        (!filters.fno || m.fno_status === filters.fno) &&
        (!q || m.symbol.toLowerCase().includes(q) || (m.company ?? "").toLowerCase().includes(q)),
    );
  }, [scan, filters.fno, filters.search]);

  const open = (m: Match) =>
    openChart({
      symbol: m.symbol,
      timeframe: m.timeframe,
      patternId: m.pattern_id,
      asof: asof || null,
      context: {
        title: `${m.pattern_name} - ${m.status_label}`,
        lines: [
          { k: "Quality (fit, not a probability)", v: `${m.quality.toFixed(0)} (${m.quality_label})` },
          { k: "Formation", v: `${fmtBarTime(m.formation_start, m.timeframe)} - ${fmtBarTime(m.formation_end, m.timeframe)}` },
          { k: "Bars / touches", v: `${m.formation_length} / ${m.upper_touches ?? "-"} up, ${m.lower_touches ?? "-"} low` },
          { k: "Latest close", v: String(m.last_close ?? "-") },
        ],
      },
    });

  const warnings = [...(scan?.scan?.warnings ?? []), ...(scan?.note ? [scan.note] : [])];
  const noScan = !loading && !scan?.scan && !active;

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-3" data-testid="dashboard">
      <ScanControls
        universe={universe}
        timeframe={timeframe}
        asof={asof}
        onChange={(p) => update({ u: p.universe, tf: p.timeframe, asof: p.asof })}
        onRefresh={onRefresh}
        refreshing={refreshing}
        active={active}
        lastScan={scan?.scan ?? null}
      />
      {error && <ErrorNote message={`Could not load scan results: ${error}`} />}
      {warnings.length > 0 && (
        <div className="rounded-md border border-forming/40 bg-forming/10 px-3 py-2 text-xs text-forming" data-testid="scan-warnings">
          {warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
        </div>
      )}
      {noScan && (
        <div className="panel px-4 py-6 text-center text-sm text-muted" data-testid="empty-state">
          No completed scan for this universe, timeframe and date yet.
          <button className="btn-primary ml-3" onClick={onRefresh} disabled={refreshing}>
            Run scan
          </button>
        </div>
      )}
      <Overview scan={scan} overview={overview} health={health} patterns={patterns} />
      <FilterBar
        value={filters}
        patterns={patterns}
        onChange={(f) =>
          update({ pattern: f.pattern, category: f.category, status: f.status, fno: f.fno, minq: f.minQuality, q: f.search, dev: f.developing })
        }
      />
      <ResultsTable rows={rows} onOpen={open} loading={loading} />
      {scan?.scan && (
        <div className="flex justify-end gap-3 text-xs text-muted" data-testid="export-links">
          Export detector results ({scan.scan.scan_id}):
          <a className="text-accent hover:underline" href={exportUrl.detections(universe, timeframe, asof || null, "csv")}>
            CSV
          </a>
          <a className="text-accent hover:underline" href={exportUrl.detections(universe, timeframe, asof || null, "json")}>
            JSON
          </a>
        </div>
      )}
    </div>
  );
}
