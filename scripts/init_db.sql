-- Polymarket Backtest — Database Initialisation
-- Run against a PostgreSQL 15 + TimescaleDB instance:
--   psql -h localhost -U poly -d polymarket -f scripts/init_db.sql

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ── Markets ───────────────────────────────────────────────────────────────────

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
CREATE INDEX IF NOT EXISTS idx_markets_volume   ON markets (volume DESC);

-- ── Price history (TimescaleDB hypertable) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS price_history (
    market_id   TEXT NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
    timestamp   TIMESTAMPTZ NOT NULL,
    price_yes   DOUBLE PRECISION NOT NULL CHECK (price_yes > 0 AND price_yes < 1),
    volume      DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (market_id, timestamp)
);

SELECT create_hypertable('price_history', 'timestamp', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ph_market_ts
    ON price_history (market_id, timestamp DESC);

-- ── Resolutions ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS resolutions (
    market_id   TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    resolved_at TIMESTAMPTZ,
    outcome     TEXT,                -- 'yes' | 'no' | 'invalid'
    final_price DOUBLE PRECISION
);

-- ── Backtest runs ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    strategy_config JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','done','failed')),
    progress_pct    DOUBLE PRECISION DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    metrics         JSONB,
    equity_curve    JSONB,
    trades          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_status     ON backtest_runs (status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON backtest_runs (created_at DESC);

-- ── Trades (denormalized for fast queries) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS trades (
    id          BIGSERIAL PRIMARY KEY,
    backtest_id TEXT NOT NULL REFERENCES backtest_runs(run_id) ON DELETE CASCADE,
    market_id   TEXT NOT NULL,
    entry_date  TIMESTAMPTZ,
    exit_date   TIMESTAMPTZ,
    entry_price DOUBLE PRECISION,
    exit_price  DOUBLE PRECISION,
    stake       DOUBLE PRECISION,
    pnl         DOUBLE PRECISION,
    pnl_pct     DOUBLE PRECISION,
    hold_days   DOUBLE PRECISION,
    exit_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_backtest ON trades (backtest_id);
CREATE INDEX IF NOT EXISTS idx_trades_market   ON trades (market_id);
