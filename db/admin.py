"""Administrator access, settings, statistics and audit logs.

Extracted from the legacy database facade without changing SQL semantics.
"""

from __future__ import annotations

import json
import logging
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
from db.auctions import auction_exists


@require_db_pool
async def is_admin(user_id: int) -> bool:
    try:
        async with db_pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT 1 FROM admins WHERE user_id = $1", user_id
            ))
    except Exception as e:
        logger.error(f"Error checking admin status for user {user_id}: {e}")
        return False


@require_db_pool
async def add_admin(user_id: int, username: str = None, added_by: int = None) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admins (user_id, username, added_by)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """, user_id, username, added_by
            )
    except Exception as e:
        logger.error(f"Error adding admin {user_id}: {e}")


@require_db_pool
async def remove_admin(user_id: int) -> None:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM admins WHERE user_id = $1", user_id
            )
    except Exception as e:
        logger.error(f"Error removing admin {user_id}: {e}")


@require_db_pool
async def list_admins() -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, username FROM admins ORDER BY added_at"
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error listing admins: {e}")
        return []


@require_db_pool
async def log_admin_action(
        user_id: Optional[int] = None,
        action_type: str = "",
        auction_id: Optional[int] = None,
        details: str = "",
        admin_id: Optional[int] = None,
) -> None:
    """Записывает действие в audit_logs.

    Исторически в коде использовались оба имени параметра: user_id и admin_id.
    Чтобы не ловить TypeError, поддерживаем оба (приоритет у user_id).
    """
    uid = user_id if user_id is not None else admin_id
    if uid is None:
        # Не ломаем поток заявок из-за логов: просто пропускаем.
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
async def get_admin_logs(limit: int = 10, log_date: str | None = None, admin_id: int | None = None) -> list[dict]:
    where = []
    params: list[Any] = []
    if log_date:
        where.append(f'DATE(created_at) = ${len(params) + 1}')
        params.append(log_date)
    if admin_id:
        where.append(f"user_id = ${len(params) + 1}")
        params.append(admin_id)

    sql = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY created_at DESC'
    if not log_date:
        sql += f" LIMIT {limit}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


@require_db_pool
async def get_settings(user_id: int) -> Optional[Dict[str, bool]]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                                      SELECT notify_auction_start,
                                             notify_bid_reminder,
                                             notify_auction_end,
                                             notify_daily_today
                                      FROM settings
                                      WHERE user_id = $1
                                      """, user_id)
            if row:
                return {
                    "notify_auction_start": bool(row["notify_auction_start"]),
                    "notify_bid_reminder": bool(row["notify_bid_reminder"]),
                    "notify_auction_end": bool(row["notify_auction_end"]),
                    "notify_daily_today": bool(row["notify_daily_today"]),
                }
            # дефолты, когда строки ещё нет
            return {
                "notify_auction_start": True,
                "notify_bid_reminder": True,
                "notify_auction_end": True,
                "notify_daily_today": True,
            }
    except Exception as e:
        logger.error(f"Ошибка получения настроек для пользователя {user_id}: {e}")
        return None


@require_db_pool
async def set_settings(user_id: int, **kwargs):
    fields = [
        "notify_auction_start",
        "notify_bid_reminder",
        "notify_auction_end",
        "notify_daily_today",
    ]
    current = await get_settings(user_id) or {}
    data = {f: kwargs.get(f, current.get(f)) for f in fields}
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                               INSERT INTO settings (user_id,
                                                     notify_auction_start,
                                                     notify_bid_reminder,
                                                     notify_auction_end,
                                                     notify_daily_today)
                               VALUES ($1, $2, $3, $4, $5)
                               ON CONFLICT (user_id) DO UPDATE
                                   SET notify_auction_start = EXCLUDED.notify_auction_start,
                                       notify_bid_reminder  = EXCLUDED.notify_bid_reminder,
                                       notify_auction_end   = EXCLUDED.notify_auction_end,
                                       notify_daily_today   = EXCLUDED.notify_daily_today
                               """,
                               user_id,
                               bool(data["notify_auction_start"]),
                               bool(data["notify_bid_reminder"]),
                               bool(data["notify_auction_end"]),
                               bool(data["notify_daily_today"]),
                               )
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек для пользователя {user_id}: {e}")


@require_db_pool
async def get_audit_logs(limit: int = 20, log_date: date | None = None, admin_id: int | None = None):
    query = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs WHERE true'
    params: list[Any] = []
    if log_date:
        query += ' AND created_at::date = $%d' % (len(params) + 1)
        params.append(log_date)
    if admin_id:
        query += ' AND user_id = $%d' % (len(params) + 1)
        params.append(admin_id)
    query += ' ORDER BY created_at DESC LIMIT $%d' % (len(params) + 1)
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

    # если кто-то передал лишние kwargs (entity/entity_id и т.д.) не выбрасываем, добавим в details
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

    name = (action or action_type or event or "unknown")
    uid = (user_id or admin_id)

    if auction_id is not None:
        try:
            if not await auction_exists(int(auction_id)):
                auction_id = None
        except Exception:
            auction_id = None

    if uid is None:
        uid = 0

    # ---- ВОТ ЭТО ТЕБЯ И СПАСАЕТ ----
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
        # dict/list/что угодно -> JSON строка
        details_str = json.dumps(payload, ensure_ascii=False, default=str)

    await execute(
        """
        INSERT INTO audit_logs (user_id, action_type, auction_id, details)
        VALUES ($1, $2, $3, $4)
        """,
        int(uid), str(name), auction_id, details_str
    )


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
