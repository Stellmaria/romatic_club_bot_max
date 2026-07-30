"""User profiles, preferences, trust and subscription flags.

Extracted from the legacy database facade without changing SQL semantics.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import date as _date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import asyncpg

from bot.core.time import ensure_utc
from bot.domain.users import Owner
from bot.repositories.uid_verification import UIDVerificationRepository
from bot.uid_crypto import (
    mask_uid,
    mask_uid_by_last4,
    norm_uid,
    uid_decrypt,
    uid_encrypt,
    uid_hash,
    uid_last4,
)
from db.core import (
    _has_column,
    _pg_column_exists,
    _pg_table_exists,
    execute,
    fetch,
    fetchrow,
    fetchval,
    get_db_pool,
    logger,
    pool_proxy as db_pool,
    require_db_pool,
)


@require_db_pool
async def add_user(user_id: int, username: str, full_name: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                               INSERT INTO users (user_id, username, full_name)
                               VALUES ($1, $2, $3)
                               ON CONFLICT (user_id) DO UPDATE
                                   SET username  = EXCLUDED.username,
                                       full_name = EXCLUDED.full_name
                               """, user_id, username, full_name)
    except Exception as e:
        logger.error(f"Error adding user {user_id}: {e}")


@require_db_pool
async def set_subscription(user_id: int, value: bool) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_subscribed = $1 WHERE user_id = $2",
                value, user_id
            )
    except Exception as e:
        logger.error(f"Error setting subscription for user {user_id}: {e}")


@require_db_pool
async def is_subscribed(user_id: int) -> Optional[bool]:
    try:
        async with db_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT is_subscribed FROM users WHERE user_id = $1", user_id
            )
    except Exception as e:
        logger.error(f"Error checking subscription for user {user_id}: {e}")
        return None


@require_db_pool
async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, username, full_name FROM users WHERE user_id = $1", user_id
            )
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return None


@require_db_pool


@require_db_pool
async def set_luxury_status(user_id: int, is_luxury: bool) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_luxury = $1 WHERE user_id = $2", is_luxury, user_id
            )
    except Exception as e:
        logger.error(f"Error setting luxury status for user {user_id}: {e}")


@require_db_pool


@require_db_pool
async def get_user_id_by_username(username: str) -> int | None:
    uname = username.strip().lstrip("@").lower()
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM users WHERE LOWER(username) = $1",
                uname
            )
            return row['user_id'] if row else None
    except Exception as e:
        logger.error(f"Error getting user_id by username {username}: {e}")
        return None


@require_db_pool
async def get_users_by_ids(user_ids: list[int]) -> list[dict]:
    if not user_ids:
        return []
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username, full_name FROM users WHERE user_id = ANY($1)", user_ids
            )
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения пользователей по ids: {e}")
        return []


@require_db_pool
async def count_new_users():
    try:
        async with db_pool.acquire() as conn:
            today = date.today()
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at::date = $1", today
            )
    except Exception as e:
        logger.error(f"Ошибка подсчёта новых пользователей: {e}")
        return 0


async def get_all_trusted_users():
    rows = await fetch("""
                       SELECT u.username, u.user_id, u.is_luxury
                       FROM users u
                       WHERE u.is_trusted = true
                         AND u.username IS NOT NULL
                       UNION
                       SELECT t.username, NULL as user_id, NULL as is_luxury
                       FROM trusted_usernames t
                       WHERE NOT EXISTS (SELECT 1 FROM users u2 WHERE u2.username = t.username AND u2.is_trusted = true)
                       ORDER BY username
                       """)
    return rows


async def is_luxury_user(user_id: int) -> bool:
    row = await fetchrow("SELECT is_luxury FROM users WHERE user_id = $1", user_id)
    if not row:
        return False
    return bool(row["is_luxury"])


async def set_trusted_status(user_id: int, is_trusted: bool):
    await execute(
        "UPDATE users SET is_trusted = $2 WHERE user_id = $1",
        user_id, is_trusted
    )


async def get_all_users():
    return await fetch("SELECT user_id, username, is_luxury FROM users")


async def has_pending_delete_request(lot_id: int):
    req = await fetchrow("SELECT 1 FROM delete_requests WHERE lot_id = $1 AND status = 'pending'", lot_id)
    return req is not None


async def sync_trusted_status(user_id: int, username: str = None):
    if not username:
        return
    uname = username.lstrip("@")
    exists = await fetchval("SELECT 1 FROM trusted_usernames WHERE username = $1", uname)
    if exists:
        await set_trusted_status(user_id, True)
    else:
        await set_trusted_status(user_id, False)


@require_db_pool
async def get_user_by_username(username: str) -> dict | None:
    un = (username or "").strip().lstrip("@").lower()
    if not un:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.users WHERE LOWER(username)=LOWER($1) LIMIT 1",
            un,
        )
    return dict(row) if row else None


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    u = str(username).strip().lstrip("@")
    return u or None


@require_db_pool
async def add_user_if_not_exists(user_id: int, username: str, full_name: str = "") -> None:
    username = _normalize_username(username)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id, username, full_name
        )


__all__ = [
    "add_user",
    "set_subscription",
    "is_subscribed",
    "get_user",
    "set_luxury_status",
    "get_user_id_by_username",
    "get_users_by_ids",
    "count_new_users",
    "get_all_trusted_users",
    "is_luxury_user",
    "set_trusted_status",
    "get_all_users",
    "has_pending_delete_request",
    "sync_trusted_status",
    "get_user_by_username",
    "_normalize_username",
    "add_user_if_not_exists",
]
