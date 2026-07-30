"""Persistence boundary for moderator thanks counters."""

from __future__ import annotations

import asyncpg


class AdminThanksRepository:
    """Read moderator feedback totals using an explicitly supplied pool."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def totals(self, normalized_author: str) -> tuple[int, int]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                WITH totals AS (
                    SELECT COALESCE(SUM(thanks_total), 0)::bigint AS total
                    FROM public.admin_thanks_totals
                    WHERE lower(trim(leading '@' FROM author)) = $1
                ), users AS (
                    SELECT COUNT(DISTINCT user_id)::bigint AS users
                    FROM public.admin_thanks_users
                    WHERE lower(trim(leading '@' FROM author)) = $1
                )
                SELECT totals.total, users.users
                FROM totals CROSS JOIN users
                """,
                normalized_author,
            )
        return int(row["total"] or 0), int(row["users"] or 0)


__all__ = ["AdminThanksRepository"]
