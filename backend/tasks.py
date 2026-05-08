"""Celery tasks for async backtest execution."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import asyncpg
from celery import Celery

from backtest import StrategyParams, run_backtest

logger = logging.getLogger(__name__)

celery_app = Celery(
    "polymarket_backtest",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _load_price_data(
    pool: asyncpg.Pool,
    market_ids: list[str],
    start_date: datetime | None,
    end_date: datetime | None,
):
    import pandas as pd

    ph_params: list = [market_ids]
    ph_where = ["market_id = ANY($1)"]
    if start_date:
        ph_params.append(start_date)
        ph_where.append(f"timestamp >= ${len(ph_params)}")
    if end_date:
        ph_params.append(end_date)
        ph_where.append(f"timestamp <= ${len(ph_params)}")

    where_sql = " AND ".join(ph_where)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT market_id, timestamp, price_yes, volume
            FROM price_history
            WHERE {where_sql}
            ORDER BY market_id, timestamp
            """,
            *ph_params,
        )

    grouped: dict[str, list] = {}
    for row in rows:
        mid = row["market_id"]
        grouped.setdefault(mid, []).append({
            "timestamp": row["timestamp"],
            "price_yes": float(row["price_yes"]),
            "volume": float(row["volume"] or 0),
        })

    price_dfs = {}
    for mid, records in grouped.items():
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        price_dfs[mid] = df
    return price_dfs


async def _execute(run_id: str, config: dict) -> None:
    pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=1,
        max_size=4,
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE backtest_runs SET status='running', progress_pct=10 WHERE run_id=$1",
                run_id,
            )

        params = StrategyParams.from_dict(config)
        start_date = datetime.fromisoformat(config["start_date"]) if config.get("start_date") else None
        end_date = datetime.fromisoformat(config["end_date"]) if config.get("end_date") else None

        # Load market metadata
        q_params: list = []
        wheres = ["active = TRUE"]
        # category filter only applied when DB actually has category data
        if params.categories:
            q_params.append(params.categories)
            wheres.append(
                f"(category IS NULL OR category = ANY(${len(q_params)}))"
            )
        if params.min_volume:
            q_params.append(params.min_volume)
            wheres.append(f"volume >= ${len(q_params)}")

        async with pool.acquire() as conn:
            market_rows = await conn.fetch(
                f"""
                SELECT m.id, m.category, m.end_date, m.volume, m.daily_volume
                FROM markets m
                INNER JOIN (SELECT DISTINCT market_id FROM price_history) ph ON m.id = ph.market_id
                WHERE {' AND '.join(wheres)}
                """,
                *q_params,
            )

        market_meta = {
            r["id"]: {
                "category": r["category"],
                "end_date": r["end_date"],
                "volume": float(r["volume"] or 0),
                "daily_volume": float(r["daily_volume"] or 0),
            }
            for r in market_rows
        }

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE backtest_runs SET progress_pct=30 WHERE run_id=$1", run_id
            )

        price_data = await _load_price_data(
            pool, list(market_meta.keys()), start_date, end_date
        )

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE backtest_runs SET progress_pct=60 WHERE run_id=$1", run_id
            )

        # Run CPU-bound simulation in a thread to avoid blocking the asyncio event loop
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = await loop.run_in_executor(
                executor,
                lambda: run_backtest(price_data, market_meta, params, start_date, end_date),
            )

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE backtest_runs SET
                    status       = 'done',
                    progress_pct = 100,
                    metrics      = $2,
                    equity_curve = $3,
                    trades       = $4,
                    completed_at = NOW()
                WHERE run_id = $1
                """,
                run_id,
                json.dumps(result["metrics"]),
                json.dumps(result["equity_curve"]),
                json.dumps(result["trades"]),
            )
        logger.info("Backtest %s completed: %d trades", run_id, result["metrics"]["total_trades"])

    except Exception as exc:
        logger.error("Backtest %s failed: %s", run_id, exc, exc_info=True)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE backtest_runs SET status='failed', error=$2, completed_at=NOW() WHERE run_id=$1",
                run_id,
                str(exc)[:2000],
            )
    finally:
        await pool.close()


@celery_app.task(name="tasks.run_backtest_task", bind=True, max_retries=2)
def run_backtest_task(self, run_id: str, config: dict) -> str:
    """Celery worker task: execute a full backtest run."""
    _run(_execute(run_id, config))
    return run_id
