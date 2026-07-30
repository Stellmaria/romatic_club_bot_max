"""Persistence for named Telegram custom emoji identifiers."""

from __future__ import annotations

from typing import Any

import asyncpg


class CustomEmojiRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Keep the historical maintenance command idempotent.

        Normal deployments create this table from ``database/bootstrap.sql``;
        the command remains available for installations predating migrations.
        """

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public.custom_emojis (
                    name TEXT PRIMARY KEY,
                    emoji_id TEXT NOT NULL UNIQUE
                )
                """
            )

    async def upsert(self, *, name: str, emoji_id: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO public.custom_emojis (name, emoji_id)
                VALUES ($1, $2)
                ON CONFLICT (name) DO UPDATE
                SET emoji_id = EXCLUDED.emoji_id
                """,
                name,
                emoji_id,
            )

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT name, emoji_id
                FROM public.custom_emojis
                ORDER BY name
                """
            )
        return [dict(row) for row in rows]

    async def delete(self, name: str) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                DELETE FROM public.custom_emojis
                WHERE name = $1
                RETURNING name
                """,
                name,
            )
        return str(value) if value is not None else None


__all__ = ["CustomEmojiRepository"]
