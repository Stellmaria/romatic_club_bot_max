from __future__ import annotations

from typing import Any, Iterable, Sequence

import asyncpg


class ExchangeCatalogRepository:
    """Read-only persistence for approved exchange catalog screens."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def approved_decks(self, deck_ids: Sequence[int]) -> list[dict[str, Any]]:
        normalized = sorted({int(deck_id) for deck_id in deck_ids})
        if not normalized:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.id AS deck_id,
                       d.name AS deck_name,
                       COUNT(eb.batch_id) FILTER (
                           WHERE COALESCE(eb.status, 'pending') = 'approved'
                             AND eb.deleted_at IS NULL
                       )::int AS cnt
                FROM public.decks d
                LEFT JOIN public.exchange_batches eb ON eb.deck_id = d.id
                WHERE d.id = ANY($1::int[])
                GROUP BY d.id, d.name
                ORDER BY d.id
                """,
                normalized,
            )
        by_id = {int(row["deck_id"]): dict(row) for row in rows}
        return [
            by_id.get(
                deck_id,
                {"deck_id": deck_id, "deck_name": "", "cnt": 0},
            )
            for deck_id in normalized
        ]


    async def approved_lots(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.deck_id,
                       COALESCE(d.name, '') AS deck_name,
                       eb.price,
                       eb.currency,
                       eb.mode,
                       eb.created_at,
                       COUNT(ei.item_id)::int AS items_count
                FROM public.exchange_batches eb
                LEFT JOIN public.decks d ON d.id = eb.deck_id
                LEFT JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                GROUP BY eb.batch_id, d.name
                ORDER BY eb.batch_id DESC
                """
            )
        return [dict(row) for row in rows]

    async def approved_batches_by_card(
        self,
        deck_id: int,
        card_id: int,
        *,
        modes: Sequence[str],
    ) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT eb.batch_id
                FROM public.exchange_batches eb
                JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = $1
                  AND ei.card_id = $2
                  AND COALESCE(eb.mode, '') = ANY($3::text[])
                ORDER BY eb.batch_id DESC
                """,
                int(deck_id),
                int(card_id),
                list(modes),
            )
        return [int(row["batch_id"]) for row in rows]

    async def deck_cards_with_counts(
        self,
        deck_id: int,
        *,
        modes: Sequence[str],
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH counts AS (
                    SELECT ei.card_id, COUNT(*)::int AS cnt
                    FROM public.exchange_items ei
                    JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                    WHERE COALESCE(eb.status, 'pending') = 'approved'
                      AND eb.deleted_at IS NULL
                      AND eb.deck_id = $1
                      AND COALESCE(eb.mode, '') = ANY($2::text[])
                    GROUP BY ei.card_id
                )
                SELECT c.card_id,
                       c.card_name,
                       c.hero_name,
                       c.num,
                       COALESCE(counts.cnt, 0)::int AS cnt
                FROM public.cards c
                LEFT JOIN counts ON counts.card_id = c.card_id
                WHERE c.deck_id = $1
                ORDER BY c.num NULLS LAST, c.card_id
                """,
                int(deck_id),
                list(modes),
            )
        return [dict(row) for row in rows]

    async def has_whole_deck_lot(self, deck_id: int, *, modes: Sequence[str]) -> bool:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.exchange_batches eb
                    WHERE COALESCE(eb.status, 'pending') = 'approved'
                      AND eb.deleted_at IS NULL
                      AND eb.deck_id = $1
                      AND COALESCE(eb.mode, '') = ANY($2::text[])
                )
                """,
                int(deck_id),
                list(modes),
            )
        return bool(value)

    async def whole_deck_count(self, deck_id: int, *, modes: Sequence[str]) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM public.exchange_batches eb
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = $1
                  AND COALESCE(eb.mode, '') = ANY($2::text[])
                """,
                int(deck_id),
                list(modes),
            )
        return int(value or 0)

    async def approved_whole_deck_batch_ids(
        self,
        deck_id: int,
        *,
        mode: str,
    ) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id
                FROM public.exchange_batches eb
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = $1
                  AND COALESCE(eb.mode, '') = $2
                ORDER BY eb.batch_id DESC
                """,
                int(deck_id),
                mode,
            )
        return [int(row["batch_id"]) for row in rows]

    async def card_info(self, card_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT c.card_id,
                       c.deck_id,
                       c.card_name,
                       c.hero_name,
                       c.rarity,
                       d.name AS deck_title
                FROM public.cards c
                LEFT JOIN public.decks d ON d.id = c.deck_id
                WHERE c.card_id = $1
                """,
                int(card_id),
            )
        return dict(row) if row else {}

    async def approved_cards_by_deck(
        self,
        deck_id: int,
        *,
        modes: Sequence[str],
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH counts AS (
                    SELECT ei.card_id, COUNT(*)::int AS cnt
                    FROM public.exchange_items ei
                    JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                    WHERE COALESCE(eb.status, 'pending') = 'approved'
                      AND eb.deleted_at IS NULL
                      AND eb.deck_id = $1
                      AND COALESCE(eb.mode, '') = ANY($2::text[])
                    GROUP BY ei.card_id
                )
                SELECT c.card_id,
                       c.card_name,
                       c.hero_name,
                       COALESCE(counts.cnt, 0)::int AS cnt
                FROM public.cards c
                LEFT JOIN counts ON counts.card_id = c.card_id
                WHERE c.deck_id = $1
                ORDER BY COALESCE(counts.cnt, 0) DESC, c.card_id ASC
                """,
                int(deck_id),
                list(modes),
            )
        return [dict(row) for row in rows]

    async def card_batches(
        self,
        deck_id: int,
        card_id: int,
        *,
        modes: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT eb.batch_id,
                                eb.user_id,
                                COALESCE(u.username, '') AS username,
                                eb.price,
                                COALESCE(eb.currency, '') AS currency
                FROM public.exchange_batches eb
                JOIN public.exchange_items ei
                  ON ei.batch_id = eb.batch_id
                 AND ei.card_id = $2
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = $1
                  AND COALESCE(eb.mode, '') = ANY($3::text[])
                ORDER BY eb.batch_id DESC
                LIMIT $4
                """,
                int(deck_id),
                int(card_id),
                list(modes),
                max(1, int(limit)),
            )
        return [dict(row) for row in rows]

    async def whole_deck_batches(
        self,
        deck_id: int,
        *,
        modes: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.deck_id,
                       eb.user_id,
                       COALESCE(u.username, '') AS username,
                       eb.mode,
                       eb.status,
                       eb.price,
                       eb.currency,
                       eb.comment,
                       eb.created_at,
                       d.name AS deck_name
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                LEFT JOIN public.decks d ON d.id = eb.deck_id
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = $1
                  AND COALESCE(eb.mode, '') = ANY($2::text[])
                ORDER BY eb.batch_id DESC
                LIMIT $3
                """,
                int(deck_id),
                list(modes),
                max(1, int(limit)),
            )
        return [dict(row) for row in rows]

    async def deck_total_cards(self, deck_id: int) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*)::int FROM public.cards WHERE deck_id = $1",
                int(deck_id),
            )
        return int(value or 0)

    async def batch_items_count(self, batch_id: int) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*)::int FROM public.exchange_items WHERE batch_id = $1",
                int(batch_id),
            )
        return int(value or 0)

    async def decks_with_approved(self, deck_ids: Sequence[int]) -> list[int]:
        normalized = sorted({int(deck_id) for deck_id in deck_ids})
        if not normalized:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT eb.deck_id
                FROM public.exchange_batches eb
                WHERE COALESCE(eb.status, 'pending') = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.deck_id = ANY($1::int[])
                ORDER BY eb.deck_id
                """,
                normalized,
            )
        return [int(row["deck_id"]) for row in rows if row["deck_id"] is not None]
