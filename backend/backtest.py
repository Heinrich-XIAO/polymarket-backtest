"""Pandas-based backtest engine for Polymarket prediction markets."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    max_days_to_resolution: int = 9999
    min_days_to_resolution: int = 0          # floor for resolution_sniper
    market_ids: list[str] = field(default_factory=list)  # explicit market whitelist
    initial_capital: float = 1000.0
    stake_pct: float = 0.05
    position_side: str = "yes"  # "yes" or "no" — trade YES or NO token

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
            max_days_to_resolution=int(filters.get("max_days_to_resolution", 9999)),
            min_days_to_resolution=int(filters.get("min_days_to_resolution", 0)),
            market_ids=list(d.get("market_ids", [])),
            initial_capital=float(d.get("initial_capital", 1000.0)),
            stake_pct=float(d.get("stake_pct", 0.05)),
            position_side=str(d.get("position_side", "yes")),
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


def _index_price_data(
    price_data: dict[str, pd.DataFrame],
) -> dict[str, tuple[list, dict]]:
    """Pre-index each market's price data for O(1) date lookups.

    Returns {market_id: (sorted_records, date_to_idx)} where
    sorted_records is list of (date, price_yes, volume) and
    date_to_idx maps date → index of last record for that date.
    """
    indexed: dict[str, tuple[list, dict]] = {}
    for mid, df in price_data.items():
        if df.empty:
            continue
        df2 = df.copy()
        df2["_date"] = df2["timestamp"].dt.date
        df2 = df2.sort_values("timestamp")
        records = list(zip(df2["_date"].tolist(), df2["price_yes"].tolist(), df2["volume"].tolist()))
        date_to_idx: dict = {}
        for i, (d, _, _) in enumerate(records):
            date_to_idx[d] = i  # last index for this date wins
        indexed[mid] = (records, date_to_idx)
    return indexed


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
    # Restrict universe to explicit market whitelist if provided
    if params.market_ids:
        allowed = set(params.market_ids)
        price_data = {k: v for k, v in price_data.items() if k in allowed}
        market_meta = {k: v for k, v in market_meta.items() if k in allowed}

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

    # Pre-index price data for fast date lookups
    indexed = _index_price_data(price_data)

    # First record date for each market (market age)
    market_first_date = {
        mid: recs[0][0]
        for mid, (recs, _) in indexed.items()
        if recs
    }

    # Daily category concentration (# of markets in same category on each date)
    date_category_count: dict[date, dict[str, int]] = {}
    for d in sorted_dates:
        counts: dict[str, int] = {}
        for mid, meta in market_meta.items():
            cat = meta.get("category")
            if not cat:
                continue
            idx_data = indexed.get(mid)
            if idx_data is None:
                continue
            if idx_data[1].get(d) is not None:
                counts[cat] = counts.get(cat, 0) + 1
        date_category_count[d] = counts

    open_positions: dict[str, dict[str, Any]] = {}

    for current_date in sorted_dates:
        for market_id, meta in market_meta.items():
            idx_data = indexed.get(market_id)
            if idx_data is None:
                continue
            records, date_to_idx = idx_data

            # Category filter — only trade in markets with matching category (NULL markets excluded)
            if params.categories and (meta.get("category") or "").lower() not in [c.lower() for c in params.categories]:
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

            # O(1) lookup for current date data
            cur_idx = date_to_idx.get(current_date)
            if cur_idx is None:
                if market_id not in open_positions:
                    continue

            # ── Manage open position ──────────────────────────────────────────
            if market_id in open_positions:
                if cur_idx is None:
                    continue
                pos = open_positions[market_id]
                current_price = float(records[cur_idx][1])
                volume = float(records[cur_idx][2])
                is_no = pos.get("is_no", False)
                exit_trade_price = (1.0 - current_price) if is_no else current_price
                exit_exec = simulate_fill(exit_trade_price, "SELL", volume)
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
                        exit_price=exit_trade_price,
                        position="no" if is_no else "yes",
                        stake=round(stake, 4),
                        pnl=round(pnl, 4),
                        pnl_pct=round(pnl_pct * 100, 2),
                        hold_days=(current_date - pos["entry_date"].date()).days,
                        exit_reason=exit_reason,
                    ))
                    del open_positions[market_id]
                continue

            # ── Look for entry signal ─────────────────────────────────────────
            if cur_idx is None:
                continue
            lb_start = current_date - timedelta(days=params.lookback_days)
            # Slice records for the lookback window using sorted list
            end_i = cur_idx + 1
            start_i = max(0, end_i - params.lookback_days - 1)
            window = [r for r in records[start_i:end_i] if r[0] >= lb_start]
            if len(window) < 2:
                continue

            current_price = float(window[-1][1])
            past_price = float(window[0][1])
            clob_vol = sum(r[2] for r in window) / len(window)
            # Fall back to market daily_volume (from volume24hr) or total/365
            daily_vol = clob_vol if clob_vol > 0 else (
                meta.get("daily_volume") or meta.get("volume", 0) / 365.0
            )

            # Volume filters
            if daily_vol < params.min_volume:
                continue
            if params.max_volume is not None and daily_vol > params.max_volume:
                continue
            if not (MIN_PRICE < current_price < MAX_PRICE):
                continue

            price_drop_pct = (past_price - current_price) / max(past_price, 0.001)

            prices_w = np.array([r[1] for r in window])
            vols_w = np.array([r[2] for r in window])
            sma = float(np.mean(prices_w))

            price_returns = (prices_w[1:] / prices_w[:-1]) - 1
            price_volatility = float(np.nan_to_num(np.std(price_returns)))

            price_momentum = (current_price - sma) / max(sma, 0.001)
            price_range = (float(np.max(prices_w)) - float(np.min(prices_w))) / max(sma, 0.001)

            current_rec_vol = float(window[-1][2])
            mean_vol = float(np.mean(vols_w))
            std_vol = float(np.std(vols_w))
            volume_zscore = (current_rec_vol - mean_vol) / max(std_vol, 0.001) if std_vol > 0 else 0.0

            first_date = market_first_date.get(market_id)
            market_age_days = (current_date - first_date).days if first_date else 0

            half = len(prices_w) // 2
            if half >= 2:
                fhs = float(prices_w[0])
                fhe = float(prices_w[half - 1])
                shs = float(prices_w[half])
                she = float(prices_w[-1])
                pda = (fhs - fhe) / max(fhs, 0.001)
                pdb = (shs - she) / max(shs, 0.001)
                price_acceleration = pdb - pda
            else:
                price_acceleration = 0.0

            concentration = date_category_count.get(current_date, {}).get(meta.get("category", ""), 0)

            try:
                signal = bool(eval(  # noqa: S307 — sandboxed strategy DSL
                    params.entry_condition,
                    {"__builtins__": {}},
                    {
                        # arithmetic helpers
                        "abs": abs, "min": min, "max": max, "round": round,
                        "sum": sum, "len": len, "pow": pow,
                        # variables
                        "price_drop_pct": price_drop_pct,
                        "price": current_price,
                        "volume": daily_vol,
                        "no_price": 1.0 - current_price,
                        "price_rise_pct": -price_drop_pct,
                        "no_price_drop_pct": -price_drop_pct,
                        "price_volatility": price_volatility,
                        "price_momentum": price_momentum,
                        "price_range": price_range,
                        "volume_zscore": volume_zscore,
                        "market_age_days": market_age_days,
                        "price_acceleration": price_acceleration,
                        "category": meta.get("category", ""),
                        "concentration": concentration,
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

            is_no = params.position_side == "no"
            entry_trade_price = (1.0 - current_price) if is_no else current_price
            entry_exec = simulate_fill(entry_trade_price, "BUY", daily_vol)
            capital -= stake
            open_positions[market_id] = {
                "entry_date": datetime.combine(current_date, datetime.min.time()),
                "entry_price": entry_trade_price,
                "entry_exec_price": entry_exec,
                "stake": stake,
                "is_no": is_no,
            }

        current_equity = capital + sum(p["stake"] for p in open_positions.values())
        equity_points.append((
            datetime.combine(current_date, datetime.min.time()),
            round(current_equity, 4),
        ))

    # Force-close remaining positions at last known price
    for market_id, pos in list(open_positions.items()):
        idx_data = indexed.get(market_id)
        if idx_data is None:
            continue
        records, _ = idx_data
        last_yes_price = float(records[-1][1])
        last_vol = float(records[-1][2])
        is_no = pos.get("is_no", False)
        last_trade_price = (1.0 - last_yes_price) if is_no else last_yes_price
        exit_exec = simulate_fill(last_trade_price, "SELL", last_vol)
        pnl_pct = (exit_exec - pos["entry_exec_price"]) / pos["entry_exec_price"]
        pnl = pos["stake"] * pnl_pct
        capital += pos["stake"] + pnl
        last_dt = equity_points[-1][0] if equity_points else datetime.utcnow()
        trades.append(TradeRecord(
            market_id=market_id,
            entry_date=pos["entry_date"],
            exit_date=last_dt,
            entry_price=pos["entry_price"],
            exit_price=last_trade_price,
            position="no" if is_no else "yes",
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
