"""Card notification and preset-subscription queries.

Extracted from the legacy database facade without changing SQL semantics.
"""

from __future__ import annotations

import json
import re
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


ALLOWED_PREFS = {
    "notify_auction_start": "notify_auction_start",
    "notify_bid_reminder": "notify_bid_reminder",
    "notify_auction_end": "notify_auction_end",
    "notify_daily_today": "notify_daily_today",
}


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
    q = f"""
        SELECT s.user_id
        FROM settings AS s
        JOIN users AS u ON u.user_id = s.user_id
        LEFT JOIN unreachable_users AS uu ON uu.user_id = s.user_id
        WHERE COALESCE(s.{col}, TRUE) = TRUE
          AND COALESCE(u.is_subscribed, TRUE) = TRUE
          AND COALESCE(u.pm_opened, FALSE) = TRUE
          AND uu.user_id IS NULL
    """
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


_WHOLE_DECK_RE = re.compile(
    r"^\s*вся\s+колода(?:\s*[№#]|\s+)?\s*(\d+)\b",
    re.IGNORECASE,
)
_SPINS_RE = re.compile(r"\b(?:кручени[яй]|spins?)\D*(10|50|100)\b", re.IGNORECASE)
_SUBSCRIPTION_RE = re.compile(
    r"\b(золот(?:ой|ая)|gold|премиум|premium)\s+(?:пропуск|подписк\w*|pass)?"
    r"\D*(1|3|6|12)\s*(?:месяц\w*|мес\.?|months?)\b",
    re.IGNORECASE,
)


def _normalized_lot_title(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().replace("ё", "е").split())


def _deck_id_from_lot_title(lot_title: Optional[str]) -> Optional[int]:
    match = _WHOLE_DECK_RE.search(_normalized_lot_title(lot_title))
    return int(match.group(1)) if match else None


def preset_keys_for_auction(
        *,
        lot_title: Optional[str],
        card_id: Optional[int] = None,
        rarity: Optional[str] = None,
        deck_id: Optional[int] = None,
        deck_name: Optional[str] = None,
) -> list[str]:
    """Return all preset keys/aliases that describe an auction lot.

    The subscription UI historically used keys such as ``deck_all_26`` and
    ``any_gold``, while the notifier searched for ``deck:26`` and
    ``rarity:gold``.  Keep both forms so existing databases and aliases remain
    compatible while every new preset follows one canonical path.
    """

    raw_title = (lot_title or "").strip()
    title = _normalized_lot_title(raw_title)
    keys: list[str] = []

    def add(value: Optional[str]) -> None:
        value = (value or "").strip()
        if value and value.lower() not in {item.lower() for item in keys}:
            keys.append(value)

    # Exact human-readable aliases remain supported.
    add(raw_title)

    title_deck_id = _deck_id_from_lot_title(raw_title)
    resolved_deck_id = int(deck_id) if deck_id else title_deck_id
    is_whole_deck = title_deck_id is not None or title.startswith("любая колода")

    rarity_slug = _rarity_slug(rarity)
    if not rarity_slug:
        title_rarity_markers = {
            "bronze": ("бронз", "bronze"),
            "silver": ("серебр", "silver"),
            "gold": ("золот", "gold"),
            "diamond": ("алмаз", "diamond"),
        }
        for slug, markers in title_rarity_markers.items():
            if any(marker in title for marker in markers):
                rarity_slug = slug
                break

    is_any_card_lot = (
        title.startswith("любая карта")
        or (title.startswith(("любая ", "любой ", "любое ", "любые ")) and bool(rarity_slug))
    )
    is_specific_card = bool(card_id) or (bool(resolved_deck_id) and not is_whole_deck)

    if is_whole_deck:
        add("any_deck")
    elif is_any_card_lot or is_specific_card:
        add("any_card")

    if resolved_deck_id:
        add(f"deck_all_{resolved_deck_id}")
        add(f"deck:{resolved_deck_id}")
    if deck_name:
        add(f"deck:{_norm(deck_name)}")

    if rarity_slug and not is_whole_deck:
        add(f"any_{rarity_slug}")
        add(f"rarity:{rarity_slug}")

    if title == "друзья+" or title.startswith("друзья плюс"):
        add("friends_plus")
    if title.startswith("слоты прогресса"):
        add("progress_slots")

    spins = _SPINS_RE.search(title)
    if spins:
        add(f"spins_{spins.group(1)}")

    subscription = _SUBSCRIPTION_RE.search(title)
    if subscription:
        plan_raw, months = subscription.groups()
        plan = "gold" if plan_raw.startswith(("золот", "gold")) else "premium"
        add(f"subscription_{plan}_{months}")

    return keys


async def subscribers_for_auction_presets(
        *,
        lot_title: Optional[str],
        card_id: Optional[int] = None,
        rarity: Optional[str] = None,
        deck_id: Optional[int] = None,
        deck_name: Optional[str] = None,
) -> List[int]:
    keys = preset_keys_for_auction(
        lot_title=lot_title,
        card_id=card_id,
        rarity=rarity,
        deck_id=deck_id,
        deck_name=deck_name,
    )
    if not keys:
        return []

    sql = """
          SELECT DISTINCT ups.user_id
          FROM user_preset_subscriptions AS ups
                   JOIN presets AS p ON p.id = ups.preset_id
                   LEFT JOIN preset_aliases AS pa ON pa.preset_id = p.id
          WHERE lower(p.key) = ANY($1::text[])
             OR lower(pa.alias) = ANY($1::text[])
          """
    normalized = list(dict.fromkeys(value.lower() for value in keys))
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, normalized)
    return [int(row["user_id"]) for row in rows]


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
    out = await _preset_user_ids_by_key_or_alias(f"rarity:{slug}")
    out += await _preset_user_ids_by_key_or_alias(f"any_{slug}")
    return list({int(user_id) for user_id in out})


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
        out += await _preset_user_ids_by_key_or_alias(f"deck_all_{int(deck_id)}")
    if deck_name:
        out += await _preset_user_ids_by_key_or_alias(f"deck:{_norm(deck_name)}")
    # убираем возможные дубли
    return list({int(x) for x in out})



__all__ = [
    "add_user_subscription",
    "get_user_subscriptions",
    "remove_user_subscription",
    "get_card_subscribers",
    "disable_all_notifications",
    "clear_all_card_subscriptions",
    "mark_user_unreachable",
    "get_users_with_pref",
    "unsubscribe_subscription",
    "get_top_subscribed_cards",
    "subscribe_preset",
    "list_my_preset_subs",
    "unsubscribe_preset",
    "subscribers_for_lot_title",
    "preset_keys_for_auction",
    "subscribers_for_auction_presets",
    "_deck_id_from_lot_title",
    "list_broadcast_targets",
    "list_user_card_subs",
    "mark_subscription_confirmed",
    "mark_unreachable_user",
    "_preset_user_ids_by_key_or_alias",
    "_norm",
    "_rarity_slug",
    "subscribers_for_rarity",
    "subscribers_for_deck",
    "ALLOWED_PREFS",
]
