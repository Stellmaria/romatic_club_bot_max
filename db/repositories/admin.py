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
    _has_column,
    auction_exists,
    is_user_uid_banned,
)

"""Admin persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'is_admin',
    'add_admin',
    'remove_admin',
    'list_admins',
    'log_admin_action',
    'get_admin_logs',
    'get_delete_request',
    'update_delete_request_status',
    'get_all_trusted_users',
    'list_pending_delete_requests',
    'set_trusted_status',
    'get_audit_logs',
    'log_audit_action',
    'add_delete_request',
    'has_pending_delete_request',
    'sync_trusted_status',
    'get_auction_owner_id',
    'add_warning',
    'get_warnings_count',
    'ban_user',
    'is_user_banned',
    'unban_user',
    'reset_warnings',
    'count_pending_delete_requests_by_kind',
]

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
        where.append(f'DATE("timestamp") = ${len(params) + 1}')
        params.append(log_date)
    if admin_id:
        where.append(f"user_id = ${len(params) + 1}")
        params.append(admin_id)

    sql = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "timestamp" DESC'
    if not log_date:
        sql += f" LIMIT {limit}"

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]

async def get_delete_request(request_id: int):
    return await fetchrow("SELECT * FROM delete_requests WHERE id = $1", request_id)

async def update_delete_request_status(request_id: int, status: str):
    await execute("UPDATE delete_requests SET status = $1 WHERE id = $2", status, request_id)

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

@require_db_pool
async def list_pending_delete_requests(
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
) -> list[dict]:
    kind = (kind or "").strip().lower() or None

    async with db_pool.acquire() as conn:
        if kind:
            rows = await conn.fetch(
                """
                SELECT dr.id, dr.lot_id, dr.user_id, dr.reason, dr.created_at, dr.status
                FROM public.delete_requests dr
                         LEFT JOIN public.auctions a ON a.auction_id = dr.lot_id
                WHERE dr.status = 'pending'
                  AND COALESCE(a.auction_kind, 'standard') = $1
                ORDER BY dr.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                kind, limit, offset,
            )
            return [dict(r) for r in rows]

        rows = await conn.fetch(
            """
            SELECT id, lot_id, user_id, reason, created_at, status
            FROM public.delete_requests
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        return [dict(r) for r in rows]

async def set_trusted_status(user_id: int, is_trusted: bool):
    await execute(
        "UPDATE users SET is_trusted = $2 WHERE user_id = $1",
        user_id, is_trusted
    )

@require_db_pool
async def get_audit_logs(limit: int = 20, log_date: date | None = None, admin_id: int | None = None):
    query = 'SELECT id, user_id, action_type, auction_id, details, created_at AS created_at FROM audit_logs WHERE true'
    params: list[Any] = []
    if log_date:
        query += ' AND "timestamp"::date = $%d' % (len(params) + 1)
        params.append(log_date)
    if admin_id:
        query += ' AND user_id = $%d' % (len(params) + 1)
        params.append(admin_id)
    query += ' ORDER BY "timestamp" DESC LIMIT $%d' % (len(params) + 1)
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
async def add_delete_request(user_id: int, lot_id: int, reason: str):
    logger.info(f"Trying to add delete_request: user_id={user_id}, lot_id={lot_id}, reason={reason!r}")
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO delete_requests (user_id, lot_id, reason, status, created_at) VALUES ($1, $2, $3, 'pending', now())",
                user_id, lot_id, reason
            )
        logger.info(f"Delete request for lot_id={lot_id} from user_id={user_id} added successfully.")
    except Exception as e:
        logger.error(f"Ошибка создания заявки на удаление: {e}")
        raise

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

async def get_auction_owner_id(auction_id: int) -> int | None:
    row = await fetchrow("SELECT user_id FROM auction_owners WHERE auction_id = $1", auction_id)
    return row['user_id'] if row else None

@require_db_pool
async def add_warning(user_id: int, reason: str, message_id: int = None, details: str = None):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_warnings (user_id, reason, issued_at, details) VALUES ($1, $2, NOW(), $3)",
                user_id, reason, details
            )
            await conn.execute(
                "UPDATE users SET warnings_count = warnings_count + 1 WHERE user_id = $1",
                user_id
            )
    except Exception as e:
        print(f"[ERROR] Ошибка добавления предупреждения: {e}")

@require_db_pool
async def get_warnings_count(user_id: int) -> int:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT warnings_count FROM users WHERE user_id = $1",
                user_id
            )
            return row["warnings_count"] if row and row["warnings_count"] is not None else 0
    except Exception as e:
        print(f"[ERROR] Ошибка получения warnings_count: {e}")
        return 0

@require_db_pool
async def ban_user(user_id: int, reason: str = "4 warnings"):
    banned_until = datetime.now() + timedelta(days=365 * 10)
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_bans (user_id, banned_until, reason) VALUES ($1, $2, $3)",
                user_id, banned_until, reason
            )
    except Exception as e:
        print(f"[ERROR] Ошибка при бане пользователя: {e}")

@require_db_pool
async def is_user_banned(user_id: int) -> bool:
    now = datetime.now()
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT banned_until FROM user_bans WHERE user_id = $1 ORDER BY id DESC LIMIT 1",
                int(user_id),
            )
            if bool(row and row["banned_until"] and row["banned_until"] > now):
                return True
    except Exception as e:
        print(f"[ERROR] Ошибка проверки is_banned: {e}")

    # ✅ доп. блокировка по UID (если UID в ЧС — считаем, что пользователь в бане)
    try:
        # функция ниже в этом же файле у тебя уже есть
        if await is_user_uid_banned(int(user_id)):
            return True
    except Exception:
        pass

    return False

async def unban_user(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_bans WHERE user_id = $1",
            user_id
        )

async def reset_warnings(user_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_warnings WHERE user_id = $1", user_id
        )
        await conn.execute(
            "UPDATE users SET warnings_count = 0 WHERE user_id = $1", user_id
        )

@require_db_pool
async def count_pending_delete_requests_by_kind() -> dict[str, int]:
    """
    Админ-меню: сколько pending заявок на удаление по типам аукциона.
    Если в delete_requests нет колонки kind — считаем через auctions.auction_kind.
    """
    async with db_pool.acquire() as conn:
        kind_col = "kind" if await _has_column(conn, "delete_requests", "kind") else None

        if kind_col:
            rows = await conn.fetch(
                f"""
                SELECT {kind_col} AS kind, COUNT(*) AS cnt
                FROM delete_requests
                WHERE status='pending'
                GROUP BY {kind_col}
                """
            )
            out: dict[str, int] = {str(r["kind"]): int(r["cnt"]) for r in rows}
        else:
            rows = await conn.fetch(
                """
                SELECT COALESCE(a.auction_kind, 'standard') AS kind, COUNT(*) AS cnt
                FROM delete_requests dr
                         LEFT JOIN auctions a ON a.auction_id = dr.lot_id
                WHERE dr.status = 'pending'
                GROUP BY COALESCE(a.auction_kind, 'standard')
                """
            )
            out = {str(r["kind"]): int(r["cnt"]) for r in rows}

        # чтобы меню было стабильным
        for k in ("standard", "reverse", "fast", "free", "black", "exchange"):
            out.setdefault(k, 0)
        return out

