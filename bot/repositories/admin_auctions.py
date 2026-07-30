"""Administrative auction read models."""

from __future__ import annotations

from typing import Any

import asyncpg


class AdminAuctionRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def full_context_row(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT a.auction_id,
                       a.hero_name,
                       a.card_name,
                       a.start_price,
                       a.currency,
                       a.auction_kind,
                       a.craft_uid_possible,
                       a.end_time,
                       a.comment,
                       a.status,
                       c.card_id,
                       c.hero_name AS c_hero,
                       c.card_name AS c_name,
                       c.num,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount,
                       c.story,
                       c.quote,
                       c.image_id AS card_image_id,
                       d.id AS deck_id,
                       d.name AS deck_name,
                       (SELECT COUNT(*)
                        FROM public.auction_owners ao
                        WHERE ao.auction_id = a.auction_id) AS owners_count,
                       (SELECT COUNT(DISTINCT ao.user_id)
                        FROM public.auction_owners ao
                        WHERE ao.auction_id = a.auction_id) AS sellers_total,
                       (SELECT COUNT(DISTINCT ao.user_id)
                        FROM public.auction_owners ao
                        JOIN public.user_uids uu
                          ON uu.user_id = ao.user_id
                         AND uu.status = 'verified'
                        WHERE ao.auction_id = a.auction_id) AS sellers_verified
                FROM public.auctions a
                LEFT JOIN public.cards c
                  ON lower(c.card_name) = lower(a.card_name)
                 AND (a.hero_name IS NULL OR lower(c.hero_name) = lower(a.hero_name))
                LEFT JOIN public.decks d ON d.id = c.deck_id
                WHERE a.auction_id = $1
                LIMIT 1
                """,
                int(auction_id),
            )
        return dict(row) if row else None
