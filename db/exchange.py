"""Exchange inventory, batches and manual-result queries.

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
async def count_exchange_cards_for_deck(deck_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM public.cards WHERE deck_id=$1",
            int(deck_id),
        )
        return int(row["cnt"])


@require_db_pool
async def get_exchange_cards_for_deck(
        deck_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Карты колоды для экранов биржи (пагинация)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.card_id,
                   c.card_name,
                   c.hero_name,
                   c.rarity,
                   c.obtain_type,
                   c.obtain_amount
            FROM public.cards c
            WHERE c.deck_id = $1
            ORDER BY c.card_id
            LIMIT $2 OFFSET $3
            """,
            int(deck_id),
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def create_exchange_items(batch_id: int, card_ids: list[int]) -> int:
    """
    Добавляет cards в exchange_items для batch_id.
    Возвращает сколько строк вставили.
    """
    if not card_ids:
        return 0

    ids = [int(x) for x in card_ids]

    async with db_pool.acquire() as conn:
        res = await conn.execute(
            """
            INSERT INTO public.exchange_items (batch_id, card_id)
            SELECT $1, x
            FROM UNNEST($2::int[]) AS t(x)
            """,
            int(batch_id),
            ids,
        )
    # res типа: "INSERT 0 N"
    try:
        return int(res.split()[-1])
    except Exception:
        return len(ids)


@require_db_pool
async def get_exchange_batches_for_card(
        card_id: int,
        status: str = "approved",
) -> list[dict[str, Any]]:
    """
    Возвращает список заявок (batch_id), в которых есть card_id:
    batch_id, user_id, username, qty
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT eb.batch_id,
                   eb.user_id,
                   u.username    AS username,
                   COUNT(*)::int AS qty
            FROM public.exchange_items ei
                     JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                     LEFT JOIN public.users u ON u.user_id = eb.user_id
            WHERE ei.card_id = $1
              AND eb.status = $2
            GROUP BY eb.batch_id, eb.user_id, u.username
            ORDER BY qty DESC, eb.batch_id DESC
            """,
            int(card_id),
            status,
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "batch_id": int(r["batch_id"]),
                "user_id": int(r["user_id"]),
                "username": (r["username"] or "").strip() or None,
                "qty": int(r["qty"]),
            }
        )
    return out


@require_db_pool
async def add_exchange_items_for_deck(
        *,
        batch_id: int,
        deck_id: int,
) -> int:
    """Добавляет все карты колоды в exchange_items. Возвращает количество добавленных."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_id, card_name, hero_name FROM public.cards WHERE deck_id=$1 ORDER BY card_id",
            int(deck_id),
        )
        if not rows:
            return 0

        await conn.executemany(
            """
            INSERT INTO public.exchange_items (batch_id, card_id, card_name, hero_name)
            VALUES ($1, $2, $3, $4)
            """,
            [
                (int(batch_id), int(r["card_id"]), r["card_name"], r["hero_name"])
                for r in rows
            ],
        )
        return len(rows)


@require_db_pool
async def get_pending_exchange_batches(
        *,
        limit: int = 30,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Список заявок биржи со статусом pending (для модерации)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT eb.batch_id,
                   eb.user_id,
                   u.username,
                   eb.deck_id,
                   d.name                                                                   AS deck_name,
                   eb.mode,
                   eb.currency,
                   eb.price,
                   eb.comment,
                   eb.proof_photo_id, -- ✅ ВОТ ЭТО
                   eb.created_at,
                   (SELECT COUNT(*) FROM exchange_items ei WHERE ei.batch_id = eb.batch_id) AS items_count
            FROM exchange_batches eb
                     LEFT JOIN users u ON u.user_id = eb.user_id
                     LEFT JOIN decks d ON d.id = eb.deck_id
            WHERE COALESCE(eb.status, 'pending') = 'pending'
            ORDER BY eb.created_at DESC
            LIMIT $1 OFFSET $2;

            """,
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def count_pending_exchange_batches() -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*)::int AS n FROM public.exchange_batches WHERE status='pending'"
        )
        return int(row["n"] or 0)


@require_db_pool
async def get_exchange_batch_by_id(batch_id: int) -> dict[str, Any] | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT eb.*, u.username AS username
            FROM public.exchange_batches eb
                     LEFT JOIN public.users u ON u.user_id = eb.user_id
            WHERE eb.batch_id = $1
            """,
            int(batch_id),
        )
    return dict(row) if row else None


@require_db_pool
async def get_exchange_cards_for_batch(batch_id: int) -> list[dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.card_id,
                   c.hero_name,
                   c.card_name,
                   COUNT(*)::int AS qty
            FROM public.exchange_items ei
                     JOIN public.cards c ON c.card_id = ei.card_id
            WHERE ei.batch_id = $1
            GROUP BY c.card_id, c.hero_name, c.card_name
            ORDER BY c.hero_name NULLS LAST, c.card_name
            """,
            int(batch_id),
        )
    return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_print_stats(batch_id: int) -> dict[str, Any] | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.exchange_print_stats WHERE batch_id=$1",
            int(batch_id),
        )
    return dict(row) if row else None


@require_db_pool
async def upsert_exchange_print_stats(
        batch_id: int,
        *,
        winner_id: int | None = None,
        winner_name: str | None = None,
        price: int | None = None,
        link: str | None = None,
        updated_by: int | None = None,
) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.exchange_print_stats
            (batch_id, manual_winner_id, manual_winner_name, manual_price, manual_link, updated_by, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (batch_id) DO UPDATE SET manual_winner_id   = COALESCE(EXCLUDED.manual_winner_id,
                                                                               public.exchange_print_stats.manual_winner_id),
                                                 manual_winner_name = COALESCE(EXCLUDED.manual_winner_name,
                                                                               public.exchange_print_stats.manual_winner_name),
                                                 manual_price       = COALESCE(EXCLUDED.manual_price,
                                                                               public.exchange_print_stats.manual_price),
                                                 manual_link        = COALESCE(EXCLUDED.manual_link,
                                                                               public.exchange_print_stats.manual_link),
                                                 updated_by         = COALESCE(EXCLUDED.updated_by,
                                                                               public.exchange_print_stats.updated_by),
                                                 updated_at         = now()
            """,
            int(batch_id),
            winner_id,
            (winner_name or "").strip() or None,
            price,
            (link or "").strip() or None,
            int(updated_by) if updated_by is not None else None,
        )


@require_db_pool
async def reset_exchange_print_stats(batch_id: int, *, updated_by: int | None = None) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.exchange_print_stats
            (batch_id, manual_winner_id, manual_winner_name, manual_price, manual_link, updated_by, updated_at)
            VALUES ($1, NULL, NULL, NULL, NULL, $2, now())
            ON CONFLICT (batch_id) DO UPDATE SET manual_winner_id=NULL,
                                                 manual_winner_name=NULL,
                                                 manual_price=NULL,
                                                 manual_link=NULL,
                                                 updated_by=$2,
                                                 updated_at=now()
            """,
            int(batch_id),
            int(updated_by) if updated_by is not None else None,
        )


@require_db_pool
async def get_exchange_items_by_batch_id(batch_id: int) -> List[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT item_id, batch_id, card_id, card_name, hero_name, created_at
            FROM public.exchange_items
            WHERE batch_id = $1
            ORDER BY item_id
            """,
            int(batch_id),
        )
        return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_deck_overview(*, status: str = "approved") -> List[Dict[str, Any]]:
    """Сводка: сколько карточек на бирже по колодам."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.deck_id,
                   COALESCE(d.name, ('#' || c.deck_id::text)) AS deck_name,
                   COUNT(*)::int                              AS items_count,
                   COUNT(DISTINCT eb.batch_id)::int           AS batches_count
            FROM public.exchange_batches eb
                     JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                     JOIN public.cards c ON c.card_id = ei.card_id
                     LEFT JOIN public.decks d ON d.id = c.deck_id
            WHERE COALESCE(eb.status, 'pending') = $1
            GROUP BY c.deck_id, deck_name
            ORDER BY items_count DESC, c.deck_id
            """,
            status,
        )
        return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_owners_for_cards(
        card_ids: list[int],
        status: str = "approved",
) -> dict[int, list[dict[str, Any]]]:
    """
    Возвращает по card_id список владельцев (по batches/items):
      - user_id, username, qty, batch_id
    qty = SUM(qty) если колонка есть, иначе COUNT(*)
    """
    if not card_ids:
        return {}

    ids = [int(x) for x in card_ids]
    st = (status or "approved").strip()

    async with db_pool.acquire() as conn:
        has_qty = await conn.fetchval(
            """
            SELECT EXISTS (SELECT 1
                           FROM information_schema.columns
                           WHERE table_schema = 'public'
                             AND table_name = 'exchange_items'
                             AND column_name = 'qty')
            """
        )

        if has_qty:
            rows = await conn.fetch(
                """
                SELECT ei.card_id,
                       eb.batch_id,
                       eb.user_id,
                       u.username                    AS username,
                       COALESCE(SUM(ei.qty), 0)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = ANY ($1::int[])
                GROUP BY ei.card_id, eb.batch_id, eb.user_id, u.username
                ORDER BY ei.card_id, qty DESC, username NULLS LAST
                """,
                ids, st
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ei.card_id,
                       eb.batch_id,
                       eb.user_id,
                       u.username    AS username,
                       COUNT(*)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = ANY ($1::int[])
                GROUP BY ei.card_id, eb.batch_id, eb.user_id, u.username
                ORDER BY ei.card_id, qty DESC, username NULLS LAST
                """,
                ids, st
            )

    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        cid = int(r["card_id"])
        out.setdefault(cid, []).append(
            {
                "batch_id": int(r["batch_id"]),
                "user_id": int(r["user_id"]),
                "username": (r["username"] or "").strip() or None,
                "qty": int(r["qty"]),
            }
        )
    return out


@require_db_pool
async def get_exchange_owner_batches_for_card(
        card_id: int,
        status: str = "approved",
) -> list[dict[str, Any]]:
    cid = int(card_id)

    async with db_pool.acquire() as conn:
        try:
            # noinspection SqlNoDataSourceInspection,SqlResolve
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username                    AS username,
                       COALESCE(SUM(ei.qty), 0)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = $1
                GROUP BY eb.batch_id, eb.user_id, u.username
                ORDER BY qty DESC, eb.batch_id DESC
                """,
                cid,
                status,
            )
        except asyncpg.UndefinedColumnError:
            # noinspection SqlNoDataSourceInspection,SqlResolve
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username    AS username,
                       COUNT(*)::int AS qty
                FROM public.exchange_items ei
                         JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                         LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.status = $2
                  AND ei.card_id = $1
                GROUP BY eb.batch_id, eb.user_id, u.username
                ORDER BY qty DESC, eb.batch_id DESC
                """,
                cid,
                status,
            )

    return [
        {
            "batch_id": int(r["batch_id"]),
            "user_id": int(r["user_id"]),
            "username": (r["username"] or "").strip() or None,
            "qty": int(r["qty"]),
        }
        for r in rows
    ]


@require_db_pool
async def get_exchange_inventory_cards_for_deck(
        deck_id: int,
        *,
        status: str = "approved",
        limit: int = 30,
        offset: int = 0,
) -> List[Dict[str, Any]]:
    """Карты на бирже по выбранной колоде (агрегация)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH approved AS (SELECT eb.batch_id, eb.currency, eb.price
                              FROM public.exchange_batches eb
                              WHERE COALESCE(eb.status, 'pending') = $2),
                 per_card AS (SELECT ei.card_id,
                                     COUNT(*)::int AS qty
                              FROM public.exchange_items ei
                                       JOIN approved a ON a.batch_id = ei.batch_id
                                       JOIN public.cards c ON c.card_id = ei.card_id
                              WHERE c.deck_id = $1
                              GROUP BY ei.card_id)
            SELECT c.card_id,
                   c.card_name,
                   c.hero_name,
                   c.rarity,
                   c.obtain_type,
                   c.obtain_amount,
                   p.qty
            FROM per_card p
                     JOIN public.cards c ON c.card_id = p.card_id
            ORDER BY p.qty DESC, c.card_id
            LIMIT $3 OFFSET $4
            """,
            int(deck_id),
            status,
            int(limit),
            int(offset),
        )
        return [dict(r) for r in rows]


MSK = ZoneInfo("Europe/Moscow")


@require_db_pool
async def get_print_win_missed_for_day(target_date: date) -> List[Dict[str, Any]]:
    sql = """
          WITH day_lots AS (SELECT a.auction_id,
                                   a.start_time,
                                   a.status,
                                   a.hero_name,
                                   a.card_name
                            FROM public.auctions a
                            WHERE a.start_time IS NOT NULL
                              AND (a.start_time AT TIME ZONE 'Europe/Moscow')::date = $1
                              AND a.status IN ('scheduled', 'active', 'finished', 'approved')
                            ORDER BY a.start_time, a.auction_id),
               mailed_full AS (SELECT m.auction_id
                               FROM public.auction_win_mailings m
                               GROUP BY m.auction_id
                               HAVING SUM(CASE WHEN m.target IN ('owner', 'both') THEN 1 ELSE 0 END) > 0
                                  AND SUM(CASE WHEN m.target IN ('winner', 'both') THEN 1 ELSE 0 END) > 0),
               bids_cnt AS (SELECT b.auction_id,
                                   COUNT(*)::int AS bids_count
                            FROM public.bids b
                            GROUP BY b.auction_id)
          SELECT d.auction_id,
                 d.start_time,
                 d.status,
                 d.hero_name,
                 d.card_name,
                 COALESCE(bc.bids_count, 0) AS bids_count
          FROM day_lots d
                   LEFT JOIN mailed_full mf ON mf.auction_id = d.auction_id
                   LEFT JOIN bids_cnt bc ON bc.auction_id = d.auction_id
          WHERE mf.auction_id IS NULL
          ORDER BY d.start_time, d.auction_id \
          """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(sql, target_date)
    return [dict(r) for r in rows]


@require_db_pool
async def get_exchange_batch(batch_id: int, **_: Any) -> Optional[Dict[str, Any]]:
    """Back-compat: старое имя. Возвращает batch по id."""
    return await get_exchange_batch_by_id(batch_id)


@require_db_pool
async def set_exchange_manual_winner(batch_id: int, winner_id: int | None, winner_username: str | None,
                                     admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_winner_id       = $2,
                manual_winner_username = $3,
                manual_set_by          = $4,
                manual_set_at          = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(winner_id) if winner_id is not None else None,
            (winner_username or "").strip() or None,
            int(admin_id),
        )


@require_db_pool
async def set_exchange_manual_price(batch_id: int, price: int | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_price  = $2,
                manual_set_by = $3,
                manual_set_at = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(price) if price is not None else None,
            int(admin_id),
        )


@require_db_pool
async def set_exchange_manual_link(batch_id: int, link: str | None, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_link   = $2,
                manual_set_by = $3,
                manual_set_at = NOW()
            WHERE batch_id = $1
            """,
            int(batch_id),
            (link or "").strip() or None,
            int(admin_id),
        )


@require_db_pool
async def reset_exchange_manual(batch_id: int, admin_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_winner_id       = NULL,
                manual_winner_username = NULL,
                manual_price           = NULL,
                manual_link            = NULL,
                manual_set_by          = $2,
                manual_set_at          = NOW(),
                manual_sent_at         = NULL
            WHERE batch_id = $1
            """,
            int(batch_id),
            int(admin_id),
        )


@require_db_pool
async def mark_exchange_manual_sent(batch_id: int) -> bool:
    """
    Атомарно лочит лот биржи (manual_sent_at).
    True  -> мы первые, залочили
    False -> уже кто-то залочил раньше
    """
    async with db_pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE public.exchange_batches
            SET manual_sent_at = NOW()
            WHERE batch_id = $1
              AND manual_sent_at IS NULL
            """,
            int(batch_id),
        )

    # asyncpg возвращает строку вида "UPDATE 0" или "UPDATE 1"
    try:
        cnt = int(str(res).split()[-1])
    except Exception:
        cnt = 0
    return cnt == 1


async def get_exchange_approved_cards_by_deck(deck_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT c.card_id,
               c.card_name,
               c.hero_name,
               COUNT(DISTINCT eb.batch_id)::int AS cnt
        FROM public.exchange_batches eb
                 JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                 JOIN public.cards c ON c.card_id = ei.card_id
        WHERE COALESCE(eb.status, 'pending') = 'approved'
          AND eb.deck_id = $1
          AND COALESCE(NULLIF(eb.mode, ''), 'card') IN ('card', 'deck_split')
        GROUP BY c.card_id, c.card_name, c.hero_name
        ORDER BY c.card_name ASC, c.hero_name ASC
        """,
        int(deck_id),
    )
    return [dict(r) for r in (rows or [])]


__all__ = [
    "count_exchange_cards_for_deck",
    "get_exchange_cards_for_deck",
    "create_exchange_items",
    "get_exchange_batches_for_card",
    "add_exchange_items_for_deck",
    "get_pending_exchange_batches",
    "count_pending_exchange_batches",
    "get_exchange_batch_by_id",
    "get_exchange_cards_for_batch",
    "get_exchange_print_stats",
    "upsert_exchange_print_stats",
    "reset_exchange_print_stats",
    "get_exchange_items_by_batch_id",
    "get_exchange_deck_overview",
    "get_exchange_owners_for_cards",
    "get_exchange_owner_batches_for_card",
    "get_exchange_inventory_cards_for_deck",
    "get_print_win_missed_for_day",
    "get_exchange_batch",
    "set_exchange_manual_winner",
    "set_exchange_manual_price",
    "set_exchange_manual_link",
    "reset_exchange_manual",
    "mark_exchange_manual_sent",
    "get_exchange_approved_cards_by_deck",
    "MSK",
]
