import { NavLink, Route, Routes } from "react-router-dom";
import { Disclaimer } from "./components/common";
import { SymbolSearch } from "./components/SymbolSearch";
import { ChartPreviewHost } from "./components/StockChartPreview";
import { BreakoutScanner } from "./pages/BreakoutScanner";
import { CandlestickScanner } from "./pages/CandlestickScanner";
import { Dashboard } from "./pages/Dashboard";
import { Evaluation } from "./pages/Evaluation";
import { Explorer } from "./pages/Explorer";
import { FreshBreakoutScanner } from "./pages/FreshBreakoutScanner";
import { VcpScanner } from "./pages/VcpScanner";
import { VolumeShocker } from "./pages/VolumeShocker";
import { Week52Scanner } from "./pages/Week52Scanner";
import { History } from "./pages/History";
import { IndexAnalysis } from "./pages/IndexAnalysis";
import { IndexExplorer } from "./pages/IndexExplorer";
import { IndicatorScanner } from "./pages/IndicatorScanner";
import { RRG } from "./pages/RRG";
import { StageAnalysis } from "./pages/StageAnalysis";

const NAV: { to: string; label: string; testid: string; end?: boolean; group?: string }[] = [
  { to: "/", label: "Pattern Scanner", testid: "nav-scanner", end: true, group: "Scanners" },
  { to: "/candlesticks", label: "Candlestick Scanner", testid: "nav-candlesticks" },
  { to: "/fresh-breakouts", label: "Fresh Breakouts", testid: "nav-fresh-breakouts" },
  { to: "/52-week", label: "52-Week Breakouts", testid: "nav-52week" },
  { to: "/volume-shocker", label: "Volume Shocker", testid: "nav-volume" },
  { to: "/vcp", label: "VCP Scanner", testid: "nav-vcp" },
  { to: "/breakouts", label: "Breakout Scanner", testid: "nav-breakouts" },
  { to: "/indicators", label: "Indicator Scanner", testid: "nav-indicators" },
  { to: "/rrg", label: "RRG", testid: "nav-rrg", group: "Markets" },
  { to: "/indices", label: "Index Analysis", testid: "nav-indices" },
  { to: "/stage", label: "Stage Analysis", testid: "nav-stage" },
  { to: "/history", label: "Pattern History", testid: "nav-history", group: "Research" },
  { to: "/evaluation", label: "Labels & evaluation", testid: "nav-evaluation" },
];

const navCls = ({ isActive }: { isActive: boolean }) =>
  `block rounded px-2.5 py-1.5 text-xs ${isActive ? "bg-panel2 text-accent" : "text-muted hover:bg-panel2/60 hover:text-ink"}`;

export default function App() {
  return (
    <ChartPreviewHost>
    <div className="flex min-h-screen flex-col bg-page">
      <header className="border-b border-line bg-panel/60">
        <div className="flex items-center gap-3 px-4 py-2.5">
          <NavLink to="/" className="text-base font-semibold tracking-tight text-ink" data-testid="app-title">
            Indian Price Action Screener
          </NavLink>
          <span className="rounded border border-accent/50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent" data-testid="nse-indicator">
            NSE India
          </span>
          <span className="ml-auto hidden text-[11px] text-muted lg:inline">Rule-based chart analysis - not trade recommendations</span>
          <SymbolSearch />
        </div>
      </header>
      <div className="flex flex-1">
        <nav className="w-48 shrink-0 border-r border-line bg-panel/40 px-2 py-3" data-testid="side-nav">
          {NAV.map((n, i) => (
            <div key={n.to}>
              {n.group && <div className={`label mb-1 px-2.5 ${i === 0 ? "" : "mt-4"}`}>{n.group}</div>}
              <NavLink to={n.to} end={n.end} className={navCls} data-testid={n.testid}>
                {n.label}
              </NavLink>
            </div>
          ))}
        </nav>
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/explorer/:symbol" element={<Explorer />} />
            <Route path="/candlesticks" element={<CandlestickScanner />} />
            <Route path="/breakouts" element={<BreakoutScanner />} />
            <Route path="/fresh-breakouts" element={<FreshBreakoutScanner />} />
            <Route path="/52-week" element={<Week52Scanner />} />
            <Route path="/volume-shocker" element={<VolumeShocker />} />
            <Route path="/vcp" element={<VcpScanner />} />
            <Route path="/indicators" element={<IndicatorScanner />} />
            <Route path="/rrg" element={<RRG />} />
            <Route path="/stage" element={<StageAnalysis />} />
            <Route path="/indices" element={<IndexAnalysis />} />
            <Route path="/index/:code" element={<IndexExplorer />} />
            <Route path="/history" element={<History />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </main>
      </div>
      <footer className="border-t border-line">
        <Disclaimer />
      </footer>
    </div>
    </ChartPreviewHost>
  );
}
