from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Any

import asyncpg

from bot.domain.auctions import Auction, Bid, AuctionNotFound, BidAlreadyRecorded, BidNotFound


_AUCTION_COLUMNS = """
    auction_id,
    status,
    currency,
    accepted_currencies,
    start_price,
    start_time,
    end_time,
    auction_kind,
    message_id,
    discussion_message_id
"""


@dataclass(slots=True)
class AuctionBidTransaction:
    connection: asyncpg.Connection

    async def get_auction_by_discussion_message(
        self,
        discussion_message_id: int,
        *,
        for_update: bool = False,
    ) -> Auction:
        lock = "FOR UPDATE" if for_update else ""
        row = await self.connection.fetchrow(
            f"""
            SELECT {_AUCTION_COLUMNS}
            FROM public.auctions
            WHERE discussion_message_id = $1
            ORDER BY auction_id DESC
            LIMIT 1
            {lock}
            """,
            int(discussion_message_id),
        )
        if not row:
            raise AuctionNotFound(f"auction for discussion message {discussion_message_id} not found")
        return Auction.from_record(row)

    async def get_auction_by_id(self, auction_id: int, *, for_update: bool = False) -> Auction:
        lock = "FOR UPDATE" if for_update else ""
        row = await self.connection.fetchrow(
            f"""
            SELECT {_AUCTION_COLUMNS}
            FROM public.auctions
            WHERE auction_id = $1
            LIMIT 1
            {lock}
            """,
            int(auction_id),
        )
        if not row:
            raise AuctionNotFound(f"auction {auction_id} not found")
        return Auction.from_record(row)

    async def ensure_user(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str | None,
    ) -> None:
        normalized_username = (username or "").strip().lstrip("@") or None
        normalized_full_name = (full_name or "").strip() or None
        await self.connection.execute(
            """
            INSERT INTO public.users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username,
                full_name = EXCLUDED.full_name
            """,
            int(user_id),
            normalized_username[:32] if normalized_username else None,
            normalized_full_name[:255] if normalized_full_name else None,
        )

    async def is_user_banned(self, user_id: int) -> bool:
        return bool(
            await self.connection.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.user_bans b
                    WHERE b.user_id = $1
                      AND (b.banned_until IS NULL OR b.banned_until > NOW())
                    UNION ALL
                    SELECT 1
                    FROM public.user_uids uu
                    JOIN public.uid_bans ub ON ub.uid_hash = uu.uid_hash
                    WHERE uu.user_id = $1
                      AND (ub.banned_until IS NULL OR ub.banned_until > NOW())
                )
                """,
                int(user_id),
            )
        )

    async def is_user_luxury(self, user_id: int) -> bool:
        return bool(
            await self.connection.fetchval(
                "SELECT COALESCE(is_luxury, FALSE) FROM public.users WHERE user_id = $1",
                int(user_id),
            )
        )

    async def get_best_bid(
        self,
        auction_id: int,
        *,
        lowest_wins: bool,
        excluding_bid_id: int | None = None,
    ) -> int | None:
        aggregate = "MIN" if lowest_wins else "MAX"
        if excluding_bid_id is None:
            value = await self.connection.fetchval(
                f"SELECT {aggregate}(amount) FROM public.bids WHERE auction_id = $1",
                int(auction_id),
            )
        else:
            value = await self.connection.fetchval(
                f"""
                SELECT {aggregate}(amount)
                FROM public.bids
                WHERE auction_id = $1 AND bid_id <> $2
                """,
                int(auction_id),
                int(excluding_bid_id),
            )
        return int(value) if value is not None else None

    async def get_best_bid_units(
        self,
        auction_id: int,
        *,
        excluding_bid_id: int | None = None,
    ) -> int | None:
        exclusion = "" if excluding_bid_id is None else "AND bid_id <> $2"
        parameters = (int(auction_id),) if excluding_bid_id is None else (int(auction_id), int(excluding_bid_id))
        value = await self.connection.fetchval(
            f"""
            SELECT MIN(
                CASE lower(COALESCE(currency, 'алмазы'))
                    WHEN 'чашки' THEN amount * 10
                    ELSE amount
                END
            )
            FROM public.bids
            WHERE auction_id = $1 {exclusion}
            """,
            *parameters,
        )
        return int(value) if value is not None else None

    async def get_max_bid(self, auction_id: int, *, excluding_bid_id: int | None = None) -> int | None:
        return await self.get_best_bid(
            auction_id,
            lowest_wins=False,
            excluding_bid_id=excluding_bid_id,
        )

    async def insert_bid(
        self,
        *,
        auction_id: int,
        bidder_id: int,
        amount: int,
        currency: str,
        discussion_message_id: int,
    ) -> Bid:
        try:
            row = await self.connection.fetchrow(
                """
                INSERT INTO public.bids (
                    auction_id,
                    bidder_id,
                    amount,
                    currency,
                    discussion_message_id
                )
                VALUES ($1, $2, $3, $4, $5)
                RETURNING bid_id, auction_id, bidder_id, amount, currency,
                          discussion_message_id, placed_at, created_at
                """,
                int(auction_id),
                int(bidder_id),
                int(amount),
                str(currency),
                int(discussion_message_id),
            )
        except asyncpg.UniqueViolationError as exc:
            raise BidAlreadyRecorded(discussion_message_id) from exc
        return Bid.from_record(row)

    async def get_bid_by_message(self, discussion_message_id: int, *, for_update: bool = False) -> Bid:
        lock = "FOR UPDATE" if for_update else ""
        row = await self.connection.fetchrow(
            f"""
            SELECT bid_id, auction_id, bidder_id, amount, currency,
                   discussion_message_id, placed_at, created_at
            FROM public.bids
            WHERE discussion_message_id = $1
            ORDER BY bid_id DESC
            LIMIT 1
            {lock}
            """,
            int(discussion_message_id),
        )
        if not row:
            raise BidNotFound(f"bid for message {discussion_message_id} not found")
        return Bid.from_record(row)

    async def update_bid_amount(
        self, bid_id: int, amount: int, *, currency: str | None = None
    ) -> Bid:
        row = await self.connection.fetchrow(
            """
            UPDATE public.bids
            SET amount = $2,
                currency = COALESCE($3, currency)
            WHERE bid_id = $1
            RETURNING bid_id, auction_id, bidder_id, amount, currency,
                      discussion_message_id, placed_at, created_at
            """,
            int(bid_id),
            int(amount),
            str(currency) if currency is not None else None,
        )
        if not row:
            raise BidNotFound(f"bid {bid_id} not found")
        return Bid.from_record(row)

    async def delete_bid(self, bid_id: int) -> Bid:
        row = await self.connection.fetchrow(
            """
            DELETE FROM public.bids
            WHERE bid_id = $1
            RETURNING bid_id, auction_id, bidder_id, amount, currency,
                      discussion_message_id, placed_at, created_at
            """,
            int(bid_id),
        )
        if not row:
            raise BidNotFound(f"bid {bid_id} not found")
        return Bid.from_record(row)


class AuctionBidRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AuctionBidTransaction]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                yield AuctionBidTransaction(connection)

    async def get_bid_by_message(self, discussion_message_id: int) -> Bid:
        async with self.transaction() as tx:
            return await tx.get_bid_by_message(discussion_message_id)

    async def get_auction_by_id(self, auction_id: int) -> Auction:
        async with self.transaction() as tx:
            return await tx.get_auction_by_id(auction_id)
