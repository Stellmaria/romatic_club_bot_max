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
            rows = await connection.fetch("""
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
                """)
        return [dict(row) for row in rows]

    async def database_metadata(self) -> dict[str, Any]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow("""
                SELECT current_database() AS db,
                       current_user AS usr,
                       inet_server_addr()::text AS host,
                       inet_server_port() AS port,
                       current_setting('TimeZone') AS tz
                """)
        return dict(row) if row else {}

    async def delayed_luxury_count(self) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval("""
                SELECT count(*)
                FROM public.auctions a
                JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                JOIN public.users u ON u.user_id = ao.user_id
                WHERE u.is_luxury = TRUE
                  AND a.status = 'scheduled'
                  AND a.start_time > (now() + INTERVAL '3 days')
                """)
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

    async def auction_publication_health(self) -> dict[str, Any]:
        async with self._pool.acquire() as connection:
            counts = await connection.fetch("""
                SELECT status, count(*) AS count
                FROM public.auctions
                WHERE status IN (
                    'publishing',
                    'publication_deferred',
                    'publication_failed'
                )
                GROUP BY status
                ORDER BY status
                """)
            oldest = await connection.fetchrow("""
                SELECT auction_id,
                       publication_started_at,
                       now() - publication_started_at AS age
                FROM public.auctions
                WHERE status = 'publication_deferred'
                  AND message_id IS NULL
                ORDER BY publication_started_at NULLS LAST, auction_id
                LIMIT 1
                """)
            invalid = await connection.fetch("""
                SELECT auction_id, status, message_id, discussion_message_id
                FROM public.auctions
                WHERE message_id <= 0
                   OR (
                       status IN (
                           'scheduled',
                           'publishing',
                           'publication_deferred'
                       )
                       AND message_id IS NOT NULL
                   )
                ORDER BY auction_id
                LIMIT 100
                """)
            deferred = await connection.fetch("""
                SELECT auction_id,
                       start_time,
                       end_time,
                       publication_started_at,
                       discussion_message_id
                FROM public.auctions
                WHERE status = 'publication_deferred'
                  AND message_id IS NULL
                ORDER BY publication_started_at NULLS LAST, auction_id
                LIMIT 100
                """)
        return {
            "counts": [dict(row) for row in counts],
            "oldest_deferred": dict(oldest) if oldest else None,
            "invalid": [dict(row) for row in invalid],
            "deferred": [dict(row) for row in deferred],
        }
