"""Sync market data from Polymarket Gamma API + CLOB API into PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GAMMA_BASE = os.environ.get("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
CLOB_BASE = "https://clob.polymarket.com"
BATCH_SIZE = 100
TIMEOUT = 30.0


class GammaSyncer:
    """Fetches markets and price history from Polymarket APIs."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.gamma = httpx.AsyncClient(
            base_url=GAMMA_BASE,
            timeout=TIMEOUT,
            headers={"User-Agent": "polymarket-backtest/1.0"},
        )
        self.clob = httpx.AsyncClient(
            base_url=CLOB_BASE,
            timeout=TIMEOUT,
            headers={"User-Agent": "polymarket-backtest/1.0"},
        )

    async def close(self) -> None:
        await self.gamma.aclose()
        await self.clob.aclose()

    async def sync_markets(self, limit: int = 500) -> int:
        """Upsert active markets from Gamma API. Returns count synced."""
        synced = 0
        offset = 0

        while synced < limit:
            try:
                resp = await self.gamma.get(
                    "/markets",
                    params={"limit": BATCH_SIZE, "offset": offset, "active": "true"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.error("Gamma API markets error at offset %d: %s", offset, exc)
                break

            markets = data if isinstance(data, list) else data.get("markets", [])
            if not markets:
                break

            rows = []
            for m in markets:
                market_id = m.get("conditionId") or m.get("id")
                if not market_id:
                    continue
                end_date = _parse_dt(m.get("endDate") or m.get("end_date_iso"))

                # Extract YES token ID from clobTokenIds (JSON array string or list)
                clob_ids_raw = m.get("clobTokenIds", "[]")
                try:
                    clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                    token_id = clob_ids[0] if clob_ids else None
                except Exception:
                    token_id = None

                rows.append((
                    str(market_id),
                    m.get("question", ""),
                    m.get("category"),
                    end_date,
                    float(m.get("volume") or m.get("volumeNum") or 0),
                    bool(m.get("active", True)),
                    token_id,
                ))

            if rows:
                async with self.pool.acquire() as conn:
                    await conn.executemany(
                        """
                        INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7)
                        ON CONFLICT (id) DO UPDATE SET
                            question  = EXCLUDED.question,
                            category  = EXCLUDED.category,
                            end_date  = EXCLUDED.end_date,
                            volume    = EXCLUDED.volume,
                            active    = EXCLUDED.active,
                            synced_at = NOW(),
                            token_id  = COALESCE(EXCLUDED.token_id, markets.token_id)
                        """,
                        rows,
                    )
                synced += len(rows)

            if len(markets) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        logger.info("Synced %d markets", synced)
        return synced

    async def sync_price_history(self, market_id: str, token_id: str) -> int:
        """Fetch daily price history for one market via CLOB API. Returns points saved."""
        try:
            resp = await self.clob.get(
                "/prices-history",
                params={"market": token_id, "interval": "max", "fidelity": "1440"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("History fetch failed for %s: %s", market_id, exc)
            return 0

        history = data if isinstance(data, list) else data.get("history", [])
        if not history:
            return 0

        rows = []
        for point in history:
            ts_raw = point.get("t") or point.get("timestamp")
            price = point.get("p") or point.get("price_yes")
            if ts_raw is None or price is None:
                continue
            try:
                ts = _parse_ts(ts_raw)
                price_f = float(price)
                if not (0.001 < price_f < 0.999):
                    continue
                vol = float(point.get("v") or point.get("volume") or 0)
                rows.append((market_id, ts, price_f, vol))
            except (ValueError, TypeError, OSError):
                continue

        if not rows:
            return 0

        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO price_history (market_id, timestamp, price_yes, volume)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (market_id, timestamp) DO UPDATE SET
                    price_yes = EXCLUDED.price_yes,
                    volume    = EXCLUDED.volume
                """,
                rows,
            )
        return len(rows)

    async def sync_all_histories(self, max_markets: int = 200) -> int:
        """Sync price history for all active markets that have a token_id."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, token_id FROM markets
                   WHERE active = TRUE AND token_id IS NOT NULL
                   ORDER BY volume DESC LIMIT $1""",
                max_markets,
            )
        if not rows:
            logger.warning("No markets with token_id found — run sync_markets first")
            return 0

        # Batch concurrency to avoid rate limiting
        total = 0
        batch_size = 20
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            results = await asyncio.gather(
                *[self.sync_price_history(r["id"], r["token_id"]) for r in batch],
                return_exceptions=True,
            )
            total += sum(r for r in results if isinstance(r, int))
            await asyncio.sleep(0.5)

        logger.info("Synced %d price points for %d markets", total, len(rows))
        return total


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_ts(raw: int | float | str) -> datetime:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


async def run_full_sync() -> None:
    from db import get_pool, init_db

    pool = await get_pool()
    await init_db()
    syncer = GammaSyncer(pool)
    try:
        await syncer.sync_markets(500)
        await syncer.sync_all_histories(200)
    finally:
        await syncer.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_full_sync())
