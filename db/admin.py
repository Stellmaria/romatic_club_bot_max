"""Administrator access, settings, statistics and audit logs.

Missing rows are valid business results. PostgreSQL failures propagate through
the typed persistence boundary instead of being converted to denied access,
empty lists or zero-valued statistics.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from db.auctions import auction_exists
from db.core import execute, fetch, pool_proxy as db_pool, require_db_pool


@require_db_pool
async def is_admin(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM admins WHERE user_id = $1",
                user_id,
            )
        )


@require_db_pool
async def add_admin(
    user_id: int,
    username: str | None = None,
    added_by: int | None = None,
) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admins (user_id, username, added_by)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            user_id,
            username,
            added_by,
        )


@require_db_pool
async def remove_admin(user_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id = $1", user_id)


@require_db_pool
async def list_admins() -> List[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username FROM admins ORDER BY added_at"
        )
    return [dict(row) for row in rows]


@require_db_pool
async def log_admin_action(
    user_id: Optional[int] = None,
    action_type: str = "",
    auction_id: Optional[int] = None,
    details: str = "",
    admin_id: Optional[int] = None,
) -> None:
    """Write an administrator action while supporting both historical ID names."""

    uid = user_id if user_id is not None else admin_id
    if uid is None:
        logging.warning("log_admin_action: user_id/admin_id is None; skip")
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.audit_logs (user_id, action_type, auction_id, details)
            VALUES ($1, $2, $3, $4)
            """,
            uid,
            action_type,
            auction_id,
            details or "",
        )


@require_db_pool
async def get_admin_logs(
    limit: int = 10,
    log_date: str | None = None,
    admin_id: int | None = None,
) -> list[dict]:
    where: list[str] = []
    params: list[Any] = []
    if log_date:
        where.append(f"DATE(created_at) = ${len(params) + 1}")
        params.append(log_date)
    if admin_id:
        where.append(f"user_id = ${len(params) + 1}")
        params.append(admin_id)

    sql = (
        "SELECT id, user_id, action_type, auction_id, details, "
        "created_at AS created_at FROM audit_logs"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    if not log_date:
        sql += f" LIMIT {max(1, int(limit))}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


@require_db_pool
async def get_settings(user_id: int) -> Dict[str, bool]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT notify_auction_start,
                   notify_bid_reminder,
                   notify_auction_end,
                   notify_daily_today
            FROM settings
            WHERE user_id = $1
            """,
            user_id,
        )
    if row:
        return {
            "notify_auction_start": bool(row["notify_auction_start"]),
            "notify_bid_reminder": bool(row["notify_bid_reminder"]),
            "notify_auction_end": bool(row["notify_auction_end"]),
            "notify_daily_today": bool(row["notify_daily_today"]),
        }
    return {
        "notify_auction_start": True,
        "notify_bid_reminder": True,
        "notify_auction_end": True,
        "notify_daily_today": True,
    }


@require_db_pool
async def set_settings(user_id: int, **kwargs: Any) -> None:
    """Atomically create or partially update notification settings."""

    values = [
        kwargs.get("notify_auction_start"),
        kwargs.get("notify_bid_reminder"),
        kwargs.get("notify_auction_end"),
        kwargs.get("notify_daily_today"),
    ]
    for value in values:
        if value is not None and not isinstance(value, bool):
            raise TypeError("notification settings must be bool or None")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO settings (
                user_id,
                notify_auction_start,
                notify_bid_reminder,
                notify_auction_end,
                notify_daily_today
            )
            VALUES (
                $1,
                COALESCE($2::boolean, TRUE),
                COALESCE($3::boolean, TRUE),
                COALESCE($4::boolean, TRUE),
                COALESCE($5::boolean, TRUE)
            )
            ON CONFLICT (user_id) DO UPDATE
            SET notify_auction_start = COALESCE($2::boolean, settings.notify_auction_start),
                notify_bid_reminder = COALESCE($3::boolean, settings.notify_bid_reminder),
                notify_auction_end = COALESCE($4::boolean, settings.notify_auction_end),
                notify_daily_today = COALESCE($5::boolean, settings.notify_daily_today)
            """,
            user_id,
            *values,
        )


@require_db_pool
async def get_audit_logs(
    limit: int = 20,
    log_date: date | None = None,
    admin_id: int | None = None,
):
    query = (
        "SELECT id, user_id, action_type, auction_id, details, "
        "created_at AS created_at FROM audit_logs WHERE true"
    )
    params: list[Any] = []
    if log_date:
        query += " AND created_at::date = $%d" % (len(params) + 1)
        params.append(log_date)
    if admin_id:
        query += " AND user_id = $%d" % (len(params) + 1)
        params.append(admin_id)
    query += " ORDER BY created_at DESC LIMIT $%d" % (len(params) + 1)
    params.append(limit)
    return await fetch(query, *params)


@require_db_pool
async def log_audit_action(*args: Any, **kwargs: Any) -> None:
    action = kwargs.pop("action", None)
    action_type = kwargs.pop("action_type", None)
    event = kwargs.pop("event", None)
    admin_id = kwargs.pop("admin_id", None)
    user_id = kwargs.pop("user_id", None)
    details = kwargs.pop("details", None)
    auction_id = kwargs.pop("auction_id", None)
    extra_kwargs = dict(kwargs)

    if args:
        if action is None and len(args) >= 1:
            action = args[0]
        if admin_id is None and len(args) >= 2:
            admin_id = args[1]
        if details is None and len(args) >= 3:
            details = args[2]
        if auction_id is None and len(args) >= 4:
            auction_id = args[3]
        if user_id is None and len(args) >= 5:
            user_id = args[4]

    name = action or action_type or event or "unknown"
    uid = user_id or admin_id or 0

    if auction_id is not None and not await auction_exists(int(auction_id)):
        auction_id = None

    payload = details
    if extra_kwargs:
        if isinstance(payload, dict):
            payload = {**payload, **extra_kwargs}
        else:
            payload = {"details": payload, **extra_kwargs}

    if payload is None:
        details_str = ""
    elif isinstance(payload, str):
        details_str = payload
    else:
        details_str = json.dumps(payload, ensure_ascii=False, default=str)

    await execute(
        """
        INSERT INTO audit_logs (user_id, action_type, auction_id, details)
        VALUES ($1, $2, $3, $4)
        """,
        int(uid),
        str(name),
        auction_id,
        details_str,
    )


@require_db_pool
async def get_stats() -> dict[str, int]:
    async with db_pool.acquire() as conn:
        users_total = await conn.fetchval("SELECT COUNT(*) FROM users")
        auctions_total = await conn.fetchval("SELECT COUNT(*) FROM auctions")
        cards_total = await conn.fetchval("SELECT COUNT(*) FROM cards")
        bids_total = await conn.fetchval("SELECT COUNT(*) FROM bids")
        admins_total = await conn.fetchval("SELECT COUNT(*) FROM admins")
        luxury_total = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_luxury = TRUE"
        )
        trusted_total = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_trusted = TRUE"
        )
    return {
        "users_total": int(users_total or 0),
        "auctions_total": int(auctions_total or 0),
        "cards_total": int(cards_total or 0),
        "bids_total": int(bids_total or 0),
        "admins_total": int(admins_total or 0),
        "luxury_total": int(luxury_total or 0),
        "trusted_total": int(trusted_total or 0),
    }


__all__ = [
    "is_admin",
    "add_admin",
    "remove_admin",
    "list_admins",
    "log_admin_action",
    "get_admin_logs",
    "get_settings",
    "set_settings",
    "get_audit_logs",
    "log_audit_action",
    "get_stats",
]
