"""Queries used by auction lifecycle and comment administration."""

from __future__ import annotations

from db.core import execute, fetchrow


async def add_bid(
    auction_id: int,
    bidder_id: int,
    amount: int,
    discussion_message_id: int,
) -> None:
    """Persist one bid together with its Telegram discussion message."""

    await execute(
        """
        INSERT INTO public.bids (
            auction_id,
            bidder_id,
            amount,
            discussion_message_id
        )
        VALUES ($1, $2, $3, $4)
        """,
        auction_id,
        bidder_id,
        amount,
        discussion_message_id,
    )


async def get_bid_auction_by_discussion_id(
    discussion_message_id: int,
) -> int | None:
    """Find an auction through a bid reply message."""

    row = await fetchrow(
        "SELECT auction_id FROM public.bids WHERE discussion_message_id = $1",
        discussion_message_id,
    )
    return int(row["auction_id"]) if row and row.get("auction_id") else None


__all__ = ["add_bid", "get_bid_auction_by_discussion_id"]
