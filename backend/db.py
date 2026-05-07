"""asyncpg connection pool and TimescaleDB schema initialization."""
from __future__ import annotations

import os

import asyncpg

_pool: asyncpg.Pool | None = None

_SCHEMA = """
-- TimescaleDB is optional (not available on Railway managed PostgreSQL)
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'TimescaleDB not available — using standard PostgreSQL tables';
END $$;

CREATE TABLE IF NOT EXISTS markets (
    id          TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    category    TEXT,
    end_date    TIMESTAMPTZ,
    volume      DOUBLE PRECISION DEFAULT 0,
    active      BOOLEAN DEFAULT TRUE,
    synced_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_category ON markets (category);
CREATE INDEX IF NOT EXISTS idx_markets_volume   ON markets (volume DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS price_history (
    market_id   TEXT NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
    timestamp   TIMESTAMPTZ NOT NULL,
    price_yes   DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (market_id, timestamp)
);

-- Only create hypertable when TimescaleDB is present
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    PERFORM create_hypertable('price_history', 'timestamp', if_not_exists => TRUE);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ph_market_ts
    ON price_history (market_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS resolutions (
    market_id   TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    resolved_at TIMESTAMPTZ,
    outcome     TEXT,
    final_price DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    strategy_config JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    progress_pct    DOUBLE PRECISION DEFAULT 0,
    metrics         JSONB,
    equity_curve    JSONB,
    trades          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON backtest_runs (status);

ALTER TABLE markets ADD COLUMN IF NOT EXISTS token_id TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS daily_volume DOUBLE PRECISION DEFAULT 0;
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA)
