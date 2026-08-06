"""Persistence for structured future-deck preorder applications."""

from __future__ import annotations

# fmt: off
from typing import Any

import asyncpg


class PreorderSubmissionRepository:
    """Store one idempotent preorder application and its composition snapshot."""

    REVIEW_STATUSES = ("draft", "moderation", "pending", "approved")

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    async def _fetch_by_request_key(
        conn: asyncpg.Connection,
        request_key: str,
    ) -> asyncpg.Record | None:
        return await conn.fetchrow(
            """
            SELECT
                a.*,
                p.deck_id AS preorder_deck_id,
                d.name AS preorder_deck_name,
                p.mode AS preorder_mode,
                p.request_key AS preorder_request_key,
                COALESCE(items.snapshot, '{}'::jsonb) AS preorder_items
            FROM public.auctions AS a
            JOIN public.auction_preorders AS p
              ON p.auction_id = a.auction_id
            JOIN public.decks AS d
              ON d.id = p.deck_id
            LEFT JOIN LATERAL (
                SELECT jsonb_object_agg(i.rarity, i.quantity) AS snapshot
                FROM public.auction_preorder_items AS i
                WHERE i.auction_id = p.auction_id
            ) AS items ON TRUE
            WHERE p.request_key = $1
            """,
            request_key,
        )

    async def create_pending(
        self,
        *,
        owner_id: int,
        deck_id: int,
        mode: str,
        items: dict[str, int],
        request_key: str,
        card_name: str,
        hero_name: str,
        image_id: str | None,
        start_price: int,
        currency: str,
        comment: str,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1::text))",
                request_key,
            )

            existing = await self._fetch_by_request_key(conn, request_key)
            if existing is not None:
                result = dict(existing)
                result["was_existing"] = True
                return result

            deck = await conn.fetchrow(
                """
                SELECT d.id, d.name, d.deck_type
                FROM public.decks AS d
                WHERE d.id = $1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.cards AS c
                      WHERE c.deck_id = d.id
                  )
                FOR SHARE OF d
                """,
                int(deck_id),
            )
            if deck is None:
                raise ValueError("future preorder deck is missing or already released")

            row = await conn.fetchrow(
                """
                INSERT INTO public.auctions (
                    card_name,
                    hero_name,
                    image_id,
                    start_price,
                    start_time,
                    end_time,
                    status,
                    created_at,
                    currency,
                    accepted_currencies,
                    custom_offer_terms,
                    comment,
                    auction_kind,
                    proof_photo_id,
                    craft_uid_possible,
                    card_id
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    NOW(),
                    NOW() + INTERVAL '31 minutes',
                    'pending',
                    NOW(),
                    $5,
                    ARRAY[$5]::text[],
                    NULL,
                    $6,
                    'preorder',
                    NULL,
                    NULL,
                    NULL
                )
                RETURNING *
                """,
                card_name[:255],
                hero_name[:255] or None,
                image_id,
                int(start_price),
                currency,
                comment[:2000],
            )
            if row is None:
                raise RuntimeError("preorder auction insert returned no row")

            auction_id = int(row["auction_id"])
            await conn.execute(
                """
                INSERT INTO public.auction_owners (auction_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                auction_id,
                int(owner_id),
            )
            await conn.execute(
                """
                INSERT INTO public.auction_preorders (
                    auction_id,
                    deck_id,
                    mode,
                    request_key
                )
                VALUES ($1, $2, $3, $4)
                """,
                auction_id,
                int(deck_id),
                mode,
                request_key,
            )
            if items:
                await conn.executemany(
                    """
                    INSERT INTO public.auction_preorder_items (
                        auction_id,
                        rarity,
                        quantity
                    )
                    VALUES ($1, $2, $3)
                    """,
                    [
                        (auction_id, rarity, int(quantity))
                        for rarity, quantity in items.items()
                    ],
                )

            created = await self._fetch_by_request_key(conn, request_key)
            if created is None:
                raise RuntimeError("created preorder could not be read back")
            result = dict(created)
            result["was_existing"] = False
            return result

    async def list_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    a.*,
                    p.deck_id AS preorder_deck_id,
                    d.name AS preorder_deck_name,
                    p.mode AS preorder_mode,
                    p.request_key AS preorder_request_key,
                    COALESCE(items.snapshot, '{}'::jsonb) AS preorder_items
                FROM public.auctions AS a
                JOIN public.auction_preorders AS p
                  ON p.auction_id = a.auction_id
                JOIN public.decks AS d
                  ON d.id = p.deck_id
                LEFT JOIN LATERAL (
                    SELECT jsonb_object_agg(i.rarity, i.quantity) AS snapshot
                    FROM public.auction_preorder_items AS i
                    WHERE i.auction_id = p.auction_id
                ) AS items ON TRUE
                WHERE a.auction_kind = 'preorder'
                  AND a.status = ANY($1::text[])
                ORDER BY a.created_at ASC, a.auction_id ASC
                LIMIT $2
                """,
                list(self.REVIEW_STATUSES),
                int(limit),
            )
        return [dict(row) for row in rows]
# fmt: on
