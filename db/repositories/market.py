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

"""Market persistence functions.

Extracted from the historical ``db.db`` god module during phase 10.
"""

__all__ = [
    'market_count_active',
    'market_create_listing',
    'market_add_items',
    'market_get_listing',
    'market_my_listings',
    'market_my_listing_counts',
    'market_set_status',
    'market_add_rate_tiers',
    'market_get_rate_tiers',
    'market_set_offer_kind',
    'market_toggle_actual',
    'market_bump',
    'market_bind_channel_message',
    'market_get_item_qty',
    'market_set_item_qty',
    'market_dec_item_qty',
    '_has_column',
    '_json_to_str_map',
    '_get_listing_core',
    'market_add_listing_item',
]

@require_db_pool
async def market_count_active(user_id: int, include_hidden: bool = False) -> int:
    async with db_pool.acquire() as conn:
        if include_hidden:
            q = "SELECT COUNT(*) FROM market_listings WHERE seller_id=$1 AND status IN ('active','hidden')"
        else:
            q = "SELECT COUNT(*) FROM market_listings WHERE seller_id=$1 AND status='active'"
        return int(await conn.fetchval(q, user_id) or 0)

@require_db_pool
async def market_create_listing(
        seller_id: int,
        currency_type: str,
        price_num: float,
        cash_code: Optional[str],
        description: Optional[str],
) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO market_listings (seller_id, currency_type, price_num, cash_code, description)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING listing_id
            """,
            seller_id, currency_type, price_num, cash_code, description,
        )
        return int(row["listing_id"])

@require_db_pool
async def market_add_items(listing_id: int, card_ids: Iterable[int]) -> int:
    async with db_pool.acquire() as conn:
        total = 0
        for cid in card_ids:
            try:
                await conn.execute(
                    "INSERT INTO market_listing_items (listing_id, card_id) VALUES ($1, $2)",
                    listing_id, int(cid),
                )
                total += 1
            except Exception:
                continue
        return total

@require_db_pool
async def market_get_listing(listing_id: int) -> Optional[dict]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM market_listings WHERE listing_id=$1", listing_id)
        return dict(row) if row else None

@require_db_pool
async def market_my_listings(user_id: int) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ml.listing_id, ml.status, ml.description, ml.cover_file_id
            FROM market_listings ml
            WHERE ml.seller_id = $1
              AND ml.status = ANY ($2::listing_status[])
              AND EXISTS (SELECT 1
                          FROM market_listing_items mli
                          WHERE mli.listing_id = ml.listing_id)
            ORDER BY ml.updated_at DESC
            """,
            user_id,
            ['active', 'hidden', 'sold', 'archived'],
        )
        return [dict(r) for r in rows]

@require_db_pool
async def market_my_listing_counts(user_id: int) -> dict[str, int]:
    allowed = ['active', 'hidden', 'sold', 'archived']
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH base(status) AS (SELECT ml.status
                                  FROM public.market_listings ml
                                  WHERE ml.seller_id = $1
                                    AND ml.status = ANY ($2::listing_status[])
                                    AND EXISTS (SELECT 1
                                                FROM public.market_listing_items mli
                                                WHERE mli.listing_id = ml.listing_id))
            SELECT COUNT(*)                                                    AS all,
                   COUNT(*) FILTER (WHERE status = 'active'::listing_status)   AS active,
                   COUNT(*) FILTER (WHERE status = 'hidden'::listing_status)   AS hidden,
                   COUNT(*) FILTER (WHERE status = 'sold'::listing_status)     AS sold,
                   COUNT(*) FILTER (WHERE status = 'archived'::listing_status) AS archived
            FROM base
            """,
            user_id, allowed,
        )
        d = {
            "all": int(row["all"] or 0),
            "active": int(row["active"] or 0),
            "hidden": int(row["hidden"] or 0),
            "sold": int(row["sold"] or 0),
            "archived": int(row["archived"] or 0),
        }
        d["all"] = d["active"] + d["hidden"] + d["sold"] + d["archived"]
        return d

@require_db_pool
async def market_set_status(listing_id: int, status: str) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET status=$2 WHERE listing_id=$1",
            listing_id, status,
        )

@require_db_pool
async def market_add_rate_tiers(listing_id: int, tiers: list[dict]) -> int:
    async with db_pool.acquire() as conn:
        total = 0
        for t in tiers:
            await conn.execute(
                """
                INSERT INTO market_rate_tiers(listing_id, label, qty, pay_type, cash_code, price, sort_order)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                listing_id, t.get("label"), t.get("qty"), t["pay_type"],
                t.get("cash_code"), t["price"], int(t.get("sort_order") or 0),
            )
            total += 1
        return total

@require_db_pool
async def market_get_rate_tiers(listing_id: int) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM market_rate_tiers WHERE listing_id=$1 ORDER BY sort_order, id",
            listing_id,
        )
        return [dict(r) for r in rows]

@require_db_pool
async def market_set_offer_kind(listing_id: int, offer_kind: str) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET offer_kind=$2 WHERE listing_id=$1",
            listing_id, offer_kind,
        )

@require_db_pool
async def market_toggle_actual(listing_id: int) -> str:
    async with db_pool.acquire() as conn:
        st = await conn.fetchval("SELECT status FROM market_listings WHERE listing_id=$1", listing_id)
        new_st = "hidden" if st == "active" else "active"
        await conn.execute("UPDATE market_listings SET status=$2 WHERE listing_id=$1", listing_id, new_st)
        return new_st

@require_db_pool
async def market_bump(listing_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE market_listings SET updated_at=now() WHERE listing_id=$1", listing_id)

@require_db_pool
async def market_bind_channel_message(listing_id: int, channel_id: int, message_id: int) -> None:
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE market_listings SET channel_id=$2, message_id=$3 WHERE listing_id=$1",
            listing_id, channel_id, message_id,
        )

async def market_get_item_qty(listing_id: int) -> int:
    sql = """
          SELECT quantity
          FROM market_listing_items
          WHERE listing_id = $1
          LIMIT 1 \
          """
    row = await db_pool.fetchrow(sql, listing_id)
    return int(row["quantity"]) if row else 0

@require_db_pool
async def market_set_item_qty(listing_id: int, qty: int) -> None:
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            if qty <= 0:
                # qty == 0 в таблице запрещён → просто удаляем позицию
                await conn.execute(
                    "DELETE FROM market_listing_items WHERE listing_id=$1",
                    listing_id,
                )
            else:
                await conn.execute(
                    "UPDATE market_listing_items SET quantity=$2 WHERE listing_id=$1",
                    listing_id, int(qty),
                )

@require_db_pool
async def market_dec_item_qty(listing_id: int, dec: int) -> int:
    """Атомарно уменьшает остаток. Если уходит в ноль — удаляет строку и возвращает 0."""
    dec = max(1, int(dec))
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT quantity FROM market_listing_items WHERE listing_id=$1 FOR UPDATE",
                listing_id,
            )
            if not row:
                return 0
            cur = int(row["quantity"] or 0)
            new = cur - dec
            if new > 0:
                row2 = await conn.fetchrow(
                    "UPDATE market_listing_items SET quantity=$2 WHERE listing_id=$1 RETURNING quantity",
                    listing_id, new,
                )
                return int(row2["quantity"]) if row2 else 0
            else:
                # qty <= 0 — строку убираем, никакого 0 в колонке с CHECK>0
                await conn.execute(
                    "DELETE FROM market_listing_items WHERE listing_id=$1",
                    listing_id,
                )
                return 0

@require_db_pool
async def _has_column(conn, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        """
        select exists(select 1
                      from information_schema.columns
                      where table_schema = 'public'
                        and table_name = $1
                        and column_name = $2) as has
        """,
        table, column
    )
    return bool(row and row["has"])

def _json_to_str_map(x) -> dict[str, str]:
    """
    Превращает что угодно в словарь {str(card_id): str(file_id)}.
    Поддерживает: dict, JSON-строку, список пар, список объектов.
    """
    if not x:
        return {}
    if isinstance(x, dict):
        return {str(k): str(v) for k, v in x.items() if v is not None}
    if isinstance(x, str):
        try:
            j = json.loads(x)
        except Exception:
            return {}
        return _json_to_str_map(j)
    if isinstance(x, (list, tuple)):
        out: dict[str, str] = {}
        for item in x:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                k, v = item
            elif isinstance(item, dict):
                k = item.get("card_id") or item.get("id") or item.get("key")
                v = item.get("proof_file_id") or item.get("value") or item.get("file_id")
            else:
                continue
            if k is not None and v is not None:
                out[str(k)] = str(v)
        return out
    # остальное нам не интересно
    return {}

@require_db_pool
async def _get_listing_core(listing_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        # какие колонки реально есть
        has_ml_proof_one = await _has_column(conn, "market_listings", "proof_file_id")
        has_ml_proof_map = await _has_column(conn, "market_listings", "proof_by_card")
        has_cover = await _has_column(conn, "market_listings", "cover_file_id")
        has_item_proof = await _has_column(conn, "market_listing_items", "proof_file_id")

        proof_one = None
        if has_ml_proof_one:
            row = await conn.fetchrow(
                "select proof_file_id, cover_file_id from market_listings where listing_id=$1",
                listing_id
            )
            if not row:
                return None
            proof_one = row["proof_file_id"] or row["cover_file_id"]
        elif has_cover:
            row = await conn.fetchrow(
                "select cover_file_id from market_listings where listing_id=$1",
                listing_id
            )
            if not row:
                return None
            proof_one = row["cover_file_id"]

        result: dict = {"listing_id": listing_id, "proof_file_id": proof_one, "proof_by_card": {}}

        # если есть JSONB в listings — используем его
        if has_ml_proof_map:
            row2 = await conn.fetchrow(
                "select proof_by_card from market_listings where listing_id=$1",
                listing_id
            )
            if row2 and row2["proof_by_card"]:
                result["proof_by_card"] = _json_to_str_map(row2["proof_by_card"])
                return result

        # иначе соберём по item-строкам
        if has_item_proof:
            rows = await conn.fetch(
                "select card_id, proof_file_id from market_listing_items where listing_id=$1 and proof_file_id is not null",
                listing_id
            )
            if rows:
                result["proof_by_card"] = {str(r["card_id"]): str(r["proof_file_id"]) for r in rows if
                                           r["proof_file_id"]}
        return result

async def market_add_listing_item(listing_id: int, card_id: int, qty: int = 1, proof_file_id: str | None = None):
    await execute(
        """
        INSERT INTO public.market_listing_items (listing_id, card_id, quantity, proof_file_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (listing_id, card_id) DO UPDATE
            SET quantity      = EXCLUDED.quantity,
                proof_file_id = COALESCE(EXCLUDED.proof_file_id, public.market_listing_items.proof_file_id)
        """,
        listing_id, card_id, max(1, qty), proof_file_id
    )

