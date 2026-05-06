# Contributing

Thank you for your interest in contributing!

## Getting Started

1. Fork the repository and clone your fork.
2. Copy `.env.example` to `.env`.
3. Start the stack: `docker compose up --build`.
4. Run tests: `cd backend && pip install -r requirements.txt && pytest tests/ -v`.

## Adding a Strategy

Create a new YAML file in `strategies/`:

```yaml
name: My Strategy
description: Brief description.

entry:
  condition: price_drop_pct > 0.08   # evaluated with price, volume, price_drop_pct
  lookback_days: 7

exit:
  take_profit: 0.12
  stop_loss: 0.07

filters:
  min_volume: 1000
  categories: [politics, crypto]
  max_days_to_resolution: 30

initial_capital: 1000.0
stake_pct: 0.05
```

### Available entry condition variables

| Variable | Description |
|---|---|
| `price` | Current YES probability (0–1) |
| `price_drop_pct` | % drop over `lookback_days` (positive = price fell) |
| `volume` | Average daily volume in the lookback window |

## Code Style

- Python: type hints + docstrings on all public functions.
- No hardcoded secrets — use environment variables.
- SQL: always use parameterized queries.
- Tests: add tests for new backtest logic in `backend/tests/`.

## Pull Request Process

1. Create a feature branch from `main`.
2. Ensure tests pass.
3. Submit a PR with a clear description of the change.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
