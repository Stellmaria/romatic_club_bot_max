"""Auction post statistics and manual correction queries.

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
async def get_post_months() -> list[dict]:
    """
    Возвращает список месяцев (YYYY-MM) по таблице auction_posts_backfill.
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT to_char(date_trunc('month', post_date_msk), 'YYYY-MM') AS ym,
                   COUNT(*)::int                                          AS cnt,
                   SUM(CASE WHEN s.checked THEN 1 ELSE 0 END)::int        AS checked_cnt
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE post_date_msk IS NOT NULL
              AND COALESCE(s.excluded, FALSE) = FALSE
            GROUP BY ym
            ORDER BY ym DESC
            """
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_post_days(ym: str) -> list[dict]:
    """
    ym: 'YYYY-MM'
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT (b.post_date_msk::date)                         AS day,
                   COUNT(*)::int                                   AS cnt,
                   SUM(CASE WHEN s.checked THEN 1 ELSE 0 END)::int AS checked_cnt
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE to_char(date_trunc('month', b.post_date_msk), 'YYYY-MM') = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            GROUP BY day
            ORDER BY day DESC
            """,
            ym,
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_posts_for_day(day: _date, offset: int = 0, limit: int = 12) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.post_id,
                   b.post_link,
                   b.post_date_msk,
                   b.end_time_msk,
                   b.deadline_msk,
                   b.thread_valid,
                   b.max_thread_valid,
                   b.winner_id,
                   COALESCE(s.checked, FALSE) AS checked
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_date_msk::date = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            ORDER BY b.post_date_msk DESC NULLS LAST
            OFFSET $2 LIMIT $3
            """,
            day, offset, limit,
        )
    return [dict(r) for r in rows]


@require_db_pool
async def count_posts_for_day(day: _date) -> int:
    async with db_pool.acquire() as conn:
        v = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_date_msk::date = $1
              AND COALESCE(s.excluded, FALSE) = FALSE
            """,
            day,
        )
    return int(v or 0)


@require_db_pool
async def get_post_details(post_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT b.*,
                   COALESCE(s.checked, FALSE)  AS checked,
                   COALESCE(s.excluded, FALSE) AS excluded,
                   s.excluded_by,
                   s.excluded_at,
                   s.excluded_reason,
                   s.checked_by,
                   s.checked_at,
                   s.manual_winner_id,
                   s.manual_max_bid,
                   s.manual_valid_bids,
                   s.manual_total_bids,
                   s.manual_note,
                   s.ordinal_no,
                   s.manual_date,
                   s.manual_time,
                   s.deck_no,
                   s.card_title,
                   s.bidders_count,
                   s.min_bid,
                   s.owner_id,
                   s.manual_link

            FROM public.auction_posts_backfill b
                     LEFT JOIN public.auction_posts_stats s USING (post_id)
            WHERE b.post_id = $1
            """,
            int(post_id),
        )
    return dict(row) if row else None


@require_db_pool
async def set_post_checked(post_id: int, checked: bool, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, checked, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET checked    = EXCLUDED.checked,
                                                checked_by = EXCLUDED.checked_by,
                                                checked_at = EXCLUDED.checked_at
            """,
            int(post_id), bool(checked), int(admin_id),
        )


@require_db_pool
async def set_post_manual_note(post_id: int, note: str | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, manual_note, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET manual_note = EXCLUDED.manual_note,
                                                checked_by  = EXCLUDED.checked_by,
                                                checked_at  = EXCLUDED.checked_at
            """,
            int(post_id), note, int(admin_id),
        )


@require_db_pool
async def set_post_manual_field(post_id: int, field: str, value: int | None, admin_id: int) -> None:
    """
    field: winner|max|valid|total
    value: int or None (очистить)
    """
    allowed = {
        "winner": "manual_winner_id",
        "max": "manual_max_bid",
        "valid": "manual_valid_bids",
        "total": "manual_total_bids",
    }
    col = allowed.get(field)
    if not col:
        raise ValueError(f"Unknown field: {field}")

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.auction_posts_stats(post_id, {col}, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET
                {col} = EXCLUDED.{col},
                checked_by = EXCLUDED.checked_by,
                checked_at = EXCLUDED.checked_at
            """,
            int(post_id),
            value,
            int(admin_id),
        )


@require_db_pool
async def set_post_excluded(post_id: int, excluded: bool, admin_id: int, reason: str | None = None) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.auction_posts_stats(post_id, excluded, excluded_by, excluded_at, excluded_reason)
            VALUES ($1, $2, $3, NOW(), $4)
            ON CONFLICT (post_id) DO UPDATE SET excluded        = EXCLUDED.excluded,
                                                excluded_by     = EXCLUDED.excluded_by,
                                                excluded_at     = EXCLUDED.excluded_at,
                                                excluded_reason = EXCLUDED.excluded_reason
            """,
            int(post_id), bool(excluded), int(admin_id), reason,
        )


_FIELD_LABELS = {
    "ordinal": "Порядковый номер",
    "date": "Дата (ДД.ММ.ГГГГ)",
    "time": "Время выхода (ЧЧ:ММ или ЧЧ:ММ:СС)",
    "deck": "Номер колоды",
    "card": "Название карты (текст)",
    "bidders": "Кол-во участников ставок (число людей)",
    "min": "Минимальная ставка (число)",
    "max": "Максимальная ставка (число)",
    "owner": "Хозяин карты (user_id)",
    "winner": "Победитель (user_id)",
    "link": "Ссылка на аукцион (текст)",
}


@require_db_pool
async def set_post_stat_value(post_id: int, field: str, value, admin_id: int) -> None:
    """
    value может быть: int | str | date | time | None
    field - ключ из allowed
    """
    allowed = {
        # INT
        "ordinal": ("ordinal_no", "any"),
        "deck": ("deck_no", "any"),
        "bidders": ("bidders_count", "any"),
        "min": ("min_bid", "any"),
        "owner": ("owner_id", "any"),
        "winner": ("manual_winner_id", "any"),  # уже было
        "max": ("manual_max_bid", "any"),  # уже было

        # DATE/TIME
        "date": ("manual_date", "any"),
        "time": ("manual_time", "any"),

        # TEXT
        "card": ("card_title", "any"),
        "link": ("manual_link", "any"),
    }

    col = allowed.get(field, (None, None))[0]
    if not col:
        raise ValueError(f"Unknown field: {field}")

    async with db_pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.auction_posts_stats(post_id, {col}, checked_by, checked_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (post_id) DO UPDATE SET
                {col} = EXCLUDED.{col},
                checked_by = EXCLUDED.checked_by,
                checked_at = EXCLUDED.checked_at
            """,
            int(post_id),
            value,
            int(admin_id),
        )


__all__ = [
    "get_post_months",
    "get_post_days",
    "get_posts_for_day",
    "count_posts_for_day",
    "get_post_details",
    "set_post_checked",
    "set_post_manual_note",
    "set_post_manual_field",
    "set_post_excluded",
    "set_post_stat_value",
    "_FIELD_LABELS",
]
