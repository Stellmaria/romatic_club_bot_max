from __future__ import annotations

from typing import Any

import asyncpg


class ExchangeSubmissionRepository:
    """Read models used while an exchange submission is being assembled."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def deck_type_for_card(self, card_id: int) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.deck_type
                FROM cards c
                JOIN decks d ON d.id = c.deck_id
                WHERE c.card_id = $1
                """,
                int(card_id),
            )
        return self._deck_type(row)

    async def deck_type_for_deck(self, deck_id: int) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT deck_type FROM decks WHERE id = $1",
                int(deck_id),
            )
        return self._deck_type(row)

    async def deck_type_for_card_identity(
        self,
        card_name: str,
        hero_name: str,
    ) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.deck_type
                FROM cards c
                JOIN decks d ON d.id = c.deck_id
                WHERE c.card_name = $1
                  AND c.hero_name = $2
                LIMIT 1
                """,
                card_name,
                hero_name,
            )
        return self._deck_type(row)

    async def latest_resource_deck_ids(self, limit: int) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id AS deck_id
                FROM public.decks
                WHERE lower(COALESCE(deck_type, '')) = 'resource'
                  AND id % 2 = 0
                ORDER BY id DESC
                LIMIT $1
                """,
                int(limit),
            )
        return sorted(
            {
                int(row["deck_id"])
                for row in rows
                if row.get("deck_id") is not None
            }
        )

    async def deck_name(self, deck_id: int) -> str | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM public.decks WHERE id=$1",
                int(deck_id),
            )
        if not row:
            return None
        return (row.get("name") or "").strip() or None

    @staticmethod
    def _deck_type(row: Any) -> str | None:
        if not row:
            return None
        value = row.get("deck_type")
        return str(value) if value is not None else None
