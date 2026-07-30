from __future__ import annotations

import asyncpg

from bot.domain.auctions import Auction, AuctionNotFound, Autobid


class AuctionAutobidRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_auction(self, auction_id: int) -> Auction:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT auction_id, status, currency, start_price, auction_kind,
                       start_time, end_time, message_id, discussion_message_id
                FROM public.auctions
                WHERE auction_id = $1
                """,
                int(auction_id),
            )
        if not row:
            raise AuctionNotFound(f"auction {auction_id} not found")
        return Auction.from_record(row)

    async def get_user_by_username(self, username: str) -> dict | None:
        normalized = (username or "").strip().lstrip("@").lower()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, username, full_name
                FROM public.users
                WHERE lower(username) = $1
                LIMIT 1
                """,
                normalized,
            )
        return dict(row) if row else None

    async def get_max_bid(self, auction_id: int) -> int | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT MAX(amount) FROM public.bids WHERE auction_id = $1",
                int(auction_id),
            )
        return int(value) if value is not None else None

    async def upsert(
        self,
        *,
        auction_id: int,
        target_user_id: int,
        target_username: str | None,
        max_amount: int,
        step: int,
        created_by: int,
    ) -> Autobid:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.autobids (
                    auction_id,
                    target_user_id,
                    target_username,
                    max_amount,
                    step,
                    is_active,
                    created_by
                )
                VALUES ($1, $2, $3, $4, $5, TRUE, $6)
                ON CONFLICT (auction_id, target_user_id)
                DO UPDATE SET target_username = EXCLUDED.target_username,
                              max_amount = EXCLUDED.max_amount,
                              step = EXCLUDED.step,
                              is_active = TRUE,
                              updated_at = NOW()
                RETURNING autobid_id, auction_id, target_user_id,
                          target_username, max_amount, step, is_active
                """,
                int(auction_id),
                int(target_user_id),
                (target_username or "").strip().lstrip("@") or None,
                int(max_amount),
                int(step),
                int(created_by),
            )
        return Autobid.from_record(row)

    async def disable(self, *, auction_id: int, target_user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.autobids
                SET is_active = FALSE,
                    updated_at = NOW()
                WHERE auction_id = $1
                  AND target_user_id = $2
                  AND is_active = TRUE
                RETURNING autobid_id
                """,
                int(auction_id),
                int(target_user_id),
            )
        return bool(row)

    async def list(self, *, auction_id: int | None = None, only_active: bool = True) -> list[Autobid]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT autobid_id, auction_id, target_user_id,
                       target_username, max_amount, step, is_active
                FROM public.autobids
                WHERE ($1::int IS NULL OR auction_id = $1)
                  AND ($2::boolean = FALSE OR is_active = TRUE)
                ORDER BY auction_id DESC, max_amount DESC, autobid_id DESC
                """,
                int(auction_id) if auction_id is not None else None,
                bool(only_active),
            )
        return [Autobid.from_record(row) for row in rows]
