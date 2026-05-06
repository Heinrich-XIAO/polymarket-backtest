"""Sync market data from Polymarket Gamma API into PostgreSQL."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GAMMA_BASE = os.environ.get("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
BATCH_SIZE = 100
TIMEOUT = 30.0


class GammaSyncer:
    """Fetches markets and price history from Gamma API."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.client = httpx.AsyncClient(
            base_url=GAMMA_BASE,
            timeout=TIMEOUT,
            headers={"User-Agent": "polymarket-backtest/1.0"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def sync_markets(self, limit: int = 500) -> int:
        """Upsert active markets from Gamma API. Returns count synced."""
        synced = 0
        offset = 0

        while synced < limit:
            try:
                resp = await self.client.get(
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
                rows.append((
                    str(market_id),
                    m.get("question", ""),
                    m.get("category"),
                    end_date,
                    float(m.get("volume") or m.get("volumeNum") or 0),
                    bool(m.get("active", True)),
                ))

            if rows:
                async with self.pool.acquire() as conn:
                    await conn.executemany(
                        """
                        INSERT INTO markets (id, question, category, end_date, volume, active, synced_at)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            question  = EXCLUDED.question,
                            category  = EXCLUDED.category,
                            end_date  = EXCLUDED.end_date,
                            volume    = EXCLUDED.volume,
                            active    = EXCLUDED.active,
                            synced_at = NOW()
                        """,
                        rows,
                    )
                synced += len(rows)

            if len(markets) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        logger.info("Synced %d markets", synced)
        return synced

    async def sync_price_history(self, market_id: str) -> int:
        """Fetch daily price history for one market. Returns points saved."""
        try:
            resp = await self.client.get(
                f"/markets/{market_id}/history",
                params={"interval": "1d", "fidelity": 1},
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
        """Sync price history for all active markets in DB."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM markets WHERE active = TRUE ORDER BY volume DESC LIMIT $1",
                max_markets,
            )
        market_ids = [r["id"] for r in rows]
        results = await asyncio.gather(
            *[self.sync_price_history(mid) for mid in market_ids],
            return_exceptions=True,
        )
        total = sum(r for r in results if isinstance(r, int))
        logger.info("Synced %d price points for %d markets", total, len(market_ids))
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
