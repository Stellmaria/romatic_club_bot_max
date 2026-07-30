"""PostgreSQL persistence for card and preset subscriptions.

The Telegram handlers intentionally do not import the database pool.  A pool
is injected here by the application service, which keeps the query boundary
small and makes the toggle transaction straightforward to test.
"""

from __future__ import annotations

from typing import Any

import asyncpg


class CardSubscriptionsRepository:
    """Store preset subscriptions and subscription confirmation state."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_presets(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT preset.key, preset.title
                FROM public.presets AS preset
                WHERE preset.key !~ '^deck_all_[0-9]+$'
                   OR EXISTS (
                        SELECT 1
                        FROM public.decks AS deck
                        WHERE preset.key = 'deck_all_' || deck.id::text
                   )
                """
            )
        return [dict(row) for row in rows]

    async def toggle_preset(self, user_id: int, key: str) -> bool | None:
        """Toggle a preset atomically.

        ``None`` means that the preset key does not exist.  Otherwise the
        returned boolean is the subscription state after the operation.
        """

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                preset_id = await connection.fetchval(
                    """
                    SELECT id
                    FROM public.presets
                    WHERE key = $1
                    """,
                    key,
                )
                if preset_id is None:
                    return None

                deleted = await connection.fetchrow(
                    """
                    DELETE FROM public.user_preset_subscriptions
                    WHERE user_id = $1
                      AND preset_id = $2
                    RETURNING id
                    """,
                    int(user_id),
                    int(preset_id),
                )
                if deleted is not None:
                    return False

                await connection.execute(
                    """
                    INSERT INTO public.user_preset_subscriptions (
                        user_id,
                        preset_id
                    )
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    int(user_id),
                    int(preset_id),
                )
                return True

    async def unsubscribe_preset_by_key(self, user_id: int, key: str) -> bool:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                DELETE FROM public.user_preset_subscriptions AS subscription
                USING public.presets AS preset
                WHERE subscription.user_id = $1
                  AND subscription.preset_id = preset.id
                  AND preset.key = $2
                RETURNING subscription.id
                """,
                int(user_id),
                key,
            )
        return row is not None

    async def card_metadata(self, card_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not card_ids:
            return {}

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT c.card_id,
                       c.card_name,
                       c.hero_name,
                       c.deck_id,
                       d.name AS deck_name
                FROM public.cards AS c
                LEFT JOIN public.decks AS d ON d.id = c.deck_id
                WHERE c.card_id = ANY ($1::int[])
                """,
                [int(card_id) for card_id in card_ids],
            )
        return {int(row["card_id"]): dict(row) for row in rows}

    async def subscriber_ids(self, card_id: int) -> list[int]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT user_id FROM public.user_subscriptions WHERE card_id = $1",
                int(card_id),
            )
        return [int(row["user_id"]) for row in rows]

    async def confirm_all(self, user_id: int) -> int:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                UPDATE public.user_subscriptions
                SET last_confirmed_at = now()
                WHERE user_id = $1
                RETURNING id
                """,
                int(user_id),
            )
        return len(rows)


__all__ = ["CardSubscriptionsRepository"]
