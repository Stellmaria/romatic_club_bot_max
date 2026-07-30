"""Persistence operations used by winner announcements and print workflows.

Keeping the SQL in this module makes the Telegram adapters and winner services
independent from the shape of the database access layer. Every operation takes
an explicit pool and framework-neutral scalar values.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

_PRINT_TABLES_READY = False
_THANKS_TABLES_READY = False


async def _fetch(
    pool: asyncpg.Pool,
    query: str,
    *args: object,
) -> list[asyncpg.Record]:
    async with pool.acquire() as connection:
        return list(await connection.fetch(query, *args))


async def _fetchrow(
    pool: asyncpg.Pool,
    query: str,
    *args: object,
) -> asyncpg.Record | None:
    async with pool.acquire() as connection:
        return await connection.fetchrow(query, *args)


async def _execute(
    pool: asyncpg.Pool,
    query: str,
    *args: object,
) -> str:
    async with pool.acquire() as connection:
        return await connection.execute(query, *args)


async def get_owners(
    pool: asyncpg.Pool,
    auction_id: int,
) -> list[dict[str, Any]]:
    rows = await _fetch(
        pool,
        """
        SELECT u.user_id, u.username
        FROM public.auction_owners ao
        JOIN public.users u ON u.user_id = ao.user_id
        WHERE ao.auction_id = $1
        ORDER BY u.user_id
        """,
        int(auction_id),
    )
    return [dict(row) for row in (rows or [])]


async def get_auction_summary(
    pool: asyncpg.Pool,
    auction_id: int,
) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        """
        SELECT auction_id, hero_name, card_name, currency, accepted_currencies,
               auction_kind, message_id, image_id, discussion_message_id
        FROM public.auctions
        WHERE auction_id = $1
        """,
        int(auction_id),
    )
    return dict(row) if row else None


async def get_auction_currency(pool: asyncpg.Pool, auction_id: int) -> str | None:
    row = await _fetchrow(
        pool,
        "SELECT currency FROM public.auctions WHERE auction_id = $1",
        int(auction_id),
    )
    return str(row["currency"]) if row and row.get("currency") else None


async def get_discussion_message_id(
    pool: asyncpg.Pool,
    auction_id: int,
) -> int | None:
    row = await _fetchrow(
        pool,
        "SELECT discussion_message_id FROM public.auctions WHERE auction_id = $1",
        int(auction_id),
    )
    return (
        int(row["discussion_message_id"])
        if row and row.get("discussion_message_id")
        else None
    )


async def get_bid_discussion_message_id(
    pool: asyncpg.Pool,
    auction_id: int,
    *,
    bidder_id: int,
    amount: int,
) -> int | None:
    row = await _fetchrow(
        pool,
        """
        SELECT discussion_message_id
        FROM public.bids
        WHERE auction_id = $1
          AND bidder_id = $2
          AND amount = $3
        ORDER BY placed_at DESC
        LIMIT 1
        """,
        int(auction_id),
        int(bidder_id),
        int(amount),
    )
    return (
        int(row["discussion_message_id"])
        if row and row.get("discussion_message_id")
        else None
    )


async def get_top_bid(
    pool: asyncpg.Pool,
    auction_id: int,
    *,
    lowest_wins: bool = False,
    latest_on_tie: bool = False,
) -> dict[str, Any] | None:
    amount_order = "ASC" if lowest_wins else "DESC"
    placed_order = "DESC" if latest_on_tie else "ASC"
    row = await _fetchrow(
        pool,
        f"""
        SELECT bidder_id, amount, discussion_message_id
        FROM public.bids
        WHERE auction_id = $1
        ORDER BY amount {amount_order}, placed_at {placed_order}
        LIMIT 1
        """,
        int(auction_id),
    )
    return dict(row) if row else None


async def get_auction_deck(
    pool: asyncpg.Pool,
    auction_id: int,
) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        """
        SELECT d.id AS deck_id, d.name AS deck_name
        FROM public.auctions a
        LEFT JOIN public.cards c
          ON lower(c.card_name) = lower(a.card_name)
         AND (a.hero_name IS NULL OR lower(c.hero_name) = lower(a.hero_name))
        LEFT JOIN public.decks d ON d.id = c.deck_id
        WHERE a.auction_id = $1
        """,
        int(auction_id),
    )
    return dict(row) if row else None


async def get_user(pool: asyncpg.Pool, user_id: int) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        """
        SELECT user_id, username, full_name
        FROM public.users
        WHERE user_id = $1
        """,
        int(user_id),
    )
    return dict(row) if row else None


async def get_user_by_username(
    pool: asyncpg.Pool,
    username: str,
) -> dict[str, Any] | None:
    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        return None
    row = await _fetchrow(
        pool,
        """
        SELECT *
        FROM public.users
        WHERE lower(username) = lower($1)
        LIMIT 1
        """,
        normalized,
    )
    return dict(row) if row else None


async def is_user_uid_verified(pool: asyncpg.Pool, user_id: int | None) -> bool:
    if not user_id:
        return False
    row = await _fetchrow(
        pool,
        """
        SELECT 1
        FROM public.user_uids
        WHERE user_id = $1
          AND status = 'verified'
        LIMIT 1
        """,
        int(user_id),
    )
    return bool(row)


async def users_uid_verification_counts(
    pool: asyncpg.Pool,
    user_ids: list[int] | None,
) -> tuple[int, int, bool]:
    ids = list(dict.fromkeys(int(value) for value in (user_ids or []) if value))
    if not ids:
        return 0, 0, False
    row = await _fetchrow(
        pool,
        """
        SELECT COUNT(*)::int AS total,
               COALESCE(
                   SUM(CASE WHEN uu.status = 'verified' THEN 1 ELSE 0 END),
                   0
               )::int AS verified_cnt
        FROM unnest($1::bigint[]) AS u(user_id)
        LEFT JOIN public.user_uids uu ON uu.user_id = u.user_id
        """,
        ids,
    )
    total = int(row["total"] or 0) if row else 0
    verified = int(row["verified_cnt"] or 0) if row else 0
    return total, verified, total > 0 and verified == total


async def get_autobid_action_by_message_id(
    pool: asyncpg.Pool,
    discussion_message_id: int,
) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        """
        SELECT *
        FROM public.autobid_actions
        WHERE discussion_message_id = $1
        """,
        int(discussion_message_id),
    )
    return dict(row) if row else None


async def get_exchange_batches_for_card(
    pool: asyncpg.Pool,
    card_id: int,
    *,
    status: str = "approved",
) -> list[dict[str, Any]]:
    rows = await _fetch(
        pool,
        """
        SELECT eb.batch_id, eb.user_id, u.username, COUNT(*)::int AS qty
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
    return [dict(row) for row in rows]


async def get_exchange_batch(
    pool: asyncpg.Pool,
    batch_id: int,
) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        """
        SELECT eb.*, u.username
        FROM public.exchange_batches eb
        LEFT JOIN public.users u ON u.user_id = eb.user_id
        WHERE eb.batch_id = $1
        """,
        int(batch_id),
    )
    return dict(row) if row else None


async def get_exchange_cards(
    pool: asyncpg.Pool,
    batch_id: int,
) -> list[dict[str, Any]]:
    rows = await _fetch(
        pool,
        """
        SELECT c.card_id, c.hero_name, c.card_name, COUNT(*)::int AS qty
        FROM public.exchange_items ei
        JOIN public.cards c ON c.card_id = ei.card_id
        WHERE ei.batch_id = $1
        GROUP BY c.card_id, c.hero_name, c.card_name
        ORDER BY c.hero_name NULLS LAST, c.card_name
        """,
        int(batch_id),
    )
    return [dict(row) for row in rows]


async def get_exchange_print_stats(
    pool: asyncpg.Pool,
    batch_id: int,
) -> dict[str, Any] | None:
    row = await _fetchrow(
        pool,
        "SELECT * FROM public.exchange_print_stats WHERE batch_id = $1",
        int(batch_id),
    )
    return dict(row) if row else None


async def upsert_exchange_print_stats(
    pool: asyncpg.Pool,
    batch_id: int,
    *,
    winner_id: int | None = None,
    winner_name: str | None = None,
    price: int | None = None,
    link: str | None = None,
    updated_by: int | None = None,
) -> None:
    await _execute(
        pool,
        """
        INSERT INTO public.exchange_print_stats (
            batch_id, manual_winner_id, manual_winner_name, manual_price,
            manual_link, updated_by, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, now())
        ON CONFLICT (batch_id) DO UPDATE SET
            manual_winner_id = COALESCE(
                EXCLUDED.manual_winner_id,
                public.exchange_print_stats.manual_winner_id
            ),
            manual_winner_name = COALESCE(
                EXCLUDED.manual_winner_name,
                public.exchange_print_stats.manual_winner_name
            ),
            manual_price = COALESCE(
                EXCLUDED.manual_price,
                public.exchange_print_stats.manual_price
            ),
            manual_link = COALESCE(
                EXCLUDED.manual_link,
                public.exchange_print_stats.manual_link
            ),
            updated_by = COALESCE(
                EXCLUDED.updated_by,
                public.exchange_print_stats.updated_by
            ),
            updated_at = now()
        """,
        int(batch_id),
        winner_id,
        (winner_name or "").strip() or None,
        price,
        (link or "").strip() or None,
        int(updated_by) if updated_by is not None else None,
    )


async def reset_exchange_print_stats(
    pool: asyncpg.Pool,
    batch_id: int,
    *,
    updated_by: int | None = None,
) -> None:
    await _execute(
        pool,
        """
        INSERT INTO public.exchange_print_stats (
            batch_id, manual_winner_id, manual_winner_name, manual_price,
            manual_link, updated_by, updated_at
        )
        VALUES ($1, NULL, NULL, NULL, NULL, $2, now())
        ON CONFLICT (batch_id) DO UPDATE SET
            manual_winner_id = NULL,
            manual_winner_name = NULL,
            manual_price = NULL,
            manual_link = NULL,
            updated_by = $2,
            updated_at = now()
        """,
        int(batch_id),
        int(updated_by) if updated_by is not None else None,
    )


async def get_print_win_missed_for_day(
    pool: asyncpg.Pool,
    target_date: date,
) -> list[dict[str, Any]]:
    rows = await _fetch(
        pool,
        """
        WITH day_lots AS (
            SELECT a.auction_id, a.start_time, a.status, a.hero_name, a.card_name
            FROM public.auctions a
            WHERE a.start_time IS NOT NULL
              AND (a.start_time AT TIME ZONE 'Europe/Moscow')::date = $1
              AND a.status IN ('scheduled', 'active', 'finished', 'approved')
        ), mailed_full AS (
            SELECT m.auction_id
            FROM public.auction_win_mailings m
            GROUP BY m.auction_id
            HAVING SUM(CASE WHEN m.target IN ('owner', 'both') THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN m.target IN ('winner', 'both') THEN 1 ELSE 0 END) > 0
        ), bids_cnt AS (
            SELECT b.auction_id, COUNT(*)::int AS bids_count
            FROM public.bids b
            GROUP BY b.auction_id
        )
        SELECT d.auction_id, d.start_time, d.status, d.hero_name, d.card_name,
               COALESCE(bc.bids_count, 0) AS bids_count
        FROM day_lots d
        LEFT JOIN mailed_full mf ON mf.auction_id = d.auction_id
        LEFT JOIN bids_cnt bc ON bc.auction_id = d.auction_id
        WHERE mf.auction_id IS NULL
        ORDER BY d.start_time, d.auction_id
        """,
        target_date,
    )
    return [dict(row) for row in rows]


async def ensure_print_tables(pool: asyncpg.Pool) -> None:
    global _PRINT_TABLES_READY
    if _PRINT_TABLES_READY:
        return

    await _execute(
        pool,
        """
        CREATE TABLE IF NOT EXISTS public.auction_win_mailings (
            id BIGSERIAL PRIMARY KEY,
            auction_id INTEGER NOT NULL,
            target TEXT NOT NULL,
            sent_by_user_id BIGINT,
            sent_by_username TEXT,
            sent_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await _execute(
        pool,
        """
        CREATE INDEX IF NOT EXISTS idx_auction_win_mailings_auction_id
        ON public.auction_win_mailings (auction_id)
        """
    )
    await _execute(
        pool,
        """
        CREATE TABLE IF NOT EXISTS public.auction_manual_results (
            auction_id INTEGER PRIMARY KEY,
            winner_user_id BIGINT,
            winner_username TEXT,
            owner_user_id BIGINT,
            owner_username TEXT,
            amount INTEGER,
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_by BIGINT
        )
        """
    )
    await _execute(
        pool,
        """
        ALTER TABLE public.auction_manual_results
        ADD COLUMN IF NOT EXISTS moderator_comment TEXT
        """
    )
    _PRINT_TABLES_READY = True


async def get_mailing_counts(
    pool: asyncpg.Pool,
    auction_id: int,
) -> tuple[int, int, int]:
    await ensure_print_tables(pool)
    row = await _fetchrow(
        pool,
        """
        SELECT COUNT(*)::bigint AS total,
               SUM(CASE WHEN target IN ('owner', 'both') THEN 1 ELSE 0 END)::bigint AS owner_cnt,
               SUM(CASE WHEN target IN ('winner', 'both') THEN 1 ELSE 0 END)::bigint AS winner_cnt
        FROM public.auction_win_mailings
        WHERE auction_id = $1
        """,
        int(auction_id),
    )
    return (
        int(row["total"] or 0),
        int(row["owner_cnt"] or 0),
        int(row["winner_cnt"] or 0),
    )


async def add_mailing(
    pool: asyncpg.Pool,
    auction_id: int,
    target: str,
    admin_id: int,
    admin_username: str | None,
) -> None:
    await ensure_print_tables(pool)
    await _execute(
        pool,
        """
        INSERT INTO public.auction_win_mailings (
            auction_id, target, sent_by_user_id, sent_by_username
        )
        VALUES ($1, $2, $3, $4)
        """,
        int(auction_id),
        target,
        int(admin_id),
        admin_username or None,
    )


async def get_manual_result(
    pool: asyncpg.Pool,
    auction_id: int,
) -> dict[str, Any] | None:
    await ensure_print_tables(pool)
    row = await _fetchrow(
        pool,
        """
        SELECT auction_id, winner_user_id, winner_username, owner_user_id,
               owner_username, amount, moderator_comment
        FROM public.auction_manual_results
        WHERE auction_id = $1
        """,
        int(auction_id),
    )
    return dict(row) if row else None


async def upsert_manual_result(
    pool: asyncpg.Pool,
    auction_id: int,
    *,
    winner_user_id: int | None,
    winner_username: str | None,
    owner_user_id: int | None,
    owner_username: str | None,
    amount: int | None,
    updated_by: int,
    moderator_comment: str | None = None,
) -> None:
    await ensure_print_tables(pool)
    await _execute(
        pool,
        """
        INSERT INTO public.auction_manual_results (
            auction_id, winner_user_id, winner_username, owner_user_id,
            owner_username, amount, updated_by, moderator_comment
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (auction_id) DO UPDATE SET
            winner_user_id = EXCLUDED.winner_user_id,
            winner_username = EXCLUDED.winner_username,
            owner_user_id = EXCLUDED.owner_user_id,
            owner_username = EXCLUDED.owner_username,
            amount = EXCLUDED.amount,
            updated_by = EXCLUDED.updated_by,
            moderator_comment = COALESCE(
                EXCLUDED.moderator_comment,
                public.auction_manual_results.moderator_comment
            ),
            updated_at = CURRENT_TIMESTAMP
        """,
        int(auction_id),
        winner_user_id,
        winner_username,
        owner_user_id,
        owner_username,
        amount,
        int(updated_by),
        moderator_comment,
    )


async def delete_manual_result(pool: asyncpg.Pool, auction_id: int) -> None:
    await ensure_print_tables(pool)
    await _execute(
        pool,
        "DELETE FROM public.auction_manual_results WHERE auction_id = $1",
        int(auction_id),
    )


def normalize_author(author: str) -> str:
    return (author or "").strip().lstrip("@").lower()


async def ensure_thanks_tables(pool: asyncpg.Pool) -> None:
    global _THANKS_TABLES_READY
    if _THANKS_TABLES_READY:
        return
    await _execute(
        pool,
        """
        CREATE TABLE IF NOT EXISTS public.admin_thanks_totals (
            author TEXT PRIMARY KEY,
            thanks_total BIGINT NOT NULL DEFAULT 0,
            users_total BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS public.admin_thanks_users (
            author TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            thanks_count BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (author, user_id)
        )
        """
    )
    await _execute(
        pool,
        """
        ALTER TABLE public.admin_thanks_users
        ADD COLUMN IF NOT EXISTS thanks_count BIGINT NOT NULL DEFAULT 0
        """
    )
    await _execute(
        pool,
        """
        UPDATE public.admin_thanks_users
        SET thanks_count = 1
        WHERE thanks_count = 0
        """
    )
    _THANKS_TABLES_READY = True


async def increment_thanks(
    pool: asyncpg.Pool,
    author: str,
    user_id: int,
) -> tuple[int, int]:
    await ensure_thanks_tables(pool)
    key = normalize_author(author)
    if not key:
        return 0, 0
    row = await _fetchrow(
        pool,
        """
        WITH up AS (
            INSERT INTO public.admin_thanks_users (author, user_id, thanks_count)
            VALUES ($1, $2, 1)
            ON CONFLICT (author, user_id) DO UPDATE SET
                thanks_count = public.admin_thanks_users.thanks_count + 1
            RETURNING CASE
                WHEN public.admin_thanks_users.thanks_count = 1 THEN 1 ELSE 0
            END AS is_new_user
        ), tot AS (
            INSERT INTO public.admin_thanks_totals (
                author, thanks_total, users_total
            )
            VALUES ($1, 1, COALESCE((SELECT SUM(is_new_user) FROM up), 0))
            ON CONFLICT (author) DO UPDATE SET
                thanks_total = public.admin_thanks_totals.thanks_total + 1,
                users_total = public.admin_thanks_totals.users_total
                    + COALESCE((SELECT SUM(is_new_user) FROM up), 0),
                updated_at = CURRENT_TIMESTAMP
            RETURNING thanks_total, users_total
        )
        SELECT thanks_total, users_total FROM tot
        """,
        key,
        int(user_id),
    )
    if not row:
        return 0, 0
    return int(row["thanks_total"] or 0), int(row["users_total"] or 0)


async def get_thanks_totals(
    pool: asyncpg.Pool,
    author: str,
) -> tuple[int, int]:
    await ensure_thanks_tables(pool)
    key = normalize_author(author)
    if not key:
        return 0, 0
    row = await _fetchrow(
        pool,
        """
        SELECT thanks_total, users_total
        FROM public.admin_thanks_totals
        WHERE author = $1
        """,
        key,
    )
    if not row:
        return 0, 0
    return int(row["thanks_total"] or 0), int(row["users_total"] or 0)
