"""PostgreSQL persistence used by card-economy administration flows."""

from __future__ import annotations

from typing import Any

import asyncpg


class CardEconomyRepository:
    """Read models for economy statistics and the auction print command."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def luxury_top(
        self,
        *,
        limit: int,
        offset: int,
        rarity: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        async with self._pool.acquire() as connection:
            total_row = await connection.fetchrow(
                """
                WITH subs AS (
                    SELECT us.card_id
                    FROM public.user_subscriptions AS us
                    WHERE us.card_id IS NOT NULL
                    GROUP BY us.card_id
                )
                SELECT COUNT(*)::int AS total
                FROM subs AS s
                JOIN public.cards AS c ON c.card_id = s.card_id
                WHERE ($1::text IS NULL OR c.rarity = $1::text)
                """,
                rarity,
            )
            rows = await connection.fetch(
                """
                WITH subs AS (
                    SELECT us.card_id,
                           COUNT(DISTINCT us.user_id) AS subs_count
                    FROM public.user_subscriptions AS us
                    WHERE us.card_id IS NOT NULL
                    GROUP BY us.card_id
                ),
                sched AS (
                    SELECT LOWER(a.card_name) AS cn,
                           LOWER(a.hero_name) AS hn,
                           COUNT(*) AS scheduled_count
                    FROM public.auctions AS a
                    WHERE a.status IN ('scheduled', 'active', 'approved')
                    GROUP BY LOWER(a.card_name), LOWER(a.hero_name)
                )
                SELECT c.card_id,
                       c.card_name,
                       c.hero_name,
                       c.deck_id,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount,
                       s.subs_count,
                       COALESCE(sc.scheduled_count, 0) AS scheduled_count
                FROM subs AS s
                JOIN public.cards AS c ON c.card_id = s.card_id
                LEFT JOIN sched AS sc
                  ON sc.cn = LOWER(c.card_name)
                 AND sc.hn = LOWER(c.hero_name)
                WHERE ($3::text IS NULL OR c.rarity = $3::text)
                ORDER BY s.subs_count DESC, c.card_name ASC
                LIMIT $1 OFFSET $2
                """,
                int(limit),
                int(offset),
                rarity,
            )

        total = int((dict(total_row).get("total") if total_row else 0) or 0)
        return [dict(row) for row in rows], total

    async def auction_core(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT a.auction_id,
                       a.card_name,
                       a.hero_name,
                       a.currency,
                       a.message_id,
                       c.deck_id
                FROM public.auctions AS a
                LEFT JOIN public.cards AS c
                  ON lower(c.card_name) = lower(a.card_name)
                 AND lower(c.hero_name) = lower(a.hero_name)
                WHERE a.auction_id = $1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def auction_owner_usernames(self, auction_id: int) -> list[str]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT COALESCE(u.username, '') AS username
                FROM public.auction_owners AS ao
                LEFT JOIN public.users AS u ON u.user_id = ao.user_id
                WHERE ao.auction_id = $1
                ORDER BY ao.id
                """,
                int(auction_id),
            )

        usernames: list[str] = []
        for row in rows:
            username = str(row.get("username") or "").lstrip("@")
            if username:
                usernames.append(f"@{username}")
        return usernames

    async def fallback_winner(
        self,
        auction_id: int,
    ) -> tuple[str | None, int | None]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT u.username, b.amount
                FROM public.bids AS b
                JOIN public.users AS u ON u.user_id = b.bidder_id
                WHERE b.auction_id = $1
                ORDER BY b.amount DESC, b.placed_at
                LIMIT 1
                """,
                int(auction_id),
            )
        if not row:
            return None, None
        username = str(row.get("username") or "").lstrip("@")
        return (f"@{username}" if username else None), int(row["amount"])


__all__ = ["CardEconomyRepository"]
