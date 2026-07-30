from __future__ import annotations

from typing import Any, Iterable

import asyncpg

from bot.domain.auctions import (
    ExchangeBatchNotFound,
    InvalidExchangeTransition,
)
from bot.domain.auctions.workflows import ExchangeDraft


class ExchangeRepository:
    """Transactional persistence for exchange batches and their cards."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, draft: ExchangeDraft) -> dict[str, Any]:
        card_ids = [int(card_id) for card_id in draft.card_ids]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                cards = await conn.fetch(
                    """
                    SELECT card_id, card_name, hero_name
                    FROM public.cards
                    WHERE deck_id = $1
                      AND card_id = ANY($2::int[])
                    """,
                    int(draft.deck_id),
                    sorted(set(card_ids)),
                )
                cards_by_id = {int(row["card_id"]): row for row in cards}
                missing = sorted(set(card_ids) - set(cards_by_id))
                if missing:
                    raise ValueError(
                        "cards do not belong to selected deck or do not exist: "
                        + ", ".join(map(str, missing))
                    )

                batch = await conn.fetchrow(
                    """
                    INSERT INTO public.exchange_batches (
                        user_id, deck_id, mode, currency, price,
                        comment, proof_photo_id, status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
                    RETURNING *
                    """,
                    int(draft.user_id),
                    int(draft.deck_id),
                    draft.mode,
                    draft.currency.value,
                    int(draft.price),
                    draft.comment,
                    draft.proof_photo_id,
                )

                if card_ids:
                    await conn.executemany(
                        """
                        INSERT INTO public.exchange_items (
                            batch_id, card_id, card_name, hero_name
                        )
                        VALUES ($1, $2, $3, $4)
                        """,
                        [
                            (
                                int(batch["batch_id"]),
                                card_id,
                                cards_by_id[card_id]["card_name"],
                                cards_by_id[card_id]["hero_name"],
                            )
                            for card_id in card_ids
                        ],
                    )
        return dict(batch)

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

    async def items(self, batch_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.card_id, c.hero_name, c.card_name, COUNT(*)::int AS qty
                FROM public.exchange_items ei
                JOIN public.cards c ON c.card_id = ei.card_id
                WHERE ei.batch_id = $1
                GROUP BY c.card_id, c.hero_name, c.card_name
                ORDER BY c.hero_name NULLS LAST, c.card_name
                """,
                int(batch_id),
            )
        return [dict(row) for row in rows]

    async def moderate(
        self,
        batch_id: int,
        *,
        target_status: str,
        moderator_id: int,
        moderator_username: str | None,
        moderator_comment: str | None = None,
    ) -> dict[str, Any]:
        if target_status not in {"approved", "rejected"}:
            raise ValueError(f"unsupported moderation status: {target_status}")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.exchange_batches
                SET status = $2,
                    moderated_at = NOW(),
                    moderated_by = $3,
                    moderated_username = $4,
                    moderated_comment = $5,
                    moderator_id = $3,
                    moderator_username = $4,
                    moderator_comment = $5
                WHERE batch_id = $1
                  AND status = 'pending'
                  AND deleted_at IS NULL
                RETURNING *
                """,
                int(batch_id),
                target_status,
                int(moderator_id),
                (moderator_username or "").strip().lstrip("@") or None,
                (moderator_comment or "").strip() or None,
            )
            if not row:
                current = await conn.fetchval(
                    "SELECT status FROM public.exchange_batches WHERE batch_id = $1",
                    int(batch_id),
                )
                if current is None:
                    raise ExchangeBatchNotFound(
                        f"exchange batch {batch_id} not found"
                    )
                raise InvalidExchangeTransition(
                    current=str(current),
                    target=target_status,
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
            if enriched:
                row = enriched
        return dict(row)

    async def mark_posted(
        self,
        batch_id: int,
        *,
        chat_id: int,
        message_id: int,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.exchange_batches
                SET status = 'published',
                    posted_chat_id = $2,
                    posted_message_id = $3,
                    posted_at = NOW(),
                    publication_finished_at = NOW(),
                    publication_error = NULL
                WHERE batch_id = $1
                  AND status = 'publishing'
                  AND posted_message_id IS NULL
                  AND deleted_at IS NULL
                RETURNING *
                """,
                int(batch_id),
                int(chat_id),
                int(message_id),
            )
            if not row:
                existing = await conn.fetchrow(
                    "SELECT * FROM public.exchange_batches WHERE batch_id = $1",
                    int(batch_id),
                )
                if not existing:
                    raise ExchangeBatchNotFound(
                        f"exchange batch {batch_id} not found"
                    )
                if existing["posted_message_id"] is not None:
                    return dict(existing)
                raise InvalidExchangeTransition(
                    current=str(existing["status"]),
                    target="published",
                )
        return dict(row)

    async def claim_for_post(self, batch_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE public.exchange_batches
                    SET status = 'publication_failed',
                        publication_finished_at = NOW(),
                        publication_error = 'publisher lease expired; manual review required'
                    WHERE batch_id = $1
                      AND status = 'publishing'
                      AND posted_message_id IS NULL
                      AND publication_started_at <= NOW() - INTERVAL '15 minutes'
                    """,
                    int(batch_id),
                )
                row = await conn.fetchrow(
                    """
                    UPDATE public.exchange_batches
                    SET status = 'publishing',
                        publication_started_at = NOW(),
                        publication_finished_at = NULL,
                        publication_error = NULL
                    WHERE batch_id = $1
                      AND status = 'approved'
                      AND posted_message_id IS NULL
                      AND deleted_at IS NULL
                    RETURNING *
                    """,
                    int(batch_id),
                )
                if not row:
                    existing = await conn.fetchrow(
                        "SELECT * FROM public.exchange_batches WHERE batch_id = $1",
                        int(batch_id),
                    )
                    if not existing:
                        raise ExchangeBatchNotFound(
                            f"exchange batch {batch_id} not found"
                        )
                    raise InvalidExchangeTransition(
                        current=str(existing["status"]),
                        target="publishing",
                    )
        return dict(row)

    async def release_post_claim(self, batch_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE public.exchange_batches
                SET status = 'approved',
                    publication_started_at = NULL,
                    publication_finished_at = NOW(),
                    publication_error = 'telegram delivery failed before confirmation'
                WHERE batch_id = $1
                  AND status = 'publishing'
                  AND posted_message_id IS NULL
                """,
                int(batch_id),
            )
        return result == "UPDATE 1"

    async def soft_delete(
        self,
        batch_id: int,
        *,
        moderator_id: int | None = None,
        moderator_username: str | None = None,
        moderator_comment: str | None = None,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.exchange_batches
                SET status = 'deleted',
                    deleted_at = COALESCE(deleted_at, NOW()),
                    moderated_at = COALESCE(moderated_at, NOW()),
                    moderated_by = COALESCE($2, moderated_by),
                    moderated_username = COALESCE($3, moderated_username),
                    moderated_comment = COALESCE($4, moderated_comment),
                    moderator_id = COALESCE($2, moderator_id),
                    moderator_username = COALESCE($3, moderator_username),
                    moderator_comment = COALESCE($4, moderator_comment)
                WHERE batch_id = $1
                  AND deleted_at IS NULL
                RETURNING *
                """,
                int(batch_id),
                int(moderator_id) if moderator_id is not None else None,
                (moderator_username or "").strip().lstrip("@") or None,
                (moderator_comment or "").strip() or None,
            )
            if not row:
                existing = await conn.fetchrow(
                    "SELECT * FROM public.exchange_batches WHERE batch_id = $1",
                    int(batch_id),
                )
                if not existing:
                    raise ExchangeBatchNotFound(
                        f"exchange batch {batch_id} not found"
                    )
                return dict(existing)
        return dict(row)

    async def pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.*, u.username, d.name AS deck_name,
                       COUNT(ei.item_id)::int AS items_count
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                LEFT JOIN public.decks d ON d.id = eb.deck_id
                LEFT JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE eb.status = 'pending'
                  AND eb.deleted_at IS NULL
                GROUP BY eb.batch_id, u.username, d.name
                ORDER BY eb.created_at ASC, eb.batch_id
                LIMIT $1
                """,
                max(1, int(limit)),
            )
        return [dict(row) for row in rows]
