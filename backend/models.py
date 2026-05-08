"""Pydantic schemas for Polymarket Backtest API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Market(BaseModel):
    id: str
    question: str
    category: str | None = None
    end_date: datetime | None = None
    volume: float = 0.0
    active: bool = True


class PricePoint(BaseModel):
    timestamp: datetime
    price_yes: float
    volume: float


class Trade(BaseModel):
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


class BacktestMetrics(BaseModel):
    total_pnl: float
    roi_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    avg_hold_days: float
    sharpe_ratio: float | None = None


class EquityPoint(BaseModel):
    timestamp: datetime
    equity: float


class BacktestRunStatus(BaseModel):
    run_id: str
    status: str
    progress_pct: float = 0.0
    created_at: datetime


class EntryConfig(BaseModel):
    condition: str = "price_drop_pct > 0.08"
    lookback_days: int = 7


class ExitConfig(BaseModel):
    take_profit: float = 0.12
    stop_loss: float = 0.07


class FiltersConfig(BaseModel):
    min_volume: float = 1000.0
    max_volume: float | None = None
    categories: list[str] = []
    max_days_to_resolution: int = 30
    min_days_to_resolution: int = 0


class StrategyConfig(BaseModel):
    name: str
    entry: EntryConfig = EntryConfig()
    exit: ExitConfig = ExitConfig()
    filters: FiltersConfig = FiltersConfig()
    initial_capital: float = 1000.0
    stake_pct: float = 0.05
    description: str = ""


class BacktestRequest(BaseModel):
    strategy_name: str | None = None
    strategy_config: StrategyConfig | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    initial_capital: float = Field(default=1000.0, gt=0)

    model_config = {
        "json_schema_extra": {
            "example": {"strategy_name": "mean_reversion", "initial_capital": 1000.0}
        }
    }


class HealthResponse(BaseModel):
    status: str
    market_count: int
    price_points: int
    latest_price: str | None = None
    version: str = "1.0.0"


class SweepRequest(BaseModel):
    name: str = "Parameter Sweep"
    base_config: StrategyConfig
    # Lists of values to try for each param — cartesian product = combinations
    entry_conditions: list[str] = []
    lookback_days: list[int] = []
    take_profit: list[float] = []
    stop_loss: list[float] = []
    min_volume: list[float] = []
    max_days_to_resolution: list[int] = []
    stake_pct: list[float] = []
    initial_capital: float = Field(default=1000.0, gt=0)
    start_date: datetime | None = None
    end_date: datetime | None = None
    max_combinations: int = Field(default=50, ge=1, le=100)
