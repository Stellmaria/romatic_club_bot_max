"""User profiles, preferences, trust and subscription flags.

Database failures propagate through the typed persistence boundary. Missing rows
remain normal business results; unavailable PostgreSQL is never represented as
``None``, ``[]`` or ``0``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from db.core import (
    execute,
    fetch,
    fetchrow,
    pool_proxy as db_pool,
    require_db_pool,
)
from db.profile_sync import sync_user_profile


async def add_user(user_id: int, username: str, full_name: str) -> None:
    """Compatibility adapter using the atomic profile synchronization contract."""

    await sync_user_profile(user_id, username, full_name)


@require_db_pool
async def set_subscription(user_id: int, value: bool) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_subscribed = $1 WHERE user_id = $2",
            value,
            user_id,
        )


@require_db_pool
async def is_subscribed(user_id: int) -> Optional[bool]:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT is_subscribed FROM users WHERE user_id = $1",
            user_id,
        )


@require_db_pool
async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, username, full_name FROM users WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row else None


@require_db_pool
async def set_luxury_status(user_id: int, is_luxury: bool) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_luxury = $1 WHERE user_id = $2",
            is_luxury,
            user_id,
        )


@require_db_pool
async def get_user_id_by_username(username: str) -> int | None:
    uname = username.strip().lstrip("@").lower()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM users WHERE LOWER(username) = $1",
            uname,
        )
    return int(row["user_id"]) if row else None


@require_db_pool
async def get_users_by_ids(user_ids: list[int]) -> list[dict]:
    if not user_ids:
        return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, full_name FROM users WHERE user_id = ANY($1)",
            user_ids,
        )
    return [dict(row) for row in rows]


@require_db_pool
async def count_new_users() -> int:
    async with db_pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at::date = $1",
            date.today(),
        )
    return int(value or 0)


async def get_all_trusted_users():
    return await fetch(
        """
        SELECT u.username, u.user_id, u.is_luxury
        FROM users u
        WHERE u.is_trusted = true
          AND u.username IS NOT NULL
        UNION
        SELECT t.username, NULL as user_id, NULL as is_luxury
        FROM trusted_usernames t
        WHERE NOT EXISTS (
            SELECT 1
            FROM users u2
            WHERE u2.username = t.username AND u2.is_trusted = true
        )
        ORDER BY username
        """
    )


async def is_luxury_user(user_id: int) -> bool:
    row = await fetchrow("SELECT is_luxury FROM users WHERE user_id = $1", user_id)
    if not row:
        return False
    return bool(row["is_luxury"])


async def set_trusted_status(user_id: int, is_trusted: bool) -> None:
    await execute(
        "UPDATE users SET is_trusted = $2 WHERE user_id = $1",
        user_id,
        is_trusted,
    )


async def get_all_users():
    return await fetch("SELECT user_id, username, is_luxury FROM users")


async def has_pending_delete_request(lot_id: int) -> bool:
    req = await fetchrow(
        "SELECT 1 FROM delete_requests WHERE lot_id = $1 AND status = 'pending'",
        lot_id,
    )
    return req is not None


@require_db_pool
async def sync_trusted_status(user_id: int, username: str | None = None) -> None:
    """Atomically derive trust from the username allow-list."""

    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET is_trusted = EXISTS (
                SELECT 1
                FROM trusted_usernames
                WHERE lower(username) = lower($2)
            )
            WHERE user_id = $1
            """,
            user_id,
            normalized,
        )


@require_db_pool
async def get_user_by_username(username: str) -> dict | None:
    normalized = (username or "").strip().lstrip("@").lower()
    if not normalized:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.users WHERE LOWER(username)=LOWER($1) LIMIT 1",
            normalized,
        )
    return dict(row) if row else None


def _normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    normalized = str(username).strip().lstrip("@")
    return normalized or None


@require_db_pool
async def add_user_if_not_exists(
    user_id: int,
    username: str,
    full_name: str = "",
) -> None:
    normalized = _normalize_username(username)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
            normalized,
            full_name,
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
    "sync_user_profile",
]
