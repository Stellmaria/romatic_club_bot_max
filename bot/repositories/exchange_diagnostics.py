from __future__ import annotations

from typing import Any, Sequence

import asyncpg


class ExchangeDiagnosticsRepository:
    """Read models and atomic admin updates for exchange diagnostics."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def is_admin(self, user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM public.admins WHERE user_id = $1)",
                    int(user_id),
                )
            )

    async def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, username, full_name FROM public.users WHERE user_id = $1",
                int(user_id),
            )
        return dict(row) if row else None

    async def user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = (username or "").strip().lstrip("@")
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

    async def deck(self, deck_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, name AS deck_name, deck_type FROM public.decks WHERE id = $1",
                int(deck_id),
            )
        return dict(row) if row else None

    async def batch(self, batch_id: int) -> dict[str, Any] | None:
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

    async def batch_items(self, batch_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT item_id, batch_id, card_id, card_name, hero_name, created_at
                FROM public.exchange_items
                WHERE batch_id = $1
                ORDER BY item_id
                """,
                int(batch_id),
            )
        return [dict(row) for row in rows]

    async def mark_batches_dispatched(
        self,
        batch_ids: Sequence[int],
        *,
        winner_id: int,
        winner_username: str | None,
        admin_id: int,
    ) -> None:
        ids = [int(batch_id) for batch_id in batch_ids]
        if not ids:
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE public.exchange_batches
                    SET manual_winner_id = $2,
                        manual_winner_username = $3,
                        manual_set_by = $4,
                        manual_set_at = NOW()
                    WHERE batch_id = ANY($1::bigint[])
                    """,
                    ids,
                    int(winner_id),
                    (winner_username or "").strip().lstrip("@") or None,
                    int(admin_id),
                )
                await conn.execute(
                    """
                    UPDATE public.exchange_batches
                    SET manual_sent_at = COALESCE(manual_sent_at, NOW())
                    WHERE batch_id = ANY($1::bigint[])
                    """,
                    ids,
                )

    async def standard_lots_by_owner(self, owner_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.auction_id, a.card_name, a.hero_name, a.status,
                       a.start_time, a.end_time, a.auction_kind
                FROM public.auctions a
                JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                WHERE ao.user_id = $1
                  AND COALESCE(a.auction_kind, 'standard') = 'standard'
                ORDER BY a.start_time DESC
                LIMIT 80
                """,
                int(owner_id),
            )
        return [dict(row) for row in rows]

    async def user_card_stats(self, user_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(eb.status, '') AS status, COUNT(*)::int AS cards_cnt
                FROM public.exchange_items ei
                JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                WHERE eb.user_id = $1
                  AND COALESCE(eb.status, '') <> 'deleted'
                  AND eb.deleted_at IS NULL
                GROUP BY COALESCE(eb.status, '')
                ORDER BY cards_cnt DESC
                """,
                int(user_id),
            )
        return [dict(row) for row in rows]

    async def user_batch_stats(self, user_id: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(status, '') AS status, COUNT(*)::int AS batches_cnt
                FROM public.exchange_batches
                WHERE user_id = $1
                  AND COALESCE(status, '') <> 'deleted'
                  AND deleted_at IS NULL
                GROUP BY COALESCE(status, '')
                ORDER BY batches_cnt DESC
                """,
                int(user_id),
            )
        return [dict(row) for row in rows]

    async def recent_user_batches(self, user_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT batch_id, status, deck_id, mode, price, currency, created_at
                FROM public.exchange_batches
                WHERE user_id = $1
                  AND COALESCE(status, '') <> 'deleted'
                  AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $2
                """,
                int(user_id),
                max(1, min(int(limit), 100)),
            )
        return [dict(row) for row in rows]

    async def dump_group_count(self) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM (
                    SELECT 1
                    FROM public.exchange_batches eb
                    WHERE eb.deleted_at IS NULL
                      AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
                    GROUP BY eb.user_id,
                             COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF')
                ) grouped
                """
            )
        return int(value or 0)

    async def dump_groups(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH groups AS (
                    SELECT eb.user_id,
                           MAX(u.username) AS username,
                           COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') AS proof,
                           array_agg(eb.batch_id ORDER BY eb.batch_id) AS batch_ids,
                           COUNT(*)::int AS batches_cnt,
                           MAX(eb.batch_id)::int AS last_batch_id
                    FROM public.exchange_batches eb
                    LEFT JOIN public.users u ON u.user_id = eb.user_id
                    WHERE eb.deleted_at IS NULL
                      AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
                    GROUP BY eb.user_id, proof
                    ORDER BY last_batch_id DESC
                    LIMIT $1 OFFSET $2
                ),
                items_total AS (
                    SELECT g.user_id, g.proof, COUNT(*)::int AS items_total
                    FROM groups g
                    JOIN public.exchange_batches eb
                      ON eb.user_id = g.user_id
                     AND COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') = g.proof
                    JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                    WHERE eb.deleted_at IS NULL
                      AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
                    GROUP BY g.user_id, g.proof
                ),
                cards AS (
                    SELECT g.user_id,
                           g.proof,
                           COALESCE(c.card_id, ei.card_id) AS card_id,
                           COALESCE(c.hero_name, ei.hero_name) AS hero_name,
                           COALESCE(c.card_name, ei.card_name) AS card_name,
                           COUNT(*)::int AS qty
                    FROM groups g
                    JOIN public.exchange_batches eb
                      ON eb.user_id = g.user_id
                     AND COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') = g.proof
                    JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                    LEFT JOIN public.cards c ON c.card_id = ei.card_id
                    WHERE eb.deleted_at IS NULL
                      AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
                    GROUP BY 1, 2, 3, 4, 5
                )
                SELECT g.user_id,
                       g.username,
                       g.proof,
                       g.batch_ids,
                       g.batches_cnt,
                       COALESCE(it.items_total, 0)::int AS items_total,
                       COALESCE(
                           json_agg(
                               json_build_object(
                                   'card_id', c.card_id,
                                   'hero_name', c.hero_name,
                                   'card_name', c.card_name,
                                   'qty', c.qty
                               ) ORDER BY c.qty DESC, c.hero_name NULLS LAST, c.card_name
                           ) FILTER (WHERE c.card_id IS NOT NULL OR c.card_name IS NOT NULL),
                           '[]'::json
                       ) AS cards
                FROM groups g
                LEFT JOIN items_total it ON it.user_id = g.user_id AND it.proof = g.proof
                LEFT JOIN cards c ON c.user_id = g.user_id AND c.proof = g.proof
                GROUP BY g.user_id, g.username, g.proof, g.batch_ids,
                         g.batches_cnt, it.items_total, g.last_batch_id
                ORDER BY g.last_batch_id DESC
                """,
                max(1, min(int(limit), 100)),
                max(0, int(offset)),
            )
        return [dict(row) for row in rows]

    async def duplicate_user_cards(
        self,
        *,
        user_id: int | None = None,
        card_id: int | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ex AS (
                    SELECT eb.user_id::bigint AS user_id,
                           ei.card_id::int AS card_id,
                           COUNT(*)::int AS ex_items_cnt,
                           COUNT(DISTINCT eb.batch_id)::int AS ex_batches_cnt,
                           array_agg(DISTINCT eb.batch_id ORDER BY eb.batch_id) AS ex_batch_ids
                    FROM public.exchange_batches eb
                    JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                    WHERE COALESCE(eb.status, 'pending') IN ('pending', 'approved', 'active')
                      AND eb.deleted_at IS NULL
                      AND ei.card_id IS NOT NULL
                    GROUP BY eb.user_id, ei.card_id
                ),
                std AS (
                    SELECT ao.user_id::bigint AS user_id,
                           a.card_id::int AS card_id,
                           COUNT(DISTINCT a.auction_id)::int AS std_lots_cnt,
                           array_agg(DISTINCT a.auction_id ORDER BY a.auction_id) AS std_auction_ids
                    FROM public.auctions a
                    JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                    WHERE COALESCE(a.auction_kind, 'standard') = 'standard'
                      AND a.status IN ('pending', 'approved', 'scheduled', 'active')
                      AND a.card_id IS NOT NULL
                    GROUP BY ao.user_id, a.card_id
                )
                SELECT ex.user_id,
                       u.username,
                       ex.card_id,
                       c.hero_name,
                       c.card_name,
                       c.deck_id,
                       ex.ex_items_cnt,
                       ex.ex_batches_cnt,
                       ex.ex_batch_ids,
                       std.std_lots_cnt,
                       std.std_auction_ids
                FROM ex
                JOIN std USING (user_id, card_id)
                LEFT JOIN public.users u ON u.user_id = ex.user_id
                LEFT JOIN public.cards c ON c.card_id = ex.card_id
                WHERE ($1::bigint IS NULL OR ex.user_id = $1)
                  AND ($2::int IS NULL OR ex.card_id = $2)
                ORDER BY (ex.ex_items_cnt + std.std_lots_cnt) DESC,
                         ex.user_id, ex.card_id
                LIMIT $3
                """,
                int(user_id) if user_id is not None else None,
                int(card_id) if card_id is not None else None,
                max(1, min(int(limit), 500)),
            )
        return [dict(row) for row in rows]

    async def unsent_for_winner(
        self,
        *,
        username: str,
        user_id: int | None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.status,
                       eb.deck_id,
                       COALESCE(NULLIF(eb.mode, ''), '—') AS mode,
                       COALESCE(eb.manual_price, eb.price) AS price,
                       COALESCE(NULLIF(eb.currency, ''), 'алмазы') AS currency,
                       eb.created_at,
                       COUNT(ei.item_id)::int AS items_count
                FROM public.exchange_batches eb
                LEFT JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE eb.deleted_at IS NULL
                  AND eb.manual_sent_at IS NULL
                  AND (
                    eb.manual_winner_id = COALESCE($1::bigint, -1)
                    OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = lower($2)
                  )
                GROUP BY eb.batch_id
                ORDER BY eb.created_at DESC NULLS LAST, eb.batch_id DESC
                LIMIT $3
                """,
                int(user_id) if user_id is not None else None,
                (username or "").strip().lstrip("@"),
                max(1, min(int(limit), 500)),
            )
        return [dict(row) for row in rows]

    async def has_lots_for_winner(self, *, username: str, user_id: int | None) -> bool:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM public.exchange_batches eb
                    WHERE eb.deleted_at IS NULL
                      AND (
                        eb.manual_winner_id = COALESCE($1::bigint, -1)
                        OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = lower($2)
                      )
                )
                """,
                int(user_id) if user_id is not None else None,
                (username or "").strip().lstrip("@"),
            )
        return bool(value)

    async def unsent_batches(self, *, deck_id: int | None, limit: int = 400) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT eb.batch_id,
                       eb.user_id,
                       u.username AS owner_username,
                       eb.deck_id,
                       COALESCE(NULLIF(eb.mode, ''), '—') AS mode,
                       eb.status,
                       eb.created_at,
                       eb.manual_winner_id,
                       eb.manual_winner_username,
                       eb.manual_sent_at,
                       COUNT(ei.item_id)::int AS items_count
                FROM public.exchange_batches eb
                LEFT JOIN public.users u ON u.user_id = eb.user_id
                LEFT JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                WHERE eb.status = 'approved'
                  AND eb.deleted_at IS NULL
                  AND eb.manual_sent_at IS NULL
                  AND ($1::int IS NULL OR eb.deck_id = $1)
                GROUP BY eb.batch_id, u.username
                ORDER BY eb.created_at ASC NULLS LAST, eb.batch_id ASC
                LIMIT $2
                """,
                int(deck_id) if deck_id is not None else None,
                max(1, min(int(limit), 1000)),
            )
        return [dict(row) for row in rows]

    async def users_by_usernames(self, usernames: Sequence[str]) -> dict[str, int]:
        normalized = sorted({str(name).strip().lstrip("@").lower() for name in usernames if str(name).strip()})
        if not normalized:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, lower(username) AS uname
                FROM public.users
                WHERE lower(username) = ANY($1::text[])
                """,
                normalized,
            )
        return {str(row["uname"]).strip().lower(): int(row["user_id"]) for row in rows}

    async def assigned_items_for_winners(
        self,
        usernames: Sequence[str],
        user_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        unames = [str(name).strip().lstrip("@").lower() for name in usernames]
        uids = [int(user_id) for user_id in user_ids]
        if not unames or len(unames) != len(uids):
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH w AS (
                    SELECT unnest($1::text[]) AS uname,
                           unnest($2::bigint[]) AS uid
                ),
                card_items AS (
                    SELECT w.uname,
                           lower(regexp_replace(
                               replace(trim(COALESCE(NULLIF(ei.card_name, ''), c.card_name)), 'ё', 'е'),
                               '\\s+', ' ', 'g'
                           )) AS card_norm,
                           (eb.manual_sent_at IS NOT NULL) AS is_sent,
                           COUNT(*)::int AS qty
                    FROM public.exchange_batches eb
                    JOIN w ON (
                        eb.manual_winner_id = w.uid
                        OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = w.uname
                    )
                    JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                    LEFT JOIN public.cards c ON c.card_id = ei.card_id
                    WHERE eb.deleted_at IS NULL
                      AND eb.status = 'approved'
                      AND eb.mode IN ('card', 'deck_split')
                    GROUP BY w.uname, card_norm, is_sent
                ),
                deck_items AS (
                    SELECT w.uname,
                           lower(regexp_replace(
                               replace(trim(COALESCE(NULLIF(d.name, ''), eb.deck_id::text || ' колода')), 'ё', 'е'),
                               '\\s+', ' ', 'g'
                           )) AS card_norm,
                           (eb.manual_sent_at IS NOT NULL) AS is_sent,
                           COUNT(*)::int AS qty
                    FROM public.exchange_batches eb
                    JOIN w ON (
                        eb.manual_winner_id = w.uid
                        OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = w.uname
                    )
                    LEFT JOIN public.decks d ON d.id = eb.deck_id
                    WHERE eb.deleted_at IS NULL
                      AND eb.status = 'approved'
                      AND eb.mode = 'deck'
                    GROUP BY w.uname, card_norm, is_sent
                )
                SELECT * FROM card_items
                UNION ALL
                SELECT * FROM deck_items
                """,
                unames,
                uids,
            )
        return [dict(row) for row in rows]
