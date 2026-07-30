"""Persistence boundary for the marketplace feature.

Telegram handlers must not know how marketplace records are stored.  This
repository owns every query used by the admin marketplace flows and accepts an
already configured pool so it remains straightforward to test.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import asyncpg


def _as_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row else None


class MarketRepository:
    """PostgreSQL storage for listings, cards, prices and proofs."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create_listing(
        self,
        *,
        seller_id: int,
        currency_type: str = "cash",
        price_num: float = 0,
        cash_code: str | None = None,
        description: str | None = None,
        status: str = "active",
        offer_kind: str = "cards",
        cover_file_id: str | None = None,
        deck_id: int | None = None,
    ) -> int:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO public.market_listings (
                    seller_id, status, description, currency_type, cash_code,
                    price_num, offer_kind, cover_file_id, deck_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING listing_id
                """,
                int(seller_id),
                status,
                description,
                currency_type,
                cash_code,
                price_num,
                offer_kind,
                cover_file_id,
                deck_id,
            )
        return int(row["listing_id"])

    async def add_listing_item(
        self,
        listing_id: int,
        card_id: int,
        quantity: int = 1,
        proof_file_id: str | None = None,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO public.market_listing_items (
                    listing_id, card_id, quantity, proof_file_id
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (listing_id, card_id) DO UPDATE
                SET quantity = EXCLUDED.quantity,
                    proof_file_id = COALESCE(
                        EXCLUDED.proof_file_id,
                        public.market_listing_items.proof_file_id
                    )
                """,
                int(listing_id),
                int(card_id),
                max(1, int(quantity)),
                proof_file_id,
            )

    async def add_items(
        self,
        listing_id: int,
        items: Iterable[int | dict[str, Any]],
    ) -> int:
        total = 0
        async with self._pool.acquire() as connection:
            for item in items:
                if isinstance(item, dict):
                    card_id = item.get("card_id") or item.get("id")
                    quantity = max(1, int(item.get("quantity") or 1))
                    proof_file_id = item.get("proof_file_id")
                else:
                    card_id = item
                    quantity = 1
                    proof_file_id = None
                if card_id is None:
                    continue
                try:
                    await connection.execute(
                        """
                        INSERT INTO public.market_listing_items (
                            listing_id, card_id, quantity, proof_file_id
                        )
                        VALUES ($1, $2, $3, $4)
                        """,
                        int(listing_id),
                        int(card_id),
                        quantity,
                        proof_file_id,
                    )
                except Exception:
                    # The legacy flow intentionally skipped duplicate/invalid
                    # cards and continued publishing the remaining items.
                    continue
                total += 1
        return total

    async def add_rate_tiers(
        self,
        listing_id: int,
        tiers: Iterable[dict[str, Any]],
    ) -> int:
        total = 0
        async with self._pool.acquire() as connection:
            for tier in tiers:
                await connection.execute(
                    """
                    INSERT INTO public.market_rate_tiers (
                        listing_id, label, qty, pay_type, cash_code, price,
                        sort_order
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    int(listing_id),
                    tier.get("label"),
                    tier.get("qty"),
                    tier["pay_type"],
                    tier.get("cash_code"),
                    tier["price"],
                    int(tier.get("sort_order") or 0),
                )
                total += 1
        return total

    async def get_rate_tiers(self, listing_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM public.market_rate_tiers
                WHERE listing_id = $1
                ORDER BY sort_order, id
                """,
                int(listing_id),
            )
        return [dict(row) for row in rows]

    async def get_listing(self, listing_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM public.market_listings
                WHERE listing_id = $1
                """,
                int(listing_id),
            )
        return _as_dict(row)

    async def set_status(self, listing_id: int, status: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listings
                SET status = $2
                WHERE listing_id = $1
                """,
                int(listing_id),
                status,
            )

    async def get_status(self, listing_id: int) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT status
                FROM public.market_listings
                WHERE listing_id = $1
                """,
                int(listing_id),
            )
        return str(value) if value is not None else None

    async def bump(self, listing_id: int) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listings
                SET updated_at = now()
                WHERE listing_id = $1
                """,
                int(listing_id),
            )

    async def toggle_actual(self, listing_id: int) -> str:
        async with self._pool.acquire() as connection:
            current = await connection.fetchval(
                """
                SELECT status
                FROM public.market_listings
                WHERE listing_id = $1
                """,
                int(listing_id),
            )
            status = "hidden" if str(current) == "active" else "active"
            await connection.execute(
                """
                UPDATE public.market_listings
                SET status = $2
                WHERE listing_id = $1
                """,
                int(listing_id),
                status,
            )
        return status

    async def toggle_named_status(self, listing_id: int, status: str) -> None:
        if status not in {"hidden", "archived", "sold"}:
            raise ValueError(f"unsupported listing status toggle: {status}")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listings
                SET status = CASE
                        WHEN status::text = $2 THEN 'active'::listing_status
                        ELSE $2::listing_status
                    END,
                    updated_at = now()
                WHERE listing_id = $1
                """,
                int(listing_id),
                status,
            )

    async def set_cover(
        self,
        listing_id: int,
        file_id: str | None,
        *,
        touch_updated_at: bool = False,
    ) -> None:
        async with self._pool.acquire() as connection:
            if touch_updated_at:
                await connection.execute(
                    """
                    UPDATE public.market_listings
                    SET cover_file_id = $2,
                        updated_at = now()
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                    file_id,
                )
            else:
                await connection.execute(
                    """
                    UPDATE public.market_listings
                    SET cover_file_id = $2
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                    file_id,
                )

    async def set_description(
        self,
        listing_id: int,
        description: str | None,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listings
                SET description = $2,
                    updated_at = now()
                WHERE listing_id = $1
                """,
                int(listing_id),
                description,
            )

    async def set_item_proof(
        self,
        listing_id: int,
        card_id: int,
        file_id: str,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listing_items
                SET proof_file_id = $3
                WHERE listing_id = $1
                  AND card_id = $2
                """,
                int(listing_id),
                int(card_id),
                file_id,
            )

    async def set_item_quantity(
        self,
        listing_id: int,
        card_id: int,
        quantity: int,
    ) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE public.market_listing_items
                SET quantity = $3
                WHERE listing_id = $1
                  AND card_id = $2
                """,
                int(listing_id),
                int(card_id),
                int(quantity),
            )

    async def set_listing_item_quantity(self, listing_id: int, quantity: int) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                if int(quantity) <= 0:
                    await connection.execute(
                        """
                        DELETE FROM public.market_listing_items
                        WHERE listing_id = $1
                        """,
                        int(listing_id),
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE public.market_listing_items
                        SET quantity = $2
                        WHERE listing_id = $1
                        """,
                        int(listing_id),
                        int(quantity),
                    )

    async def decrement_item_quantity(self, listing_id: int, amount: int) -> int:
        amount = max(1, int(amount))
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT quantity
                    FROM public.market_listing_items
                    WHERE listing_id = $1
                    FOR UPDATE
                    """,
                    int(listing_id),
                )
                if not row:
                    return 0
                remaining = int(row["quantity"] or 0) - amount
                if remaining > 0:
                    updated = await connection.fetchrow(
                        """
                        UPDATE public.market_listing_items
                        SET quantity = $2
                        WHERE listing_id = $1
                        RETURNING quantity
                        """,
                        int(listing_id),
                        remaining,
                    )
                    return int(updated["quantity"]) if updated else 0
                await connection.execute(
                    """
                    DELETE FROM public.market_listing_items
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                return 0

    async def decrement_all_items_and_total(self, listing_id: int) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                # Avoid writing a forbidden zero value while retaining the
                # original "decrement every row by one" behaviour.
                await connection.execute(
                    """
                    DELETE FROM public.market_listing_items
                    WHERE listing_id = $1
                      AND quantity <= 1
                    """,
                    int(listing_id),
                )
                await connection.execute(
                    """
                    UPDATE public.market_listing_items
                    SET quantity = quantity - 1
                    WHERE listing_id = $1
                      AND quantity > 1
                    """,
                    int(listing_id),
                )
                value = await connection.fetchval(
                    """
                    SELECT COALESCE(SUM(quantity), 0)
                    FROM public.market_listing_items
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
        return int(value or 0)

    async def quantity_total(self, listing_id: int) -> int:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT COALESCE(SUM(quantity), 0)
                FROM public.market_listing_items
                WHERE listing_id = $1
                """,
                int(listing_id),
            )
        return int(value or 0)

    async def delete_all_prices(self, listing_id: int) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM public.market_rate_tiers
                WHERE listing_id = $1
                """,
                int(listing_id),
            )

    async def replace_price(
        self,
        listing_id: int,
        *,
        pay_type: str,
        cash_code: str | None,
        price: float | None,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    DELETE FROM public.market_rate_tiers
                    WHERE listing_id = $1
                      AND pay_type = $2
                      AND COALESCE(cash_code, '') = COALESCE($3, '')
                    """,
                    int(listing_id),
                    pay_type,
                    cash_code,
                )
                if price is not None:
                    await connection.execute(
                        """
                        INSERT INTO public.market_rate_tiers (
                            listing_id, label, qty, pay_type, cash_code,
                            price, sort_order
                        )
                        VALUES ($1, NULL, NULL, $2, $3, $4, 999)
                        """,
                        int(listing_id),
                        pay_type,
                        cash_code,
                        float(price),
                    )

    async def hard_delete_listing(self, listing_id: int) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    DELETE FROM public.market_rate_tiers
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                await connection.execute(
                    """
                    DELETE FROM public.market_listing_items
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                await connection.execute(
                    """
                    DELETE FROM public.market_listings
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )

    async def seller_listing_ids(
        self,
        seller_id: int,
        statuses: list[str],
    ) -> list[int]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT ml.listing_id
                FROM public.market_listings ml
                WHERE ml.seller_id = $1
                  AND ml.status = ANY ($2::listing_status[])
                ORDER BY ml.updated_at DESC NULLS LAST, ml.listing_id DESC
                """,
                int(seller_id),
                statuses,
            )
        return [int(row["listing_id"]) for row in rows]

    async def seller_listing_summaries(
        self,
        seller_id: int,
        statuses: list[str],
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT ml.*, COALESCE(cnt.items_count, 0) AS items_count
                FROM public.market_listings ml
                LEFT JOIN (
                    SELECT listing_id, COUNT(*) AS items_count
                    FROM public.market_listing_items
                    GROUP BY listing_id
                ) cnt ON cnt.listing_id = ml.listing_id
                WHERE ml.seller_id = $1
                  AND ml.status = ANY ($2::listing_status[])
                ORDER BY ml.updated_at DESC NULLS LAST, ml.listing_id DESC
                """,
                int(seller_id),
                statuses,
            )
        return [dict(row) for row in rows]

    async def seller_listings(self, seller_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT listing_id, status, description, created_at,
                       cover_file_id
                FROM public.market_listings
                WHERE seller_id = $1
                ORDER BY CASE status
                           WHEN 'active' THEN 0
                           WHEN 'hidden' THEN 1
                           WHEN 'sold' THEN 2
                           ELSE 3
                         END,
                         created_at DESC
                """,
                int(seller_id),
            )
        return [dict(row) for row in rows]

    async def listing_items(self, listing_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT mli.card_id, mli.quantity, c.hero_name, c.card_name,
                       c.rarity, c.image_id
                FROM public.market_listing_items mli
                JOIN public.cards c ON c.card_id = mli.card_id
                WHERE mli.listing_id = $1
                ORDER BY c.hero_name, c.card_name
                """,
                int(listing_id),
            )
        return [dict(row) for row in rows]

    async def listing_display_tiers(
        self,
        listing_id: int,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT pay_type, cash_code, price, COALESCE(qty, 1) AS qty
                FROM public.market_rate_tiers
                WHERE listing_id = $1
                ORDER BY sort_order, id
                """,
                int(listing_id),
            )
        return [dict(row) for row in rows]

    async def listing_navigation_view(
        self,
        listing_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        async with self._pool.acquire() as connection:
            lot_row = await connection.fetchrow(
                """
                SELECT ml.*,
                       COALESCE(cnt.items_count, 0)::int AS items_count,
                       mli.card_id
                FROM public.market_listings ml
                LEFT JOIN LATERAL (
                    SELECT card_id
                    FROM public.market_listing_items
                    WHERE listing_id = ml.listing_id
                    ORDER BY id ASC
                    LIMIT 1
                ) mli ON TRUE
                LEFT JOIN (
                    SELECT listing_id, COUNT(*) AS items_count
                    FROM public.market_listing_items
                    GROUP BY listing_id
                ) cnt ON cnt.listing_id = ml.listing_id
                WHERE ml.listing_id = $1
                """,
                int(listing_id),
            )
            lot = _as_dict(lot_row) or {}
            card: dict[str, Any] = {}
            if lot.get("card_id"):
                card_row = await connection.fetchrow(
                    """
                    SELECT c.card_id,
                           c.card_name AS title,
                           c.rarity,
                           c.deck_id,
                           d.name AS deck_name,
                           CASE
                             WHEN lower(c.obtain_type::text) IN ('diamonds', 'diamond')
                               THEN c.obtain_amount
                             ELSE 0
                           END AS diamonds,
                           CASE
                             WHEN lower(c.obtain_type::text) IN ('tea', 'cups', 'cup')
                               THEN c.obtain_amount
                             ELSE 0
                           END AS cups,
                           CASE
                             WHEN lower(c.obtain_type::text) IN ('treasures', 'treasure')
                               THEN c.obtain_amount
                             ELSE 0
                           END AS treasures
                    FROM public.cards c
                    LEFT JOIN public.decks d ON d.id = c.deck_id
                    WHERE c.card_id = $1
                    """,
                    int(lot["card_id"]),
                )
                card = _as_dict(card_row) or {}
            tier_rows = await connection.fetch(
                """
                SELECT pay_type, price, cash_code, label, qty
                FROM public.market_rate_tiers
                WHERE listing_id = $1
                ORDER BY sort_order NULLS LAST, price
                """,
                int(listing_id),
            )
        return lot, card, [dict(row) for row in tier_rows]

    async def listing_reload_view(self, listing_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT ml.status,
                       ml.description,
                       ml.cover_file_id,
                       ml.created_at,
                       first_item.card_id,
                       COALESCE(totals.quantity, 0)::int AS quantity_left,
                       COALESCE(proofs.count, 0)::int AS item_proof_count
                FROM public.market_listings ml
                LEFT JOIN LATERAL (
                    SELECT card_id
                    FROM public.market_listing_items
                    WHERE listing_id = ml.listing_id
                    ORDER BY id
                    LIMIT 1
                ) first_item ON TRUE
                LEFT JOIN LATERAL (
                    SELECT SUM(quantity) AS quantity
                    FROM public.market_listing_items
                    WHERE listing_id = ml.listing_id
                ) totals ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS count
                    FROM public.market_listing_items
                    WHERE listing_id = ml.listing_id
                      AND proof_file_id IS NOT NULL
                ) proofs ON TRUE
                WHERE ml.listing_id = $1
                """,
                int(listing_id),
            )
        return _as_dict(row)

    async def search(
        self,
        *,
        deck_id: int | None = None,
        rarity: str | None = None,
        query: str | None = None,
        currency: str | None = None,
        cash_code: str | None = None,
        offer_kind: str | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["ml.status = 'active'"]
        arguments: list[Any] = []

        def add(clause: str, value: Any) -> None:
            arguments.append(value)
            where.append(clause.format(index=len(arguments)))

        if offer_kind:
            add("ml.offer_kind = ${index}", offer_kind)
        if currency:
            add("ml.currency_type = ${index}", currency)
        if cash_code:
            add("ml.cash_code = ${index}", cash_code)
        if price_min is not None:
            add("ml.price_num >= ${index}", price_min)
        if price_max is not None:
            add("ml.price_num <= ${index}", price_max)
        if deck_id is not None:
            add("c.deck_id = ${index}", int(deck_id))
        if rarity:
            add("c.rarity = ${index}", rarity)
        if query:
            pattern = f"%{query}%"
            indexes = []
            for _ in range(3):
                arguments.append(pattern)
                indexes.append(len(arguments))
            where.append(
                "(c.card_name ILIKE ${0} OR c.hero_name ILIKE ${1} "
                "OR c.story ILIKE ${2})".format(*indexes)
            )

        limit_index = len(arguments) + 1
        offset_index = len(arguments) + 2
        arguments.extend([int(limit), int(offset)])
        statement = f"""
            SELECT ml.listing_id, ml.seller_id, ml.currency_type,
                   ml.cash_code, ml.price_num, ml.description, ml.updated_at,
                   ml.offer_kind, c.card_id, c.deck_id, c.card_name,
                   c.hero_name, c.rarity, c.image_id
            FROM public.market_listings ml
            JOIN public.market_listing_items mli
              ON mli.listing_id = ml.listing_id
            JOIN public.cards c ON c.card_id = mli.card_id
            WHERE {' AND '.join(where)}
            ORDER BY ml.updated_at DESC, ml.listing_id DESC
            LIMIT ${limit_index} OFFSET ${offset_index}
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(statement, *arguments)
        return [dict(row) for row in rows]

    async def get_cover_file_id(self, listing_id: int) -> str | None:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT cover_file_id
                FROM public.market_listings
                WHERE listing_id = $1
                """,
                int(listing_id),
            )
        return str(value) if value else None

    async def has_any_proof(self, listing_id: int) -> bool:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT ml.cover_file_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1
                           FROM public.market_listing_items mli
                           WHERE mli.listing_id = ml.listing_id
                             AND mli.proof_file_id IS NOT NULL
                       ) AS has_proof
                FROM public.market_listings ml
                WHERE ml.listing_id = $1
                """,
                int(listing_id),
            )
        return bool(row and row["has_proof"])

    async def price_map(self, listing_id: int) -> dict[str, int | float]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT pay_type, cash_code, price
                FROM public.market_rate_tiers
                WHERE listing_id = $1
                ORDER BY sort_order, id
                """,
                int(listing_id),
            )
        result: dict[str, int | float] = {}
        for row in rows:
            pay_type = str(row["pay_type"] or "").lower()
            if pay_type == "cash":
                code = str(row["cash_code"] or "").upper()
                if code:
                    result[f"cash:{code}"] = float(row["price"])
            else:
                result[pay_type] = int(row["price"])
        return result

    async def fetch_card(self, card_id: int) -> dict[str, Any]:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT card_id, deck_id, hero_name, card_name, rarity, story,
                       image_id
                FROM public.cards
                WHERE card_id = $1
                """,
                int(card_id),
            )
        return _as_dict(row) or {}

    async def all_decks(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id AS deck_id, name AS deck_name, deck_type
                FROM public.decks
                ORDER BY id ASC
                """
            )
        return [dict(row) for row in rows]

    async def cards_by_deck(self, deck_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM public.cards
                WHERE deck_id = $1
                ORDER BY num
                """,
                int(deck_id),
            )
        return [dict(row) for row in rows]

    async def card_ids_by_deck(self, deck_id: int) -> list[int]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT card_id
                FROM public.cards
                WHERE deck_id = $1
                ORDER BY card_id
                """,
                int(deck_id),
            )
        return [int(row["card_id"]) for row in rows]

    async def persist_proofs(
        self,
        listing_id: int,
        *,
        proof_file_id: str | None,
        proof_by_card: dict[str, str],
    ) -> None:
        async with self._pool.acquire() as connection:
            async def has_column(table: str, column: str) -> bool:
                row = await connection.fetchrow(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = $1
                          AND column_name = $2
                    ) AS has
                    """,
                    table,
                    column,
                )
                return bool(row and row["has"])

            has_listing_proof = await has_column(
                "market_listings", "proof_file_id"
            )
            has_cover = await has_column("market_listings", "cover_file_id")
            has_proof_map = await has_column("market_listings", "proof_by_card")
            has_item_proof = await has_column(
                "market_listing_items", "proof_file_id"
            )

            if proof_file_id:
                if has_listing_proof:
                    await connection.execute(
                        """
                        UPDATE public.market_listings
                        SET proof_file_id = $2
                        WHERE listing_id = $1
                        """,
                        int(listing_id),
                        proof_file_id,
                    )
                elif has_cover:
                    await connection.execute(
                        """
                        UPDATE public.market_listings
                        SET cover_file_id = $2
                        WHERE listing_id = $1
                        """,
                        int(listing_id),
                        proof_file_id,
                    )

            if proof_by_card:
                if has_proof_map:
                    await connection.execute(
                        """
                        UPDATE public.market_listings
                        SET proof_by_card = $2
                        WHERE listing_id = $1
                        """,
                        int(listing_id),
                        json.dumps(proof_by_card),
                    )
                elif has_item_proof:
                    for raw_card_id, file_id in proof_by_card.items():
                        try:
                            card_id = int(raw_card_id)
                        except (TypeError, ValueError):
                            continue
                        await connection.execute(
                            """
                            UPDATE public.market_listing_items
                            SET proof_file_id = $3
                            WHERE listing_id = $1
                              AND card_id = $2
                            """,
                            int(listing_id),
                            card_id,
                            file_id,
                        )

    async def listing_core(self, listing_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            async def has_column(table: str, column: str) -> bool:
                row = await connection.fetchrow(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = $1
                          AND column_name = $2
                    ) AS has
                    """,
                    table,
                    column,
                )
                return bool(row and row["has"])

            has_listing_proof = await has_column(
                "market_listings", "proof_file_id"
            )
            has_cover = await has_column("market_listings", "cover_file_id")
            has_proof_map = await has_column("market_listings", "proof_by_card")
            has_item_proof = await has_column(
                "market_listing_items", "proof_file_id"
            )

            proof_file_id: str | None = None
            if has_listing_proof:
                row = await connection.fetchrow(
                    """
                    SELECT proof_file_id, cover_file_id
                    FROM public.market_listings
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                if not row:
                    return None
                proof_file_id = row["proof_file_id"] or row["cover_file_id"]
            elif has_cover:
                row = await connection.fetchrow(
                    """
                    SELECT cover_file_id
                    FROM public.market_listings
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                if not row:
                    return None
                proof_file_id = row["cover_file_id"]
            else:
                exists = await connection.fetchval(
                    """
                    SELECT 1
                    FROM public.market_listings
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                if not exists:
                    return None

            proof_by_card: dict[str, str] = {}
            if has_proof_map:
                value = await connection.fetchval(
                    """
                    SELECT proof_by_card
                    FROM public.market_listings
                    WHERE listing_id = $1
                    """,
                    int(listing_id),
                )
                if value:
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            value = {}
                    if isinstance(value, dict):
                        proof_by_card = {
                            str(key): str(file_id)
                            for key, file_id in value.items()
                            if file_id is not None
                        }
            if not proof_by_card and has_item_proof:
                rows = await connection.fetch(
                    """
                    SELECT card_id, proof_file_id
                    FROM public.market_listing_items
                    WHERE listing_id = $1
                      AND proof_file_id IS NOT NULL
                    """,
                    int(listing_id),
                )
                proof_by_card = {
                    str(row["card_id"]): str(row["proof_file_id"])
                    for row in rows
                    if row["proof_file_id"]
                }

        return {
            "listing_id": int(listing_id),
            "proof_file_id": proof_file_id,
            "proof_by_card": proof_by_card,
        }
