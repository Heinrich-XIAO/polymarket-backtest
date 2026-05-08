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

_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("politics", [
        "election", "president", "congress", "senate", "vote", "democrat", "republican",
        "biden", "trump", "harris", "primary", "ballot", "governor", "parliament",
        "minister", "party", "political", "government", "campaign", "inauguration",
        "supreme court", "legislation", "impeach", "poll ",
    ]),
    ("crypto", [
        "bitcoin", "ethereum", "btc", "eth", "sol", "solana", "crypto", "blockchain",
        "altcoin", "defi", "nft", "token", "binance", "coinbase", "matic", "polygon",
        "avalanche", "avax", "doge", "dogecoin", "xrp", "ripple", "cardano", "ada",
        "halving", "satoshi",
    ]),
    ("economics", [
        "gdp", "inflation", "cpi", "fed ", "federal reserve", "interest rate", "economy",
        "recession", "unemployment", "jobs", "payroll", "stock market", "s&p", "nasdaq",
        "dow jones", "oil price", "gold price", "bond", "yield", "ipo",
    ]),
    ("sports", [
        "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
        "championship", "super bowl", "world cup", "olympics", "tennis", "golf",
        "match", "tournament", "playoffs", "league", "team", "player", "coach",
        "mls", "ufc", "boxing", "formula 1", "f1", "derby",
    ]),
    ("entertainment", [
        "oscar", "academy award", "emmy", "grammy", "box office", "movie", "film",
        "season", "episode", "tv show", "celebrity", "singer", "actor", "album",
        "music", "award", "billie", "taylor swift",
    ]),
]


def _assign_category(question: str) -> str | None:
    """Assign category by keyword-matching the market question."""
    q = question.lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(kw in q for kw in keywords):
            return category
    return None


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

                # Extract current YES price from outcomePrices
                outcome_prices_raw = m.get("outcomePrices", "[]")
                try:
                    outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                    current_price = float(outcome_prices[0]) if outcome_prices else None
                except Exception:
                    current_price = None

                question = m.get("question", "")
                category = m.get("category") or _assign_category(question)
                rows.append((
                    str(market_id),
                    question,
                    category,
                    end_date,
                    float(m.get("volume") or m.get("volumeNum") or 0),
                    bool(m.get("active", True)),
                    token_id,
                    float(m.get("volume24hr") or 0),
                    current_price,
                ))

            if rows:
                async with self.pool.acquire() as conn:
                    await conn.executemany(
                        """
                        INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id, daily_volume, current_price)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
                        ON CONFLICT (id) DO UPDATE SET
                            question      = EXCLUDED.question,
                            category      = EXCLUDED.category,
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
                synced += len(rows)

            if len(markets) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        logger.info("Synced %d markets", synced)
        return synced

    async def sync_price_history(self, market_id: str, token_id: str, fidelity: int = 1440) -> int:
        """Fetch price history for one market via CLOB API. fidelity=1440 daily, 60 hourly."""
        try:
            resp = await self.clob.get(
                "/prices-history",
                params={"market": token_id, "interval": "max", "fidelity": str(fidelity)},
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

    async def sync_near_resolution(self, max_days: int = 90, limit: int = 200) -> int:
        """Sync markets ending within max_days, then fetch their price histories."""
        from datetime import date, timedelta
        today = date.today()
        end_date_max = (today + timedelta(days=max_days)).isoformat()
        end_date_min = today.isoformat()

        inserted = 0
        offset = 0
        fetched_ids: list[tuple[str, str | None]] = []

        async with httpx.AsyncClient(base_url=GAMMA_BASE, timeout=TIMEOUT) as client:
            while len(fetched_ids) < limit:
                try:
                    resp = await client.get(
                        "/markets",
                        params={
                            "limit": BATCH_SIZE,
                            "offset": offset,
                            "active": "true",
                            "end_date_min": end_date_min,
                            "end_date_max": end_date_max,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.error("near-resolution sync error at offset %d: %s", offset, exc)
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
                    clob_ids_raw = m.get("clobTokenIds", "[]")
                    try:
                        clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                        token_id = clob_ids[0] if clob_ids else None
                    except Exception:
                        token_id = None
                    outcome_prices_raw = m.get("outcomePrices", "[]")
                    try:
                        outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                        current_price = float(outcome_prices[0]) if outcome_prices else None
                    except Exception:
                        current_price = None
                    q2 = m.get("question", "")
                    rows.append((
                        str(market_id),
                        q2,
                        m.get("category") or _assign_category(q2),
                        end_date,
                        float(m.get("volume") or m.get("volumeNum") or 0),
                        True,
                        token_id,
                        float(m.get("volume24hr") or 0),
                        current_price,
                    ))
                    if token_id:
                        fetched_ids.append((str(market_id), token_id))

                if rows:
                    async with self.pool.acquire() as conn:
                        await conn.executemany(
                            """
                            INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id, daily_volume, current_price)
                            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
                            ON CONFLICT (id) DO UPDATE SET
                                question      = EXCLUDED.question,
                                category      = EXCLUDED.category,
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

                if len(markets) < BATCH_SIZE:
                    break
                offset += BATCH_SIZE

        logger.info("Near-resolution sync: %d markets (within %d days)", inserted, max_days)

        # Sync price histories for the new near-resolution markets
        total_pts = 0
        batch_size = 20
        for i in range(0, len(fetched_ids), batch_size):
            batch = fetched_ids[i:i + batch_size]
            results = await asyncio.gather(
                *[self.sync_price_history(mid, tid) for mid, tid in batch],
                return_exceptions=True,
            )
            total_pts += sum(r for r in results if isinstance(r, int))
            await asyncio.sleep(0.5)

        logger.info("Near-resolution histories: %d price points", total_pts)
        return inserted

    async def sync_resolved_markets(self, limit: int = 500) -> int:
        """Sync resolved (active=false) markets + their price histories. Great for backtesting outcomes."""
        synced = 0
        offset = 0
        fetched_ids: list[tuple[str, str]] = []

        while synced < limit:
            try:
                resp = await self.gamma.get(
                    "/markets",
                    params={"limit": BATCH_SIZE, "offset": offset, "active": "false", "closed": "true"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.error("Resolved markets fetch error at offset %d: %s", offset, exc)
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
                clob_ids_raw = m.get("clobTokenIds", "[]")
                try:
                    clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
                    token_id = clob_ids[0] if clob_ids else None
                except Exception:
                    token_id = None
                outcome_prices_raw = m.get("outcomePrices", "[]")
                try:
                    outcome_prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                    current_price = float(outcome_prices[0]) if outcome_prices else None
                except Exception:
                    current_price = None

                # Store resolved outcome (1.0 = YES resolved, 0.0 = NO resolved)
                resolved_price = None
                if m.get("resolved"):
                    try:
                        res_prices = json.loads(m.get("resolutionSources") or "[]")
                        _ = res_prices
                    except Exception:
                        pass
                    # Use final outcomePrices as resolved value
                    if current_price is not None:
                        resolved_price = current_price

                q3 = m.get("question", "")
                rows.append((
                    str(market_id),
                    q3,
                    m.get("category") or _assign_category(q3),
                    end_date,
                    float(m.get("volume") or m.get("volumeNum") or 0),
                    False,  # active = False for resolved markets
                    token_id,
                    float(m.get("volume24hr") or 0),
                    resolved_price or current_price,
                ))
                if token_id:
                    fetched_ids.append((str(market_id), token_id))

            if rows:
                async with self.pool.acquire() as conn:
                    await conn.executemany(
                        """
                        INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id, daily_volume, current_price)
                        VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
                        ON CONFLICT (id) DO UPDATE SET
                            question      = EXCLUDED.question,
                            category      = EXCLUDED.category,
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
                synced += len(rows)

            if len(markets) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        logger.info("Resolved markets synced: %d", synced)

        # Sync price histories for resolved markets
        total_pts = 0
        batch_size = 20
        for i in range(0, len(fetched_ids), batch_size):
            batch = fetched_ids[i:i + batch_size]
            results = await asyncio.gather(
                *[self.sync_price_history(mid, tid) for mid, tid in batch],
                return_exceptions=True,
            )
            total_pts += sum(r for r in results if isinstance(r, int))
            await asyncio.sleep(0.5)

        logger.info("Resolved histories: %d price points", total_pts)
        return synced

    async def sync_all_histories(self, max_markets: int = 200, prefer_competitive: bool = False, fidelity: int = 1440) -> int:
        """Sync price history for active markets that have a token_id."""
        async with self.pool.acquire() as conn:
            if prefer_competitive:
                rows = await conn.fetch(
                    """SELECT id, token_id FROM markets
                       WHERE active = TRUE AND token_id IS NOT NULL
                         AND current_price IS NOT NULL
                         AND current_price BETWEEN 0.1 AND 0.9
                       ORDER BY ABS(current_price - 0.5) ASC, volume DESC
                       LIMIT $1""",
                    max_markets,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, token_id FROM markets
                       WHERE active = TRUE AND token_id IS NOT NULL
                       ORDER BY volume DESC LIMIT $1""",
                    max_markets,
                )
        if not rows:
            logger.warning("No markets with token_id found — run sync_markets first")
            return 0

        total = 0
        batch_size = 20
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            results = await asyncio.gather(
                *[self.sync_price_history(r["id"], r["token_id"], fidelity=fidelity) for r in batch],
                return_exceptions=True,
            )
            total += sum(r for r in results if isinstance(r, int))
            await asyncio.sleep(0.5)

        logger.info("Synced %d price points for %d markets (fidelity=%d)", total, len(rows), fidelity)
        return total

    # ── Data API (data-api.polymarket.com) ────────────────────────────────────

    async def sync_trades_for_market(self, market_id: str) -> int:
        """Fetch all historical trades from data-api.polymarket.com, aggregate to daily candles."""
        from collections import defaultdict
        DATA_API = "https://data-api.polymarket.com"
        all_trades: list[dict] = []
        offset = 0
        limit = 500  # keep each request small

        async with httpx.AsyncClient(base_url=DATA_API, timeout=60.0,
                                     headers={"User-Agent": "polymarket-backtest/1.0"}) as client:
            while len(all_trades) < 100_000:  # safety cap per market
                try:
                    resp = await client.get("/trades", params={
                        "market": market_id,
                        "limit": limit,
                        "offset": offset,
                    })
                    resp.raise_for_status()
                    trades = resp.json()
                except Exception as exc:
                    logger.debug("Data API error for %s: %s", market_id, exc)
                    break
                if not trades:
                    break
                all_trades.extend(trades)
                if len(trades) < limit:
                    break
                offset += limit
                await asyncio.sleep(0.05)

        if not all_trades:
            return 0

        # Aggregate trades to daily price candles
        daily: dict = defaultdict(lambda: {"prices": [], "volume": 0.0})
        for t in all_trades:
            try:
                ts_raw = t.get("timestamp") or t.get("createdAt")
                price_f = float(t["price"])
                if ts_raw is None or not (0.001 < price_f < 0.999):
                    continue
                ts = _parse_ts(ts_raw)
                d = ts.date()
                daily[d]["prices"].append(price_f)
                daily[d]["volume"] += float(t.get("usdAmount") or t.get("size") or 0)
            except Exception:
                continue

        rows = []
        for d, data in sorted(daily.items()):
            if not data["prices"]:
                continue
            avg_price = sum(data["prices"]) / len(data["prices"])
            ts = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
            rows.append((market_id, ts, round(avg_price, 6), round(data["volume"], 2)))

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

    async def sync_data_api_histories(self, max_markets: int = 200) -> int:
        """Sync trade-based daily candles from data-api.polymarket.com for top-volume markets."""
        async with self.pool.acquire() as conn:
            market_rows = await conn.fetch(
                """SELECT id FROM markets
                   WHERE volume > 0
                   ORDER BY volume DESC LIMIT $1""",
                max_markets,
            )
        if not market_rows:
            return 0

        total = 0
        batch_size = 10  # conservative — each market may have many pages
        for i in range(0, len(market_rows), batch_size):
            batch = market_rows[i:i + batch_size]
            results = await asyncio.gather(
                *[self.sync_trades_for_market(r["id"]) for r in batch],
                return_exceptions=True,
            )
            total += sum(r for r in results if isinstance(r, int))
            await asyncio.sleep(1.0)

        logger.info("Data API sync: %d price points for %d markets", total, len(market_rows))
        return total

    # ── HuggingFace datasets-server ───────────────────────────────────────────

    async def sync_hf_markets(self, limit: int = 5000) -> int:
        """Import market metadata from HuggingFace SII-WANGZJ/Polymarket_data (train split)."""
        HF_API = "https://datasets-server.huggingface.co"
        DATASET = "SII-WANGZJ/Polymarket_data"
        PAGE = 100
        inserted = 0

        async with httpx.AsyncClient(base_url=HF_API, timeout=60.0) as client:
            for offset in range(0, limit, PAGE):
                try:
                    resp = await client.get("/rows", params={
                        "dataset": DATASET,
                        "config": "default",
                        "split": "train",
                        "offset": offset,
                        "length": PAGE,
                    })
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.error("HF API error at offset %d: %s", offset, exc)
                    break

                row_items = data.get("rows", [])
                if not row_items:
                    break

                rows = []
                for item in row_items:
                    r = item.get("row", {})
                    # HF dataset schema: conditionId, question, token1, token2, outcome_prices, volume, end_date
                    market_id = r.get("conditionId") or r.get("token1")
                    if not market_id:
                        continue
                    end_date = _parse_dt(r.get("endDate") or r.get("end_date"))
                    # outcome_prices may be "[0.65, 0.35]" string or list
                    yes_price = None
                    try:
                        op = r.get("outcomePrices") or r.get("outcome_prices") or "[]"
                        if isinstance(op, str):
                            import re
                            nums = re.findall(r"[\d.]+", op)
                            yes_price = float(nums[0]) if nums else None
                        elif isinstance(op, list):
                            yes_price = float(op[0]) if op else None
                    except Exception:
                        pass

                    # Use token1 as token_id for CLOB price history lookup
                    token_id = r.get("token1") or None

                    vol = float(r.get("volume") or r.get("volumeNum") or 0)
                    if vol < 100:  # skip zero-volume auto-generated markets
                        continue
                    rows.append((
                        str(market_id),
                        r.get("question", ""),
                        r.get("category") or None,
                        end_date,
                        vol,
                        bool(r.get("active", False)),
                        token_id,
                        0.0,
                        yes_price,
                    ))

                if rows:
                    async with self.pool.acquire() as conn:
                        await conn.executemany(
                            """
                            INSERT INTO markets (id, question, category, end_date, volume, active, synced_at, token_id, daily_volume, current_price)
                            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9)
                            ON CONFLICT (id) DO UPDATE SET
                                question      = EXCLUDED.question,
                                category      = COALESCE(EXCLUDED.category, markets.category),
                                volume        = GREATEST(EXCLUDED.volume, markets.volume),
                                active        = EXCLUDED.active,
                                synced_at     = NOW(),
                                token_id      = COALESCE(markets.token_id, EXCLUDED.token_id),
                                current_price = COALESCE(markets.current_price, EXCLUDED.current_price)
                            """,
                            rows,
                        )
                    inserted += len(rows)

                if len(row_items) < PAGE:
                    break
                await asyncio.sleep(0.2)

        logger.info("HuggingFace markets imported: %d", inserted)
        return inserted


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
