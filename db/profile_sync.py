"""Atomic Telegram profile persistence with username ownership transfer."""

from __future__ import annotations

from db.core import pool_proxy as db_pool, require_db_pool
from db.performance import track_database_query

# Telegram usernames are globally unique. Serializing the tiny profile mutation
# transaction prevents two concurrent updates from both observing the same stale
# owner and racing the case-insensitive unique index.
_PROFILE_SYNC_LOCK_KEY = 4_520_260_805


def _normalize_profile_username(username: str | None) -> str:
    return (username or "").strip().lstrip("@")


@require_db_pool
async def sync_user_profile(user_id: int, username: str, full_name: str) -> bool:
    """Upsert one Telegram profile and transfer a reused username atomically.

    Telegram can reassign a username after its previous owner changes or removes
    it. The database keeps a case-insensitive unique index, so the stale owner is
    cleared before the current profile is written. ``RETURNING`` still reports
    whether the current user's row changed physically.
    """

    normalized_user_id = int(user_id)
    normalized_username = _normalize_profile_username(username)
    normalized_full_name = (full_name or "").strip()

    async with db_pool.acquire() as conn:
        async with track_database_query("users.profile.sync", pool=db_pool.pool):
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    _PROFILE_SYNC_LOCK_KEY,
                )
                if normalized_username:
                    await conn.execute(
                        """
                        UPDATE public.users
                        SET username = NULL
                        WHERE user_id <> $1
                          AND username IS NOT NULL
                          AND lower(username) = lower($2)
                        """,
                        normalized_user_id,
                        normalized_username,
                    )
                changed_user_id = await conn.fetchval(
                    """
                    INSERT INTO public.users (user_id, username, full_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE
                    SET username = EXCLUDED.username,
                        full_name = EXCLUDED.full_name
                    WHERE users.username IS DISTINCT FROM EXCLUDED.username
                       OR users.full_name IS DISTINCT FROM EXCLUDED.full_name
                    RETURNING user_id
                    """,
                    normalized_user_id,
                    normalized_username,
                    normalized_full_name,
                )

    return changed_user_id is not None


__all__ = ["sync_user_profile"]
