"""Pandas-based backtest engine for Polymarket prediction markets."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COMMISSION = 0.02
MIN_PRICE = 0.02
MAX_PRICE = 0.98


@dataclass
class StrategyParams:
    name: str
    entry_condition: str = "price_drop_pct > 0.08"
    lookback_days: int = 7
    take_profit: float = 0.12
    stop_loss: float = 0.07
    min_volume: float = 1000.0
    max_volume: float | None = None          # cap for low-volume arb strategies
    categories: list[str] = field(default_factory=list)
    max_days_to_resolution: int = 365
    min_days_to_resolution: int = 0          # floor for resolution_sniper
    initial_capital: float = 1000.0
    stake_pct: float = 0.05

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyParams":
        entry = d.get("entry", {})
        exit_ = d.get("exit", {})
        filters = d.get("filters", {})
        return cls(
            name=d.get("name", "Custom"),
            entry_condition=entry.get("condition", "price_drop_pct > 0.08"),
            lookback_days=int(entry.get("lookback_days", 7)),
            take_profit=float(exit_.get("take_profit", 0.12)),
            stop_loss=float(exit_.get("stop_loss", 0.07)),
            min_volume=float(filters.get("min_volume", 1000.0)),
            max_volume=float(filters["max_volume"]) if filters.get("max_volume") else None,
            categories=list(filters.get("categories", [])),
            max_days_to_resolution=int(filters.get("max_days_to_resolution", 30)),
            min_days_to_resolution=int(filters.get("min_days_to_resolution", 0)),
            initial_capital=float(d.get("initial_capital", 1000.0)),
            stake_pct=float(d.get("stake_pct", 0.05)),
        )


@dataclass
class TradeRecord:
    market_id: str
    entry_date: datetime
    exit_date: datetime
    entry_price: float
    exit_price: float
    position: str
    stake: float
    pnl: float
    pnl_pct: float
    hold_days: float
    exit_reason: str


def simulate_fill(price: float, side: str, daily_volume: float, spread: float | None = None) -> float:
    """
    Compute execution price approximating Polymarket CLOB.

    spread    ≈ |P(yes) − (1 − P(no))|  — gap between YES+NO and 1.0.
                Since we only have P(yes), default to 0.02 (typical 2-cent CLOB spread).
    slippage  = min(0.5%, 100 / daily_volume)
    commission = 2% per trade
    """
    if spread is None:
        spread = 0.02  # 2-cent bid-ask spread, typical for liquid Polymarket markets
    slippage = min(0.005, 100.0 / max(daily_volume, 1.0))
    if side == "BUY":
        exec_price = (price + spread / 2 + slippage) * (1 + COMMISSION)
    else:
        exec_price = (price - spread / 2 - slippage) * (1 - COMMISSION)
    return float(np.clip(exec_price, MIN_PRICE, MAX_PRICE))


def run_backtest(
    price_data: dict[str, pd.DataFrame],
    market_meta: dict[str, dict[str, Any]],
    params: StrategyParams,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Run strategy backtest over price_data.

    price_data:  {market_id -> DataFrame[timestamp, price_yes, volume]}
    market_meta: {market_id -> {category, end_date, volume}}
    Returns dict with metrics, equity_curve, trades.
    """
    capital = params.initial_capital
    equity_points: list[tuple[datetime, float]] = []
    trades: list[TradeRecord] = []

    all_dates: set = set()
    for df in price_data.values():
        if not df.empty:
            all_dates.update(df["timestamp"].dt.date.unique())

    if not all_dates:
        return _empty_result()

    sorted_dates = sorted(all_dates)
    if start_date:
        sorted_dates = [d for d in sorted_dates if d >= start_date.date()]
    if end_date:
        sorted_dates = [d for d in sorted_dates if d <= end_date.date()]
    if not sorted_dates:
        return _empty_result()

    open_positions: dict[str, dict[str, Any]] = {}

    for current_date in sorted_dates:
        for market_id, meta in market_meta.items():
            df = price_data.get(market_id)
            if df is None or df.empty:
                continue

            # Category filter
            if params.categories and meta.get("category") not in params.categories:
                continue

            # Days-to-resolution filters
            meta_end = meta.get("end_date")
            meta_end_date = None
            if meta_end:
                meta_end_date = meta_end.date() if hasattr(meta_end, "date") else meta_end
                days_left = (meta_end_date - current_date).days
                if days_left < 0:
                    continue
                if days_left > params.max_days_to_resolution:
                    continue
                if days_left < params.min_days_to_resolution:
                    continue

            current_rows = df[df["timestamp"].dt.date == current_date]

            # ── Manage open position ──────────────────────────────────────────
            if market_id in open_positions:
                if current_rows.empty:
                    continue
                pos = open_positions[market_id]
                current_price = float(current_rows.iloc[-1]["price_yes"])
                volume = float(current_rows["volume"].sum())
                exit_exec = simulate_fill(current_price, "SELL", volume)
                pnl_pct = (exit_exec - pos["entry_exec_price"]) / pos["entry_exec_price"]

                exit_reason = ""
                if pnl_pct >= params.take_profit:
                    exit_reason = "take_profit"
                elif pnl_pct <= -params.stop_loss:
                    exit_reason = "stop_loss"
                elif meta_end_date and current_date >= meta_end_date:
                    exit_reason = "resolution"

                if exit_reason:
                    stake = pos["stake"]
                    pnl = stake * pnl_pct
                    capital += stake + pnl
                    trades.append(TradeRecord(
                        market_id=market_id,
                        entry_date=pos["entry_date"],
                        exit_date=datetime.combine(current_date, datetime.min.time()),
                        entry_price=pos["entry_price"],
                        exit_price=current_price,
                        position="yes",
                        stake=round(stake, 4),
                        pnl=round(pnl, 4),
                        pnl_pct=round(pnl_pct * 100, 2),
                        hold_days=(current_date - pos["entry_date"].date()).days,
                        exit_reason=exit_reason,
                    ))
                    del open_positions[market_id]
                continue

            # ── Look for entry signal ─────────────────────────────────────────
            lb_start = current_date - timedelta(days=params.lookback_days)
            window = df[
                (df["timestamp"].dt.date >= lb_start) &
                (df["timestamp"].dt.date <= current_date)
            ]
            if len(window) < 2:
                continue

            current_price = float(window.iloc[-1]["price_yes"])
            past_price = float(window.iloc[0]["price_yes"])
            clob_vol = float(window["volume"].sum()) / max(len(window), 1)
            # Fall back to market total volume / 365 when CLOB returns no volume data
            daily_vol = clob_vol if clob_vol > 0 else meta.get("volume", 0) / 365.0

            # Volume filters
            if daily_vol < params.min_volume:
                continue
            if params.max_volume is not None and daily_vol > params.max_volume:
                continue
            if not (MIN_PRICE < current_price < MAX_PRICE):
                continue

            price_drop_pct = (past_price - current_price) / max(past_price, 0.001)

            try:
                signal = bool(eval(  # noqa: S307 — sandboxed strategy DSL
                    params.entry_condition,
                    {"__builtins__": {}},
                    {
                        "price_drop_pct": price_drop_pct,
                        "price": current_price,
                        "volume": daily_vol,
                    },
                ))
            except Exception as exc:
                logger.debug("Entry condition eval error: %s", exc)
                continue

            if not signal:
                continue

            stake = capital * params.stake_pct
            if stake < 1.0 or stake > capital:
                continue

            entry_exec = simulate_fill(current_price, "BUY", daily_vol)
            capital -= stake
            open_positions[market_id] = {
                "entry_date": datetime.combine(current_date, datetime.min.time()),
                "entry_price": current_price,
                "entry_exec_price": entry_exec,
                "stake": stake,
            }

        current_equity = capital + sum(p["stake"] for p in open_positions.values())
        equity_points.append((
            datetime.combine(current_date, datetime.min.time()),
            round(current_equity, 4),
        ))

    # Force-close remaining positions at last known price
    for market_id, pos in list(open_positions.items()):
        df = price_data.get(market_id)
        if df is not None and not df.empty:
            last_row = df.iloc[-1]
            last_price = float(last_row["price_yes"])
            last_vol = float(last_row["volume"])
            exit_exec = simulate_fill(last_price, "SELL", last_vol)
            pnl_pct = (exit_exec - pos["entry_exec_price"]) / pos["entry_exec_price"]
            pnl = pos["stake"] * pnl_pct
            capital += pos["stake"] + pnl
            last_dt = equity_points[-1][0] if equity_points else datetime.utcnow()
            trades.append(TradeRecord(
                market_id=market_id,
                entry_date=pos["entry_date"],
                exit_date=last_dt,
                entry_price=pos["entry_price"],
                exit_price=last_price,
                position="yes",
                stake=round(pos["stake"], 4),
                pnl=round(pnl, 4),
                pnl_pct=round(pnl_pct * 100, 2),
                hold_days=(last_dt.date() - pos["entry_date"].date()).days,
                exit_reason="end_of_period",
            ))

    return {
        "metrics": compute_metrics(trades, equity_points, params.initial_capital),
        "equity_curve": [
            {"timestamp": t.isoformat(), "equity": e} for t, e in equity_points
        ],
        "trades": [
            {
                "market_id": tr.market_id,
                "entry_date": tr.entry_date.isoformat(),
                "exit_date": tr.exit_date.isoformat(),
                "entry_price": tr.entry_price,
                "exit_price": tr.exit_price,
                "position": tr.position,
                "stake": tr.stake,
                "pnl": tr.pnl,
                "pnl_pct": tr.pnl_pct,
                "hold_days": tr.hold_days,
                "exit_reason": tr.exit_reason,
            }
            for tr in trades
        ],
    }


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: list[tuple[datetime, float]],
    initial_capital: float,
) -> dict[str, Any]:
    total_pnl = sum(t.pnl for t in trades)
    roi_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
    winning = [t for t in trades if t.pnl > 0]
    win_rate = (len(winning) / len(trades) * 100) if trades else 0.0
    avg_hold = (sum(t.hold_days for t in trades) / len(trades)) if trades else 0.0

    max_drawdown = 0.0
    sharpe = None
    if equity_curve:
        equities = np.array([e for _, e in equity_curve], dtype=float)
        peak = np.maximum.accumulate(equities)
        drawdowns = np.where(peak > 0, (equities - peak) / peak, 0.0)
        max_drawdown = float(drawdowns.min() * 100)
        if len(equities) > 1:
            daily_ret = np.diff(equities) / np.where(equities[:-1] > 0, equities[:-1], 1)
            if daily_ret.std() > 0:
                sharpe = round(float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)), 4)

    return {
        "total_pnl": round(total_pnl, 4),
        "roi_pct": round(roi_pct, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "win_rate_pct": round(win_rate, 2),
        "total_trades": len(trades),
        "winning_trades": len(winning),
        "avg_hold_days": round(avg_hold, 1),
        "sharpe_ratio": sharpe,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "metrics": {
            "total_pnl": 0.0,
            "roi_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate_pct": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "avg_hold_days": 0.0,
            "sharpe_ratio": None,
        },
        "equity_curve": [],
        "trades": [],
    }
