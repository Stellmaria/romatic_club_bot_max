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

"""Autobids persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'list_autobids',
    'pick_best_autobid_candidate',
    'record_autobid_action',
    'get_autobid_action_by_msg_id',
]

@require_db_pool
async def list_autobids(
        auction_id: int | None = None,
        *,
        only_active: bool = True,
) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ab.autobid_id,
                   ab.auction_id,
                   ab.target_user_id,
                   ab.target_username,
                   ab.max_amount,
                   ab.step,
                   ab.is_active,
                   a.currency AS auction_currency
            FROM public.autobids ab
                     LEFT JOIN public.auctions a
                               ON a.auction_id = ab.auction_id
            WHERE ($1::int IS NULL OR ab.auction_id = $1)
              AND ($2::bool = FALSE OR ab.is_active = TRUE)
            ORDER BY ab.auction_id DESC,
                     ab.max_amount DESC,
                     ab.autobid_id DESC
            """,
            int(auction_id) if auction_id is not None else None,
            bool(only_active),
        )
        return [dict(r) for r in rows]

@require_db_pool
async def pick_best_autobid_candidate(
        auction_id: int,
        current_max: int | None = None,
        current_leader_id: int | None = None,
) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT autobid_id,
                   auction_id,
                   target_user_id,
                   target_username,
                   max_amount,
                   step,
                   is_active
            FROM public.autobids
            WHERE auction_id = $1
              AND is_active = TRUE
              AND ($2::bigint IS NULL OR target_user_id <> $2)
            ORDER BY max_amount DESC, autobid_id DESC
            LIMIT 1
            """,
            int(auction_id),
            int(current_leader_id) if current_leader_id is not None else None,
        )
        return dict(row) if row else None

@require_db_pool
async def record_autobid_action(
        autobid_id: int,
        auction_id: int,
        target_user_id: int,
        amount: int | None = None,
        discussion_message_id: int | None = None,
        *,
        bid_amount: int | None = None,
        bid_msg_id: int | None = None,
) -> dict | None:
    real_amount = amount if amount is not None else bid_amount
    real_msg_id = discussion_message_id if discussion_message_id is not None else bid_msg_id
    if not real_amount or not real_msg_id:
        return None

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.autobid_actions
                (autobid_id, auction_id, target_user_id, amount, discussion_message_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (discussion_message_id)
                DO NOTHING
            RETURNING *
            """,
            int(autobid_id),
            int(auction_id),
            int(target_user_id),
            int(real_amount),
            int(real_msg_id),
        )
        return dict(row) if row else None

@require_db_pool
async def get_autobid_action_by_msg_id(discussion_message_id: int) -> Optional[dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM public.autobid_actions
            WHERE discussion_message_id = $1
            """,
            int(discussion_message_id),
        )
    return dict(row) if row else None

