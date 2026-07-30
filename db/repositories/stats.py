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

"""Stats persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'count_new_auctions',
    'get_stats',
]

@require_db_pool
async def count_new_auctions():
    try:
        async with db_pool.acquire() as conn:
            today = date.today()
            return await conn.fetchval(
                "SELECT COUNT(*) FROM auctions WHERE created_at::date = $1", today
            )
    except Exception as e:
        logger.error(f"Ошибка подсчёта новых аукционов: {e}")
        return 0

@require_db_pool
async def get_stats():
    try:
        async with db_pool.acquire() as conn:
            users_total = await conn.fetchval("SELECT COUNT(*) FROM users")
            auctions_total = await conn.fetchval("SELECT COUNT(*) FROM auctions")
            cards_total = await conn.fetchval("SELECT COUNT(*) FROM cards")
            bids_total = await conn.fetchval("SELECT COUNT(*) FROM bids")
            admins_total = await conn.fetchval("SELECT COUNT(*) FROM admins")
            luxury_total = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_luxury = TRUE")
            trusted_total = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_trusted = TRUE")
            return {
                "users_total": users_total,
                "auctions_total": auctions_total,
                "cards_total": cards_total,
                "bids_total": bids_total,
                "admins_total": admins_total,
                "luxury_total": luxury_total,
                "trusted_total": trusted_total,
            }
    except Exception as e:
        logger.error(f"Ошибка сбора статистики: {e}")
        return None

