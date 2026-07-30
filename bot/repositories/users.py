"""PostgreSQL reads for user account attributes."""

from __future__ import annotations

import asyncpg


class UserRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def is_luxury(self, user_id: int) -> bool:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT is_luxury FROM public.users WHERE user_id = $1",
                int(user_id),
            )
        return bool(value)

    async def mark_private_chat_opened(self, user_id: int) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.users
                SET pm_opened = TRUE,
                    first_pm_at = COALESCE(first_pm_at, NOW()),
                    last_pm_at = NOW()
                WHERE user_id = $1
                """,
                int(user_id),
            )

    async def mark_private_chat_closed(self, user_id: int) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.users
                SET pm_opened = FALSE,
                    last_pm_at = NOW()
                WHERE user_id = $1
                """,
                int(user_id),
            )
