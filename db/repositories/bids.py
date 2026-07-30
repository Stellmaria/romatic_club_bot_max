from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from datetime import date as _date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import asyncpg

from db.core import (
    close_db,
    db_pool,
    execute,
    fetch,
    fetchall,
    fetchrow,
    fetchval,
    get_db_pool,
    logger,
    require_db_pool,
)
# No cross-domain legacy dependencies.

"""Bids persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'get_bids_for_auction',
    'get_top_bid_for_auction',
    'get_auction_by_discussion_id',
    'get_active_auction_ids',
    'get_auction_winner',
    'get_valid_bid_msg_ids',
    'get_current_auction',
]

@require_db_pool
async def get_bids_for_auction(auction_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT b.*
                FROM public.bids b
                JOIN public.auctions a ON a.auction_id = b.auction_id
                WHERE b.auction_id = $1
                ORDER BY
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse' THEN b.amount END ASC,
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse' THEN b.amount END DESC,
                    b.placed_at ASC,
                    b.bid_id ASC
                """,
                auction_id
            )
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[ERROR] Ошибка получения ставок по лоту: {e}")
        return []

@require_db_pool
async def get_top_bid_for_auction(auction_id: int) -> tuple[int | None, int | None]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.bidder_id, b.amount
            FROM public.bids b
            JOIN public.auctions a ON a.auction_id = b.auction_id
            WHERE b.auction_id = $1
            ORDER BY
                CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse' THEN b.amount END ASC,
                CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse' THEN b.amount END DESC,
                b.placed_at ASC,
                b.bid_id ASC
            LIMIT 1
            """,
            int(auction_id),
        )

    if not row:
        return None, None
    return int(row["amount"]), int(row["bidder_id"])

@require_db_pool
async def get_auction_by_discussion_id(discussion_msg_id):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM auctions WHERE discussion_message_id = $1",
            discussion_msg_id
        )
        return dict(row) if row else None

@require_db_pool
async def get_active_auction_ids():
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT auction_id, discussion_message_id, end_time, status FROM auctions WHERE status = 'active'")
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[ERROR] Ошибка поиска активных аукционов: {e}")
        return []

@require_db_pool
async def get_auction_winner(auction_id: int) -> dict | None:
    try:
        async with db_pool.acquire() as conn:
            auction_row = await conn.fetchrow(
                "SELECT end_time, auction_kind FROM auctions WHERE auction_id = $1",
                auction_id
            )
            if not auction_row or not auction_row['end_time']:
                return None
            end_time = auction_row['end_time']
            order = "ASC" if str(auction_row["auction_kind"] or "").lower() == "reverse" else "DESC"
            row = await conn.fetchrow(
                f"""
                SELECT u.user_id, u.username, b.amount AS bid
                FROM bids b
                         JOIN users u ON b.bidder_id = u.user_id
                WHERE b.auction_id = $1
                  AND b.created_at <= $2
                ORDER BY b.amount {order}, b.created_at ASC, b.bid_id ASC
                LIMIT 1
                """,
                auction_id, end_time
            )
            if row:
                return {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "bid": row["bid"],
                }
            return None
    except Exception as e:
        logger.error(f"Ошибка получения победителя аукциона {auction_id}: {e}")
        return None

@require_db_pool
async def get_valid_bid_msg_ids(auction_id: int) -> list[int]:
    try:
        async with db_pool.acquire() as conn:
            auction_row = await conn.fetchrow(
                "SELECT end_time FROM auctions WHERE auction_id = $1", auction_id
            )
            if not auction_row or not auction_row["end_time"]:
                return []
            end_time = auction_row["end_time"]
            end_dt = end_time + timedelta(minutes=1) - timedelta(seconds=1)
            rows = await conn.fetch(
                """
                SELECT discussion_message_id, amount
                FROM bids
                WHERE auction_id = $1
                  AND created_at <= $2
                  AND amount ~ '^\\d+$' -- Только цифры!
                """,
                auction_id, end_dt
            )
            return [row["discussion_message_id"] for row in rows if row["discussion_message_id"]]
    except Exception as e:
        logger.error(f"Ошибка получения валидных message_id ставок для аукциона {auction_id}: {e}")
        return []

@require_db_pool
async def get_current_auction() -> Optional[dict]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM auctions
                WHERE start_time <= NOW()
                  AND end_time >= NOW()
                  AND status = 'active'
                ORDER BY end_time ASC
                LIMIT 1
                """
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка получения текущего аукциона: {e}")
        return None

