"""Read models used by the administrative schedule UI."""

from __future__ import annotations

import asyncpg


class AdminScheduleRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def last_nonempty_deck_id(self) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT COALESCE(MAX(deck_id), 0) FROM public.cards"
            )
        return int(value or 0)
