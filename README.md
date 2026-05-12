# Polymarket Backtest & Strategy Simulator

Open-source backtester for prediction-market trading strategies on [Polymarket](https://polymarket.com).

**Built for the Polymarket Builders Program.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-GitHub%20Pages-brightgreen)](https://distank.github.io/polymarket-backtest/)

---

## Live Demo

**[https://distank.github.io/polymarket-backtest/](https://distank.github.io/polymarket-backtest/)**

- Browse 10,800+ real Polymarket markets
- Run 180-day backtests in < 5 seconds
- Compare 18 pre-built strategies
- Export results to CSV

---

## Screenshots

### Strategy selector & results
![Strategy selector](https://distank.github.io/polymarket-backtest/screenshot-strategy.png)

### Equity curve & trade log
![Equity curve](https://distank.github.io/polymarket-backtest/screenshot-results.png)

### Parameter sweep leaderboard
![Sweep leaderboard](https://distank.github.io/polymarket-backtest/screenshot-sweep.png)

---

## Features

- **Realistic execution model** — spread, slippage, 2% commission
- **10+ pre-built strategies** in YAML format (mean reversion, momentum, contrarian, …)
- **180-day backtests in < 5 seconds** via pandas + TimescaleDB
- **Live data** from Polymarket Gamma API
- **Full metrics** — PnL, ROI, Max Drawdown, Win Rate, Sharpe Ratio
- **Interactive UI** — equity curve chart, trade log, CSV export
- **Async API** — FastAPI + Celery for non-blocking execution
- **One-command setup** — Docker Compose

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/distank/polymarket-backtest
cd polymarket-backtest

# 2. Configure
cp .env.example .env

# 3. Start
docker compose up --build

# 4. Sync market data (run once)
curl -X POST http://localhost:8000/admin/sync

# 5. Open UI
open http://localhost:3000
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Next.js 14 UI  (port 3000)                             │
│  • Dashboard  • Strategy selector  • Results + chart    │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP
┌────────────────────────▼────────────────────────────────┐
│  FastAPI Backend  (port 8000)                           │
│  POST /backtest/run → Celery task → Redis               │
│  GET  /results/{id}  ← poll status                      │
└──────────┬──────────────────────┬───────────────────────┘
           │ asyncpg              │ Celery
┌──────────▼──────────┐  ┌───────▼───────────────────────┐
│  TimescaleDB (pg15) │  │  Celery Worker                │
│  • markets          │  │  • loads price_history        │
│  • price_history    │  │  • runs pandas backtest       │
│  • backtest_runs    │  │  • saves metrics + trades     │
└─────────────────────┘  └───────────────────────────────┘
```

---

## Execution Model

Polymarket doesn't expose full orderbook history, so we approximate:

```
spread    = |P(yes) − (1 − P(no))|
slippage  = min(1%, 1000 / daily_volume)
commission = 2% per trade

fill_price (BUY)  = (price + spread/2 + slippage) × 1.02
fill_price (SELL) = (price − spread/2 − slippage) × 0.98
```

---

## Strategy YAML Format

```yaml
name: Mean Reversion
description: Buy on dips, sell on recovery.

entry:
  condition: price_drop_pct > 0.08   # vars: price, price_drop_pct, volume
  lookback_days: 7

exit:
  take_profit: 0.12   # +12%
  stop_loss: 0.07     # -7%

filters:
  min_volume: 1000
  categories: [politics, crypto, economics]
  max_days_to_resolution: 30

initial_capital: 1000.0
stake_pct: 0.05    # 5% of capital per trade
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Status + market/point counts |
| GET | `/strategies` | List all YAML strategies |
| POST | `/backtest/run` | Launch backtest → `{run_id}` |
| GET | `/backtest/{id}/status` | `pending/running/done/failed` |
| GET | `/results/{id}` | Metrics + equity curve + trades |
| GET | `/results/{id}/export` | Download CSV |
| GET | `/markets` | Search/filter markets |
| POST | `/admin/sync` | Sync data from Gamma API |

---

## Metrics

| Metric | Formula |
|--------|---------|
| Total PnL | Σ trade.pnl |
| ROI | total_pnl / initial_capital × 100 |
| Max Drawdown | min((equity − peak) / peak) × 100 |
| Win Rate | winning_trades / total_trades × 100 |
| Avg Hold | mean(exit_date − entry_date) in days |
| Sharpe | mean(daily_returns) / std(daily_returns) × √252 |

---

## Development

```bash
# Backend tests
cd backend
pip install -r requirements.txt
pytest tests/ -v

# Backend hot-reload (without Docker)
uvicorn main:app --reload

# Sync data manually
python sync_gamma.py
```

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.
