"""Read models used by miscellaneous auction discussion handlers."""

from __future__ import annotations

from typing import Any

import asyncpg


class AuctionCommentRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def is_active_lot_owner(self, *, user_id: int, username: str) -> bool:
        async with self._pool.acquire() as connection:
            return bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM public.auctions AS auction
                        JOIN public.auction_owners AS owner USING (auction_id)
                        LEFT JOIN public.users AS user_row
                          ON user_row.user_id = owner.user_id
                        WHERE auction.status = 'active'
                          AND (
                              owner.user_id = $1
                              OR lower(user_row.username) = $2
                          )
                    )
                    """,
                    int(user_id),
                    (username or "").strip().lower(),
                )
            )

    async def get_current_auction(self) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM public.auctions
                WHERE start_time <= NOW()
                  AND end_time >= NOW()
                  AND status = 'active'
                ORDER BY end_time ASC
                LIMIT 1
                """
            )
        return dict(row) if row else None


__all__ = ["AuctionCommentRepository"]
