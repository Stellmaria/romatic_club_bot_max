from __future__ import annotations

from typing import Any

import asyncpg


class AuctionAdminRepository:
    """Small transactional boundary for moderator-only auction mutations."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def delete_bid_with_warning(
        self,
        *,
        discussion_message_id: int,
        reason: str,
    ) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            bid = await conn.fetchrow(
                """
                SELECT
                    b.bid_id,
                    b.auction_id,
                    b.amount,
                    b.currency,
                    b.bidder_id,
                    u.username
                FROM public.bids b
                JOIN public.users u ON u.user_id = b.bidder_id
                WHERE b.discussion_message_id = $1
                FOR UPDATE OF b, u
                """,
                int(discussion_message_id),
            )
            if not bid:
                return None

            await conn.execute(
                "DELETE FROM public.bids WHERE bid_id = $1",
                int(bid["bid_id"]),
            )
            await conn.execute(
                """
                INSERT INTO public.user_warnings (user_id, reason, issued_at)
                VALUES ($1, $2, now())
                """,
                int(bid["bidder_id"]),
                (reason or "admin_delete_bid")[:255],
            )
            warnings_count = await conn.fetchval(
                """
                UPDATE public.users
                SET warnings_count = COALESCE(warnings_count, 0) + 1
                WHERE user_id = $1
                RETURNING warnings_count
                """,
                int(bid["bidder_id"]),
            )
            banned = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM public.user_bans
                    WHERE user_id = $1
                      AND (banned_until IS NULL OR banned_until > now())
                )
                """,
                int(bid["bidder_id"]),
            )

        result = dict(bid)
        result["warnings_count"] = int(warnings_count or 0)
        result["is_banned"] = bool(banned)
        return result
