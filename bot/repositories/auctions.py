from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg


class AuctionFinalizationRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def claim_due(self, *, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
        """Atomically reserve due auctions for one worker.

        `FOR UPDATE SKIP LOCKED` allows multiple bot instances without duplicate
        winner announcements. A claimed lot leaves the active queue immediately.
        """

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    WITH due AS (
                        SELECT auction_id
                        FROM public.auctions
                        WHERE status='active'
                          AND date_trunc('minute', end_time)
                              + INTERVAL '1 minute' <= $1
                        ORDER BY end_time, auction_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT $2
                    )
                    UPDATE public.auctions a
                    SET status='finalizing',
                        finalization_started_at=now(),
                        finalization_finished_at=NULL,
                        finalization_error=NULL,
                        finalization_attempts=COALESCE(finalization_attempts, 0) + 1
                    FROM due
                    WHERE a.auction_id=due.auction_id
                    RETURNING a.*
                    """,
                    now,
                    max(1, int(limit)),
                )
        return [dict(row) for row in rows]

    async def fail_stale_claims(self, *, older_than_minutes: int = 15) -> list[int]:
        """Move abandoned `finalizing` rows to a visible failure state.

        We deliberately do not auto-retry them: Telegram delivery may have
        happened immediately before a worker crash, and blind retries would
        create duplicate winner announcements.
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE public.auctions
                SET status='finalization_failed',
                    finalization_error='worker lease expired; manual review required'
                WHERE status='finalizing'
                  AND finalization_finished_at IS NULL
                  AND finalization_started_at <=
                      now() - make_interval(mins => $1::int)
                RETURNING auction_id
                """,
                max(1, int(older_than_minutes)),
            )
        return [int(row["auction_id"]) for row in rows]

    async def get_bids(self, auction_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM public.bids
                WHERE auction_id=$1
                ORDER BY amount DESC, placed_at, bid_id
                """,
                int(auction_id),
            )
        return [dict(row) for row in rows]

    async def mark_finished(self, auction_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status='finished',
                    finalization_finished_at=now(),
                    finalization_error=NULL
                WHERE auction_id=$1 AND status='finalizing'
                RETURNING auction_id
                """,
                int(auction_id),
            )
        return bool(row)

    async def mark_failed(self, auction_id: int, error: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status='finalization_failed',
                    finalization_error=$2
                WHERE auction_id=$1 AND status='finalizing'
                RETURNING auction_id
                """,
                int(auction_id),
                (error or "unknown finalization error")[:2000],
            )
        return bool(row)
