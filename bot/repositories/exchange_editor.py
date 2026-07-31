from __future__ import annotations

from typing import Any

import asyncpg

from bot.domain.auctions import ExchangeBatchNotFound, InvalidExchangeTransition


_EDITABLE_COLUMNS = {
    "mode": "mode",
    "price": "price",
    "currency": "currency",
    "comment": "comment",
    "proof_photo_id": "proof_photo_id",
}


class ApprovedExchangeEditorRepository:
    """Narrow persistence API for editing approved, unpublished exchange lots."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get(self, batch_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT eb.*, u.username
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.batch_id = $1
                """,
                int(batch_id),
            )
        if not row:
            raise ExchangeBatchNotFound(f"exchange batch {batch_id} not found")
        return dict(row)

    async def deck_card_count(self, deck_id: int) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*)::int FROM public.cards WHERE deck_id = $1",
                int(deck_id),
            )
        return int(value or 0)

    async def batch_card_count(self, batch_id: int) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT card_id)::int
                FROM public.exchange_items
                WHERE batch_id = $1
                """,
                int(batch_id),
            )
        return int(value or 0)

    async def update_field(
        self,
        batch_id: int,
        *,
        field: str,
        value: object,
    ) -> dict[str, Any]:
        column = _EDITABLE_COLUMNS.get(field)
        if column is None:
            raise ValueError(f"unsupported exchange edit field: {field}")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE public.exchange_batches
                SET {column} = $2
                WHERE batch_id = $1
                  AND status = 'approved'
                  AND deleted_at IS NULL
                RETURNING *
                """,
                int(batch_id),
                value,
            )
            if not row:
                current = await conn.fetchrow(
                    """
                    SELECT status, deleted_at
                    FROM public.exchange_batches
                    WHERE batch_id = $1
                    """,
                    int(batch_id),
                )
                if not current:
                    raise ExchangeBatchNotFound(
                        f"exchange batch {batch_id} not found"
                    )
                current_status = (
                    "deleted"
                    if current["deleted_at"] is not None
                    else str(current["status"] or "unknown")
                )
                raise InvalidExchangeTransition(
                    current=current_status,
                    target="approved_edit",
                )

            enriched = await conn.fetchrow(
                """
                SELECT eb.*, u.username
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.batch_id = $1
                """,
                int(batch_id),
            )
        return dict(enriched or row)
