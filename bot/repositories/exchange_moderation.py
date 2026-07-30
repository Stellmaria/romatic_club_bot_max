from __future__ import annotations

from typing import Any

import asyncpg


class ExchangeModerationRepository:
    """Persistence used by exchange moderation screens.

    This repository intentionally contains the read/admin-log queries that used
    to live in Telegram handlers. State transitions remain in ExchangeRepository
    so there is still one transactional owner for approve/reject/delete/post.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def is_admin(self, user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.admins WHERE user_id = $1)",
                int(user_id),
            )
        return bool(value)

    async def batch(self, batch_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT eb.*, u.username, u.full_name, u.is_luxury
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.batch_id = $1
                """,
                int(batch_id),
            )
        return dict(row) if row else None

    async def deck(self, deck_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, name AS deck_name, deck_type FROM public.decks WHERE id = $1",
                int(deck_id),
            )
        return dict(row) if row else None

    async def raw_items(self, batch_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT item_id, batch_id, card_id, card_name, hero_name, created_at
                FROM public.exchange_items
                WHERE batch_id = $1
                ORDER BY item_id
                """,
                int(batch_id),
            )
        return [dict(row) for row in rows]

    async def grouped_cards(self, batch_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(c.card_id, ei.card_id) AS card_id,
                       COALESCE(c.hero_name, ei.hero_name) AS hero_name,
                       COALESCE(c.card_name, ei.card_name) AS card_name,
                       COUNT(*)::int AS qty
                FROM public.exchange_items ei
                LEFT JOIN public.cards c ON c.card_id = ei.card_id
                WHERE ei.batch_id = $1
                GROUP BY 1, 2, 3
                ORDER BY hero_name NULLS LAST, card_name
                """,
                int(batch_id),
            )
        return [dict(row) for row in rows]

    async def item_count(self, batch_id: int) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*)::int FROM public.exchange_items WHERE batch_id = $1",
                int(batch_id),
            )
        return int(value or 0)

    async def pending_total(self) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM public.exchange_batches
                WHERE COALESCE(status, 'pending') = 'pending'
                  AND deleted_at IS NULL
                """
            )
        return int(value or 0)

    async def pending_batches(
        self,
        *,
        limit: int | None = None,
        include_luxury: bool = False,
    ) -> list[dict[str, Any]]:
        capped = None if limit is None else max(1, min(int(limit), 200))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury,
                       eb.deck_id,
                       d.name AS deck_name,
                       eb.mode,
                       eb.currency,
                       eb.price,
                       eb.comment,
                       eb.proof_photo_id,
                       eb.created_at,
                       COUNT(ei.item_id)::int AS items_count
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                LEFT JOIN public.decks d ON d.id = eb.deck_id
                LEFT JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE COALESCE(eb.status, 'pending') = 'pending'
                  AND eb.deleted_at IS NULL
                GROUP BY eb.batch_id, u.username, u.full_name, u.is_luxury, d.name
                ORDER BY eb.created_at DESC
                LIMIT COALESCE($1::int, 2147483647)
                """,
                capped,
            )
        result = [dict(row) for row in rows]
        if not include_luxury:
            for row in result:
                row.pop("is_luxury", None)
        return result

    async def log_admin_action(
        self,
        *,
        user_id: int,
        action_type: str,
        auction_id: int | None,
        details: str | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.audit_logs(user_id, action_type, auction_id, details)
                VALUES ($1, $2, $3, $4)
                """,
                int(user_id),
                str(action_type),
                int(auction_id) if auction_id is not None else None,
                details,
            )
