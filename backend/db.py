"""aiosqlite storage layer for the Polymarket backtest backend.

Drop-in replacement for the old asyncpg/Postgres pool: exposes a `Pool` facade
with `acquire()` so the rest of the codebase can keep using
`async with pool.acquire() as conn` plus conn.fetch/fetchrow/fetchval/execute/
executemany. Postgres-flavoured SQL ($1 placeholders, NOW(), GREATEST, ILIKE,
NULLS LAST) is translated to SQLite automatically. No Docker required.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import aiosqlite

DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///data/polymarket.db")
for _prefix in ("sqlite:///", "sqlite://"):
    if DB_PATH.startswith(_prefix):
        DB_PATH = DB_PATH[len(_prefix):]
        break

sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_adapter(bool, lambda b: 1 if b else 0)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    id            TEXT PRIMARY KEY,
    question      TEXT NOT NULL,
    category      TEXT,
    end_date      TEXT,
    volume        REAL DEFAULT 0,
    active        INTEGER DEFAULT 1,
    synced_at     TEXT,
    token_id      TEXT,
    daily_volume  REAL DEFAULT 0,
    current_price REAL,
    no_token_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_markets_category ON markets (category);
CREATE INDEX IF NOT EXISTS idx_markets_volume   ON markets (volume DESC);

CREATE TABLE IF NOT EXISTS price_history (
    market_id   TEXT NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
    timestamp   TEXT NOT NULL,
    price_yes   REAL NOT NULL,
    volume      REAL DEFAULT 0,
    PRIMARY KEY (market_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ph_market_ts ON price_history (market_id, timestamp);

CREATE TABLE IF NOT EXISTS resolutions (
    market_id   TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    resolved_at TEXT,
    outcome     TEXT,
    final_price REAL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    strategy_config TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    progress_pct    REAL DEFAULT 0,
    metrics         TEXT,
    equity_curve    TEXT,
    trades          TEXT,
    error           TEXT,
    created_at      TEXT,
    completed_at    TEXT,
    sweep_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON backtest_runs (status);
CREATE INDEX IF NOT EXISTS idx_runs_sweep_id ON backtest_runs (sweep_id);

CREATE TABLE IF NOT EXISTS backtest_sweeps (
    sweep_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    base_config  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    total_runs   INTEGER DEFAULT 0,
    done_runs    INTEGER DEFAULT 0,
    created_at   TEXT,
    completed_at TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_iso(value: Any) -> str | None:
    """Normalise a DB timestamp (str or datetime) to ISO-8601 for API output."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _translate(sql: str) -> str:
    """Translate Postgres-flavoured SQL fragments to SQLite."""
    sql = re.sub(r"\$(\d+)", "?", sql)
    sql = re.sub(r"\bNOW\(\)", "'{}'".format(_utcnow()), sql)
    sql = re.sub(r"\bGREATEST\(", "max(", sql)
    sql = re.sub(r"\bILIKE\b", "LIKE", sql)
    sql = re.sub(r"\bNULLS LAST\b", "", sql)
    sql = re.sub(r"::\w+", "", sql)
    return sql


def _param_order(sql: str) -> list[int]:
    """Placeholder numbers ($N) in order of first appearance in the SQL."""
    order: list[int] = []
    seen: set[int] = set()

    def _collect(m: re.Match) -> str:
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            order.append(n)
        return "?"

    re.sub(r"\$(\d+)", _collect, sql)
    return order


def _bind(sql: str, params: tuple) -> tuple[str, tuple]:
    """Translate Postgres SQL and reorder params to match SQLite's positional `?`.

    Postgres binds $1..$n by number; SQLite `?` binds positionally. If a statement
    uses placeholders out of ascending order (e.g. SET x=$2 ... WHERE id=$1), the
    param order must follow the order of first appearance in the SQL.
    """
    order = _param_order(sql)
    sql = _translate(sql)
    if not order:
        return sql, params
    return sql, tuple(params[i - 1] for i in order)


async def _open(path: str) -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = await aiosqlite.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


class _Conn:
    """Thin asyncpg-like wrapper around a single aiosqlite connection."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def fetch(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        sql, params = _bind(sql, params)
        cursor = await self._db.execute(sql, params)
        return await cursor.fetchall()

    async def fetchrow(self, sql: str, *params: Any) -> sqlite3.Row | None:
        sql, params = _bind(sql, params)
        cursor = await self._db.execute(sql, params)
        return await cursor.fetchone()

    async def fetchval(self, sql: str, *params: Any) -> Any:
        row = await self.fetchrow(sql, *params)
        return row[0] if row is not None else None

    async def execute(self, sql: str, *params: Any) -> sqlite3.Cursor:
        sql, params = _bind(sql, params)
        return await self._db.execute(sql, params)

    async def executemany(self, sql: str, seq_of_params: list[tuple]) -> None:
        order = _param_order(sql)
        sql = _translate(sql)
        rows = [tuple(p[i - 1] for i in order) if order else tuple(p) for p in seq_of_params]
        await self._db.executemany(sql, rows)

    async def close(self) -> None:
        await self._db.close()


class _Pool:
    """asyncpg-like pool facade that opens a connection per acquire()."""

    def __init__(self, path: str) -> None:
        self.path = path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_Conn]:
        db = await _open(self.path)
        try:
            yield _Conn(db)
        finally:
            await db.close()

    async def close(self) -> None:
        pass


_pool: _Pool | None = None


async def get_pool() -> _Pool:
    global _pool
    if _pool is None:
        _pool = _Pool(DB_PATH)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init_db() -> None:
    db = await _open(DB_PATH)
    try:
        await db.executescript(_SCHEMA)
    finally:
        await db.close()
