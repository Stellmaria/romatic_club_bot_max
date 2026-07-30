"""Persistence boundary for warning administration and retention."""

from __future__ import annotations

from typing import Any

import asyncpg


class WarningRepository:
    """Queries used by the warning Telegram commands.

    The repository owns SQL and receives the application pool explicitly so it
    can be exercised with a disposable/fake pool without importing handlers.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_users_with_warnings(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT username, full_name, user_id, warnings_count
                FROM public.users
                WHERE warnings_count > 0
                ORDER BY warnings_count DESC, user_id
                """
            )
        return [dict(row) for row in rows]

    async def list_deleted_bid_warnings(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT warning.user_id, user_row.username, warning.issued_at
                FROM public.user_warnings AS warning
                JOIN public.users AS user_row ON user_row.user_id = warning.user_id
                WHERE warning.reason = 'delete_bid'
                ORDER BY warning.issued_at DESC
                LIMIT $1
                """,
                max(1, int(limit)),
            )
        return [dict(row) for row in rows]

    async def find_user_id_by_username(self, username: str) -> int | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT user_id
                FROM public.users
                WHERE lower(username) = lower($1)
                LIMIT 1
                """,
                username,
            )
        return int(value) if value is not None else None

    async def prune_old(
        self,
        *,
        maximum_warning_count: int,
        age_days: int,
        target_user_id: int | None,
        dry_run: bool,
    ) -> list[dict[str, int]]:
        parameters = (
            max(1, int(maximum_warning_count)),
            max(1, int(age_days)),
            int(target_user_id) if target_user_id is not None else None,
        )
        if dry_run:
            query = """
                WITH warning_counts AS (
                    SELECT user_id
                    FROM public.user_warnings
                    GROUP BY user_id
                    HAVING COUNT(*) < $1
                ), candidates AS (
                    SELECT warning.user_id
                    FROM public.user_warnings AS warning
                    JOIN warning_counts USING (user_id)
                    WHERE warning.issued_at
                        < (NOW() AT TIME ZONE 'Europe/Moscow')
                          - make_interval(days => $2)
                      AND ($3::bigint IS NULL OR warning.user_id = $3)
                )
                SELECT user_id, COUNT(*) AS removed
                FROM candidates
                GROUP BY user_id
                ORDER BY removed DESC
            """
        else:
            query = """
                WITH warning_counts AS (
                    SELECT user_id
                    FROM public.user_warnings
                    GROUP BY user_id
                    HAVING COUNT(*) < $1
                ), deleted AS (
                    DELETE FROM public.user_warnings AS warning
                    USING warning_counts
                    WHERE warning.user_id = warning_counts.user_id
                      AND warning.issued_at
                        < (NOW() AT TIME ZONE 'Europe/Moscow')
                          - make_interval(days => $2)
                      AND ($3::bigint IS NULL OR warning.user_id = $3)
                    RETURNING warning.user_id
                )
                SELECT user_id, COUNT(*) AS removed
                FROM deleted
                GROUP BY user_id
                ORDER BY removed DESC
            """

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, *parameters)
        return [{"user_id": int(row["user_id"]), "removed": int(row["removed"])} for row in rows]

    async def count_warnings(self, user_id: int) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM public.user_warnings
                WHERE user_id = $1
                """,
                int(user_id),
            )
        return int(value or 0)


__all__ = ["WarningRepository"]
