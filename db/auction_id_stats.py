"""Read and reserve gaps in the historical auction identifier sequence."""

from __future__ import annotations

from db.core import pool_proxy as db_pool, require_db_pool


_MAX_RESULT_LIMIT = 200
_RESERVATION_LOCK_KEY = 7_611_004_207


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value or 1), _MAX_RESULT_LIMIT))


@require_db_pool
async def count_missing_auction_ids() -> int:
    """Count positive integer gaps below the current maximum auction ID."""

    async with db_pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT GREATEST(
                       COALESCE(MAX(auction_id), 0)
                       - COUNT(*) FILTER (WHERE auction_id > 0),
                       0
                   )::bigint AS missing_count
            FROM public.auctions
            """
        )
    return int(row["missing_count"] or 0) if row else 0


@require_db_pool
async def get_missing_auction_ids(limit: int = 50) -> list[int]:
    """Return the first missing positive auction IDs in ascending order."""

    safe_limit = _bounded_limit(limit)
    async with db_pool.acquire() as connection:
        rows = await connection.fetch(
            """
            WITH bounds AS (
                SELECT COALESCE(MAX(auction_id), 0)::bigint AS max_id
                FROM public.auctions
            )
            SELECT candidate::bigint AS auction_id
            FROM bounds
            CROSS JOIN LATERAL generate_series(1::bigint, bounds.max_id) AS candidate
            LEFT JOIN public.auctions AS existing
              ON existing.auction_id = candidate
            WHERE existing.auction_id IS NULL
            ORDER BY candidate
            LIMIT $1
            """,
            safe_limit,
        )
    return [int(row["auction_id"]) for row in rows]


@require_db_pool
async def reserve_first_missing_auction_id_for_stats(
    *,
    admin_user_id: int,
    admin_username: str | None,
    scan_limit: int = 200,
) -> int | None:
    """Atomically reserve the first missing ID with a visible pending row.

    A transaction-scoped advisory lock serializes competing administrators.
    The insert still uses ``ON CONFLICT DO NOTHING`` as a second race boundary.
    """

    safe_limit = _bounded_limit(scan_limit)
    username = (admin_username or "").strip().lstrip("@")
    actor = f"@{username}" if username else f"id{int(admin_user_id)}"

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock($1)",
                _RESERVATION_LOCK_KEY,
            )
            candidates = await connection.fetch(
                """
                WITH bounds AS (
                    SELECT COALESCE(MAX(auction_id), 0)::bigint AS max_id
                    FROM public.auctions
                )
                SELECT candidate::bigint AS auction_id
                FROM bounds
                CROSS JOIN LATERAL generate_series(1::bigint, bounds.max_id) AS candidate
                LEFT JOIN public.auctions AS existing
                  ON existing.auction_id = candidate
                WHERE existing.auction_id IS NULL
                ORDER BY candidate
                LIMIT $1
                """,
                safe_limit,
            )

            for candidate_row in candidates:
                candidate = int(candidate_row["auction_id"])
                inserted = await connection.fetchrow(
                    """
                    INSERT INTO public.auctions (
                        auction_id,
                        card_name,
                        hero_name,
                        image_id,
                        start_price,
                        start_time,
                        end_time,
                        status,
                        created_at,
                        currency,
                        accepted_currencies,
                        custom_offer_terms,
                        comment,
                        auction_kind,
                        proof_photo_id,
                        craft_uid_possible
                    )
                    VALUES (
                        $1,
                        $2,
                        'SYSTEM',
                        '',
                        1,
                        NOW(),
                        NOW() + INTERVAL '30 minutes',
                        'pending',
                        NOW(),
                        'алмазы',
                        ARRAY['алмазы']::text[],
                        NULL,
                        $3,
                        'standard',
                        NULL,
                        FALSE
                    )
                    ON CONFLICT (auction_id) DO NOTHING
                    RETURNING auction_id
                    """,
                    candidate,
                    f"[RESERVED ID {candidate}]",
                    f"ID reserved from statistics panel by {actor}",
                )
                if inserted is None:
                    continue

                await connection.execute(
                    """
                    INSERT INTO public.auction_owners (auction_id, user_id)
                    SELECT $1, $2
                    WHERE EXISTS (
                        SELECT 1 FROM public.users WHERE user_id = $2
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    candidate,
                    int(admin_user_id),
                )
                return candidate

    return None


__all__ = [
    "count_missing_auction_ids",
    "get_missing_auction_ids",
    "reserve_first_missing_auction_id_for_stats",
]
