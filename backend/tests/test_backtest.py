"""Tests for the backtest engine — win / loss / stop-loss and edge-case scenarios."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtest import StrategyParams, compute_metrics, run_backtest, simulate_fill

START = datetime(2024, 1, 1)


def _price_df(prices: list[float], start: datetime = START) -> pd.DataFrame:
    ts = [start + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame({
        "timestamp": pd.to_datetime(ts),
        "price_yes": prices,
        "volume": [20_000.0] * len(prices),
    })


def _params(**kw) -> StrategyParams:
    defaults = dict(
        name="test",
        entry_condition="price_drop_pct > 0.08",
        lookback_days=3,
        take_profit=0.12,
        stop_loss=0.07,
        min_volume=100.0,
        max_volume=None,
        categories=[],
        max_days_to_resolution=90,
        min_days_to_resolution=0,
        initial_capital=1000.0,
        stake_pct=0.10,
    )
    defaults.update(kw)
    return StrategyParams(**defaults)


def _meta(end_days: int = 90, cat: str = "politics") -> dict:
    return {
        "category": cat,
        "end_date": START + timedelta(days=end_days),
        "volume": 50_000.0,
    }


# ── Scenario 1: Winning trade ─────────────────────────────────────────────────

def test_winning_trade_produces_positive_pnl():
    # Drop 10% over 3 days triggers entry. Price then holds, rises to hit take_profit (+12%).
    # Prices stay above SL threshold after entry so stop_loss doesn't fire first.
    prices = [0.60, 0.57, 0.54, 0.57, 0.65, 0.73]
    df = _price_df(prices)
    result = run_backtest(
        {"m1": df}, {"m1": _meta()}, _params(),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] >= 1
    assert result["metrics"]["total_pnl"] > 0
    assert result["metrics"]["win_rate_pct"] > 0


# ── Scenario 2: Losing trade ──────────────────────────────────────────────────

def test_losing_trade_produces_negative_pnl():
    # Price keeps falling after entry → stop_loss fires.
    prices = [0.70, 0.64, 0.60, 0.55, 0.48, 0.40]
    df = _price_df(prices)
    result = run_backtest(
        {"m2": df}, {"m2": _meta()}, _params(),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] >= 1
    assert result["metrics"]["total_pnl"] < 0


# ── Scenario 3: Stop-loss triggers explicitly ─────────────────────────────────

def test_stop_loss_exit_reason_present():
    prices = [0.70, 0.65, 0.62, 0.57, 0.51, 0.44]
    df = _price_df(prices)
    result = run_backtest(
        {"m3": df}, {"m3": _meta()}, _params(take_profit=0.50),
        START, START + timedelta(days=len(prices)),
    )
    stop_trades = [t for t in result["trades"] if t["exit_reason"] == "stop_loss"]
    assert len(stop_trades) >= 1


# ── simulate_fill tests ───────────────────────────────────────────────────────

def test_buy_fill_price_exceeds_mid():
    fill = simulate_fill(0.50, "BUY", 10_000.0)
    assert fill > 0.50


def test_sell_fill_price_below_mid():
    fill = simulate_fill(0.50, "SELL", 10_000.0)
    assert fill < 0.50


def test_fill_price_clamped_to_valid_range():
    assert simulate_fill(0.99, "BUY", 1.0) <= MAX_PRICE
    assert simulate_fill(0.01, "SELL", 1.0) >= MIN_PRICE


def test_high_volume_reduces_slippage():
    low_vol_fill = simulate_fill(0.50, "BUY", 100.0)
    high_vol_fill = simulate_fill(0.50, "BUY", 1_000_000.0)
    assert high_vol_fill < low_vol_fill  # less slippage at high volume


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_data_returns_zero_trades():
    result = run_backtest({}, {}, _params())
    assert result["metrics"]["total_trades"] == 0
    assert result["equity_curve"] == []
    assert result["trades"] == []


def test_volume_filter_min_blocks_entry():
    prices = [0.60, 0.57, 0.54, 0.50, 0.65, 0.75]
    df = _price_df(prices)
    result = run_backtest(
        {"m4": df}, {"m4": _meta()}, _params(min_volume=100_000.0),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] == 0


def test_volume_filter_max_blocks_entry():
    # max_volume=5000 while df volume=20000 → no entry
    prices = [0.60, 0.57, 0.54, 0.50, 0.65, 0.75]
    df = _price_df(prices)
    result = run_backtest(
        {"m5": df}, {"m5": _meta()}, _params(max_volume=5_000.0),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] == 0


def test_category_filter_blocks_entry():
    prices = [0.60, 0.57, 0.54, 0.50, 0.65, 0.75]
    df = _price_df(prices)
    result = run_backtest(
        {"m6": df}, {"m6": _meta(cat="sports")}, _params(categories=["politics"]),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] == 0


def test_min_days_to_resolution_blocks_early_entry():
    # min_days_to_resolution=5, but end_date is only 3 days away
    prices = [0.60, 0.57, 0.54, 0.50, 0.65, 0.75]
    df = _price_df(prices)
    result = run_backtest(
        {"m7": df}, {"m7": _meta(end_days=3)}, _params(min_days_to_resolution=5),
        START, START + timedelta(days=len(prices)),
    )
    assert result["metrics"]["total_trades"] == 0


def test_equity_curve_length_matches_date_range():
    prices = [0.50] * 10
    df = _price_df(prices)
    result = run_backtest(
        {"m8": df}, {"m8": _meta()}, _params(),
        START, START + timedelta(days=10),
    )
    assert len(result["equity_curve"]) == 10


def test_roi_consistent_with_pnl():
    prices = [0.60, 0.57, 0.54, 0.51, 0.62, 0.78]
    df = _price_df(prices)
    result = run_backtest(
        {"m9": df}, {"m9": _meta()}, _params(initial_capital=1000.0),
        START, START + timedelta(days=len(prices)),
    )
    m = result["metrics"]
    expected = round(m["total_pnl"] / 1000.0 * 100, 2)
    assert abs(m["roi_pct"] - expected) < 0.01


# import needed for fill price clamp test
from backtest import MAX_PRICE, MIN_PRICE
