"""Background task execution for backtests (in-process — no Celery/Redis/Docker)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from db import get_pool

from backtest import StrategyParams, run_backtest

logger = logging.getLogger(__name__)

# Genuine concurrency cap: at most this many backtests run at once.
# New runs queue on the semaphore (status stays 'pending') instead of erroring.
_EXEC_LIMIT = 2
_exec_semaphore = asyncio.Semaphore(_EXEC_LIMIT)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _load_price_data(
    pool,
    market_ids: list[str] | None,
    start_date: datetime | None,
    end_date: datetime | None,
):
    import pandas as pd

    where: list[str] = []
    params: list = []
    if market_ids is not None:
        placeholders = ",".join("?" for _ in market_ids)
        where.append(f"market_id IN ({placeholders})")
        params.extend(market_ids)
    if start_date:
        where.append("timestamp >= ?")
        params.append(start_date)
    if end_date:
        where.append("timestamp <= ?")
        params.append(end_date)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT market_id, timestamp, price_yes, volume
            FROM price_history
            {where_sql}
            ORDER BY market_id, timestamp
            """,
            *params,
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


async def _load_market_meta(pool, params: StrategyParams) -> dict:
    """Load market metadata, optionally filtered by strategy params."""
    async with pool.acquire() as conn:
        if params.market_ids:
            placeholders = ",".join("?" for _ in params.market_ids)
            market_rows = await conn.fetch(
                f"""
                SELECT m.id, m.category, m.end_date, m.volume, m.daily_volume
                FROM markets m
                INNER JOIN (SELECT DISTINCT market_id FROM price_history) ph ON m.id = ph.market_id
                WHERE m.id IN ({placeholders})
                """,
                *params.market_ids,
            )
        else:
            wheres = ["active = TRUE"]
            q_params: list = []
            if params.categories:
                placeholders = ",".join("?" for _ in params.categories)
                wheres.append(f"(category IS NULL OR category IN ({placeholders}))")
                q_params.extend(params.categories)
            if params.min_volume:
                wheres.append("volume >= ?")
                q_params.append(params.min_volume)
            market_rows = await conn.fetch(
                f"""
                SELECT m.id, m.category, m.end_date, m.volume, m.daily_volume
                FROM markets m
                INNER JOIN (SELECT DISTINCT market_id FROM price_history) ph ON m.id = ph.market_id
                WHERE {' AND '.join(wheres)}
                """,
                *q_params,
            )

    return {
        r["id"]: {
            "category": r["category"],
            "end_date": _parse_dt(r["end_date"]),
            "volume": float(r["volume"] or 0),
            "daily_volume": float(r["daily_volume"] or 0),
        }
        for r in market_rows
    }


async def _execute(run_id: str, config: dict) -> None:
    pool = await get_pool()
    try:
        async with _exec_semaphore:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE backtest_runs SET status='running', progress_pct=10 WHERE run_id=$1",
                    run_id,
                )

            params = StrategyParams.from_dict(config)
            start_date = datetime.fromisoformat(config["start_date"]) if config.get("start_date") else None
            end_date = datetime.fromisoformat(config["end_date"]) if config.get("end_date") else None

            market_meta = await _load_market_meta(pool, params)

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

            result = run_backtest(price_data, market_meta, params, start_date, end_date)

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


async def _load_all_market_data(
    pool,
    start_date: datetime | None,
    end_date: datetime | None,
):
    """Load ALL markets with price history (no category/volume filter) for sweep reuse."""
    async with pool.acquire() as conn:
        market_rows = await conn.fetch(
            """
            SELECT m.id, m.category, m.end_date, m.volume, m.daily_volume
            FROM markets m
            INNER JOIN (SELECT DISTINCT market_id FROM price_history) ph ON m.id = ph.market_id
            """
        )

    market_meta = {
        r["id"]: {
            "category": r["category"],
            "end_date": _parse_dt(r["end_date"]),
            "volume": float(r["volume"] or 0),
            "daily_volume": float(r["daily_volume"] or 0),
        }
        for r in market_rows
    }

    price_data = await _load_price_data(pool, None, start_date, end_date)
    return market_meta, price_data


async def _execute_sweep_bg(
    sweep_id: str,
    run_id_config_pairs: list[tuple[str, dict]],
    start_date: datetime | None,
    end_date: datetime | None,
) -> None:
    """Run all sweep combinations sequentially, reusing shared price data."""
    pool = await get_pool()
    try:
        logger.info("Sweep %s: loading market data for %d combinations", sweep_id, len(run_id_config_pairs))
        market_meta, price_data = await _load_all_market_data(pool, start_date, end_date)
        logger.info("Sweep %s: %d markets, %d price series loaded", sweep_id, len(market_meta), len(price_data))

        for i, (run_id, config) in enumerate(run_id_config_pairs):
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE backtest_runs SET status='running' WHERE run_id=$1", run_id
                    )

                params = StrategyParams.from_dict(config)

                result = run_backtest(price_data, market_meta, params, start_date, end_date)

                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE backtest_runs SET
                            status='done', progress_pct=100,
                            metrics=$2, equity_curve=$3, trades=$4, completed_at=NOW()
                        WHERE run_id=$1
                        """,
                        run_id,
                        json.dumps(result["metrics"]),
                        json.dumps(result["equity_curve"]),
                        json.dumps(result["trades"]),
                    )
                    await conn.execute(
                        "UPDATE backtest_sweeps SET done_runs = done_runs + 1 WHERE sweep_id=$1",
                        sweep_id,
                    )

                logger.info("Sweep %s: %d/%d done (%s, %d trades)",
                            sweep_id, i + 1, len(run_id_config_pairs),
                            run_id[:8], result["metrics"]["total_trades"])

            except Exception as exc:
                logger.error("Sweep %s: run %s failed: %s", sweep_id, run_id[:8], exc)
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE backtest_runs SET status='failed', error=$2, completed_at=NOW() WHERE run_id=$1",
                        run_id, str(exc)[:2000],
                    )
                    await conn.execute(
                        "UPDATE backtest_sweeps SET done_runs = done_runs + 1 WHERE sweep_id=$1",
                        sweep_id,
                    )

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE backtest_sweeps SET status='done', completed_at=NOW() WHERE sweep_id=$1",
                sweep_id,
            )
        logger.info("Sweep %s: all %d combinations finished", sweep_id, len(run_id_config_pairs))

    except Exception as exc:
        logger.error("Sweep %s: fatal error: %s", sweep_id, exc, exc_info=True)
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE backtest_sweeps SET status='failed', completed_at=NOW() WHERE sweep_id=$1",
                    sweep_id,
                )
        except Exception:
            pass
