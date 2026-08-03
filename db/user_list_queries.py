"""Bounded SQL pages for administrative user lists.

Every public function performs exactly one PostgreSQL query, returns at most the
requested page size, and exposes an opaque cursor for the next page. No caller
needs to load or sort the complete users/admins/trusted data set in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db.core import pool_proxy as db_pool, require_db_pool
from db.performance import track_database_query

_MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageCursor:
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserListPage:
    rows: tuple[dict[str, Any], ...]
    next_cursor: PageCursor | None


def _page_size(limit: int) -> int:
    return min(_MAX_PAGE_SIZE, max(1, int(limit)))


def _finish_page(
    raw_rows: list[Any],
    *,
    limit: int,
    cursor_fields: tuple[str, ...],
) -> UserListPage:
    has_more = len(raw_rows) > limit
    visible = [dict(row) for row in raw_rows[:limit]]
    next_cursor: PageCursor | None = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = PageCursor(tuple(str(last[field]) for field in cursor_fields))
    return UserListPage(tuple(visible), next_cursor)


@require_db_pool
async def list_users_page(
    *,
    limit: int = 20,
    after_user_id: int | None = None,
) -> UserListPage:
    page_size = _page_size(limit)
    async with db_pool.acquire() as conn:
        async with track_database_query("admin.users.page", pool=db_pool.pool):
            rows = await conn.fetch(
                """
                SELECT user_id, username, is_luxury
                FROM public.users
                WHERE $1::bigint IS NULL OR user_id > $1
                ORDER BY user_id
                LIMIT $2
                """,
                after_user_id,
                page_size + 1,
            )
    return _finish_page(rows, limit=page_size, cursor_fields=("user_id",))


@require_db_pool
async def list_admins_page(
    owner_ids: list[int],
    *,
    limit: int = 20,
    after_owner_rank: int | None = None,
    after_user_id: int | None = None,
) -> UserListPage:
    page_size = _page_size(limit)
    normalized_owner_ids = sorted({int(user_id) for user_id in owner_ids})
    async with db_pool.acquire() as conn:
        async with track_database_query("admin.admins.page", pool=db_pool.pool):
            rows = await conn.fetch(
                """
                WITH owner_ids AS (
                    SELECT DISTINCT unnest($1::bigint[]) AS user_id
                ),
                combined AS (
                    SELECT
                        a.user_id,
                        COALESCE(NULLIF(a.username, ''), u.username) AS username,
                        EXISTS (
                            SELECT 1 FROM owner_ids o WHERE o.user_id = a.user_id
                        ) AS is_owner
                    FROM public.admins a
                    LEFT JOIN public.users u USING (user_id)

                    UNION ALL

                    SELECT
                        o.user_id,
                        u.username,
                        TRUE AS is_owner
                    FROM owner_ids o
                    LEFT JOIN public.users u USING (user_id)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.admins a WHERE a.user_id = o.user_id
                    )
                ),
                ranked AS (
                    SELECT
                        user_id,
                        username,
                        is_owner,
                        CASE WHEN is_owner THEN 0 ELSE 1 END AS owner_rank
                    FROM combined
                )
                SELECT user_id, username, is_owner, owner_rank
                FROM ranked
                WHERE $2::smallint IS NULL
                   OR owner_rank > $2
                   OR (owner_rank = $2 AND user_id > $3)
                ORDER BY owner_rank, user_id
                LIMIT $4
                """,
                normalized_owner_ids,
                after_owner_rank,
                after_user_id,
                page_size + 1,
            )
    return _finish_page(
        rows,
        limit=page_size,
        cursor_fields=("owner_rank", "user_id"),
    )


@require_db_pool
async def list_trusted_users_page(
    *,
    limit: int = 20,
    after_username: str | None = None,
    after_user_id: int | None = None,
) -> UserListPage:
    page_size = _page_size(limit)
    normalized_cursor = (
        after_username.strip().lstrip("@").lower() if after_username else None
    )
    cursor_user_id = int(after_user_id or 0)
    async with db_pool.acquire() as conn:
        async with track_database_query("admin.trusted.page", pool=db_pool.pool):
            rows = await conn.fetch(
                """
                WITH registered AS (
                    SELECT
                        lower(ltrim(u.username, '@')) AS sort_username,
                        u.username,
                        u.user_id,
                        u.is_luxury
                    FROM public.users u
                    WHERE u.is_trusted = TRUE
                      AND NULLIF(ltrim(u.username, '@'), '') IS NOT NULL
                ),
                allow_list AS (
                    SELECT DISTINCT ON (lower(ltrim(t.username, '@')))
                        lower(ltrim(t.username, '@')) AS sort_username,
                        ltrim(t.username, '@') AS username
                    FROM public.trusted_usernames t
                    WHERE NULLIF(ltrim(t.username, '@'), '') IS NOT NULL
                    ORDER BY lower(ltrim(t.username, '@')), ltrim(t.username, '@')
                ),
                combined AS (
                    SELECT
                        sort_username,
                        username,
                        user_id,
                        is_luxury,
                        user_id AS sort_user_id
                    FROM registered

                    UNION ALL

                    SELECT
                        a.sort_username,
                        a.username,
                        NULL::bigint AS user_id,
                        NULL::boolean AS is_luxury,
                        0::bigint AS sort_user_id
                    FROM allow_list a
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM registered r
                        WHERE r.sort_username = a.sort_username
                    )
                )
                SELECT
                    username,
                    user_id,
                    is_luxury,
                    sort_username,
                    sort_user_id
                FROM combined
                WHERE $1::text IS NULL
                   OR sort_username > $1
                   OR (sort_username = $1 AND sort_user_id > $2)
                ORDER BY sort_username, sort_user_id
                LIMIT $3
                """,
                normalized_cursor,
                cursor_user_id,
                page_size + 1,
            )
    return _finish_page(
        rows,
        limit=page_size,
        cursor_fields=("sort_username", "sort_user_id"),
    )


__all__ = [
    "PageCursor",
    "UserListPage",
    "list_admins_page",
    "list_trusted_users_page",
    "list_users_page",
]
