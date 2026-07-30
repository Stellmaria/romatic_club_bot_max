from __future__ import annotations

from datetime import date
from typing import Any, Literal

import asyncpg


MailTarget = Literal["owner", "winner", "both"]


class AuctionWinnerRepository:
    """Persistence boundary for winner announcements and manual result flows."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_print_win_schema(self) -> None:
        """Keep legacy winner tables available without exposing DDL to handlers."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public.auction_win_mailings (
                    id BIGSERIAL PRIMARY KEY,
                    auction_id INTEGER NOT NULL,
                    target TEXT NOT NULL,
                    sent_by_user_id BIGINT,
                    sent_by_username TEXT,
                    sent_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_auction_win_mailings_auction_id
                    ON public.auction_win_mailings (auction_id);
                CREATE TABLE IF NOT EXISTS public.auction_manual_results (
                    auction_id INTEGER PRIMARY KEY,
                    winner_user_id BIGINT,
                    winner_username TEXT,
                    owner_user_id BIGINT,
                    owner_username TEXT,
                    amount INTEGER,
                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_by BIGINT,
                    moderator_comment TEXT
                );
                ALTER TABLE public.auction_manual_results
                    ADD COLUMN IF NOT EXISTS moderator_comment TEXT;
                """
            )

    async def ensure_admin_thanks_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
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
                );
                ALTER TABLE public.admin_thanks_users
                    ADD COLUMN IF NOT EXISTS thanks_count BIGINT NOT NULL DEFAULT 0;
                UPDATE public.admin_thanks_users
                SET thanks_count = 1
                WHERE thanks_count = 0;
                """
            )

    async def auction(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT auction_id,
                       hero_name,
                       card_name,
                       currency,
                       message_id,
                       discussion_message_id,
                       image_id,
                       auction_kind,
                       accepted_currencies
                FROM public.auctions
                WHERE auction_id = $1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def auction_currency(self, auction_id: int) -> str | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT currency FROM public.auctions WHERE auction_id = $1",
                int(auction_id),
            )
        return str(value) if value is not None else None

    async def discussion_message_id(self, auction_id: int) -> int | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT discussion_message_id FROM public.auctions WHERE auction_id = $1",
                int(auction_id),
            )
        return int(value) if value is not None else None

    async def owners(self, auction_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.user_id, u.username
                FROM public.auction_owners ao
                JOIN public.users u ON u.user_id = ao.user_id
                WHERE ao.auction_id = $1
                ORDER BY u.user_id
                """,
                int(auction_id),
            )
        return [dict(row) for row in rows]

    async def user(self, user_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, username, full_name
                FROM public.users
                WHERE user_id = $1
                """,
                int(user_id),
            )
        return dict(row) if row else None

    async def user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lstrip("@")
        if not normalized:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, username, full_name
                FROM public.users
                WHERE lower(username) = lower($1)
                LIMIT 1
                """,
                normalized,
            )
        return dict(row) if row else None

    async def uid_verified(self, user_id: int | None) -> bool:
        if not user_id:
            return False
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM public.user_uids
                    WHERE user_id = $1
                      AND status = 'verified'
                )
                """,
                int(user_id),
            )
        return bool(value)

    async def uid_verification_counts(self, user_ids: list[int] | None) -> tuple[int, int, bool]:
        ids = list(dict.fromkeys(int(value) for value in (user_ids or []) if value))
        if not ids:
            return 0, 0, False
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)::int AS total,
                       COALESCE(
                           SUM(CASE WHEN uu.status = 'verified' THEN 1 ELSE 0 END),
                           0
                       )::int AS verified_cnt
                FROM unnest($1::bigint[]) AS source(user_id)
                LEFT JOIN public.user_uids uu ON uu.user_id = source.user_id
                """,
                ids,
            )
        total = int(row["total"] or 0) if row else 0
        verified = int(row["verified_cnt"] or 0) if row else 0
        return total, verified, total > 0 and total == verified

    async def top_bid(self, auction_id: int, *, lowest_wins: bool = False) -> dict[str, Any] | None:
        direction = "ASC" if lowest_wins else "DESC"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT bidder_id, amount, discussion_message_id, placed_at
                FROM public.bids
                WHERE auction_id = $1
                ORDER BY amount {direction}, placed_at ASC, bid_id ASC
                LIMIT 1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def ranked_bids(self, auction_id: int, *, limit: int | None = None) -> list[dict[str, Any]]:
        limit_sql = "LIMIT $2" if limit is not None else ""
        parameters: tuple[int, ...] = (int(auction_id),)
        if limit is not None:
            parameters = (int(auction_id), max(1, int(limit)))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT b.*
                FROM public.bids AS b
                JOIN public.auctions AS a ON a.auction_id = b.auction_id
                WHERE b.auction_id = $1
                ORDER BY
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse'
                        THEN b.amount END ASC,
                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) <> 'reverse'
                        THEN b.amount END DESC,
                    b.placed_at ASC,
                    b.bid_id ASC
                {limit_sql}
                """,
                *parameters,
            )
        return [dict(row) for row in rows]

    async def bid_message_id(self, auction_id: int, bidder_id: int, amount: int) -> int | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT discussion_message_id
                FROM public.bids
                WHERE auction_id = $1
                  AND bidder_id = $2
                  AND amount = $3
                ORDER BY placed_at DESC, bid_id DESC
                LIMIT 1
                """,
                int(auction_id),
                int(bidder_id),
                int(amount),
            )
        return int(value) if value is not None else None

    async def autobid_action(self, discussion_message_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT autobid_id, auction_id, target_user_id, amount, discussion_message_id
                FROM public.autobid_actions
                WHERE discussion_message_id = $1
                """,
                int(discussion_message_id),
            )
        return dict(row) if row else None

    async def deck_for_auction(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.id AS deck_id, d.name AS deck_name
                FROM public.auctions a
                LEFT JOIN public.cards c
                  ON lower(c.card_name) = lower(a.card_name)
                 AND (a.hero_name IS NULL OR lower(c.hero_name) = lower(a.hero_name))
                LEFT JOIN public.decks d ON d.id = c.deck_id
                WHERE a.auction_id = $1
                ORDER BY c.card_id
                LIMIT 1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def manual_result(self, auction_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT auction_id,
                       winner_user_id,
                       winner_username,
                       owner_user_id,
                       owner_username,
                       amount,
                       moderator_comment,
                       updated_at,
                       updated_by
                FROM public.auction_manual_results
                WHERE auction_id = $1
                """,
                int(auction_id),
            )
        return dict(row) if row else None

    async def upsert_manual_result(
        self,
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
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.auction_manual_results
                    (auction_id, winner_user_id, winner_username,
                     owner_user_id, owner_username, amount, updated_by,
                     moderator_comment)
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
                int(winner_user_id) if winner_user_id is not None else None,
                (winner_username or "").strip().lstrip("@") or None,
                int(owner_user_id) if owner_user_id is not None else None,
                (owner_username or "").strip().lstrip("@") or None,
                int(amount) if amount is not None else None,
                int(updated_by),
                moderator_comment,
            )

    async def clear_manual_result(self, auction_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM public.auction_manual_results WHERE auction_id = $1",
                int(auction_id),
            )

    async def mailing_counts(self, auction_id: int) -> tuple[int, int, int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)::bigint AS total,
                       COUNT(*) FILTER (WHERE target IN ('owner', 'both'))::bigint AS owner_cnt,
                       COUNT(*) FILTER (WHERE target IN ('winner', 'both'))::bigint AS winner_cnt
                FROM public.auction_win_mailings
                WHERE auction_id = $1
                """,
                int(auction_id),
            )
        if not row:
            return 0, 0, 0
        return int(row["total"] or 0), int(row["owner_cnt"] or 0), int(row["winner_cnt"] or 0)

    async def add_mailing(
        self,
        auction_id: int,
        target: MailTarget,
        *,
        admin_user_id: int,
        admin_username: str | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.auction_win_mailings
                    (auction_id, target, sent_by_user_id, sent_by_username)
                VALUES ($1, $2, $3, $4)
                """,
                int(auction_id),
                str(target),
                int(admin_user_id),
                (admin_username or "").strip().lstrip("@") or None,
            )

    async def missed_mailings_for_day(self, target_date: date) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH day_lots AS (
                    SELECT a.auction_id,
                           a.start_time,
                           a.status,
                           a.hero_name,
                           a.card_name
                    FROM public.auctions a
                    WHERE a.start_time IS NOT NULL
                      AND (a.start_time AT TIME ZONE 'Europe/Moscow')::date = $1
                      AND a.status IN ('scheduled', 'active', 'finished', 'approved')
                ), mailed_full AS (
                    SELECT m.auction_id
                    FROM public.auction_win_mailings m
                    GROUP BY m.auction_id
                    HAVING COUNT(*) FILTER (WHERE m.target IN ('owner', 'both')) > 0
                       AND COUNT(*) FILTER (WHERE m.target IN ('winner', 'both')) > 0
                ), bids_cnt AS (
                    SELECT b.auction_id, COUNT(*)::int AS bids_count
                    FROM public.bids b
                    GROUP BY b.auction_id
                )
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
                ORDER BY d.start_time, d.auction_id
                """,
                target_date,
            )
        return [dict(row) for row in rows]

    async def exchange_batches_for_card(
        self,
        card_id: int,
        *,
        status: str = "approved",
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username,
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
                str(status),
            )
        return [dict(row) for row in rows]

    async def exchange_batch(self, batch_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT eb.*, u.username
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                WHERE eb.batch_id = $1
                """,
                int(batch_id),
            )
        return dict(row) if row else None

    async def exchange_cards(self, batch_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
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
        return [dict(row) for row in rows]

    async def exchange_print_stats(self, batch_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM public.exchange_print_stats WHERE batch_id = $1",
                int(batch_id),
            )
        return dict(row) if row else None

    async def upsert_exchange_print_stats(
        self,
        batch_id: int,
        *,
        winner_id: int | None = None,
        winner_name: str | None = None,
        price: int | None = None,
        link: str | None = None,
        updated_by: int | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.exchange_print_stats
                    (batch_id, manual_winner_id, manual_winner_name,
                     manual_price, manual_link, updated_by, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (batch_id) DO UPDATE SET
                    manual_winner_id = COALESCE(EXCLUDED.manual_winner_id, public.exchange_print_stats.manual_winner_id),
                    manual_winner_name = COALESCE(EXCLUDED.manual_winner_name, public.exchange_print_stats.manual_winner_name),
                    manual_price = COALESCE(EXCLUDED.manual_price, public.exchange_print_stats.manual_price),
                    manual_link = COALESCE(EXCLUDED.manual_link, public.exchange_print_stats.manual_link),
                    updated_by = COALESCE(EXCLUDED.updated_by, public.exchange_print_stats.updated_by),
                    updated_at = now()
                """,
                int(batch_id),
                int(winner_id) if winner_id is not None else None,
                (winner_name or "").strip().lstrip("@") or None,
                int(price) if price is not None else None,
                (link or "").strip() or None,
                int(updated_by) if updated_by is not None else None,
            )

    async def reset_exchange_print_stats(self, batch_id: int, *, updated_by: int | None = None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.exchange_print_stats
                    (batch_id, manual_winner_id, manual_winner_name,
                     manual_price, manual_link, updated_by, updated_at)
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

    async def increment_admin_thanks(self, author: str, user_id: int) -> tuple[int, int]:
        normalized = (author or "").strip().lstrip("@").lower()
        if not normalized:
            return 0, 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO public.admin_thanks_users(author, user_id, thanks_count)
                    VALUES ($1, $2, 1)
                    ON CONFLICT (author, user_id) DO NOTHING
                    RETURNING 1
                    """,
                    normalized,
                    int(user_id),
                )
                if not inserted:
                    await conn.execute(
                        """
                        UPDATE public.admin_thanks_users
                        SET thanks_count = thanks_count + 1
                        WHERE author = $1 AND user_id = $2
                        """,
                        normalized,
                        int(user_id),
                    )
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.admin_thanks_totals(author, thanks_total, users_total)
                    VALUES ($1, 1, $2)
                    ON CONFLICT (author) DO UPDATE SET
                        thanks_total = public.admin_thanks_totals.thanks_total + 1,
                        users_total = public.admin_thanks_totals.users_total + $2,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING thanks_total, users_total
                    """,
                    normalized,
                    1 if inserted else 0,
                )
        return int(row["thanks_total"] or 0), int(row["users_total"] or 0)

    async def admin_thanks_totals(self, author: str) -> tuple[int, int]:
        normalized = (author or "").strip().lstrip("@").lower()
        if not normalized:
            return 0, 0
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                WITH totals AS (
                    SELECT COALESCE(SUM(thanks_total), 0)::bigint AS total
                    FROM public.admin_thanks_totals
                    WHERE lower(trim(leading '@' FROM author)) = $1
                ), users AS (
                    SELECT COUNT(DISTINCT user_id)::bigint AS users
                    FROM public.admin_thanks_users
                    WHERE lower(trim(leading '@' FROM author)) = $1
                )
                SELECT totals.total, users.users
                FROM totals CROSS JOIN users
                """,
                normalized,
            )
        if not row:
            return 0, 0
        return int(row["total"] or 0), int(row["users"] or 0)
