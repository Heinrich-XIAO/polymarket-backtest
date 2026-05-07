"""FastAPI application — Polymarket Backtest & Strategy Simulator."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

from db import close_pool, get_pool, init_db
from models import BacktestRequest, BacktestRunStatus, HealthResponse
from tasks import run_backtest_task

logger = logging.getLogger(__name__)

STRATEGIES_DIR = Path(os.environ.get("STRATEGIES_DIR", "strategies"))

app = FastAPI(
    title="Polymarket Backtest API",
    description="Backtest trading strategies on Polymarket prediction markets.",
    version="1.0.0",
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    logger.info("DB initialized")


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_pool()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        market_count = int(await conn.fetchval("SELECT COUNT(*) FROM markets") or 0)
        price_points = int(await conn.fetchval("SELECT COUNT(*) FROM price_history") or 0)
        latest_row = await conn.fetchrow(
            "SELECT timestamp FROM price_history ORDER BY timestamp DESC LIMIT 1"
        )
    latest_price = latest_row["timestamp"].isoformat() if latest_row else None
    return HealthResponse(
        status="ok",
        market_count=market_count,
        price_points=price_points,
        latest_price=latest_price,
    )


# ── Strategies ────────────────────────────────────────────────────────────────

@app.get("/strategies", tags=["strategies"])
async def list_strategies() -> list[dict[str, Any]]:
    """Return all YAML strategy files from the strategies directory."""
    result = []
    for yaml_file in sorted(STRATEGIES_DIR.glob("*.yaml")):
        try:
            config = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            result.append({
                "file": yaml_file.stem,
                "name": config.get("name", yaml_file.stem),
                "description": config.get("description", ""),
                "entry_condition": config.get("entry", {}).get("condition", ""),
                "take_profit": config.get("exit", {}).get("take_profit"),
                "stop_loss": config.get("exit", {}).get("stop_loss"),
                "categories": config.get("filters", {}).get("categories", []),
                "min_volume": config.get("filters", {}).get("min_volume"),
            })
        except Exception as exc:
            logger.warning("Cannot parse strategy %s: %s", yaml_file.name, exc)
    return result


@app.get("/strategies/{name}", tags=["strategies"])
async def get_strategy(name: str) -> dict[str, Any]:
    yaml_file = STRATEGIES_DIR / f"{name}.yaml"
    if not yaml_file.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    return yaml.safe_load(yaml_file.read_text(encoding="utf-8"))


# ── Backtest ──────────────────────────────────────────────────────────────────

@app.post("/backtest/run", status_code=202, tags=["backtest"])
async def run_backtest_endpoint(
    req: BacktestRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Start a backtest run. Returns run_id for polling."""
    if req.strategy_name:
        yaml_file = STRATEGIES_DIR / f"{req.strategy_name}.yaml"
        if not yaml_file.exists():
            raise HTTPException(404, f"Strategy '{req.strategy_name}' not found")
        config: dict = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    elif req.strategy_config:
        config = req.strategy_config.model_dump()
    else:
        raise HTTPException(400, "Provide strategy_name or strategy_config")

    config["initial_capital"] = req.initial_capital
    if req.start_date:
        config["start_date"] = req.start_date.isoformat()
    if req.end_date:
        config["end_date"] = req.end_date.isoformat()

    run_id = str(uuid.uuid4())
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO backtest_runs (run_id, strategy_name, strategy_config, status)
            VALUES ($1, $2, $3, 'pending')
            """,
            run_id,
            config.get("name", req.strategy_name or "custom"),
            json.dumps(config),
        )

    try:
        run_backtest_task.delay(run_id, config)
    except Exception:
        # Celery not available — run in-process via FastAPI BackgroundTasks
        from tasks import _execute
        background_tasks.add_task(_execute, run_id, config)

    return {"run_id": run_id}


@app.get("/backtest/{run_id}/status", response_model=BacktestRunStatus, tags=["backtest"])
async def get_status(run_id: str) -> BacktestRunStatus:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_id, status, progress_pct, created_at FROM backtest_runs WHERE run_id=$1",
            run_id,
        )
    if not row:
        raise HTTPException(404, "Run not found")
    return BacktestRunStatus(
        run_id=row["run_id"],
        status=row["status"],
        progress_pct=float(row["progress_pct"] or 0),
        created_at=row["created_at"],
    )


# ── Results ───────────────────────────────────────────────────────────────────

@app.get("/results/{run_id}", tags=["results"])
async def get_results(run_id: str) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, strategy_name, status, metrics, equity_curve, trades,
                   created_at, completed_at, error
            FROM backtest_runs WHERE run_id=$1
            """,
            run_id,
        )
    if not row:
        raise HTTPException(404, "Run not found")

    if row["status"] not in ("done", "failed"):
        return {"run_id": run_id, "status": row["status"]}

    return {
        "run_id": row["run_id"],
        "strategy_name": row["strategy_name"],
        "status": row["status"],
        "metrics": json.loads(row["metrics"]) if row["metrics"] else None,
        "equity_curve": json.loads(row["equity_curve"]) if row["equity_curve"] else [],
        "trades": json.loads(row["trades"]) if row["trades"] else [],
        "created_at": row["created_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "error": row["error"],
    }


@app.get("/results/{run_id}/export", tags=["results"])
async def export_results(run_id: str) -> StreamingResponse:
    """Download backtest trades as CSV."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trades, strategy_name FROM backtest_runs WHERE run_id=$1 AND status='done'",
            run_id,
        )
    if not row:
        raise HTTPException(404, "Run not found or not completed")

    trades: list[dict] = json.loads(row["trades"]) if row["trades"] else []
    if not trades:
        raise HTTPException(404, "No trades to export")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(trades[0].keys()))
    writer.writeheader()
    writer.writerows(trades)
    output.seek(0)

    filename = f"backtest_{run_id[:8]}_{row['strategy_name']}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Markets ───────────────────────────────────────────────────────────────────

@app.get("/markets", tags=["markets"])
async def list_markets(
    query: str | None = Query(None, description="Search by question text"),
    category: str | None = Query(None),
    min_volume: float | None = Query(None, ge=0),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    pool = await get_pool()
    params: list = []
    wheres: list[str] = []

    if active_only:
        wheres.append("active = TRUE")
    if query:
        params.append(f"%{query}%")
        wheres.append(f"question ILIKE ${len(params)}")
    if category:
        params.append(category)
        wheres.append(f"category = ${len(params)}")
    if min_volume is not None:
        params.append(min_volume)
        wheres.append(f"volume >= ${len(params)}")

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    count_params = params[:]
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        total = int(await conn.fetchval(f"SELECT COUNT(*) FROM markets {where_sql}", *count_params) or 0)
        rows = await conn.fetch(
            f"""
            SELECT id, question, category, end_date, volume, active
            FROM markets {where_sql}
            ORDER BY volume DESC NULLS LAST
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )

    return {
        "total": total,
        "items": [
            {
                "id": r["id"],
                "question": r["question"],
                "category": r["category"],
                "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                "volume": float(r["volume"] or 0),
                "active": r["active"],
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }


# ── Sync trigger (admin) ──────────────────────────────────────────────────────

@app.post("/admin/sync", status_code=202, tags=["admin"])
async def trigger_sync(
    background_tasks: BackgroundTasks,
    max_markets: int = Query(200, ge=1, le=1000),
) -> dict[str, str]:
    """Trigger a data sync from Gamma API in the background."""
    async def _do_sync() -> None:
        from sync_gamma import GammaSyncer
        pool = await get_pool()
        syncer = GammaSyncer(pool)
        try:
            await syncer.sync_markets(500)
            await syncer.sync_all_histories(max_markets)
        except Exception as exc:
            logger.error("Background sync failed: %s", exc)
        finally:
            await syncer.close()

    background_tasks.add_task(_do_sync)
    return {"message": f"Sync started in background (max_markets={max_markets})"}


@app.post("/admin/sync-diverse", status_code=202, tags=["admin"])
async def trigger_diverse_sync(
    background_tasks: BackgroundTasks,
    fetch_pages: int = Query(20, ge=1, le=50),
    min_price: float = Query(0.1, ge=0.01, le=0.49),
    max_price: float = Query(0.9, ge=0.51, le=0.99),
    history_limit: int = Query(200, ge=1, le=500),
) -> dict[str, str]:
    """Sync markets with competitive prices (default 0.1–0.9) for better backtests."""
    async def _do_diverse_sync() -> None:
        from sync_gamma import GammaSyncer
        import json as _json
        import httpx as _httpx

        pool = await get_pool()
        syncer = GammaSyncer(pool)
        try:
            # Fetch many pages from Gamma API, keep only competitive-price markets
            inserted = 0
            offset = 0
            batch = 100
            total_pages = fetch_pages

            async with _httpx.AsyncClient(base_url="https://gamma-api.polymarket.com", timeout=30) as client:
                for _ in range(total_pages):
                    try:
                        resp = await client.get("/markets", params={"limit": batch, "offset": offset, "active": "true"})
                        resp.raise_for_status()
                        markets = resp.json()
                    except Exception as exc:
                        logger.error("Diverse sync fetch error at offset %d: %s", offset, exc)
                        break

                    if not markets:
                        break

                    rows = []
                    for m in markets:
                        market_id = m.get("conditionId") or m.get("id")
                        if not market_id:
                            continue

                        # Parse current YES price
                        try:
                            prices = _json.loads(m.get("outcomePrices", "[]") or "[]")
                            yes_price = float(prices[0]) if prices else None
                        except Exception:
                            yes_price = None

                        # Only keep markets with competitive prices
                        if yes_price is None or not (min_price <= yes_price <= max_price):
                            continue

                        try:
                            clob_ids = _json.loads(m.get("clobTokenIds", "[]") or "[]")
                            token_id = clob_ids[0] if clob_ids else None
                        except Exception:
                            token_id = None

                        from sync_gamma import _parse_dt
                        end_date = _parse_dt(m.get("endDate") or m.get("end_date_iso"))

                        rows.append((
                            str(market_id),
                            m.get("question", ""),
                            m.get("category") or None,
                            end_date,
                            float(m.get("volume") or m.get("volumeNum") or 0),
                            True,
                            token_id,
                            float(m.get("volume24hr") or 0),
                            yes_price,
                        ))

                    if rows:
                        async with pool.acquire() as conn:
                            await conn.executemany(
                                """
                                INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id, daily_volume, current_price)
                                VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
                                ON CONFLICT (id) DO UPDATE SET
                                    question      = EXCLUDED.question,
                                    end_date      = EXCLUDED.end_date,
                                    volume        = EXCLUDED.volume,
                                    active        = EXCLUDED.active,
                                    synced_at     = NOW(),
                                    token_id      = COALESCE(EXCLUDED.token_id, markets.token_id),
                                    daily_volume  = EXCLUDED.daily_volume,
                                    current_price = EXCLUDED.current_price
                                """,
                                rows,
                            )
                        inserted += len(rows)

                    offset += batch
                    if len(markets) < batch:
                        break

            logger.info("Diverse sync: inserted/updated %d competitive markets", inserted)

            # Now sync price histories for newly added markets
            await syncer.sync_all_histories(history_limit)

        except Exception as exc:
            logger.error("Diverse sync failed: %s", exc)
        finally:
            await syncer.close()

    background_tasks.add_task(_do_diverse_sync)
    return {"message": f"Diverse sync started: fetching {fetch_pages} pages, price {min_price}–{max_price}, history for {history_limit} markets"}
