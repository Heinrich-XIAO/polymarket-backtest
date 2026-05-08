"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Preset {
  file: string;
  name: string;
  entry_condition: string;
  take_profit: number | null;
  stop_loss: number | null;
  min_volume: number | null;
}

function ChipInput({
  chips,
  onAdd,
  onRemove,
  placeholder,
}: {
  chips: string[];
  onAdd: (v: string) => void;
  onRemove: (i: number) => void;
  placeholder: string;
}) {
  const [input, setInput] = useState("");

  function commit() {
    const v = input.trim().replace(/,\s*$/, "");
    if (v && !chips.includes(v)) {
      onAdd(v);
      setInput("");
    }
  }

  return (
    <div className="flex flex-wrap gap-1 p-2 bg-slate-800 border border-slate-700 rounded-lg min-h-[42px]">
      {chips.map((c, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-900/60 text-blue-200 rounded text-xs font-mono"
        >
          {c}
          <button
            type="button"
            onClick={() => onRemove(i)}
            className="text-blue-400 hover:text-red-400 leading-none ml-0.5"
          >
            ×
          </button>
        </span>
      ))}
      <input
        className="bg-transparent text-slate-200 text-sm outline-none flex-1 min-w-[100px] placeholder-slate-600"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
        placeholder={chips.length === 0 ? placeholder : "Add more…"}
      />
    </div>
  );
}

function SweepRow({
  label,
  hint,
  active,
  onToggle,
  children,
}: {
  label: string;
  hint?: string;
  active: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`p-3 rounded-lg border transition-colors ${
        active ? "border-blue-500/50 bg-blue-500/5" : "border-slate-700"
      }`}
    >
      <div className="flex items-center gap-3 mb-2">
        <button
          type="button"
          onClick={onToggle}
          className={`w-5 h-5 rounded border flex items-center justify-center flex-shrink-0 transition-colors ${
            active ? "bg-blue-500 border-blue-500" : "border-slate-600 hover:border-slate-400"
          }`}
        >
          {active && (
            <svg
              className="w-3 h-3 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={3}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
        </button>
        <div>
          <span className={`text-sm font-medium ${active ? "text-slate-100" : "text-slate-400"}`}>
            {label}
          </span>
          {hint && <span className="text-xs text-slate-500 ml-2">{hint}</span>}
        </div>
      </div>
      {active && <div className="mt-1">{children}</div>}
    </div>
  );
}

function SweepBuilder() {
  const router = useRouter();
  const [presets, setPresets] = useState<Preset[]>([]);

  const [sweepName, setSweepName] = useState("My Sweep");
  const [entryCondition, setEntryCondition] = useState("price_drop_pct > 0.08");
  const [lookbackDays, setLookbackDays] = useState("7");
  const [takeProfit, setTakeProfit] = useState("0.20");
  const [stopLoss, setStopLoss] = useState("0.10");
  const [minVolume, setMinVolume] = useState("1000");
  const [maxDays, setMaxDays] = useState("9999");
  const [stakePct, setStakePct] = useState("0.05");
  const [capital, setCapital] = useState("1000");

  const [swEntryConditions, setSwEntryConditions] = useState<string[]>([]);
  const [swLookbackDays, setSwLookbackDays] = useState<string[]>([]);
  const [swTakeProfit, setSwTakeProfit] = useState<string[]>([]);
  const [swStopLoss, setSwStopLoss] = useState<string[]>([]);
  const [swMinVolume, setSwMinVolume] = useState<string[]>([]);
  const [swMaxDays, setSwMaxDays] = useState<string[]>([]);
  const [swStakePct, setSwStakePct] = useState<string[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/strategies`)
      .then((r) => r.json())
      .then((d) => setPresets(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  function loadPreset(p: Preset) {
    setEntryCondition(p.entry_condition || "price_drop_pct > 0.08");
    if (p.take_profit != null) setTakeProfit(String(p.take_profit));
    if (p.stop_loss != null) setStopLoss(String(p.stop_loss));
    if (p.min_volume != null) setMinVolume(String(p.min_volume));
  }

  const axes = [
    swEntryConditions.length,
    swLookbackDays.length,
    swTakeProfit.length,
    swStopLoss.length,
    swMinVolume.length,
    swMaxDays.length,
    swStakePct.length,
  ];
  const rawCombos = axes.reduce((acc, n) => acc * Math.max(1, n), 1);
  const combos = Math.min(rawCombos, 50);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const body = {
      name: sweepName,
      base_config: {
        name: sweepName,
        entry: {
          condition: entryCondition,
          lookback_days: parseInt(lookbackDays) || 7,
        },
        exit: {
          take_profit: parseFloat(takeProfit) || 0.2,
          stop_loss: parseFloat(stopLoss) || 0.1,
        },
        filters: {
          min_volume: parseFloat(minVolume) || 1000,
          categories: [],
          max_days_to_resolution: parseInt(maxDays) || 9999,
        },
        initial_capital: parseFloat(capital) || 1000,
        stake_pct: parseFloat(stakePct) || 0.05,
        description: "",
      },
      entry_conditions: swEntryConditions,
      lookback_days: swLookbackDays.map(Number).filter((n) => !isNaN(n)),
      take_profit: swTakeProfit.map(Number).filter((n) => !isNaN(n)),
      stop_loss: swStopLoss.map(Number).filter((n) => !isNaN(n)),
      min_volume: swMinVolume.map(Number).filter((n) => !isNaN(n)),
      max_days_to_resolution: swMaxDays.map(Number).filter((n) => !isNaN(n)),
      stake_pct: swStakePct.map(Number).filter((n) => !isNaN(n)),
      initial_capital: parseFloat(capital) || 1000,
      max_combinations: 50,
    };

    try {
      const resp = await fetch(`${API}/backtest/sweep`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const { sweep_id } = await resp.json();
      router.push(`/sweep/results?id=${sweep_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setLoading(false);
    }
  }

  function makeAdder(setter: React.Dispatch<React.SetStateAction<string[]>>) {
    return (v: string) => setter((prev) => (prev.includes(v) ? prev : [...prev, v]));
  }
  function makeRemover(setter: React.Dispatch<React.SetStateAction<string[]>>) {
    return (i: number) => setter((prev) => prev.filter((_, j) => j !== i));
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Parameter Sweep</h1>
        <p className="text-slate-400 mt-1">
          Test multiple strategy configurations at once. The engine runs all combinations and
          returns a ranked leaderboard sorted by ROI.
        </p>
      </div>

      {error && (
        <div className="card border-red-500/50 text-red-400 text-sm">{error}</div>
      )}

      {/* Meta */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-slate-200">Sweep Name</h2>
        <input
          className="input w-full"
          value={sweepName}
          onChange={(e) => setSweepName(e.target.value)}
          placeholder="My Sweep"
        />
        {presets.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 mb-2">Load base params from a preset:</p>
            <div className="flex flex-wrap gap-2">
              {presets.map((p) => (
                <button
                  key={p.file}
                  type="button"
                  onClick={() => loadPreset(p)}
                  className="text-xs px-3 py-1.5 rounded border border-slate-600 text-slate-300 hover:border-blue-500 hover:text-blue-300 transition-colors"
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Base params */}
      <div className="card space-y-4">
        <div>
          <h2 className="font-semibold text-slate-200">Base Parameters</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Used as the default when a parameter is not being swept
          </p>
        </div>
        <div>
          <label className="block text-sm text-slate-400 mb-1">Entry Condition</label>
          <input
            className="input w-full font-mono text-sm"
            value={entryCondition}
            onChange={(e) => setEntryCondition(e.target.value)}
          />
          <p className="text-xs text-slate-500 mt-1">
            Available:{" "}
            <code className="text-slate-400">price_drop_pct</code>,{" "}
            <code className="text-slate-400">price</code>,{" "}
            <code className="text-slate-400">volume</code>
          </p>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {[
            { label: "Lookback Days", val: lookbackDays, set: setLookbackDays, type: "number" },
            { label: "Take Profit (0–1)", val: takeProfit, set: setTakeProfit, type: "number", step: "0.01" },
            { label: "Stop Loss (0–1)", val: stopLoss, set: setStopLoss, type: "number", step: "0.01" },
            { label: "Min Volume ($)", val: minVolume, set: setMinVolume, type: "number" },
            { label: "Max Days to Resolution", val: maxDays, set: setMaxDays, type: "number" },
            { label: "Stake per Trade (0–1)", val: stakePct, set: setStakePct, type: "number", step: "0.01" },
          ].map(({ label, val, set, type, step }) => (
            <div key={label}>
              <label className="block text-sm text-slate-400 mb-1">{label}</label>
              <input
                type={type}
                step={step}
                className="input w-full"
                value={val}
                onChange={(e) => set(e.target.value)}
              />
            </div>
          ))}
        </div>
        <div>
          <label className="block text-sm text-slate-400 mb-1">Initial Capital ($)</label>
          <input
            type="number"
            className="input w-48"
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
          />
        </div>
      </div>

      {/* Sweep axes */}
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold text-slate-200">Sweep Axes</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Enable a param and type multiple values (press Enter or comma after each).
            The engine runs every combination.
          </p>
        </div>

        <SweepRow
          label="Entry Condition"
          hint="text — press Enter after each"
          active={swEntryConditions.length > 0}
          onToggle={() =>
            setSwEntryConditions((v) =>
              v.length ? [] : ["price_drop_pct > 0.05", "price_drop_pct > 0.10", "price_drop_pct > 0.15"]
            )
          }
        >
          <ChipInput
            chips={swEntryConditions}
            onAdd={makeAdder(setSwEntryConditions)}
            onRemove={makeRemover(setSwEntryConditions)}
            placeholder="e.g. price_drop_pct > 0.05"
          />
        </SweepRow>

        <SweepRow
          label="Lookback Days"
          hint="integers"
          active={swLookbackDays.length > 0}
          onToggle={() =>
            setSwLookbackDays((v) => (v.length ? [] : ["3", "7", "14"]))
          }
        >
          <ChipInput
            chips={swLookbackDays}
            onAdd={makeAdder(setSwLookbackDays)}
            onRemove={makeRemover(setSwLookbackDays)}
            placeholder="e.g. 7"
          />
        </SweepRow>

        <SweepRow
          label="Take Profit"
          hint="fractions (0–1)"
          active={swTakeProfit.length > 0}
          onToggle={() =>
            setSwTakeProfit((v) => (v.length ? [] : ["0.10", "0.20", "0.50", "0.85"]))
          }
        >
          <ChipInput
            chips={swTakeProfit}
            onAdd={makeAdder(setSwTakeProfit)}
            onRemove={makeRemover(setSwTakeProfit)}
            placeholder="e.g. 0.20"
          />
        </SweepRow>

        <SweepRow
          label="Stop Loss"
          hint="fractions (0–1)"
          active={swStopLoss.length > 0}
          onToggle={() =>
            setSwStopLoss((v) => (v.length ? [] : ["0.05", "0.10", "0.20"]))
          }
        >
          <ChipInput
            chips={swStopLoss}
            onAdd={makeAdder(setSwStopLoss)}
            onRemove={makeRemover(setSwStopLoss)}
            placeholder="e.g. 0.10"
          />
        </SweepRow>

        <SweepRow
          label="Min Volume ($)"
          hint="integers"
          active={swMinVolume.length > 0}
          onToggle={() =>
            setSwMinVolume((v) => (v.length ? [] : ["500", "1000", "5000", "10000"]))
          }
        >
          <ChipInput
            chips={swMinVolume}
            onAdd={makeAdder(setSwMinVolume)}
            onRemove={makeRemover(setSwMinVolume)}
            placeholder="e.g. 1000"
          />
        </SweepRow>

        <SweepRow
          label="Max Days to Resolution"
          hint="integers"
          active={swMaxDays.length > 0}
          onToggle={() =>
            setSwMaxDays((v) => (v.length ? [] : ["30", "60", "90", "180"]))
          }
        >
          <ChipInput
            chips={swMaxDays}
            onAdd={makeAdder(setSwMaxDays)}
            onRemove={makeRemover(setSwMaxDays)}
            placeholder="e.g. 30"
          />
        </SweepRow>

        <SweepRow
          label="Stake per Trade"
          hint="fractions (0–1)"
          active={swStakePct.length > 0}
          onToggle={() =>
            setSwStakePct((v) => (v.length ? [] : ["0.03", "0.05", "0.10"]))
          }
        >
          <ChipInput
            chips={swStakePct}
            onAdd={makeAdder(setSwStakePct)}
            onRemove={makeRemover(setSwStakePct)}
            placeholder="e.g. 0.05"
          />
        </SweepRow>
      </div>

      {/* Submit bar */}
      <div className="flex items-center justify-between pt-2">
        <div className="text-sm text-slate-400">
          <span className="text-slate-100 font-semibold">{combos}</span>{" "}
          combination{combos !== 1 ? "s" : ""}
          {rawCombos > 50 && (
            <span className="text-yellow-400 ml-2">(capped at 50 — reduce axes to run all)</span>
          )}
        </div>
        <button type="submit" disabled={loading} className="btn-primary">
          {loading
            ? "Launching…"
            : `Run ${combos} Combination${combos !== 1 ? "s" : ""} →`}
        </button>
      </div>
    </form>
  );
}

export default function SweepPage() {
  return (
    <Suspense fallback={<div className="text-slate-400">Loading…</div>}>
      <SweepBuilder />
    </Suspense>
  );
}
