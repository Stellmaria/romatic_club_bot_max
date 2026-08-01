"""Queries used by auction lifecycle administration."""

from __future__ import annotations

from db.core import fetchrow


async def get_bid_auction_by_discussion_id(
    discussion_message_id: int,
) -> int | None:
    """Find an auction through a bid reply message."""

    row = await fetchrow(
        "SELECT auction_id FROM public.bids WHERE discussion_message_id = $1",
        discussion_message_id,
    )
    return int(row["auction_id"]) if row and row.get("auction_id") else None


__all__ = ["get_bid_auction_by_discussion_id"]
