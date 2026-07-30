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
from db.repositories._compat import (
    _normalize_username,
)

"""Users persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'add_user',
    'set_subscription',
    'is_subscribed',
    'get_user',
    'set_luxury_status',
    'get_user_id_by_username',
    'get_users_by_ids',
    'count_new_users',
    'is_luxury_user',
    'get_all_users',
    'get_user_by_username',
    'add_user_if_not_exists',
    'get_user_admin_info',
    'get_user_admin_info_by_username',
    'get_user_basic_info',
    'get_user_basic_info_by_username',
    'get_whois_admin_payload',
]

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

async def is_luxury_user(user_id: int) -> bool:
    row = await fetchrow("SELECT is_luxury FROM users WHERE user_id = $1", user_id)
    if not row:
        return False
    return bool(row["is_luxury"])

async def get_all_users():
    return await fetch("SELECT user_id, username, is_luxury FROM users")

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

async def get_user_admin_info(user_id: int) -> Optional[dict[str, Any]]:
    r = await fetchrow(
        """
        SELECT user_id,
               username,
               full_name,
               is_subscribed,
               is_luxury,
               warnings_count,
               created_at,
               is_trusted
        FROM users
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(r) if r else None

async def get_user_admin_info_by_username(username: str) -> Optional[dict[str, Any]]:
    uname = username.strip().lstrip("@").lower()
    r = await fetchrow(
        """
        SELECT user_id,
               username,
               full_name,
               is_subscribed,
               is_luxury,
               warnings_count,
               created_at,
               is_trusted
        FROM users
        WHERE lower(username) = $1
        """,
        uname,
    )
    return dict(r) if r else None

async def get_user_basic_info(*, user_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT user_id, username, full_name
        FROM public.users
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(row) if row else None

async def get_user_basic_info_by_username(username: str) -> dict | None:
    uname = (username or "").strip()
    if uname.startswith("@"):
        uname = uname[1:]
    uname = uname.strip().lower()
    if not uname:
        return None

    row = await fetchrow(
        """
        SELECT u.user_id,
               u.username,
               u.full_name,
               u.is_luxury,
               u.created_at                                               AS registered_at,
               u.pm_opened,
               u.first_pm_at,
               u.last_pm_at,
               EXISTS(SELECT 1 FROM admins a WHERE a.user_id = u.user_id) AS is_admin
        FROM users u
        WHERE lower(u.username) = $1
        LIMIT 1
        """,
        uname,
    )
    return dict(row) if row else None

async def get_whois_admin_payload(*, user_id: int) -> dict | None:
    """
    Данные для /who и /whois:
    - user: базовая инфа + флаги + счётчики подтверждений
    - lots_posted: сколько лотов выставлял
    - uid_record: verified UID + его бан-статус
    - uid_verif: последняя заявка на UID-верификацию
    - unreachable: последняя недоступность
    - user_ban: активный user-ban
    - in_blacklist: итоговый флаг (user_ban OR uid_ban)
    """
    uid = int(user_id)

    try:
        u = await fetchrow(
            """
            SELECT u.user_id,
                   u.username,
                   u.full_name,
                   u.is_luxury,
                   u.warnings_count,
                   u.created_at,
                   u.is_trusted,
                   u.pm_opened,
                   u.first_pm_at,
                   u.last_pm_at,
                   u.uid_verif_confirmed_count,
                   u.uid_verif_rejected_count,
                   u.uid_verif_last_confirmed_at,
                   u.uid_verif_last_rejected_at,
                   (a.user_id IS NOT NULL) AS is_admin
            FROM public.users u
            LEFT JOIN public.admins a ON a.user_id = u.user_id
            WHERE u.user_id = $1
            """,
            uid,
        )
    except Exception:
        u = await fetchrow(
            """
            SELECT u.user_id,
                   u.username,
                   u.full_name,
                   u.is_luxury,
                   u.warnings_count,
                   u.created_at,
                   u.is_trusted,
                   u.pm_opened,
                   u.first_pm_at,
                   u.last_pm_at,
                   (a.user_id IS NOT NULL) AS is_admin
            FROM public.users u
            LEFT JOIN public.admins a ON a.user_id = u.user_id
            WHERE u.user_id = $1
            """,
            uid,
        )

    if not u:
        return None

    try:
        lots_posted = int(
            await fetchval(
                """
                SELECT COUNT(*)
                FROM public.auction_owners ao
                WHERE ao.user_id = $1
                """,
                uid,
            ) or 0
        )
    except Exception:
        lots_posted = 0

    uid_record: dict | None = None
    try:
        r_uid = await fetchrow(
            """
            SELECT uu.user_id,
                   uu.status,
                   uu.verified_at,
                   uu.verified_by,
                   uu.updated_at,
                   uu.uid_last4,
                   (ub.uid_hash IS NOT NULL) AS is_banned,
                   ub.banned_at,
                   ub.banned_by,
                   ub.banned_until,
                   ub.reason AS ban_reason
            FROM public.user_uids uu
            LEFT JOIN public.uid_bans ub
                   ON ub.uid_hash = uu.uid_hash
                  AND (ub.banned_until IS NULL OR ub.banned_until > NOW())
            WHERE uu.user_id = $1
            LIMIT 1
            """,
            uid,
        )
        uid_record = dict(r_uid) if r_uid else None
    except Exception:
        uid_record = None

    uid_verif = None
    try:
        r_ver = await fetchrow(
            """
            SELECT r.*, u.username, u.full_name
            FROM public.uid_verification_requests r
            LEFT JOIN public.users u ON u.user_id = r.user_id
            WHERE r.user_id = $1
            ORDER BY r.id DESC
            LIMIT 1
            """,
            uid,
        )
        if r_ver:
            rid = int(r_ver["id"])
            confs = await fetch(
                """
                SELECT *
                FROM public.uid_verification_confirmations
                WHERE request_id = $1
                ORDER BY id
                """,
                rid,
            )
            uid_verif = dict(r_ver)
            uid_verif["confirmations"] = [dict(c) for c in confs]
    except Exception:
        uid_verif = None

    unreachable = None
    try:
        unr = await fetchrow(
            """
            SELECT user_id, reason, last_seen
            FROM public.unreachable_users
            WHERE user_id = $1
            LIMIT 1
            """,
            uid,
        )
        unreachable = dict(unr) if unr else None
    except Exception:
        unreachable = None

    user_ban = None
    try:
        ub = await fetchrow(
            """
            SELECT user_id, banned_until, reason, issued_at
            FROM public.user_bans
            WHERE user_id = $1
              AND banned_until > NOW()
            ORDER BY issued_at DESC
            LIMIT 1
            """,
            uid,
        )
        user_ban = dict(ub) if ub else None
    except Exception:
        user_ban = None

    uid_in_blacklist = bool((uid_record or {}).get("is_banned"))
    user_in_blacklist = bool(user_ban)
    in_blacklist = bool(uid_in_blacklist or user_in_blacklist)

    return {
        "user": dict(u),
        "lots_posted": lots_posted,
        "uid_record": uid_record,
        "uid_verif": uid_verif,
        "unreachable": unreachable,
        "user_ban": user_ban,
        "uid_in_blacklist": uid_in_blacklist,
        "user_in_blacklist": user_in_blacklist,
        "in_blacklist": in_blacklist,
    }

