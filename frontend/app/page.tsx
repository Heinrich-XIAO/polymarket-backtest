"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Health {
  status: string;
  market_count: number;
  price_points: number;
  version: string;
}

interface Strategy {
  file: string;
  name: string;
  description: string;
  entry_condition: string;
  take_profit: number | null;
  stop_loss: number | null;
  categories: string[];
  min_volume: number | null;
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value text-blue-400">{value}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function StrategyCard({ strategy }: { strategy: Strategy }) {
  return (
    <div className="card hover:border-blue-500/50 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-semibold text-slate-100">{strategy.name}</h3>
        <div className="flex gap-1 flex-wrap">
          {strategy.categories.slice(0, 3).map((c) => (
            <span key={c} className="badge bg-slate-700 text-slate-300">
              {c}
            </span>
          ))}
        </div>
      </div>
      <p className="text-sm text-slate-400 mb-3">{strategy.description || "No description."}</p>
      <div className="grid grid-cols-2 gap-2 text-xs text-slate-400 mb-4">
        <div>
          <span className="text-slate-500">Entry: </span>
          <code className="text-green-400 font-mono text-xs">{strategy.entry_condition}</code>
        </div>
        <div className="flex gap-3">
          {strategy.take_profit != null && (
            <span><span className="text-slate-500">TP</span> <span className="text-green-400">{(strategy.take_profit * 100).toFixed(0)}%</span></span>
          )}
          {strategy.stop_loss != null && (
            <span><span className="text-slate-500">SL</span> <span className="text-red-400">{(strategy.stop_loss * 100).toFixed(0)}%</span></span>
          )}
        </div>
      </div>
      <Link
        href={`/strategy?name=${strategy.file}`}
        className="btn-primary text-sm inline-block text-center w-full"
      >
        Run Backtest
      </Link>
    </div>
  );
}

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch(`${API}/health`).then((r) => r.json()).catch(() => null),
      fetch(`${API}/strategies`).then((r) => r.json()).catch(() => []),
    ]).then(([h, s]) => {
      setHealth(h);
      setStrategies(Array.isArray(s) ? s : []);
    }).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="text-center py-8">
        <h1 className="text-4xl font-bold text-slate-100 mb-3">
          Polymarket <span className="text-blue-400">Backtest</span>
        </h1>
        <p className="text-slate-400 text-lg max-w-2xl mx-auto">
          Test prediction-market trading strategies against historical Polymarket data.
          Realistic execution model with spread, slippage, and 2% commission.
        </p>
        <div className="flex items-center justify-center gap-4 mt-6">
          <Link href="/strategy" className="btn-primary">
            Run a Backtest
          </Link>
          <Link href="/sweep" className="btn-secondary">
            Parameter Sweep
          </Link>
          <a href={`${API}/docs`} target="_blank" className="btn-secondary">
            API Docs
          </a>
        </div>
      </div>

      {/* Stats */}
      {health && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Markets" value={health.market_count.toLocaleString()} sub="in database" />
          <StatCard label="Price Points" value={health.price_points.toLocaleString()} sub="historical candles" />
          <StatCard label="Strategies" value={String(strategies.length)} sub="YAML configs" />
          <StatCard
            label="API Status"
            value={health.status === "ok" ? "Online" : "Error"}
            sub={`v${health.version}`}
          />
        </div>
      )}

      {error && (
        <div className="card border-red-500/50 text-red-400 text-sm">
          Could not connect to backend: {error}. Make sure Docker is running.
        </div>
      )}

      {/* Strategies */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-slate-100">Available Strategies</h2>
          <span className="text-sm text-slate-400">{strategies.length} strategies</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {strategies.map((s) => (
            <StrategyCard key={s.file} strategy={s} />
          ))}
          {strategies.length === 0 && !error && (
            <div className="col-span-3 card text-center text-slate-400 py-8">
              No strategies loaded. Check that the backend is running.
            </div>
          )}
        </div>
      </div>

      {/* How it works */}
      <div className="card">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Execution Model</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-sm text-slate-400">
          <div>
            <div className="text-slate-200 font-medium mb-1">Spread</div>
            <code className="text-blue-300 text-xs">|P(yes) − (1 − P(no))|</code>
            <p className="mt-1">Half-spread added on buy, subtracted on sell.</p>
          </div>
          <div>
            <div className="text-slate-200 font-medium mb-1">Slippage</div>
            <code className="text-blue-300 text-xs">min(1%, 1000 / daily_volume)</code>
            <p className="mt-1">Decreases as volume increases.</p>
          </div>
          <div>
            <div className="text-slate-200 font-medium mb-1">Commission</div>
            <code className="text-blue-300 text-xs">2% per trade (Polymarket fee)</code>
            <p className="mt-1">Applied on top of execution price.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
