"""Read-only catalog queries used while composing an auction lot."""

from __future__ import annotations

from typing import Any

import asyncpg


class AuctionSubmissionRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def card(self, card_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM public.cards WHERE card_id = $1",
                int(card_id),
            )
        return dict(row) if row else None

    async def cards_for_deck(
        self,
        deck_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT c.card_id,
                       c.card_name,
                       c.hero_name,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount
                FROM public.cards c
                WHERE c.deck_id = $1
                ORDER BY c.card_id
                LIMIT $2 OFFSET $3
                """,
                int(deck_id),
                int(limit),
                int(offset),
            )
        return [dict(row) for row in rows]

    async def future_empty_decks(self) -> list[dict[str, Any]]:
        """Return decks that do not contain cards yet and are valid for preorder."""

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT d.id AS deck_id,
                       d.name AS deck_name,
                       d.deck_type::text AS deck_type
                FROM public.decks d
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM public.cards c
                    WHERE c.deck_id = d.id
                )
                ORDER BY d.id ASC
                """
            )
        return [dict(row) for row in rows]

    async def future_empty_deck(self, deck_id: int) -> dict[str, Any] | None:
        """Revalidate one preorder deck immediately before advancing or submitting."""

        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT d.id AS deck_id,
                       d.name AS deck_name,
                       d.deck_type::text AS deck_type
                FROM public.decks d
                WHERE d.id = $1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.cards c
                      WHERE c.deck_id = d.id
                  )
                """,
                int(deck_id),
            )
        return dict(row) if row else None

    async def obtain_type(self, card_id: int) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT obtain_type::text FROM public.cards WHERE card_id = $1",
                int(card_id),
            )
        return str(value) if value is not None else None

    async def deck_type_for_card(self, card_id: int) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT d.deck_type::text
                FROM public.cards c
                JOIN public.decks d ON d.id = c.deck_id
                WHERE c.card_id = $1
                """,
                int(card_id),
            )
        return str(value) if value is not None else None

    async def deck_type_for_deck(self, deck_id: int) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT deck_type::text FROM public.decks WHERE id = $1",
                int(deck_id),
            )
        return str(value) if value is not None else None

    async def deck_type_for_identity(self, *, card_name: str, hero_name: str) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT d.deck_type::text
                FROM public.cards c
                JOIN public.decks d ON d.id = c.deck_id
                WHERE c.card_name = $1
                  AND c.hero_name = $2
                LIMIT 1
                """,
                card_name,
                hero_name,
            )
        return str(value) if value is not None else None
