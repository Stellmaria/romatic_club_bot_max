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

"""Subscriptions persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'add_user_subscription',
    'get_user_subscriptions',
    'remove_user_subscription',
    'get_settings',
    'set_settings',
    'get_card_subscribers',
    'disable_all_notifications',
    'clear_all_card_subscriptions',
    'mark_user_unreachable',
    'get_users_with_pref',
    'unsubscribe_subscription',
    'get_top_subscribed_cards',
    'subscribe_preset',
    'list_my_preset_subs',
    'unsubscribe_preset',
    'subscribers_for_lot_title',
    'list_broadcast_targets',
    'list_user_card_subs',
    'mark_subscription_confirmed',
    'mark_unreachable_user',
    '_preset_user_ids_by_key_or_alias',
    '_norm',
    '_rarity_slug',
    'subscribers_for_rarity',
    'subscribers_for_deck',
]

ALLOWED_PREFS = {
    "notify_auction_start": "notify_auction_start",
    "notify_bid_reminder": "notify_bid_reminder",
    "notify_auction_end": "notify_auction_end",
    "notify_daily_today": "notify_daily_today",
}

@require_db_pool
async def add_user_subscription(user_id: int, card_id: int, *_ignored):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_subscriptions (user_id, card_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, card_id) DO NOTHING
                """,
                user_id, card_id
            )
    except Exception as e:
        logger.error(f"Error adding user subscription: {e}")

@require_db_pool
async def get_user_subscriptions(user_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM user_subscriptions WHERE user_id = $1", user_id
            )
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        return []

@require_db_pool
async def remove_user_subscription(sub_id: int, user_id: int):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_subscriptions WHERE id = $1 AND user_id = $2", sub_id, user_id
            )
    except Exception as e:
        logger.error(f"Error removing subscription: {e}")

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
async def get_card_subscribers(card_id: int) -> list[int]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM user_subscriptions WHERE card_id = $1", card_id
            )
            return [row["user_id"] for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения подписчиков карты {card_id}: {e}")
        return []

@require_db_pool
async def disable_all_notifications(user_id: int) -> None:
    q = """
        UPDATE settings
        SET notify_auction_start = FALSE,
            notify_bid_reminder  = FALSE,
            notify_auction_end   = FALSE,
            notify_daily_today   = FALSE
        WHERE user_id = $1 \
        """
    async with db_pool.acquire() as conn:
        await conn.execute(q, user_id)

@require_db_pool
async def clear_all_card_subscriptions(user_id: int) -> None:
    q = "DELETE FROM user_subscriptions WHERE user_id = $1"
    async with db_pool.acquire() as conn:
        await conn.execute(q, user_id)

@require_db_pool
async def mark_user_unreachable(user_id: int, reason: str) -> None:
    q = """
        INSERT INTO unreachable_users(user_id, reason, last_seen)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET reason    = EXCLUDED.reason,
                last_seen = NOW() \
        """
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(q, user_id, reason)
        except Exception:
            pass

@require_db_pool
async def get_users_with_pref(pref: str) -> List[int]:
    col = ALLOWED_PREFS.get(pref)
    if not col:
        return []
    q = f"SELECT user_id FROM settings WHERE COALESCE({col}, TRUE) = TRUE"
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(q)
    return [int(r["user_id"]) for r in rows]

async def unsubscribe_subscription(sub_id: int, user_id: int) -> bool:
    """
    Удаляет запись подписки по id, только если она принадлежит user_id.
    Возвращает True, если удалили.
    """
    sql = """
          DELETE
          FROM user_subscriptions
          WHERE id = $1
            AND user_id = $2
          RETURNING id \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, sub_id, user_id)
    return row is not None

@require_db_pool
async def get_top_subscribed_cards(
        limit: int = 20,
        offset: int = 0,
        only_luxury: bool = False,
) -> Tuple[list[dict], int]:
    async with db_pool.acquire() as conn:
        subs_sql = """
                   WITH subs AS (SELECT us.card_id, COUNT(*) AS subs_count
                                 FROM user_subscriptions us
                                          JOIN users u ON u.user_id = us.user_id
                                 WHERE us.card_id IS NOT NULL
                                   AND ($3::bool IS FALSE OR u.is_luxury = TRUE)
                                 GROUP BY us.card_id),
                        sched AS (SELECT LOWER(a.card_name) AS cn,
                                         LOWER(a.hero_name) AS hn,
                                         COUNT(*)           AS scheduled_count
                                  FROM auctions a
                                  WHERE a.status IN ('scheduled', 'active', 'approved')
                                  GROUP BY LOWER(a.card_name), LOWER(a.hero_name))
                   SELECT c.card_id,
                          c.card_name,
                          c.hero_name,
                          c.deck_id,
                          s.subs_count,
                          COALESCE(sc.scheduled_count, 0) AS scheduled_count
                   FROM subs s
                            JOIN cards c ON c.card_id = s.card_id
                            LEFT JOIN sched sc
                                      ON sc.cn = LOWER(c.card_name)
                                          AND sc.hn = LOWER(c.hero_name)
                   ORDER BY s.subs_count DESC, c.card_name ASC
                   LIMIT $1 OFFSET $2 \
                   """
        total_sql = """
                    WITH subs AS (SELECT us.card_id
                                  FROM user_subscriptions us
                                           JOIN users u ON u.user_id = us.user_id
                                  WHERE us.card_id IS NOT NULL
                                    AND ($1::bool IS FALSE OR u.is_luxury = TRUE)
                                  GROUP BY us.card_id)
                    SELECT COUNT(*)::int
                    FROM subs \
                    """
        rows = await conn.fetch(subs_sql, limit, offset, only_luxury)
        total = await conn.fetchval(total_sql, only_luxury)
        return [dict(r) for r in rows], int(total)

async def subscribe_preset(user_id: int, key: str) -> None:
    await execute(
        """
        INSERT INTO user_preset_subscriptions(user_id, preset_id)
        SELECT $1, p.id
        FROM presets p
        WHERE p.key = $2
        ON CONFLICT DO NOTHING
        """,
        user_id, key
    )

async def list_my_preset_subs(user_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT ups.id, p.key, p.title, ups.created_at
        FROM user_preset_subscriptions ups
                 JOIN presets p ON p.id = ups.preset_id
        WHERE ups.user_id = $1
        ORDER BY ups.id DESC
        """,
        user_id
    )
    return [dict(r) for r in rows]

async def unsubscribe_preset(sub_id: int, user_id: int) -> None:
    await execute(
        "DELETE FROM user_preset_subscriptions WHERE id=$1 AND user_id=$2",
        sub_id, user_id
    )

async def subscribers_for_lot_title(lot_title: str) -> List[int]:
    rows = await fetch(
        """
        SELECT DISTINCT ups.user_id
        FROM user_preset_subscriptions ups
                 JOIN preset_aliases a ON a.preset_id = ups.preset_id
        WHERE LOWER(a.alias) = LOWER($1)
        """,
        lot_title
    )
    return [r["user_id"] for r in rows]

@require_db_pool
async def list_broadcast_targets() -> List[int]:
    """
    Все пользователи, которым можно писать в ЛС:
      • не помечены как недоступные (unreachable_users)
      • глобально не отписаны (is_subscribed != FALSE)
      • открывали ЛС с ботом (pm_opened = TRUE)
    """
    sql = """
          SELECT u.user_id
          FROM users u
                   LEFT JOIN unreachable_users uu ON uu.user_id = u.user_id
          WHERE uu.user_id IS NULL
            AND COALESCE(u.is_subscribed, TRUE) = TRUE
            AND COALESCE(u.pm_opened, FALSE) = TRUE \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [int(r["user_id"]) for r in rows]

@require_db_pool
async def list_user_card_subs(user_id: int) -> list[dict]:
    """
    Список подписок пользователя с названиями карт и героев.
    Пустые строки в cards.* считаем отсутствием данных.
    """
    sql = """
          SELECT us.id                   AS id,
                 us.card_id,
                 NULLIF(c.card_name, '') AS card_name,
                 NULLIF(c.hero_name, '') AS hero_name,
                 us.last_confirmed_at
          FROM user_subscriptions us
                   JOIN cards c ON c.card_id = us.card_id
          WHERE us.user_id = $1
          ORDER BY c.card_name, c.hero_name \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]

async def mark_subscription_confirmed(sub_id: int, user_id: int) -> bool:
    """
    Ставит отметку подтверждения этой подписки на сейчас.
    """
    sql = """
          UPDATE user_subscriptions
          SET last_confirmed_at = now()
          WHERE id = $1
            AND user_id = $2
          RETURNING id \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, sub_id, user_id)
    return row is not None

async def mark_unreachable_user(user_id: int, reason: str) -> None:
    """
    Сохраняем факт недоступности пользователя (заблокировал, запрет ЛС и т.д.)
    """
    sql = """
          INSERT INTO unreachable_users (user_id, reason, last_seen)
          VALUES ($1, $2, now())
          ON CONFLICT (user_id) DO UPDATE
              SET reason    = EXCLUDED.reason,
                  last_seen = now() \
          """
    async with db_pool.acquire() as conn:
        await conn.execute(sql, user_id, reason)

async def _preset_user_ids_by_key_or_alias(key: str) -> List[int]:
    """
    Возвращает список user_id, подписанных на пресет с заданным ключом
    или любой из его алиасов.
    """
    sql = """
          SELECT ups.user_id
          FROM user_preset_subscriptions ups
                   JOIN presets p ON p.id = ups.preset_id
                   LEFT JOIN preset_aliases pa ON pa.preset_id = p.id
          WHERE lower(p.key) = lower($1)
             OR lower(pa.alias) = lower($1) \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, key)
    return [int(r["user_id"]) for r in rows]

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _rarity_slug(r: Optional[str]) -> Optional[str]:
    r = _norm(r)
    if not r:
        return None
    # русские прилагательные, существительные и англ
    mapping = {
        "бронзовая": "bronze", "бронза": "bronze", "bronze": "bronze",
        "серебряная": "silver", "серебро": "silver", "silver": "silver",
        "золотая": "gold", "золото": "gold", "gold": "gold",
        "алмазная": "diamond", "алмазы": "diamond", "алмаз": "diamond",
        "diamond": "diamond", "diamonds": "diamond",
    }
    return mapping.get(r, r)  # если пришло что-то экзотическое — используем как есть

async def subscribers_for_rarity(rarity: Optional[str]) -> List[int]:
    """
    rarity= 'золотая'/'gold'/... -> ищем пресет с ключом 'rarity:<slug>'
    и его алиасы.
    """
    slug = _rarity_slug(rarity)
    if not slug:
        return []
    key = f"rarity:{slug}"
    return await _preset_user_ids_by_key_or_alias(key)

async def subscribers_for_deck(deck_id: Optional[int], deck_name: Optional[str]) -> List[int]:
    """
    Ищем два вида ключей:
      - 'deck:<id>'
      - 'deck:<имя колоды>' (в нижнем регистре)
    Любой из них может быть основным ключом или алиасом.
    """
    out: List[int] = []
    if deck_id:
        out += await _preset_user_ids_by_key_or_alias(f"deck:{int(deck_id)}")
    if deck_name:
        out += await _preset_user_ids_by_key_or_alias(f"deck:{_norm(deck_name)}")
    # убираем возможные дубли
    return list({int(x) for x in out})

