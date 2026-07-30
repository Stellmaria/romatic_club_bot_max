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
)

"""Cards persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'add_card_db_func',
    'get_cards_by_deck',
    'get_card_by_id',
    'get_all_cards',
    'search_cards',
    'delete_card_by_id',
    'get_all_decks',
    'get_card_by_num',
    'add_card',
    'add_deck',
    'get_cards_by_deck_id',
    '_norm_deck_type',
    '_norm_obtain_type',
    'update_deck_type',
    '_pg_column_exists',
    '_pg_table_exists',
    '_audit_created_col',
    '_get_card_basic',
    '_update_card_image',
    'get_deck',
    'set_deck_type',
    'get_card',
    'set_card_obtain',
    'norm_deck_type',
    'norm_obtain_type',
    'get_card_full_by_id',
    'find_card_by_name_hero',
    'get_cards_meta_bulk',
    'get_obtain_variants_for_rarity',
    'get_deck_obtain_totals',
    'get_max_obtain_for_rarity',
    'get_deck_treasure_sum',
    'get_deck_by_id',
    'set_card_video_by_id',
    'get_cards_by_ids',
    'get_cards_ids_by_deck',
]

RU2DECK = {
    "рулеточная": "roulette",
    "ресурсная": "resource",
}

RU2OBTAIN = {
    "алмазы": "diamonds",
    "алмаз": "diamonds",
    "чай": "tea",
    "чашки": "tea",
    "чашка": "tea",
}

@require_db_pool
async def add_card_db_func(deck_id, card_name, num, hero_name, image_id, rarity, story, quote):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cards (deck_id, card_name, num, hero_name, image_id, rarity, story, quote)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                deck_id, card_name, num, hero_name, image_id, rarity, story, quote
            )
    except Exception as e:
        logger.error(f"Ошибка добавления карты: {e}")

@require_db_pool
async def get_cards_by_deck(deck_id: int):
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM cards WHERE deck_id = $1 ORDER BY num", deck_id)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения карт по deck_id={deck_id}: {e}")
        return []

@require_db_pool
async def get_card_by_id(card_id: int):
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM cards WHERE card_id = $1", card_id)
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Ошибка получения карты по card_id={card_id}: {e}")
        return None

@require_db_pool
async def get_all_cards():
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM cards ORDER BY deck_id, num")
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения всех карт: {e}")
        return []

@require_db_pool
async def search_cards(query: str):
    try:
        async with db_pool.acquire() as conn:
            pattern = f"%{query}%"
            rows = await conn.fetch("""
                                    SELECT *
                                    FROM cards
                                    WHERE hero_name ILIKE $1
                                       OR story ILIKE $1
                                       OR quote ILIKE $1
                                    ORDER BY deck_id, num
                                    """, pattern)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка поиска карт: {e}")
        return []

@require_db_pool
async def delete_card_by_id(card_id: int):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM cards WHERE card_id = $1", card_id)
    except Exception as e:
        logger.error(f"Ошибка удаления карты {card_id}: {e}")

async def get_all_decks() -> list[dict]:
    rows = await fetch(
        """
        SELECT id   AS deck_id,
               name AS deck_name,
               deck_type
        FROM public.decks
        ORDER BY id ASC
        """
    )
    return [dict(r) for r in rows]

async def get_card_by_num(num: int):
    row = await fetchrow("SELECT * FROM cards WHERE num = $1", num)
    return row

async def add_card(
        card_name: str,
        num: int,
        hero_name: str,
        image_id: str,
        rarity: str,
        deck_id: int,
        story: str,
        quote: str | None,
        *,
        gift_cups: int = 0,
        gift_diamonds: int = 0,
        obtain_type: str | None = None,  # 'diamonds' | 'tea' (enum obtain_type)
        obtain_amount: int | None = None
) -> None:
    # нормализация значений
    gift_cups = max(0, int(gift_cups or 0))
    gift_diamonds = max(0, int(gift_diamonds or 0))
    if obtain_amount is not None:
        obtain_amount = max(0, int(obtain_amount))

    # базовые колонки обязательные всегда
    columns = ["card_name", "num", "hero_name", "image_id", "rarity", "deck_id", "story", "quote"]
    values = [card_name, num, hero_name, image_id, rarity, deck_id, story, quote]

    if obtain_type is not None:
        columns.append("obtain_type");
        values.append(obtain_type)
    if obtain_amount is not None:
        columns.append("obtain_amount");
        values.append(obtain_amount)

    # плейсхолдеры $1..$N под asyncpg
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    sql = f"""
        INSERT INTO cards ({", ".join(columns)})
        VALUES ({placeholders})
    """

    await execute(sql, *values)

@require_db_pool
async def add_deck(deck_name: str) -> bool:
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO decks (name) VALUES ($1)", deck_name)
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления колоды: {e}")
        return False

@require_db_pool
async def get_cards_by_deck_id(deck_id: int):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id, card_name, hero_name, num, image_id, rarity, story, quote FROM cards WHERE deck_id = $1 ORDER BY num",
            deck_id
        )
        return [dict(row) for row in rows]

def _norm_deck_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2DECK.get(v, v)

def _norm_obtain_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2OBTAIN.get(v, v)

@require_db_pool
async def update_deck_type(deck_id: int, deck_type: str) -> None:
    deck_type = _norm_deck_type(deck_type)
    if deck_type not in {"roulette", "resource"}:
        raise ValueError("deck_type must be 'roulette' or 'resource'")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE decks SET deck_type = $2 WHERE id = $1",
            deck_id, deck_type
        )

@require_db_pool
async def _pg_column_exists(table: str, column: str) -> bool:
    async with db_pool.acquire() as conn:
        return bool(await conn.fetchval(
            """
            select 1
            from information_schema.columns
            where table_schema = 'public'
              and table_name = $1
              and column_name = $2
            """,
            table, column
        ))

@require_db_pool
async def _pg_table_exists(table: str) -> bool:
    async with db_pool.acquire() as conn:
        # to_regclass вернёт NULL, если таблицы нет
        return bool(await conn.fetchval("select to_regclass($1)", f"public.{table}"))

@require_db_pool
async def _audit_created_col() -> str:
    if await _pg_column_exists("audit_logs", "created_at"):
        return "created_at"
    return '"timestamp"'

async def _get_card_basic(card_id: int) -> Optional[dict]:
    row = await fetchrow(
        """
        SELECT card_id, card_name, image_id
        FROM cards
        WHERE card_id = $1
        """,
        card_id,
    )
    return dict(row) if row else None

async def _update_card_image(card_id: int, file_id: str) -> Optional[dict]:
    row = await fetchrow(
        """
        UPDATE cards
        SET image_id=$2
        WHERE card_id = $1
        RETURNING card_id, card_name, image_id
        """,
        card_id,
        file_id,
    )
    return dict(row) if row else None

async def get_deck(deck_id: int) -> Optional[Dict[str, Any]]:
    row = await fetchrow("SELECT id, name, deck_type FROM decks WHERE id=$1", deck_id)
    return dict(row) if row else None

async def set_deck_type(deck_id: int, deck_type: str) -> Tuple[Optional[str], str]:
    deck_type = norm_deck_type(deck_type)
    if deck_type not in {"roulette", "resource"}:
        raise ValueError("deck_type must be roulette|resource")
    before = await fetchrow("SELECT deck_type FROM decks WHERE id=$1", deck_id)
    await execute("UPDATE decks SET deck_type=$2 WHERE id=$1", deck_id, deck_type)
    return (before["deck_type"] if before else None, deck_type)

async def get_card(card_id: int) -> Optional[Dict[str, Any]]:
    row = await fetchrow("""
                         SELECT card_id, card_name, obtain_type, obtain_amount
                         FROM cards
                         WHERE card_id = $1
                         """, card_id)
    return dict(row) if row else None

async def set_card_obtain(card_id: int, obtain_type: str, amount: int) -> Tuple[Tuple[str, int], Tuple[str, int]]:
    ot = norm_obtain_type(obtain_type)
    if ot not in {"diamonds", "tea"}:
        raise ValueError("obtain_type must be diamonds|tea")
    amount = int(amount)
    if amount < 0:
        raise ValueError("amount must be >= 0")
    before = await fetchrow("SELECT obtain_type, obtain_amount FROM cards WHERE card_id=$1", card_id)
    await execute("UPDATE cards SET obtain_type=$2, obtain_amount=$3 WHERE card_id=$1", card_id, ot, amount)
    b = (str(before["obtain_type"]), int(before["obtain_amount"])) if before else ("diamonds", 0)
    return b, (ot, amount)

def norm_deck_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2DECK.get(v, v)

def norm_obtain_type(value: str) -> str:
    v = (value or "").strip().lower()
    return RU2OBTAIN.get(v, v)

async def get_card_full_by_id(card_id: int) -> Optional[Dict[str, Any]]:
    sql = """
          SELECT c.*, d.name AS deck_name
          FROM cards c
                   LEFT JOIN decks d ON d.id = c.deck_id
          WHERE c.card_id = $1
          LIMIT 1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, card_id)
    return dict(row) if row else None

async def find_card_by_name_hero(card_name: str, hero_name: str) -> Optional[Dict[str, Any]]:
    sql = """
          SELECT c.*, d.name AS deck_name
          FROM cards c
                   LEFT JOIN decks d ON d.id = c.deck_id
          WHERE lower(c.card_name) = lower($1)
            AND lower(c.hero_name) = lower($2)
          LIMIT 1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, card_name, hero_name)
    return dict(row) if row else None

@require_db_pool
async def get_cards_meta_bulk(card_ids: Iterable[int]) -> Dict[int, dict]:
    """
    Возвращает мета-инфу по картам пачкой:
      hero_name, deck_id, rarity, gifts_cups/gifts_diamonds.
    gifts_* считаются из obtain_type/obtain_amount.
    """
    ids = [int(x) for x in set(card_ids) if x is not None]
    if not ids:
        return {}

    sql = """
          SELECT c.card_id,
                 c.hero_name,
                 c.deck_id,
                 c.rarity,
                 CASE
                     WHEN c.obtain_type::text IN ('tea', 'cups', 'cup') THEN c.obtain_amount
                     ELSE 0
                     END AS gifts_cups,
                 CASE
                     WHEN c.obtain_type::text IN ('diamonds', 'diamond') THEN c.obtain_amount
                     ELSE 0
                     END AS gifts_diamonds
          FROM cards c
          WHERE c.card_id = ANY ($1::int[]) \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, ids)

    out: Dict[int, dict] = {}
    for r in rows:
        out[int(r["card_id"])] = {
            "hero_name": r["hero_name"],
            "deck_id": r["deck_id"],
            "rarity": r["rarity"],
            "gifts_cups": int(r["gifts_cups"] or 0),
            "gifts_diamonds": int(r["gifts_diamonds"] or 0),
        }
    return out

@require_db_pool
async def get_obtain_variants_for_rarity(rarity_norm: str | None) -> set[str]:
    """
    Возвращает множество {'diamonds','cups'} для заданной редкости
    (учитываем рус/англ синонимы). Если rarity_norm=None — по всем картам.
    """
    rar_aliases = {
        "bronze": ["bronze", "бронза", "бронзовая"],
        "silver": ["silver", "серебро", "серебряная"],
        "gold": ["gold", "золото", "золотая"],
        "diamond": ["diamond", "diamonds", "алмаз", "алмазы", "алмазная"],
    }

    where = ""
    params: list = []
    if rarity_norm:
        arr = [x.lower() for x in rar_aliases.get(rarity_norm, [rarity_norm])]
        where = "WHERE lower(c.rarity) = ANY($1::text[])"
        params = [arr]

    sql = f"""
        SELECT DISTINCT
               CASE
                   WHEN lower(c.obtain_type::text) IN ('diamonds','diamond','алмазы','алмаз') THEN 'diamonds'
                   WHEN lower(c.obtain_type::text) IN ('tea','cups','cup','чай','чашки','чашка') THEN 'cups'
                   ELSE NULL
               END AS t
        FROM cards c
        {where}
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    out: set[str] = set()
    for r in rows:
        t = r["t"]
        if t:
            out.add(t)
    return out

@require_db_pool
async def get_deck_obtain_totals(deck_id: int) -> dict:
    """
    Возвращает {'diamonds': X, 'cups': Y} по всей колоде.
    """
    sql = """
          SELECT COALESCE(SUM(CASE
                                  WHEN lower(c.obtain_type::text) IN ('diamonds', 'diamond', 'алмазы', 'алмаз')
                                      THEN c.obtain_amount
                                  ELSE 0 END), 0) AS diamonds,
                 COALESCE(SUM(CASE
                                  WHEN lower(c.obtain_type::text) IN ('tea', 'cups', 'cup', 'чай', 'чашки', 'чашка')
                                      THEN c.obtain_amount
                                  ELSE 0 END), 0) AS cups
          FROM cards c
          WHERE c.deck_id = $1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, deck_id)

    return {
        "diamonds": int(row["diamonds"] or 0) if row else 0,
        "cups": int(row["cups"] or 0) if row else 0,
    }

@require_db_pool
async def get_max_obtain_for_rarity(rarity_norm: str | None) -> dict:
    """
    Возвращает максимальные значения по типам для редкости:
    {'cups': X, 'diamonds': Y}
    rarity_norm: 'bronze'|'silver'|'gold'|'diamond' (или None — тогда по всем).
    """
    rar_aliases = {
        "bronze": ["bronze", "бронза", "бронзовая"],
        "silver": ["silver", "серебро", "серебряная"],
        "gold": ["gold", "золото", "золотая"],
        "diamond": ["diamond", "diamonds", "алмаз", "алмазы", "алмазная"],
    }
    where = ""
    params: list = []
    if rarity_norm:
        arr = [x.lower() for x in rar_aliases.get(rarity_norm, [rarity_norm])]
        where = "WHERE lower(c.rarity) = ANY($1::text[])"
        params = [arr]

    sql = f"""
        SELECT
            COALESCE(MAX(CASE WHEN lower(c.obtain_type::text) IN ('tea','cups','cup','чай','чашки','чашка')
                              THEN c.obtain_amount END), 0) AS cups_max,
            COALESCE(MAX(CASE WHEN lower(c.obtain_type::text) IN ('diamonds','diamond','алмазы','алмаз')
                              THEN c.obtain_amount END), 0) AS diamonds_max
        FROM cards c
        {where}
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)

    return {
        "cups": int(row["cups_max"] or 0) if row else 0,
        "diamonds": int(row["diamonds_max"] or 0) if row else 0,
    }

@require_db_pool
async def get_deck_treasure_sum(deck_id: int) -> int:
    """
    Возвращает сумму сокровищ по колоде согласно мапе редкостей:
    bronze=10, silver=20, gold=40, diamond=60.
    """
    sql = """
          SELECT COALESCE(SUM(
                                  CASE
                                      WHEN lower(c.rarity) IN ('diamond', 'алмаз', 'алмазы', 'алмазная') THEN 60
                                      WHEN lower(c.rarity) IN ('gold', 'золото', 'золотая') THEN 40
                                      WHEN lower(c.rarity) IN ('silver', 'серебро', 'серебряная') THEN 20
                                      WHEN lower(c.rarity) IN ('bronze', 'бронза', 'бронзовая') THEN 10
                                      ELSE 0
                                      END
                          ), 0) AS t_sum
          FROM cards c
          WHERE c.deck_id = $1 \
          """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(sql, deck_id)
    return int(row["t_sum"] or 0) if row else 0

async def get_deck_by_id(deck_id: int):
    return await get_deck(deck_id)

@require_db_pool
async def set_card_video_by_id(
        card_id: int,
        video_file_id: str,
        *,
        unique_id: Optional[str] = None,
        thumb_file_id: Optional[str] = None,
) -> dict:
    """
    Ставит видео на карту:
      - cards.image_id = video_file_id
      - cards.media_type='video' (если колонка есть)

    И пытается обновить auctions.image_id для лотов этой карты
    (по hero_name + card_name; плюс fallback, если hero_name в auctions пустой).

    Важно: auctions может падать на CHECK по времени (NOT VALID, но enforced on UPDATE).
    Поэтому:
      - сначала пробуем апдейт с допуском <=2 сек (если ты поменял CHECK на допуск)
      - если упали на CheckViolationError, повторяем апдейт только для строго валидных строк
        (end_time = start_time + 31 minutes), чтобы не падать.
    """
    video_file_id = (video_file_id or "").strip()
    if not video_file_id:
        return {"ok": False, "reason": "empty_file_id"}

    async with db_pool.acquire() as conn:
        card = await conn.fetchrow(
            "SELECT card_id, hero_name, card_name FROM public.cards WHERE card_id=$1",
            int(card_id),
        )
        if not card:
            return {"ok": False, "reason": "card_not_found"}

        hero_name = (card["hero_name"] or "").strip()
        card_name = (card["card_name"] or "").strip()

        has_media_type = await _has_column(conn, "cards", "media_type")

        # 1) обновляем карту
        if has_media_type:
            res_card = await conn.execute(
                "UPDATE public.cards SET image_id=$1, media_type='video' WHERE card_id=$2",
                video_file_id,
                int(card_id),
            )
        else:
            res_card = await conn.execute(
                "UPDATE public.cards SET image_id=$1 WHERE card_id=$2",
                video_file_id,
                int(card_id),
            )

        card_updated = int(res_card.split()[-1]) if res_card else 0

        # 2) обновляем auctions.image_id (аккуратно)
        auctions_updated_strict = 0
        auctions_updated_fallback = 0
        auctions_error = None

        async def _update_auctions_strict(where_time_sql: str) -> int:
            res = await conn.execute(
                f"""
                UPDATE public.auctions
                   SET image_id = $1
                 WHERE lower(trim(card_name)) = lower(trim($2))
                   AND lower(trim(coalesce(hero_name,''))) = lower(trim(coalesce($3,'')))
                   {where_time_sql}
                """,
                video_file_id,
                card_name,
                hero_name,
            )
            return int(res.split()[-1]) if res else 0

        async def _update_auctions_fallback(where_time_sql: str) -> int:
            # fallback только если card_name уникален среди cards
            cnt_same_name = await conn.fetchval(
                "SELECT COUNT(*) FROM public.cards WHERE lower(trim(card_name)) = lower(trim($1))",
                card_name,
            )
            if int(cnt_same_name or 0) != 1:
                return 0

            res = await conn.execute(
                f"""
                UPDATE public.auctions
                   SET image_id = $1
                 WHERE lower(trim(card_name)) = lower(trim($2))
                   AND (hero_name IS NULL OR trim(hero_name) = '')
                   {where_time_sql}
                """,
                video_file_id,
                card_name,
            )
            return int(res.split()[-1]) if res else 0

        # Пытаемся “мягко” (если у тебя CHECK уже с допуском)
        try:
            where_time_soft = """
              AND abs(extract(epoch from (end_time - (start_time + interval '31 minutes')))) <= 2
            """
            auctions_updated_strict = await _update_auctions_strict(where_time_soft)
            auctions_updated_fallback = await _update_auctions_fallback(where_time_soft)

        except asyncpg.exceptions.CheckViolationError as e:
            # CHECK строгий (или строки грязные) -> повторяем только на строго валидных строках
            auctions_error = str(e).splitlines()[0]

            try:
                where_time_strict = " AND end_time = (start_time + interval '31 minutes') "
                auctions_updated_strict = await _update_auctions_strict(where_time_strict)
                auctions_updated_fallback = await _update_auctions_fallback(where_time_strict)
            except Exception as e2:
                auctions_error = auctions_error or str(e2).splitlines()[0]

        return {
            "ok": True,
            "card_id": int(card_id),
            "hero_name": hero_name,
            "card_name": card_name,
            "card_updated": card_updated,
            "auctions_updated_strict": auctions_updated_strict,
            "auctions_updated_fallback": auctions_updated_fallback,
            "auctions_error": auctions_error,
            "has_media_type": has_media_type,
            "unique_id": unique_id,
            "thumb_file_id": thumb_file_id,
        }

@require_db_pool
async def get_cards_by_ids(card_ids: list[int]) -> list[dict]:
    if not card_ids:
        return []

    ids = [int(x) for x in card_ids if x is not None]
    if not ids:
        return []

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT card_id,
                   hero_name,
                   card_name,
                   rarity,
                   deck_id,
                   image_id,
                   story,
                   quote,
                   obtain_type,
                   obtain_amount,
                   media_type,
                   media_file_id,
                   media_unique_id,
                   thumb_file_id
            FROM public.cards
            WHERE card_id = ANY ($1::int[])
            """,
            ids,
        )

    m: dict[int, dict] = {}
    for r in rows:
        d = dict(r)

        # ✅ совместимость со старым названием полей
        d["gift_currency"] = d.get("obtain_type")
        d["gift_value"] = d.get("obtain_amount")

        # ✅ если где-то используешь image_id как “медиа”, подстрахуемся
        if not (d.get("image_id") or "").strip():
            mf = (d.get("media_file_id") or "").strip()
            if mf:
                d["image_id"] = mf

        m[int(d["card_id"])] = d

    return [m[cid] for cid in ids if cid in m]

@require_db_pool
async def get_cards_ids_by_deck(deck_id: int) -> list[int]:
    deck_id = int(deck_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id FROM public.cards WHERE deck_id=$1 ORDER BY card_id",
            deck_id,
        )
    return [int(r["card_id"]) for r in rows]

