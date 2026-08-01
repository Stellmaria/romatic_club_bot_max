"""Strict compatibility mutations for legacy persistence entry points.

The modern auction workflow repository is the preferred API. These functions
keep old callers safe until they are migrated: multi-step writes are atomic and
technical failures propagate through the persistence boundary.
"""

from __future__ import annotations

from datetime import datetime

from bot.core.errors import PersistenceOperationError
from db.core import pool_proxy as db_pool, require_db_pool


@require_db_pool
async def add_auction(
    card_name: str,
    hero_name: str,
    image_id: str,
    owner_id: int,
    start_price: int,
    currency: str,
    start_time: datetime,
    end_time: datetime,
    status: str,
    comment: str,
) -> None:
    """Create an auction and its owner relation in one transaction."""

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO auctions (
                    card_name,
                    hero_name,
                    image_id,
                    start_price,
                    currency,
                    start_time,
                    end_time,
                    status,
                    comment
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING auction_id
                """,
                card_name,
                hero_name,
                image_id,
                start_price,
                currency,
                start_time,
                end_time,
                status,
                comment,
            )
            if row is None:
                raise PersistenceOperationError("db.add_auction.returning")
            await conn.execute(
                """
                INSERT INTO auction_owners (auction_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                int(row["auction_id"]),
                int(owner_id),
            )


__all__ = ["add_auction"]
