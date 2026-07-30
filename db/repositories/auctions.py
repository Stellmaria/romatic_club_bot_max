from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from datetime import date as _date
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import asyncpg

from bot.core.time import ensure_utc
from db.types import Owner
from config import DATABASE_URL
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
    get_user,
)

"""Auctions persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'add_auction',
    'get_lots_by_owner',
    'list_auctions',
    'get_pending_auctions',
    'get_auctions_by_date',
    'get_occupied_slots',
    'get_lot_by_id',
    'get_auctions_by_day',
    'get_lot_owners',
    'auction_exists',
    'get_expected_auction_for_now',
    'release_stale_unpublished_lots',
    'has_pending_lot',
    'cancel_owner_unpublished_lots',
    'get_lot_by_message_id',
    'get_lot_approval_info',
    'get_auctions_by_card_ref',
    'get_auctions_in_range',
    'get_auctions_for_local_day',
    'get_auctions_by_date_with_owners',
    'auto_finish_old_lots_for_owner',
    'set_owner_lot_folder',
    'get_lots_by_owner_view',
    'show_pending_auction_lots',
    'count_sold_same_card',
    'count_sold_by_card_id',
    'get_auction_ids_ended_on',
]

@require_db_pool
async def add_auction(card_name: str, hero_name: str, image_id: str, owner_id: int,
                      start_price: int, currency: str, start_time: datetime, end_time: datetime,
                      status: str, comment: str) -> None:
    try:
        async with db_pool.acquire() as conn:
            # Сначала вставляем сам аукцион (без owner_id!)
            row = await conn.fetchrow(
                """INSERT INTO auctions
                   (card_name, hero_name, image_id, start_price, currency, start_time, end_time, status, comment)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   RETURNING auction_id
                """,
                card_name, hero_name, image_id, start_price, currency, start_time, end_time, status, comment
            )
            auction_id = row["auction_id"]
            # Затем добавляем связь с владельцем
            await conn.execute(
                "INSERT INTO auction_owners (auction_id, user_id) VALUES ($1, $2)",
                auction_id, owner_id
            )
    except Exception as e:
        logger.error(f"Error adding auction: {e}")

@require_db_pool
async def get_lots_by_owner(user_id: int) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.*
                FROM auctions a
                         JOIN auction_owners ao ON a.auction_id = ao.auction_id
                WHERE ao.user_id = $1
                ORDER BY a.start_time DESC
                """,
                user_id
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting lots by owner {user_id}: {e}")
        return []

@require_db_pool
async def list_auctions(statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            if statuses:
                q = """
                    SELECT a.*, c.card_id
                    FROM auctions a
                             LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                    WHERE a.status = ANY ($1::varchar[])
                    ORDER BY a.start_time, a.auction_id
                    """
                return [dict(r) for r in await conn.fetch(q, statuses)]
            else:
                q = """
                    SELECT a.*, c.card_id
                    FROM auctions a
                             LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                    ORDER BY a.start_time \
                    """
                return [dict(r) for r in await conn.fetch(q)]

    except Exception as e:
        logger.error(f"Error listing auctions: {e}")
        return []

@require_db_pool
async def get_pending_auctions(
        auction_kind: Optional[str] = None,
        *,
        limit: int = 50,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    where = ["a.status='pending'"]
    args: List[Any] = []

    if auction_kind:
        where.append("a.auction_kind=$%d" % (len(args) + 1))
        args.append(auction_kind)

    where_sql = " AND ".join(where)

    args.append(int(limit))
    limit_i = len(args)
    args.append(int(offset))
    offset_i = len(args)

    sql = f"""
        SELECT
            a.auction_id,
            a.card_name,
            a.hero_name,
            COALESCE(NULLIF(a.image_id, ''), c.image_id) AS image_id,
            a.start_price,
            a.currency,
            a.comment,
            a.created_at,
            a.proof_photo_id,
            a.auction_kind,
            a.created_at,
a.proof_photo_id,
a.craft_uid_possible,
a.auction_kind,
            c.card_id,
            c.num AS card_num,
            c.deck_id,
            d.name AS deck_name,
            c.rarity,
            c.obtain_type,
            c.obtain_amount,
            c.story,
            c.quote,
            c.image_id AS card_image_id
        FROM public.auctions a
        LEFT JOIN public.cards c
            ON lower(trim(c.card_name)) = lower(trim(a.card_name))
           AND lower(trim(coalesce(c.hero_name, ''))) = lower(trim(coalesce(a.hero_name, '')))
        LEFT JOIN public.decks d
            ON d.id = c.deck_id
        WHERE {where_sql}
        ORDER BY a.created_at ASC
        LIMIT ${limit_i} OFFSET ${offset_i}
    """

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

@require_db_pool
async def get_auctions_by_date(selected_date: date) -> List[Dict[str, Any]]:
    SQL = """
          SELECT a.auction_id,
                 a.card_name,
                 a.hero_name,
                 a.start_time,
                 a.end_time,
                 a.currency,
                 a.status,
                 a.message_id,
                 c.card_id,
                 c.deck_id
          FROM public.auctions a
                   LEFT JOIN LATERAL (
              SELECT candidate.card_id, candidate.deck_id
              FROM public.cards candidate
              WHERE lower(trim(candidate.card_name)) = lower(trim(a.card_name))
                AND lower(trim(coalesce(candidate.hero_name, ''))) =
                    lower(trim(coalesce(a.hero_name, '')))
              ORDER BY candidate.card_id
              LIMIT 1
              ) c ON true
          WHERE CASE
                  WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                    THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                  ELSE a.start_time::date
                END = $1
            AND a.status IN ('approved', 'scheduled', 'publishing', 'active', 'finished')
          ORDER BY a.start_time, a.auction_id
          """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(SQL, selected_date)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting auctions by date {selected_date}: {e}")
        return []

@require_db_pool
async def get_occupied_slots(selected_date: date) -> List[Tuple]:
    """Return occupied schedule slots as naive Moscow wall-clock times."""
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT start_time, end_time
                FROM auctions
                WHERE CASE
                        WHEN pg_typeof(start_time)::text = 'timestamp with time zone'
                          THEN (start_time AT TIME ZONE 'Europe/Moscow')::date
                        ELSE start_time::date
                      END = $1
                  AND status IN ('approved', 'scheduled', 'publishing', 'active')
                """,
                selected_date,
            )
            from bot.core.time import to_moscow_wall
            return [
                (
                    to_moscow_wall(row['start_time']).time(),
                    to_moscow_wall(row['end_time']).time(),
                )
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Error getting occupied slots for {selected_date}: {e}")
        return []

async def get_lot_by_id(auction_id: int) -> Optional[Dict[str, Any]]:
    def _infer_any_rarity_from_title(title: str) -> str | None:
        t = (title or "").strip().lower()
        if "бронз" in t:
            return "bronze"
        if "сереб" in t:
            return "silver"
        if "золот" in t:
            return "gold"
        if "алмаз" in t or "эпик" in t:
            return "diamond"
        return None

    def _is_any_lot(title: str) -> bool:
        t = (title or "").strip().lower()
        return ("любая" in t) or ("любой" in t)

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.*,
                       c.card_id,
                       c.num      AS card_num,
                       c.deck_id,
                       c.rarity,
                       c.obtain_type,
                       c.obtain_amount,
                       c.story,
                       c.quote,
                       c.image_id AS card_image_id,
                       d.name     AS deck_name
                FROM public.auctions a
                         LEFT JOIN public.cards c
                                   ON lower(trim(c.card_name)) = lower(trim(a.card_name))
                                       AND
                                      lower(trim(coalesce(c.hero_name, ''))) = lower(trim(coalesce(a.hero_name, '')))
                         LEFT JOIN public.decks d
                                   ON d.id = c.deck_id
                WHERE a.auction_id = $1
                """,
                auction_id,
            )
            if not row:
                return None

            lot = dict(row)

            # --- ДОП.ИНФА ДЛЯ ЛОТОВ "ЛЮБАЯ ..." (которые не матчатся на cards) ---
            title = str(lot.get("card_name") or "").strip()
            if (not lot.get("card_id")) and _is_any_lot(title):
                any_rarity = _infer_any_rarity_from_title(title)

                deck_rows = await conn.fetch(
                    """
                    SELECT DISTINCT c.deck_id
                    FROM public.cards c
                    WHERE c.deck_id IS NOT NULL
                      AND ($1::text IS NULL OR lower(c.rarity) = lower($1))
                    ORDER BY c.deck_id
                    """,
                    any_rarity,
                )

                lot["any_rarity"] = any_rarity
                lot["possible_deck_ids"] = [int(r["deck_id"]) for r in deck_rows if r["deck_id"] is not None]

            return lot

    except Exception as e:
        logger.error(f"Error getting lot by id {auction_id}: {e}")
        return None

@require_db_pool
async def get_auctions_by_day(chosen_date: date) -> List[Dict[str, Any]]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                                    SELECT *
                                    FROM auctions
                                    WHERE (start_time AT TIME ZONE 'Europe/Moscow')::date = $1
                                    ORDER BY start_time
                                    """, chosen_date)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения аукционов на день: {e}")
        return []

@require_db_pool
async def get_lot_owners(lot_id: int) -> list[Owner]:
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.user_id,
                       u.username,
                       u.full_name,
                       u.is_luxury,
                       u.is_trusted
                FROM public.auction_owners ao
                         JOIN public.users u ON u.user_id = ao.user_id
                WHERE ao.auction_id = $1
                ORDER BY COALESCE(NULLIF(u.username, ''), u.full_name, u.user_id::text)
                """,
                lot_id,
            )
            result: list[Owner] = [
                {
                    "user_id": int(r["user_id"]),
                    "username": (r["username"] or None),
                    "full_name": (r["full_name"] or None),
                    "is_luxury": bool(r["is_luxury"]),
                    "is_trusted": bool(r["is_trusted"]),
                }
                for r in rows
            ]
            return result
    except Exception as e:
        logger.error(f"Error getting lot owners for lot {lot_id}: {e}")
        return []

@require_db_pool
async def auction_exists(auction_id: int) -> bool:
    async with db_pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM auctions WHERE auction_id = $1",
            auction_id
        ))

async def get_expected_auction_for_now():
    conn = await asyncpg.connect(dsn=DATABASE_URL)
    row = await conn.fetchrow("""
                              SELECT auction_id, card_name, start_time
                              FROM auctions
                              WHERE discussion_message_id IS NULL
                                AND start_time <= now() + interval '10 minutes'
                              ORDER BY start_time ASC
                              LIMIT 1
                              """)
    await conn.close()
    return dict(row) if row else None

@require_db_pool
async def release_stale_unpublished_lots(user_id: int | None = None) -> list[int]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.auctions AS a
            SET status = 'publication_failed'
            WHERE a.message_id IS NULL
              AND a.status IN ('scheduled', 'publishing')
              AND a.start_time < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
              AND (
                    $1::bigint IS NULL
                    OR EXISTS (
                        SELECT 1 FROM public.auction_owners AS ao
                        WHERE ao.auction_id = a.auction_id
                          AND ao.user_id = $1
                    )
                  )
            RETURNING a.auction_id
            """,
            int(user_id) if user_id is not None else None,
        )
        return [int(row["auction_id"]) for row in rows]


@require_db_pool
async def has_pending_lot(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.auctions AS a
            SET status = 'publication_failed'
            WHERE a.message_id IS NULL
              AND a.status IN ('scheduled', 'publishing')
              AND a.start_time < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
              AND EXISTS (
                    SELECT 1 FROM public.auction_owners AS ao
                    WHERE ao.auction_id = a.auction_id
                      AND ao.user_id = $1
                  )
            """,
            int(user_id),
        )
        result = await conn.fetchval(
            """
            SELECT EXISTS (SELECT 1
                           FROM auctions a
                                    JOIN auction_owners ao ON a.auction_id = ao.auction_id
                           WHERE ao.user_id = $1
                             AND a.status IN (
                                 'draft', 'moderation', 'pending', 'approved',
                                 'scheduled', 'publishing', 'active'
                             ))
            """,
            user_id
        )
        return bool(result)


@require_db_pool
async def cancel_owner_unpublished_lots(user_id: int) -> list[int]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.auctions AS a
            SET status = 'cancelled'
            WHERE a.message_id IS NULL
              AND a.status IN (
                    'draft', 'moderation', 'pending', 'approved',
                    'publication_failed'
                  )
              AND EXISTS (
                    SELECT 1 FROM public.auction_owners AS ao
                    WHERE ao.auction_id = a.auction_id
                      AND ao.user_id = $1
                  )
            RETURNING a.auction_id
            """,
            int(user_id),
        )
        return [int(row["auction_id"]) for row in rows]

@require_db_pool
async def get_lot_by_message_id(message_id: int) -> Optional[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM auctions
            WHERE message_id = $1
            """,
            message_id
        )
        return dict(row) if row else None

async def get_lot_approval_info(auction_id: int) -> tuple[str, datetime | None]:
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, created_at AS created_at
                FROM audit_logs
                WHERE auction_id = $1
                  AND action_type = 'approve_lot'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                auction_id
            )
            if row:
                admin_id = row["user_id"]
                admin_user = await get_user(admin_id)
                admin_username = admin_user["username"] if admin_user and admin_user.get(
                    "username") else f"id{admin_id}"
                approved_at = row["created_at"]
                return admin_username, approved_at
            return "-", None
    except Exception as e:
        logger.error(f"Ошибка поиска approval_info для лота {auction_id}: {e}")
        return "-", None

@require_db_pool
async def get_auctions_by_card_ref(query: str, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    st = statuses or ["pending", "scheduled", "active"]
    qraw = (query or "").strip()

    # Попытка распарсить card_id
    card_id: Optional[int] = None
    try:
        card_id = int(qraw)
    except Exception:
        pass

    # Попытка распарсить формат "(Герой) Название"
    hero_exact = name_exact = None
    if "(" in qraw and ")" in qraw and qraw.index("(") < qraw.index(")"):
        try:
            hero_exact = qraw[qraw.index("(") + 1: qraw.index(")")].strip() or None
            name_exact = qraw[qraw.index(")") + 1:].strip() or None
        except Exception:
            hero_exact = name_exact = None

    async with db_pool.acquire() as conn:
        rows = []
        if card_id is not None:
            sql = """
                  SELECT DISTINCT ON (a.auction_id) a.auction_id,
                                                    a.start_time,
                                                    a.end_time,
                                                    a.status,
                                                    a.currency,
                                                    a.start_price,
                                                    c.card_id                                       AS card_id,
                                                    COALESCE(NULLIF(a.card_name, '-'), c.card_name) AS card_name,
                                                    COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) AS hero_name,
                                                    COALESCE(a.image_id, c.image_id)                AS image_id,
                                                    c.deck_id
                  FROM auctions a
                           LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                  WHERE a.status = ANY ($1::varchar[])
                    AND c.card_id = $2
                  ORDER BY a.auction_id, a.start_time \
                  """
            rows = await conn.fetch(sql, st, card_id)

        elif hero_exact or name_exact:
            where_parts, params = [], [st]
            if hero_exact:
                where_parts.append(f"COALESCE(NULLIF(a.hero_name,'-'), c.hero_name) ILIKE ${len(params) + 1}")
                params.append(f"%{hero_exact}%")
            if name_exact:
                where_parts.append(f"COALESCE(NULLIF(a.card_name,'-'), c.card_name) ILIKE ${len(params) + 1}")
                params.append(f"%{name_exact}%")

            sql = f"""
                SELECT DISTINCT ON (a.auction_id)
                    a.auction_id, a.start_time, a.end_time, a.status, a.currency, a.start_price,
                    c.card_id AS card_id,
                    COALESCE(NULLIF(a.card_name,'-'), c.card_name) AS card_name,
                    COALESCE(NULLIF(a.hero_name,'-'),  c.hero_name)  AS hero_name,
                    COALESCE(a.image_id, c.image_id) AS image_id,
                    c.deck_id
                FROM auctions a
                LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                WHERE a.status = ANY ($1::varchar[])
                  AND {" AND ".join(where_parts)}
                ORDER BY a.auction_id, a.start_time
            """
            rows = await conn.fetch(sql, *params)

        else:
            patt = f"%{qraw}%"
            sql = """
                  SELECT DISTINCT ON (a.auction_id) a.auction_id,
                                                    a.start_time,
                                                    a.end_time,
                                                    a.status,
                                                    a.currency,
                                                    a.start_price,
                                                    c.card_id                                       AS card_id,
                                                    COALESCE(NULLIF(a.card_name, '-'), c.card_name) AS card_name,
                                                    COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) AS hero_name,
                                                    COALESCE(a.image_id, c.image_id)                AS image_id,
                                                    c.deck_id
                  FROM auctions a
                           LEFT JOIN cards c ON a.card_name = c.card_name AND a.hero_name = c.hero_name
                  WHERE a.status = ANY ($1::varchar[])
                    AND (
                      COALESCE(NULLIF(a.hero_name, '-'), c.hero_name) ILIKE $2
                          OR COALESCE(NULLIF(a.card_name, '-'), c.card_name) ILIKE $2
                      )
                  ORDER BY a.auction_id, a.start_time \
                  """
            rows = await conn.fetch(sql, st, patt)

        return [dict(r) for r in rows]

@require_db_pool
async def get_auctions_in_range(start_dt: datetime, end_dt: datetime, statuses: Optional[List[str]] = None) -> List[
    Dict[str, Any]]:
    st = statuses or ["pending", "scheduled", "active"]
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT auction_id,
                   start_time,
                   end_time,
                   status
            FROM auctions
            WHERE start_time >= $1
              AND start_time < $2
              AND status = ANY ($3::varchar[])
            ORDER BY start_time
            """,
            ensure_utc(start_dt), ensure_utc(end_dt), st
        )
        return [dict(r) for r in rows]

async def get_auctions_for_local_day(day: _date, tzname: str = "Europe/Moscow") -> list[dict]:
    pool = await get_db_pool()
    tz = tzname

    sql = f"""
    WITH src AS (
      SELECT
        a,
        COALESCE(
          NULLIF(to_jsonb(a)->>'starts_at', '')::timestamptz,
          NULLIF(to_jsonb(a)->>'start_time', '')::timestamptz,
          NULLIF(to_jsonb(a)->>'dt', '' )::timestamptz,
          NULLIF(to_jsonb(a)->>'ts', '' )::timestamptz
        ) AS ts_utc
      FROM auctions a
    )
    SELECT
      (ts_utc AT TIME ZONE '{tz}')::time AS time,
      COALESCE(
        NULLIF(to_jsonb(a)->>'title', ''),
        NULLIF(to_jsonb(a)->>'name', ''),
        NULLIF(to_jsonb(a)->>'lot_title', ''),
        NULLIF(to_jsonb(a)->>'caption', ''),
        NULLIF(to_jsonb(a)->>'text', ''),
        'Лот'
      ) AS title
    FROM src
    WHERE ts_utc IS NOT NULL
      AND (ts_utc AT TIME ZONE '{tz}')::date = $1::date
    ORDER BY ts_utc;
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, day)

    return [dict(r) for r in rows]

@require_db_pool
async def get_auctions_by_date_with_owners(day: date) -> List[Dict[str, Any]]:
    """Return the current schedule snapshot for a Moscow calendar day.

    The admin ``Расписание`` button must describe slots that are still part of
    the live workflow.  ``finished`` rows are history and must not reserve a
    slot, while ``approved`` and ``publishing`` rows are already committed to
    the schedule even before their final status becomes ``scheduled``.

    A lateral single-card lookup avoids multiplying one auction when legacy
    card rows contain duplicate names.
    """

    sql = """
          SELECT a.*,
                 c.card_id,
                 c.deck_id,
                 COALESCE(o.owners_json, '[]'::json) AS owners_json
          FROM public.auctions a
                   LEFT JOIN LATERAL (
              SELECT json_agg(
                     json_build_object('user_id', ao.user_id, 'username', u.username)
                     ORDER BY ao.id
                             ) FILTER (WHERE ao.user_id IS NOT NULL) AS owners_json
              FROM public.auction_owners ao
                       LEFT JOIN public.users u ON u.user_id = ao.user_id
              WHERE ao.auction_id = a.auction_id
              ) o ON true
                   LEFT JOIN LATERAL (
              SELECT candidate.card_id, candidate.deck_id
              FROM public.cards candidate
              WHERE lower(trim(candidate.card_name)) = lower(trim(a.card_name))
                AND lower(trim(coalesce(candidate.hero_name, ''))) =
                    lower(trim(coalesce(a.hero_name, '')))
              ORDER BY candidate.card_id
              LIMIT 1
              ) c ON true
          WHERE CASE
                  WHEN pg_typeof(a.start_time)::text = 'timestamp with time zone'
                    THEN (a.start_time AT TIME ZONE 'Europe/Moscow')::date
                  ELSE a.start_time::date
                END = $1
            AND a.status IN ('approved', 'scheduled', 'publishing', 'active')
          ORDER BY a.start_time, a.auction_id
          """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(sql, day)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_auctions_by_date_with_owners failed: %s", e)
        return []

@require_db_pool
async def auto_finish_old_lots_for_owner(user_id: int) -> int:
    """
    Закрываем старые лоты владельца, чтобы /my_lots показывал актуал.
    Возвращает количество закрытых лотов.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE auctions a
            SET status = 'finished'
            WHERE a.status = 'active'
              AND a.end_time IS NOT NULL
              AND a.end_time < NOW()
              AND EXISTS (SELECT 1
                          FROM auction_owners ao
                          WHERE ao.auction_id = a.auction_id
                            AND ao.user_id = $1)
            RETURNING a.auction_id
            """,
            user_id,
        )
        finished_ids = [int(r["auction_id"]) for r in rows]
        if not finished_ids:
            return 0

        # Если есть папки у владельца, переместим в archived
        col = None
        if await _has_column(conn, "auction_owners", "owner_folder"):
            col = "owner_folder"
        elif await _has_column(conn, "auction_owners", "folder"):
            col = "folder"

        if col:
            await conn.execute(
                f"""
                UPDATE auction_owners
SET {col} = 'archived'
                 WHERE user_id = $1
                   AND auction_id = ANY($2::int[])
                """,
                user_id,
                finished_ids,
            )

        return len(finished_ids)

@require_db_pool
async def set_owner_lot_folder(user_id: int, auction_id: int, folder: str) -> None:
    """
    Ставит "папку" (категорию) конкретного лота для владельца.
    Поддерживает owner_folder / folder (что реально в схеме).
    """
    f = (folder or "").strip().lower()
    if f == "archive":
        f = "archived"
    if f not in {"default", "payable", "archived"}:
        f = "default"

    async with db_pool.acquire() as conn:
        col = None
        if await _has_column(conn, "auction_owners", "owner_folder"):
            col = "owner_folder"
        elif await _has_column(conn, "auction_owners", "folder"):
            col = "folder"

        if not col:
            logger.warning(
                "auction_owners has no owner_folder/folder column; set_owner_lot_folder is a no-op"
            )
            return

        await conn.execute(
            f"""
            UPDATE auction_owners
               SET {col} = $3
             WHERE user_id = $1
               AND auction_id = $2
            """,
            int(user_id),
            int(auction_id),
            f,
        )

async def get_lots_by_owner_view(
        owner_id: int,
        folder: Optional[str] = None,
        status: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 20,
        offset: int = 0,
):
    """
    Лоты владельца с фильтром по статусам + папке (auction_owners.folder / owner_folder).
    folder:
      - None -> без фильтра по папке
      - default -> только default (и NULL тоже считаем default)
      - payable -> только payable
      - archived -> archived + старое archive
    """
    # статусы -> единый список
    all_statuses: list[str] = []
    if statuses:
        all_statuses.extend([s for s in statuses if s])
    if status:
        all_statuses.append(status)

    seen = set()
    all_statuses = [s for s in all_statuses if not (s in seen or seen.add(s))]

    async with db_pool.acquire() as conn:
        # какая колонка папки реально есть
        folder_col = None
        if await _has_column(conn, "auction_owners", "folder"):
            folder_col = "folder"
        elif await _has_column(conn, "auction_owners", "owner_folder"):
            folder_col = "owner_folder"

        where = ["ao.user_id = $1"]
        params: list[object] = [int(owner_id)]
        idx = 2

        if all_statuses:
            ph = []
            for s in all_statuses:
                ph.append(f"${idx}")
                params.append(s)
                idx += 1
            where.append(f"a.status IN ({', '.join(ph)})")

        # фильтр по папке
        f = (folder or "").strip().lower() if folder is not None else None
        if f == "archive":
            f = "archived"

        if f is not None and folder_col:
            if f == "default":
                where.append(f"COALESCE(ao.{folder_col}, 'default') = 'default'")
            elif f == "payable":
                where.append(f"ao.{folder_col} = 'payable'")
            elif f == "archived":
                where.append(f"ao.{folder_col} IN ('archived', 'archive')")
            else:
                where.append(f"COALESCE(ao.{folder_col}, 'default') = 'default'")

        params.append(int(limit))
        params.append(int(offset))

        folder_select = (
            f"COALESCE(ao.{folder_col}, 'default') AS folder"
            if folder_col
            else "'default'::text AS folder"
        )

        q = f"""
            SELECT
                a.auction_id,
                a.card_name,
                a.hero_name,
                a.image_id,
                a.start_price,
                a.start_time,
                a.end_time,
                a.status,
                a.currency,
                a.comment,
                a.message_id,
                a.discussion_message_id,
                a.proof_photo_id,
                a.created_at,
                {folder_select}
            FROM public.auctions a
            JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
            WHERE {" AND ".join(where)}
            ORDER BY COALESCE(a.start_time, a.created_at) DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        return await conn.fetch(q, *params)

@require_db_pool
async def show_pending_auction_lots(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """
    Для админки: pending-аукционы.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT auction_id,
                   hero_name,
                   card_name,
                   start_price,
                   currency,
                   accepted_currencies,
                   custom_offer_terms,
                   created_at,
                   comment,
                   auction_kind
            FROM auctions
            WHERE status = 'pending'
            ORDER BY created_at DESC, auction_id DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        return [dict(r) for r in rows]

@require_db_pool
async def count_sold_same_card(hero_name: str, card_name: str) -> int:
    """
    Сколько раз такая карта продавалась/закрывалась.
    """
    async with db_pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM auctions
                WHERE hero_name = $1
                  AND card_name = $2
                  AND status IN ('finished', 'sold', 'paid')
                """,
                hero_name, card_name,
            )
            or 0
        )

@require_db_pool
async def count_sold_by_card_id(card_id: int) -> int:
    """
    По card_id (из cards) считаем продажи через совпадение hero_name+card_name в auctions.
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hero_name, card_name FROM cards WHERE card_id=$1",
            card_id,
        )
        if not row:
            return 0
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM auctions
                WHERE hero_name = $1
                  AND card_name = $2
                  AND status IN ('finished', 'sold', 'paid')
                """,
                row["hero_name"], row["card_name"],
            )
            or 0
        )

@require_db_pool
async def get_auction_ids_ended_on(day: date) -> list[int]:
    sql = """
          SELECT a.auction_id
          FROM public.auctions a
          WHERE a.end_time IS NOT NULL
            AND (a.end_time AT TIME ZONE 'Europe/Moscow')::date = $1
          ORDER BY a.end_time ASC \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, day)
    return [int(r["auction_id"]) for r in rows]

