from __future__ import annotations

import asyncpg


class AuctionIdStatsRepository:
    """Read-only diagnostics for sequence gaps; gaps are never fake-filled."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def missing(self, *, limit: int = 50) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH bounds AS (
                    SELECT COALESCE(MAX(auction_id), 0)::int AS max_id
                    FROM public.auctions
                )
                SELECT candidate::int AS auction_id
                FROM bounds,
                     LATERAL generate_series(1, bounds.max_id) AS candidate
                LEFT JOIN public.auctions a ON a.auction_id = candidate
                WHERE a.auction_id IS NULL
                ORDER BY candidate
                LIMIT $1
                """,
                max(1, min(int(limit), 200)),
            )
        return [int(row["auction_id"]) for row in rows]

    async def count_missing(self) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT GREATEST(
                    COALESCE(MAX(auction_id), 0) - COUNT(*) FILTER (WHERE auction_id > 0),
                    0
                )::bigint
                FROM public.auctions
                """
            )
        return int(value or 0)

