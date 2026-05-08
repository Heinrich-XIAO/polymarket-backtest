"use client";

import { useEffect, useState, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface RunMetrics {
  total_pnl: number;
  roi_pct: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  total_trades: number;
  winning_trades: number;
  avg_hold_days: number;
  sharpe_ratio: number | null;
}

interface SweepRun {
  run_id: string;
  name: string;
  params: {
    entry_condition: string;
    lookback_days: number;
    take_profit: number;
    stop_loss: number;
    min_volume: number;
    max_days_to_resolution: number;
    stake_pct: number;
    categories: string[];
  };
  status: string;
  metrics: RunMetrics | null;
  error: string | null;
}

interface SweepResult {
  sweep_id: string;
  name: string;
  status: string;
  total_runs: number;
  done_runs: number;
  created_at: string;
  completed_at: string | null;
  runs: SweepRun[];
}

type SortKey = "roi_pct" | "sharpe_ratio" | "win_rate_pct" | "total_trades";

function fmt(n: number, d = 2) {
  return n.toFixed(d);
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "done"
      ? "bg-green-900/50 text-green-400"
      : status === "running"
      ? "bg-blue-900/50 text-blue-400"
      : status === "failed"
      ? "bg-red-900/50 text-red-400"
      : "bg-slate-700 text-slate-400";
  return (
    <span className={`badge ${cls}`}>
      {status === "running" && (
        <svg className="animate-spin h-2.5 w-2.5 mr-1 inline" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
      )}
      {status}
    </span>
  );
}

function SweepContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const id = searchParams.get("id");

  const [sweep, setSweep] = useState<SweepResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortKey>("roi_pct");

  const fetchSweep = useCallback(async () => {
    if (!id) {
      setError("No sweep ID provided");
      return;
    }
    try {
      const resp = await fetch(`${API}/sweep/${id}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSweep(await resp.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error loading sweep");
    }
  }, [id]);

  useEffect(() => {
    fetchSweep();
  }, [fetchSweep]);

  useEffect(() => {
    if (!sweep || sweep.status === "done" || sweep.status === "failed") return;
    const interval = setInterval(fetchSweep, 2000);
    return () => clearInterval(interval);
  }, [sweep, fetchSweep]);

  if (error) {
    return (
      <div className="card border-red-500/50 text-red-400 max-w-lg mx-auto">
        <p className="font-medium mb-2">Error</p>
        <p className="text-sm">{error}</p>
        <Link href="/sweep" className="btn-secondary mt-4 inline-block">
          New Sweep
        </Link>
      </div>
    );
  }

  if (!sweep) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400 gap-3">
        <svg className="animate-spin h-8 w-8 text-blue-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        Loading sweep…
      </div>
    );
  }

  const progress =
    sweep.total_runs > 0 ? (sweep.done_runs / sweep.total_runs) * 100 : 0;

  const sortedRuns = [...sweep.runs].sort((a, b) => {
    const av = a.metrics?.[sortBy] ?? -999999;
    const bv = b.metrics?.[sortBy] ?? -999999;
    return (bv as number) - (av as number);
  });

  const doneCount = sweep.runs.filter((r) => r.status === "done").length;
  const bestRun = sortedRuns.find((r) => r.metrics != null);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">{sweep.name}</h1>
          <p className="text-slate-400 text-sm mt-1">
            {sweep.done_runs} / {sweep.total_runs} combinations completed
            {sweep.status === "done" && " · Finished"}
            {sweep.status === "failed" && " · Failed"}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/sweep" className="btn-secondary text-sm">
            New Sweep
          </Link>
        </div>
      </div>

      {/* Progress bar */}
      {sweep.status === "running" && (
        <div className="card">
          <div className="flex justify-between text-sm text-slate-400 mb-2">
            <span className="flex items-center gap-2">
              <svg className="animate-spin h-4 w-4 text-blue-400" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              Running sweep…
            </span>
            <span>{Math.round(progress)}%</span>
          </div>
          <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 transition-all duration-500 rounded-full"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Best result highlight */}
      {bestRun?.metrics && (
        <div className="card border-green-500/30 bg-green-500/5">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold text-green-400 uppercase tracking-wide">
              Best so far
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-slate-500 text-xs">ROI</div>
              <div className={`font-bold text-lg ${bestRun.metrics.roi_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                {bestRun.metrics.roi_pct >= 0 ? "+" : ""}
                {fmt(bestRun.metrics.roi_pct)}%
              </div>
            </div>
            <div>
              <div className="text-slate-500 text-xs">Win Rate</div>
              <div className="font-semibold text-slate-200">{fmt(bestRun.metrics.win_rate_pct)}%</div>
            </div>
            <div>
              <div className="text-slate-500 text-xs">Sharpe</div>
              <div className="font-semibold text-slate-200">
                {bestRun.metrics.sharpe_ratio != null ? fmt(bestRun.metrics.sharpe_ratio) : "—"}
              </div>
            </div>
            <div>
              <div className="text-slate-500 text-xs">Trades</div>
              <div className="font-semibold text-slate-200">{bestRun.metrics.total_trades}</div>
            </div>
          </div>
          <div className="mt-3 text-xs font-mono text-slate-400 truncate">
            {bestRun.params.entry_condition}
            {" · "}TP {(bestRun.params.take_profit * 100).toFixed(0)}%
            {" · "}SL {(bestRun.params.stop_loss * 100).toFixed(0)}%
            {" · "}LB {bestRun.params.lookback_days}d
            {" · "}MinVol ${bestRun.params.min_volume.toLocaleString()}
          </div>
          <button
            onClick={() => router.push(`/results?id=${bestRun.run_id}`)}
            className="btn-primary text-xs mt-3"
          >
            Open Full Results →
          </button>
        </div>
      )}

      {/* Sort controls */}
      {sortedRuns.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-slate-400">Sort by:</span>
          {(
            [
              ["roi_pct", "ROI"],
              ["sharpe_ratio", "Sharpe"],
              ["win_rate_pct", "Win Rate"],
              ["total_trades", "Trades"],
            ] as [SortKey, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setSortBy(key)}
              className={`px-3 py-1 rounded border text-xs transition-colors ${
                sortBy === key
                  ? "border-blue-500 text-blue-300 bg-blue-500/10"
                  : "border-slate-700 text-slate-400 hover:border-slate-500"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Leaderboard */}
      <div className="card overflow-x-auto">
        <h2 className="font-semibold text-slate-200 mb-4">
          Leaderboard{" "}
          <span className="text-slate-500 font-normal text-sm">
            ({doneCount} / {sweep.total_runs} done)
          </span>
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-700 text-xs">
              <th className="pb-2 pr-2 font-medium">#</th>
              <th className="pb-2 pr-3 font-medium">Entry Condition</th>
              <th className="pb-2 pr-2 font-medium">TP</th>
              <th className="pb-2 pr-2 font-medium">SL</th>
              <th className="pb-2 pr-2 font-medium">LB</th>
              <th className="pb-2 pr-2 font-medium">MinVol</th>
              <th className="pb-2 pr-2 font-medium">Stk</th>
              <th className="pb-2 pr-2 font-medium">Trades</th>
              <th className="pb-2 pr-2 font-medium">ROI%</th>
              <th className="pb-2 pr-2 font-medium">Win%</th>
              <th className="pb-2 pr-2 font-medium">Sharpe</th>
              <th className="pb-2 pr-2 font-medium">MaxDD</th>
              <th className="pb-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {sortedRuns.map((run, i) => {
              const m = run.metrics;
              const isClickable = run.status === "done";
              return (
                <tr
                  key={run.run_id}
                  className={`transition-colors ${
                    isClickable
                      ? "hover:bg-slate-800/60 cursor-pointer"
                      : ""
                  }`}
                  onClick={() => isClickable && router.push(`/results?id=${run.run_id}`)}
                  title={isClickable ? "Click to open full results" : undefined}
                >
                  <td className="py-2 pr-2 text-slate-500 text-xs">{i + 1}</td>
                  <td className="py-2 pr-3 font-mono text-xs text-slate-300 max-w-[180px]">
                    <span className="truncate block" title={run.params.entry_condition}>
                      {run.params.entry_condition || "—"}
                    </span>
                  </td>
                  <td className="py-2 pr-2 text-slate-300 text-xs">
                    {(run.params.take_profit * 100).toFixed(0)}%
                  </td>
                  <td className="py-2 pr-2 text-slate-300 text-xs">
                    {(run.params.stop_loss * 100).toFixed(0)}%
                  </td>
                  <td className="py-2 pr-2 text-slate-300 text-xs">{run.params.lookback_days}d</td>
                  <td className="py-2 pr-2 text-slate-300 text-xs">
                    {run.params.min_volume >= 1000
                      ? `${(run.params.min_volume / 1000).toFixed(0)}k`
                      : run.params.min_volume}
                  </td>
                  <td className="py-2 pr-2 text-slate-300 text-xs">
                    {(run.params.stake_pct * 100).toFixed(0)}%
                  </td>

                  {run.status === "pending" ? (
                    <td colSpan={5} className="py-2 pr-2 text-slate-600 text-xs">queued</td>
                  ) : run.status === "running" ? (
                    <td colSpan={5} className="py-2 pr-2 text-blue-400 text-xs">
                      <svg className="animate-spin h-3 w-3 inline mr-1" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                      running
                    </td>
                  ) : run.status === "failed" ? (
                    <td colSpan={5} className="py-2 pr-2 text-red-400 text-xs" title={run.error || ""}>
                      {(run.error || "failed").slice(0, 40)}
                    </td>
                  ) : m ? (
                    <>
                      <td className="py-2 pr-2 text-slate-300 text-xs">{m.total_trades}</td>
                      <td
                        className={`py-2 pr-2 font-semibold text-xs ${
                          m.roi_pct >= 0 ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        {m.roi_pct >= 0 ? "+" : ""}
                        {fmt(m.roi_pct)}%
                      </td>
                      <td
                        className={`py-2 pr-2 text-xs ${
                          m.win_rate_pct >= 50 ? "text-green-400" : "text-yellow-400"
                        }`}
                      >
                        {fmt(m.win_rate_pct)}%
                      </td>
                      <td
                        className={`py-2 pr-2 text-xs ${
                          m.sharpe_ratio != null && m.sharpe_ratio >= 1
                            ? "text-green-400"
                            : "text-slate-300"
                        }`}
                      >
                        {m.sharpe_ratio != null ? fmt(m.sharpe_ratio) : "—"}
                      </td>
                      <td className="py-2 pr-2 text-red-400 text-xs">{fmt(m.max_drawdown_pct)}%</td>
                    </>
                  ) : (
                    <td colSpan={5} className="py-2 text-slate-600 text-xs">—</td>
                  )}

                  <td className="py-2">
                    <StatusBadge status={run.status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {sortedRuns.length === 0 && (
          <p className="text-slate-500 text-sm text-center py-8">No runs yet…</p>
        )}
      </div>

      <p className="text-slate-600 text-xs text-center">
        Click any completed row to open the full results page (equity curve + trades)
      </p>
    </div>
  );
}

export default function SweepResultsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20 text-slate-400">Loading…</div>
      }
    >
      <SweepContent />
    </Suspense>
  );
}
