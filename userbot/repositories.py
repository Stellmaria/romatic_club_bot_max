"""Database access used by the Telethon userbot.

Every query is kept in this module.  ``UserbotRepository`` accepts an explicit
pool, while ``create`` is the single composition hook for the application's
managed pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from db.core import get_db_pool


@dataclass(slots=True)
class UserbotRepository:
    pool: Any

    @classmethod
    async def create(cls) -> "UserbotRepository":
        return cls(pool=await get_db_pool())

    async def fetch_auction_by_root(self, root_id: int) -> dict | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT auction_id,
                       start_price,
                       currency,
                       accepted_currencies,
                       start_time,
                       end_time,
                       status,
                       message_id,
                       discussion_message_id,
                       auction_kind
                FROM public.auctions
                WHERE discussion_message_id = $1
                   OR message_id = $1
                ORDER BY auction_id DESC
                LIMIT 1
                """,
                int(root_id),
            )
        return dict(row) if row else None

    async def fetch_best_bid(self, auction_id: int, *, lowest_wins: bool) -> int | None:
        query = (
            "SELECT MIN(amount) FROM public.bids WHERE auction_id=$1"
            if lowest_wins
            else "SELECT MAX(amount) FROM public.bids WHERE auction_id=$1"
        )
        async with self.pool.acquire() as connection:
            value = await connection.fetchval(query, int(auction_id))
        return int(value) if value is not None else None

    async def get_bid_by_message_id(self, message_id: int) -> dict | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT bid_id, auction_id, bidder_id, amount,
                       discussion_message_id, created_at
                FROM public.bids
                WHERE discussion_message_id = $1
                ORDER BY bid_id DESC
                LIMIT 1
                """,
                int(message_id),
            )
        return dict(row) if row else None

    async def update_bid_amount(self, bid_id: int, new_amount: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                "UPDATE public.bids SET amount=$1 WHERE bid_id=$2",
                int(new_amount),
                int(bid_id),
            )

    async def delete_bid(self, bid_id: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute("DELETE FROM public.bids WHERE bid_id=$1", int(bid_id))

    async def warnings_count(self, user_id: int) -> int:
        async with self.pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT warnings_count FROM public.users WHERE user_id=$1",
                int(user_id),
            )
        return int(value or 0)

    async def add_warning(self, user_id: int, reason: str, details: str = "") -> int:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO public.user_warnings (user_id, reason, details)
                    VALUES ($1, $2, $3)
                    """,
                    int(user_id),
                    reason,
                    details,
                )
                value = await connection.fetchval(
                    """
                    UPDATE public.users
                    SET warnings_count = COALESCE(warnings_count, 0) + 1
                    WHERE user_id = $1
                    RETURNING warnings_count
                    """,
                    int(user_id),
                )
        return int(value or 0)

    async def ban_user(self, user_id: int, banned_until: datetime, reason: str) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO public.user_bans (user_id, banned_until, reason)
                VALUES ($1, $2, $3)
                """,
                int(user_id),
                banned_until,
                reason,
            )

    async def auction_thread_root(self, auction_id: int) -> int | None:
        async with self.pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT discussion_message_id FROM public.auctions WHERE auction_id=$1",
                int(auction_id),
            )
        return int(value) if value else None

    async def fetch_auction_meta(self, auction_id: int) -> dict | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT auction_id, status, start_time, end_time, discussion_message_id
                FROM public.auctions
                WHERE auction_id = $1
                LIMIT 1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def remove_last_warnings(self, user_id: int, count: int) -> int:
        count = max(1, int(count))
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    DELETE FROM public.user_warnings
                    WHERE id IN (
                        SELECT id FROM public.user_warnings
                        WHERE user_id=$1
                        ORDER BY id DESC
                        LIMIT $2
                    )
                    """,
                    int(user_id),
                    count,
                )
                value = await connection.fetchval(
                    """
                    UPDATE public.users
                    SET warnings_count = GREATEST(COALESCE(warnings_count, 0) - $2, 0)
                    WHERE user_id=$1
                    RETURNING warnings_count
                    """,
                    int(user_id),
                    count,
                )
        return int(value or 0)

    async def list_bid_messages(self, auction_id: int) -> list[dict]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT bid_id, discussion_message_id
                FROM public.bids
                WHERE auction_id=$1
                """,
                int(auction_id),
            )
        return [dict(row) for row in rows]


__all__ = ["UserbotRepository"]
