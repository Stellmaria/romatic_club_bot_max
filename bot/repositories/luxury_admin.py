"""Database operations for administrative Luxury-status changes."""

from __future__ import annotations

from typing import Any

import asyncpg


class LuxuryAdminRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def find_by_id(self, user_id: int) -> dict[str, Any] | None:
        return await self._find("u.user_id = $1", int(user_id))

    async def find_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lstrip("@")
        if not normalized:
            return None
        return await self._find("lower(u.username) = lower($1)", normalized)

    async def remove_luxury(
        self,
        *,
        user_id: int,
        actor_id: int,
        username: str | None,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE public.users SET is_luxury = FALSE WHERE user_id = $1",
                    int(user_id),
                )
                await connection.execute(
                    """
                    INSERT INTO public.audit_logs (
                        user_id,
                        action_type,
                        auction_id,
                        details
                    )
                    VALUES ($1, 'remove_luxury_status', NULL, $2)
                    """,
                    int(actor_id),
                    f"removed luxury from user_id={int(user_id)} username={username or '-'}",
                )

    async def _find(self, predicate: str, value: object) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                SELECT u.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury
                FROM public.users u
                WHERE {predicate}
                LIMIT 1
                """,
                value,
            )
        return dict(row) if row else None
