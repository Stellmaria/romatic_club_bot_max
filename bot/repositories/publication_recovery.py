from __future__ import annotations

import asyncpg


_PENDING_MARKER = "telegram scheduled message awaiting channel_post"


class AuctionPublicationRecoveryRepository:
    """Recover publications whose initial Bot API response had message_id=0."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def mark_awaiting_channel_post(self, auction_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET publication_error = $2,
                    publication_finished_at = NULL,
                    publication_next_attempt_at = NULL
                WHERE auction_id = $1
                  AND status = 'publishing'
                  AND message_id IS NULL
                RETURNING auction_id
                """,
                int(auction_id),
                _PENDING_MARKER,
            )
        return bool(row)

    async def confirm_channel_post(
        self,
        auction_id: int,
        *,
        message_id: int,
    ) -> bool:
        actual_message_id = int(message_id)
        if actual_message_id <= 0:
            raise ValueError("channel message_id must be positive")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.auctions
                SET status = 'active',
                    message_id = $2,
                    publication_finished_at = NOW(),
                    publication_error = NULL,
                    publication_next_attempt_at = NULL
                WHERE auction_id = $1
                  AND message_id IS NULL
                  AND (
                        status = 'publication_failed'
                        OR (
                            status = 'publishing'
                            AND (
                                publication_error = $3
                                OR publication_started_at <= NOW() - INTERVAL '30 seconds'
                            )
                        )
                  )
                RETURNING auction_id
                """,
                int(auction_id),
                actual_message_id,
                _PENDING_MARKER,
            )
        return bool(row)


__all__ = ["AuctionPublicationRecoveryRepository"]
