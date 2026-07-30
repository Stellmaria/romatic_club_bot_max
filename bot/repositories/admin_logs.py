"""Persistence operations required by admin logging workflows."""

from __future__ import annotations

from typing import Any

import asyncpg


def _infer_any_rarity_from_title(title: str) -> str | None:
    normalized = (title or "").strip().lower()
    if "бронз" in normalized:
        return "bronze"
    if "сереб" in normalized:
        return "silver"
    if "золот" in normalized:
        return "gold"
    if "алмаз" in normalized or "эпик" in normalized:
        return "diamond"
    return None


def _is_any_lot(title: str) -> bool:
    normalized = (title or "").strip().lower()
    return "любая" in normalized or "любой" in normalized


class AdminLogsRepository:
    """Database boundary for owner lookup and admin audit records."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_lot(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT a.*,
                       c.card_id,
                       c.num AS card_num,
                       c.deck_id,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount,
                       c.story,
                       c.quote,
                       c.image_id AS card_image_id,
                       d.name AS deck_name
                FROM public.auctions a
                LEFT JOIN public.cards c
                  ON lower(trim(c.card_name)) = lower(trim(a.card_name))
                 AND lower(trim(coalesce(c.hero_name, ''))) =
                     lower(trim(coalesce(a.hero_name, '')))
                LEFT JOIN public.decks d ON d.id = c.deck_id
                WHERE a.auction_id = $1
                """,
                int(auction_id),
            )
            if not row:
                return None

            lot = dict(row)
            title = str(lot.get("card_name") or "").strip()
            if not lot.get("card_id") and _is_any_lot(title):
                any_rarity = _infer_any_rarity_from_title(title)
                deck_rows = await connection.fetch(
                    """
                    SELECT DISTINCT c.deck_id
                    FROM public.cards c
                    WHERE c.deck_id IS NOT NULL
                      AND ($1::text IS NULL OR lower(c.rarity) = lower($1))
                    ORDER BY c.deck_id
                    """,
                    any_rarity,
                )
                lot["any_rarity"] = any_rarity
                lot["possible_deck_ids"] = [
                    int(deck_row["deck_id"])
                    for deck_row in deck_rows
                    if deck_row["deck_id"] is not None
                ]
            return lot

    async def get_lot_owners(self, auction_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT u.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury,
                       u.is_trusted
                FROM public.auction_owners ao
                JOIN public.users u ON u.user_id = ao.user_id
                WHERE ao.auction_id = $1
                ORDER BY COALESCE(
                    NULLIF(u.username, ''),
                    u.full_name,
                    u.user_id::text
                )
                """,
                int(auction_id),
            )
        return [
            {
                "user_id": int(row["user_id"]),
                "username": row["username"] or None,
                "full_name": row["full_name"] or None,
                "is_luxury": bool(row["is_luxury"]),
                "is_trusted": bool(row["is_trusted"]),
            }
            for row in rows
        ]

    async def add_audit_action(
        self,
        *,
        user_id: int,
        action_type: str,
        auction_id: int | None,
        details: str,
    ) -> None:
        async with self._pool.acquire() as connection:
            normalized_auction_id = int(auction_id) if auction_id is not None else None
            if normalized_auction_id is not None:
                exists = await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM public.auctions
                        WHERE auction_id = $1
                    )
                    """,
                    normalized_auction_id,
                )
                if not exists:
                    normalized_auction_id = None

            await connection.execute(
                """
                INSERT INTO public.audit_logs (
                    user_id,
                    action_type,
                    auction_id,
                    details
                )
                VALUES ($1, $2, $3, $4)
                """,
                int(user_id),
                str(action_type),
                normalized_auction_id,
                details,
            )


__all__ = ["AdminLogsRepository"]
