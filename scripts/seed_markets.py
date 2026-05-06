"""
Seed the database with synthetic market data for local demo/testing.

Usage (from project root):
    docker compose exec backend python scripts/seed_markets.py

Or standalone (with DATABASE_URL set):
    python scripts/seed_markets.py
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timedelta, timezone

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://poly:secret@localhost:5432/polymarket"
)

CATEGORIES = ["politics", "crypto", "economics", "sports"]

SAMPLE_MARKETS = [
    ("Will Biden win the 2024 election?", "politics", 180),
    ("Will BTC reach $100k by end of 2024?", "crypto", 120),
    ("Will the Fed cut rates in Q1 2025?", "economics", 60),
    ("Will Team USA win the 2024 Olympics?", "sports", 90),
    ("Will Ethereum ETF be approved in 2024?", "crypto", 150),
    ("Will inflation fall below 3% by June 2024?", "economics", 75),
    ("Will Trump run in 2024?", "politics", 200),
    ("Will Bitcoin halving happen before May 2024?", "crypto", 45),
    ("Will the Super Bowl LIX be won by NFC?", "sports", 30),
    ("Will Apple stock hit $250 by year-end?", "economics", 120),
    ("Will Congress pass the AI regulation bill?", "politics", 90),
    ("Will SOL flip ETH by market cap?", "crypto", 180),
    ("Will housing prices drop in 2025?", "economics", 180),
    ("Will there be a recession in 2025?", "economics", 200),
    ("Will Nvidia remain the most valuable company?", "crypto", 60),
    ("Will the NBA Finals go to 7 games?", "sports", 20),
    ("Will Trump be found guilty on any charge?", "politics", 120),
    ("Will XRP win its SEC case?", "crypto", 90),
    ("Will unemployment exceed 5% in 2025?", "economics", 150),
    ("Will a new country join NATO?", "politics", 180),
]


def _random_price_walk(
    days: int,
    start_price: float = 0.5,
    volatility: float = 0.03,
    drift: float = 0.001,
) -> list[tuple[datetime, float, float]]:
    """Generate a mean-reverting random walk of (timestamp, price, volume)."""
    price = start_price
    now = datetime.now(tz=timezone.utc)
    start_ts = now - timedelta(days=days)
    rows = []
    for i in range(days):
        ts = start_ts + timedelta(days=i)
        shock = random.gauss(drift, volatility)
        mean_rev = -0.05 * (price - start_price)  # pull toward start_price
        price = max(0.03, min(0.97, price + shock + mean_rev))
        volume = random.uniform(500, 50_000)
        rows.append((ts, price, volume))
    return rows


async def seed(pool: asyncpg.Pool) -> None:
    now = datetime.now(tz=timezone.utc)
    market_rows = []
    for i, (question, category, resolution_days) in enumerate(SAMPLE_MARKETS):
        market_id = f"seed_{i:04d}"
        end_date = now + timedelta(days=random.randint(5, resolution_days))
        volume = random.uniform(1_000, 200_000)
        market_rows.append((market_id, question, category, end_date, volume, True))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO markets (id, question, category, end_date, volume, active, synced_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            market_rows,
        )
        print(f"Seeded {len(market_rows)} markets.")

        price_rows = []
        for i, (question, category, resolution_days) in enumerate(SAMPLE_MARKETS):
            market_id = f"seed_{i:04d}"
            history_days = min(180, resolution_days)
            start_price = random.uniform(0.3, 0.7)
            walk = _random_price_walk(history_days, start_price)
            for ts, price, volume in walk:
                price_rows.append((market_id, ts, price, volume))

        await conn.executemany(
            """
            INSERT INTO price_history (market_id, timestamp, price_yes, volume)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (market_id, timestamp) DO NOTHING
            """,
            price_rows,
        )
        print(f"Seeded {len(price_rows)} price points.")


async def main() -> None:
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=3)
    try:
        await seed(pool)
        print("Seed complete. Run a backtest at http://localhost:3000/strategy")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
