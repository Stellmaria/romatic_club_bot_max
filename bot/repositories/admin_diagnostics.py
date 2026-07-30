"""Read-only PostgreSQL queries for administrative diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg


class AdminDiagnosticsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def delayed_luxury_lots(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT a.auction_id,
                       a.card_name,
                       a.hero_name,
                       a.start_time,
                       u.user_id,
                       u.username,
                       u.full_name
                FROM public.auctions a
                JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                JOIN public.users u ON u.user_id = ao.user_id
                WHERE u.is_luxury = TRUE
                  AND a.status = 'scheduled'
                  AND a.start_time > (now() + INTERVAL '3 days')
                ORDER BY a.start_time
                LIMIT 400
                """
            )
        return [dict(row) for row in rows]

    async def database_metadata(self) -> dict[str, Any]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT current_database() AS db,
                       current_user AS usr,
                       inet_server_addr()::text AS host,
                       inet_server_port() AS port,
                       current_setting('TimeZone') AS tz
                """
            )
        return dict(row) if row else {}

    async def delayed_luxury_count(self) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT count(*)
                FROM public.auctions a
                JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                JOIN public.users u ON u.user_id = ao.user_id
                WHERE u.is_luxury = TRUE
                  AND a.status = 'scheduled'
                  AND a.start_time > (now() + INTERVAL '3 days')
                """
            )
        return int(value or 0)

    async def owners_with_multiple_future_lots(
        self,
        *,
        after: datetime,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                WITH future AS (
                    SELECT a.auction_id,
                           a.card_name,
                           a.hero_name,
                           a.start_time,
                           u.user_id,
                           u.username,
                           u.full_name
                    FROM public.auctions a
                    JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                    JOIN public.users u ON u.user_id = ao.user_id
                    WHERE a.status = 'scheduled'
                      AND a.start_time > $1
                ),
                owners AS (
                    SELECT user_id, COUNT(*) AS cnt
                    FROM future
                    GROUP BY user_id
                    HAVING COUNT(*) > 1
                )
                SELECT f.*, o.cnt
                FROM future f
                JOIN owners o USING (user_id)
                ORDER BY o.cnt DESC, f.start_time
                LIMIT 400
                """,
                after,
            )
        return [dict(row) for row in rows]
