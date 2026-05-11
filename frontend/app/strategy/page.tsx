"use client";

import { useEffect, useRef, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { z } from "zod";
import { API, fetchWithRetry } from "../lib/api";

const RunSchema = z.object({
  run_id: z.string(),
});

interface Strategy {
  file: string;
  name: string;
  description: string;
  entry_condition: string;
  take_profit: number | null;
  stop_loss: number | null;
  categories: string[];
}

interface MarketResult {
  id: string;
  question: string;
  category: string | null;
  volume: number;
  active: boolean;
}

function StrategyForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const preselect = searchParams.get("name") || "";

  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selected, setSelected] = useState(preselect);
  const [capital, setCapital] = useState("1000");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [warmingUp, setWarmingUp] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Market selection state
  const [marketQuery, setMarketQuery] = useState("");
  const [marketResults, setMarketResults] = useState<MarketResult[]>([]);
  const [selectedMarkets, setSelectedMarkets] = useState<MarketResult[]>([]);
  const [marketSearching, setMarketSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/strategies`)
      .then((r) => r.json())
      .then((data) => {
        setStrategies(Array.isArray(data) ? data : []);
        if (!selected && data.length > 0) setSelected(data[0].file);
      })
      .catch(() => setError("Failed to load strategies from backend."));
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  // Debounced market search
  useEffect(() => {
    if (searchDebounce.current) clearTimeout(searchDebounce.current);
    if (marketQuery.trim().length < 2) {
      setMarketResults([]);
      setShowDropdown(false);
      return;
    }
    searchDebounce.current = setTimeout(async () => {
      setMarketSearching(true);
      try {
        const resp = await fetch(
          `${API}/markets?query=${encodeURIComponent(marketQuery.trim())}&limit=20&active_only=false`
        );
        const data = await resp.json();
        setMarketResults(Array.isArray(data.items) ? data.items : []);
        setShowDropdown(true);
      } catch {
        setMarketResults([]);
      } finally {
        setMarketSearching(false);
      }
    }, 350);
  }, [marketQuery]);

  function addMarket(m: MarketResult) {
    if (!selectedMarkets.find((s) => s.id === m.id)) {
      setSelectedMarkets((prev) => [...prev, m]);
    }
    setMarketQuery("");
    setShowDropdown(false);
  }

  function removeMarket(id: string) {
    setSelectedMarkets((prev) => prev.filter((m) => m.id !== id));
  }

  const selectedStrategy = strategies.find((s) => s.file === selected);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const capitalNum = parseFloat(capital);
    if (isNaN(capitalNum) || capitalNum <= 0) {
      setError("Initial capital must be a positive number.");
      setLoading(false);
      return;
    }

    const body: Record<string, unknown> = {
      strategy_name: selected,
      initial_capital: capitalNum,
    };
    if (startDate) body.start_date = new Date(startDate).toISOString();
    if (endDate) body.end_date = new Date(endDate).toISOString();
    if (selectedMarkets.length > 0) {
      body.market_ids = selectedMarkets.map((m) => m.id);
    }

    try {
      const resp = await fetchWithRetry(
        `${API}/backtest/run`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
        { onWarmingUp: () => setWarmingUp(true) }
      );
      setWarmingUp(false);

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const data = RunSchema.parse(await resp.json());
      router.push(`/results?id=${data.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setWarmingUp(false);
      setLoading(false);
    }
  }

  // Filtered dropdown: exclude already selected
  const filteredResults = marketResults.filter(
    (m) => !selectedMarkets.find((s) => s.id === m.id)
  );

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Run a Backtest</h1>
        <p className="text-slate-400 mt-1">
          Select a strategy, set parameters, and launch the backtest engine.
        </p>
      </div>

      {error && (
        <div className="card border-red-500/50 text-red-400 text-sm">{error}</div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Strategy Selector */}
        <div className="card space-y-4">
          <h2 className="font-semibold text-slate-200">Strategy</h2>
          <div className="grid grid-cols-1 gap-2">
            {strategies.map((s) => (
              <label
                key={s.file}
                className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                  selected === s.file
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-slate-700 hover:border-slate-500"
                }`}
              >
                <input
                  type="radio"
                  name="strategy"
                  value={s.file}
                  checked={selected === s.file}
                  onChange={() => setSelected(s.file)}
                  className="mt-0.5 accent-blue-500"
                />
                <div>
                  <div className="font-medium text-slate-200 text-sm">{s.name}</div>
                  <div className="text-xs text-slate-400">{s.description}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        {/* Selected strategy details */}
        {selectedStrategy && (
          <div className="card bg-slate-900/50 text-sm space-y-2">
            <div className="flex gap-4 text-slate-400">
              <span>
                Entry: <code className="text-green-400">{selectedStrategy.entry_condition}</code>
              </span>
            </div>
            <div className="flex gap-4 text-slate-400">
              {selectedStrategy.take_profit != null && (
                <span>Take Profit: <span className="text-green-400">{(selectedStrategy.take_profit * 100).toFixed(0)}%</span></span>
              )}
              {selectedStrategy.stop_loss != null && (
                <span>Stop Loss: <span className="text-red-400">{(selectedStrategy.stop_loss * 100).toFixed(0)}%</span></span>
              )}
            </div>
            {selectedStrategy.categories.length > 0 && (
              <div className="flex gap-1">
                {selectedStrategy.categories.map((c) => (
                  <span key={c} className="badge bg-slate-700 text-slate-300">{c}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Parameters */}
        <div className="card space-y-4">
          <h2 className="font-semibold text-slate-200">Parameters</h2>
          <div>
            <label className="block text-sm text-slate-400 mb-1">Initial Capital ($)</label>
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              min="1"
              step="100"
              className="input w-full"
              placeholder="1000"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-slate-400 mb-1">Start Date (optional)</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="input w-full"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">End Date (optional)</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="input w-full"
              />
            </div>
          </div>
        </div>

        {/* Market Selection */}
        <div className="card space-y-3">
          <div>
            <h2 className="font-semibold text-slate-200">Market Selection</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Optional — leave empty to run across all markets matching the strategy filters.
              Select specific markets to narrow the universe for a more precise test.
            </p>
          </div>

          {/* Selected chips */}
          {selectedMarkets.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {selectedMarkets.map((m) => (
                <span
                  key={m.id}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-blue-500/15 border border-blue-500/40 text-blue-300 text-xs max-w-xs"
                >
                  <span className="truncate" title={m.question}>{m.question}</span>
                  <button
                    type="button"
                    onClick={() => removeMarket(m.id)}
                    className="ml-0.5 text-blue-400 hover:text-red-400 transition-colors flex-shrink-0"
                    aria-label="Remove"
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Search input + dropdown */}
          <div className="relative" ref={dropdownRef}>
            <div className="relative">
              <input
                type="text"
                value={marketQuery}
                onChange={(e) => setMarketQuery(e.target.value)}
                onFocus={() => filteredResults.length > 0 && setShowDropdown(true)}
                placeholder="Search markets by keyword…"
                className="input w-full pr-8"
              />
              {marketSearching && (
                <svg
                  className="animate-spin h-4 w-4 text-slate-400 absolute right-2 top-1/2 -translate-y-1/2"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              )}
            </div>

            {showDropdown && filteredResults.length > 0 && (
              <div className="absolute z-20 mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden max-h-64 overflow-y-auto">
                {filteredResults.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => addMarket(m)}
                    className="w-full text-left px-3 py-2.5 hover:bg-slate-700 transition-colors border-b border-slate-700/50 last:border-0"
                  >
                    <div className="text-sm text-slate-200 leading-snug">{m.question}</div>
                    <div className="flex gap-2 mt-0.5 text-xs text-slate-500">
                      {m.category && (
                        <span className="badge bg-slate-700 text-slate-400 text-[10px]">{m.category}</span>
                      )}
                      <span>vol ${m.volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                      {!m.active && <span className="text-amber-500">resolved</span>}
                    </div>
                  </button>
                ))}
              </div>
            )}

            {showDropdown && !marketSearching && marketQuery.trim().length >= 2 && filteredResults.length === 0 && (
              <div className="absolute z-20 mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-3 text-sm text-slate-400">
                No markets found for &ldquo;{marketQuery}&rdquo;
              </div>
            )}
          </div>

          {selectedMarkets.length > 0 && (
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>{selectedMarkets.length} market{selectedMarkets.length !== 1 ? "s" : ""} selected</span>
              <button
                type="button"
                onClick={() => setSelectedMarkets([])}
                className="text-slate-500 hover:text-red-400 transition-colors"
              >
                Clear all
              </button>
            </div>
          )}
        </div>

        <button type="submit" disabled={loading || !selected} className="btn-primary w-full text-center">
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              {warmingUp ? "Backend warming up…" : "Starting backtest…"}
            </span>
          ) : (
            selectedMarkets.length > 0
              ? `Run Backtest on ${selectedMarkets.length} market${selectedMarkets.length !== 1 ? "s" : ""}`
              : "Run Backtest"
          )}
        </button>
      </form>
    </div>
  );
}

export default function StrategyPage() {
  return (
    <Suspense fallback={<div className="text-slate-400">Loading...</div>}>
      <StrategyForm />
    </Suspense>
  );
}
