"use client";

import { useEffect, useState, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Metrics {
  total_pnl: number;
  roi_pct: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  total_trades: number;
  winning_trades: number;
  avg_hold_days: number;
  sharpe_ratio: number | null;
}

interface EquityPoint {
  timestamp: string;
  equity: number;
}

interface Trade {
  market_id: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  position: string;
  stake: number;
  pnl: number;
  pnl_pct: number;
  hold_days: number;
  exit_reason: string;
}

interface Result {
  run_id: string;
  strategy_name: string;
  status: string;
  metrics: Metrics | null;
  equity_curve: EquityPoint[];
  trades: Trade[];
  created_at: string;
  completed_at: string | null;
  error: string | null;
}

function MetricCard({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${className}`}>{value}</div>
    </div>
  );
}

function fmt(n: number, decimals = 2) { return n.toFixed(decimals); }
function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" });
}

function RunDetail({ result }: { result: Result }) {
  const m = result.metrics!;
  const chartData = result.equity_curve.map((pt) => ({
    date: fmtDate(pt.timestamp),
    equity: pt.equity,
  }));
  const initialEquity = chartData[0]?.equity ?? 1000;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">{result.strategy_name}</h1>
          <p className="text-slate-400 text-sm mt-1">
            Run <code className="text-blue-400">{result.run_id.slice(0, 8)}</code>
            {result.completed_at && ` · Completed ${fmtDate(result.completed_at)}`}
          </p>
        </div>
        <div className="flex gap-2">
          <a href={`${API}/results/${result.run_id}/export`} className="btn-secondary text-sm" download>
            Export CSV
          </a>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard label="Total PnL" value={`${m.total_pnl >= 0 ? "+" : ""}$${fmt(m.total_pnl)}`} className={m.total_pnl >= 0 ? "text-green-400" : "text-red-400"} />
        <MetricCard label="ROI" value={`${m.roi_pct >= 0 ? "+" : ""}${fmt(m.roi_pct)}%`} className={m.roi_pct >= 0 ? "text-green-400" : "text-red-400"} />
        <MetricCard label="Max Drawdown" value={`${fmt(m.max_drawdown_pct)}%`} className="text-red-400" />
        <MetricCard label="Win Rate" value={`${fmt(m.win_rate_pct)}%`} className={m.win_rate_pct >= 50 ? "text-green-400" : "text-yellow-400"} />
        <MetricCard label="Total Trades" value={String(m.total_trades)} className="text-slate-100" />
        <MetricCard label="Winning Trades" value={`${m.winning_trades} / ${m.total_trades}`} className="text-slate-100" />
        <MetricCard label="Avg Hold" value={`${fmt(m.avg_hold_days, 1)} days`} className="text-slate-100" />
        <MetricCard label="Sharpe Ratio" value={m.sharpe_ratio != null ? fmt(m.sharpe_ratio) : "N/A"} className={m.sharpe_ratio == null ? "text-slate-400" : m.sharpe_ratio >= 1 ? "text-green-400" : "text-yellow-400"} />
      </div>

      {chartData.length > 1 && (
        <div className="card">
          <h2 className="font-semibold text-slate-200 mb-4">Equity Curve</h2>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} interval={Math.max(1, Math.floor(chartData.length / 8))} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} tickFormatter={(v) => `$${v.toLocaleString()}`} width={70} />
              <Tooltip contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155", borderRadius: 8 }} labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [`$${v.toLocaleString()}`, "Equity"]} />
              <ReferenceLine y={initialEquity} stroke="#475569" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {result.trades.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-slate-200">Trades ({result.trades.length})</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-400 border-b border-slate-700">
                  <th className="pb-2 pr-4 font-medium">Market</th>
                  <th className="pb-2 pr-4 font-medium">Entry</th>
                  <th className="pb-2 pr-4 font-medium">Exit</th>
                  <th className="pb-2 pr-4 font-medium">Entry P</th>
                  <th className="pb-2 pr-4 font-medium">Exit P</th>
                  <th className="pb-2 pr-4 font-medium">Stake</th>
                  <th className="pb-2 pr-4 font-medium">PnL</th>
                  <th className="pb-2 pr-4 font-medium">PnL%</th>
                  <th className="pb-2 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {result.trades.slice(0, 100).map((t, i) => (
                  <tr key={i} className="hover:bg-slate-800/50">
                    <td className="py-2 pr-4 text-slate-300 max-w-[120px] truncate font-mono text-xs">{t.market_id.slice(0, 10)}…</td>
                    <td className="py-2 pr-4 text-slate-400 text-xs">{fmtDate(t.entry_date)}</td>
                    <td className="py-2 pr-4 text-slate-400 text-xs">{fmtDate(t.exit_date)}</td>
                    <td className="py-2 pr-4 text-slate-300">{fmt(t.entry_price, 3)}</td>
                    <td className="py-2 pr-4 text-slate-300">{fmt(t.exit_price, 3)}</td>
                    <td className="py-2 pr-4 text-slate-300">${fmt(t.stake)}</td>
                    <td className={`py-2 pr-4 font-medium ${t.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}</td>
                    <td className={`py-2 pr-4 font-medium ${t.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>{t.pnl_pct >= 0 ? "+" : ""}{fmt(t.pnl_pct)}%</td>
                    <td className="py-2">
                      <span className={`badge ${t.exit_reason === "take_profit" ? "bg-green-900/50 text-green-400" : t.exit_reason === "stop_loss" ? "bg-red-900/50 text-red-400" : "bg-slate-700 text-slate-400"}`}>
                        {t.exit_reason.replace("_", " ")}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.trades.length > 100 && (
              <p className="text-slate-500 text-xs mt-2 text-center">
                Showing first 100 of {result.trades.length} trades. Download CSV for full list.
              </p>
            )}
          </div>
        </div>
      )}

      {result.trades.length === 0 && (
        <div className="card text-center text-slate-400 py-8">
          No trades were executed. Try different strategy parameters or sync more market data.
        </div>
      )}
    </div>
  );
}

function ComparisonTable({ results }: { results: Result[] }) {
  const done = results.filter((r) => r.status === "done" && r.metrics);
  if (done.length < 2) return null;
  const rows: { label: string; render: (m: Metrics) => string; cls?: (m: Metrics) => string }[] = [
    { label: "Total PnL", render: (m) => `${m.total_pnl >= 0 ? "+" : ""}$${fmt(m.total_pnl)}`, cls: (m) => (m.total_pnl >= 0 ? "text-green-400" : "text-red-400") },
    { label: "ROI", render: (m) => `${m.roi_pct >= 0 ? "+" : ""}${fmt(m.roi_pct)}%`, cls: (m) => (m.roi_pct >= 0 ? "text-green-400" : "text-red-400") },
    { label: "Max Drawdown", render: (m) => `${fmt(m.max_drawdown_pct)}%`, cls: () => "text-red-400" },
    { label: "Win Rate", render: (m) => `${fmt(m.win_rate_pct)}%`, cls: (m) => (m.win_rate_pct >= 50 ? "text-green-400" : "text-yellow-400") },
    { label: "Total Trades", render: (m) => String(m.total_trades), cls: () => "text-slate-100" },
    { label: "Sharpe Ratio", render: (m) => (m.sharpe_ratio != null ? fmt(m.sharpe_ratio) : "N/A"), cls: (m) => (m.sharpe_ratio != null && m.sharpe_ratio >= 1 ? "text-green-400" : "text-slate-400") },
  ];
  return (
    <div className="card">
      <h2 className="font-semibold text-slate-200 mb-4">Strategy Comparison</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-400 border-b border-slate-700">
              <th className="pb-2 pr-4 font-medium">Metric</th>
              {done.map((r) => (
                <th key={r.run_id} className="pb-2 pr-4 font-medium">{r.strategy_name}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="py-2 pr-4 text-slate-400">{row.label}</td>
                {done.map((r) => (
                  <td key={r.run_id} className={`py-2 pr-4 ${row.cls?.(r.metrics!) ?? ""}`}>{row.render(r.metrics!)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResultsContent() {
  const searchParams = useSearchParams();
  const ids = (searchParams.get("id") || "").split(",").filter(Boolean);
  const idKey = ids.join(",");
  const [results, setResults] = useState<Result[]>([]);
  const [polling, setPolling] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchResults = useCallback(async () => {
    if (ids.length === 0) { setError("No run ID provided"); setPolling(false); return; }
    try {
      const fetched = await Promise.all(
        ids.map(async (id) => {
          const resp = await fetch(`${API}/results/${id}`);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          return resp.json() as Promise<Result>;
        })
      );
      setResults(fetched);
      if (fetched.every((r) => r.status === "done" || r.status === "failed")) setPolling(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error fetching results");
      setPolling(false);
    }
  }, [idKey]);

  useEffect(() => { fetchResults(); }, [fetchResults]);

  useEffect(() => {
    if (!polling) return;
    const interval = setInterval(fetchResults, 1500);
    return () => clearInterval(interval);
  }, [polling, fetchResults]);

  if (error) {
    return (
      <div className="card border-red-500/50 text-red-400 max-w-lg mx-auto">
        <p className="font-medium mb-2">Error loading results</p>
        <p className="text-sm">{error}</p>
        <Link href="/" className="btn-secondary mt-4 inline-block">Back to Dashboard</Link>
      </div>
    );
  }

  const allTerminal = results.length > 0 && results.every((r) => r.status === "done" || r.status === "failed");
  if (!allTerminal) {
    const running = results.filter((r) => r.status === "running").length;
    const progress = running > 0 ? `Running ${running} backtest${running !== 1 ? "s" : ""}...` : "Queued...";
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4 text-slate-400">
        <svg className="animate-spin h-10 w-10 text-blue-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        <div className="text-lg font-medium">{progress}</div>
        <div className="text-sm">Runs: <code className="text-blue-400">{ids.join(", ")}</code></div>
      </div>
    );
  }

  const failed = results.filter((r) => r.status === "failed");
  if (failed.length === results.length) {
    return (
      <div className="max-w-lg mx-auto space-y-4">
        <div className="card border-red-500/50">
          <p className="text-red-400 font-semibold mb-1">All Backtests Failed</p>
          <p className="text-sm text-slate-400">{failed[0].error || "Unknown error"}</p>
        </div>
        <Link href="/strategy" className="btn-secondary inline-block">Try Again</Link>
      </div>
    );
  }

  const header = (
    <div className="flex items-start justify-between">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">
          {results.length > 1 ? "Backtest Results" : results[0]?.strategy_name}
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          {results.length > 1
            ? `${results.length} strategies · ${results.filter((r) => r.status === "done").length} completed`
            : `Run <code className="text-blue-400">${results[0]?.run_id.slice(0, 8)}</code>`}
        </p>
      </div>
      <div className="flex gap-2">
        {results.filter((r) => r.status === "done").length > 1 && (
          <Link href="/strategy" className="btn-secondary text-sm">New Backtest</Link>
        )}
        <Link href="/strategy" className="btn-primary text-sm">New Backtest</Link>
      </div>
    </div>
  );

  return (
    <div className="space-y-8">
      {header}
      {failed.length > 0 && (
        <div className="card border-amber-500/50">
          <p className="text-amber-400 font-medium mb-1">
            {failed.length} backtest{failed.length !== 1 ? "s" : ""} failed
          </p>
          {failed.map((r) => (
            <p key={r.run_id} className="text-sm text-slate-400">
              <span className="text-slate-200">{r.strategy_name}:</span> {r.error || "Unknown error"}
            </p>
          ))}
        </div>
      )}
      <ComparisonTable results={results} />
      {results.map((r, i) => (
        <div key={r.run_id} className={results.length > 1 ? "border-t border-slate-800 pt-8" : ""}>
          {i > 0 && <h2 className="text-lg font-semibold text-slate-200 mb-4">{r.strategy_name}</h2>}
          {r.status === "failed" ? (
            <div className="card border-red-500/50 text-sm text-red-400">{r.error || "Backtest failed"}</div>
          ) : (
            <RunDetail result={r} />
          )}
        </div>
      ))}
    </div>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-20 text-slate-400">Loading...</div>}>
      <ResultsContent />
    </Suspense>
  );
}
