"""Persistence queries for schedule publication timing and pin rotation."""

from __future__ import annotations

from datetime import date, datetime

from db.core import fetchrow


async def get_last_auction_close_for_day(day: date) -> datetime | None:
    """Return the real bidding-close boundary of the day's last auction.

    Auction bids are accepted through the final second of ``end_time``'s
    minute, so publication may start only at the next whole minute.
    """

    row = await fetchrow(
        """
        SELECT MAX(date_trunc('minute', end_time) + interval '1 minute') AS closes_at
        FROM public.auctions
        WHERE end_time IS NOT NULL
          AND CASE
                WHEN pg_typeof(start_time)::text = 'timestamp with time zone'
                    THEN (start_time AT TIME ZONE 'Europe/Moscow')::date
                ELSE start_time::date
              END = $1
          AND status IN (
                'approved',
                'scheduled',
                'publishing',
                'active',
                'finalizing',
                'finished',
                'finalization_failed',
                'closed',
                'ended',
                'completed'
          )
        """,
        day,
    )
    if not row or row["closes_at"] is None:
        return None
    return row["closes_at"]


async def get_previous_published_schedule_message(target_date: date) -> int | None:
    """Return only the preceding schedule post, never unrelated channel pins."""

    row = await fetchrow(
        """
        SELECT channel_message_id
        FROM public.schedule_publication_reviews
        WHERE target_date < $1
          AND status = 'published'
          AND channel_message_id IS NOT NULL
        ORDER BY target_date DESC
        LIMIT 1
        """,
        target_date,
    )
    if not row or row["channel_message_id"] is None:
        return None
    return int(row["channel_message_id"])


__all__ = [
    "get_last_auction_close_for_day",
    "get_previous_published_schedule_message",
]
