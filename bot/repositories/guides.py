"""Persistence boundary for auction guide counters and rankings."""

from __future__ import annotations

import asyncio

import asyncpg


_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()


class GuideThanksRepository:
    """Store guide reactions without leaking SQL into Telegram handlers."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Keep compatibility with installations predating the schema bootstrap."""

        global _SCHEMA_READY
        if _SCHEMA_READY:
            return

        async with _SCHEMA_LOCK:
            if _SCHEMA_READY:
                return
            async with self._pool.acquire() as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.guides_thanks (
                        user_id BIGINT PRIMARY KEY,
                        thanks_count INTEGER NOT NULL DEFAULT 0,
                        last_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

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
                        PRIMARY KEY (author, user_id)
                    );
                    """
                )
            _SCHEMA_READY = True

    async def totals(self) -> tuple[int, int]:
        await self.ensure_schema()
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT COALESCE(SUM(thanks_count), 0) AS total,
                       COUNT(*) AS users
                FROM public.guides_thanks
                """
            )
        return int(row["total"] or 0), int(row["users"] or 0)

    async def increment(
        self,
        *,
        user_id: int,
        author: str | None = None,
    ) -> tuple[int, int]:
        await self.ensure_schema()
        normalized_author = self.normalize_author(author)

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO public.guides_thanks (user_id, thanks_count)
                    VALUES ($1, 1)
                    ON CONFLICT (user_id) DO UPDATE SET
                        thanks_count = public.guides_thanks.thanks_count + 1,
                        last_at = CURRENT_TIMESTAMP
                    """,
                    int(user_id),
                )
                if normalized_author:
                    await self._increment_admin_on_connection(
                        connection,
                        author=normalized_author,
                        user_id=int(user_id),
                    )
                row = await connection.fetchrow(
                    """
                    SELECT COALESCE(SUM(thanks_count), 0) AS total,
                           COUNT(*) AS users
                    FROM public.guides_thanks
                    """
                )
        return int(row["total"] or 0), int(row["users"] or 0)

    async def increment_admin(self, *, author: str, user_id: int) -> None:
        await self.ensure_schema()
        normalized_author = self.normalize_author(author)
        if not normalized_author:
            return
        async with self._pool.acquire() as connection:
            await self._increment_admin_on_connection(
                connection,
                author=normalized_author,
                user_id=int(user_id),
            )

    async def reset(self) -> None:
        await self.ensure_schema()
        async with self._pool.acquire() as connection:
            await connection.execute("TRUNCATE TABLE public.guides_thanks")

    async def admin_page(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[str, int, int]], int]:
        await self.ensure_schema()
        requested_page = max(0, int(page))
        size = max(1, int(page_size))

        async with self._pool.acquire() as connection:
            total_items = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT lower(trim(leading '@' FROM author))
                        FROM public.admin_thanks_totals
                        GROUP BY 1
                    ) AS normalized_authors
                    """
                )
                or 0
            )
            total_pages = max(1, (total_items + size - 1) // size)
            selected_page = min(requested_page, total_pages - 1)
            rows = await connection.fetch(
                """
                WITH totals AS (
                    SELECT lower(trim(leading '@' FROM author)) AS author,
                           SUM(thanks_total)::BIGINT AS thanks_total
                    FROM public.admin_thanks_totals
                    GROUP BY 1
                ), users AS (
                    SELECT lower(trim(leading '@' FROM author)) AS author,
                           COUNT(DISTINCT user_id)::BIGINT AS users_total
                    FROM public.admin_thanks_users
                    GROUP BY 1
                )
                SELECT totals.author,
                       totals.thanks_total,
                       COALESCE(users.users_total, 0) AS users_total
                FROM totals
                LEFT JOIN users USING (author)
                ORDER BY totals.thanks_total DESC,
                         COALESCE(users.users_total, 0) DESC,
                         totals.author ASC
                LIMIT $1 OFFSET $2
                """,
                size,
                selected_page * size,
            )

        data = [
            (str(row["author"]), int(row["thanks_total"]), int(row["users_total"])) for row in rows
        ]
        return data, total_pages

    async def is_luxury_user(self, user_id: int) -> bool:
        async with self._pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT is_luxury FROM public.users WHERE user_id = $1",
                int(user_id),
            )
        return bool(value)

    @staticmethod
    def normalize_author(author: str | None) -> str:
        return (author or "").strip().lstrip("@").lower()

    @staticmethod
    async def _increment_admin_on_connection(
        connection: asyncpg.Connection,
        *,
        author: str,
        user_id: int,
    ) -> None:
        await connection.execute(
            """
            WITH inserted_user AS (
                INSERT INTO public.admin_thanks_users (author, user_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                RETURNING 1
            )
            INSERT INTO public.admin_thanks_totals (
                author,
                thanks_total,
                users_total
            )
            VALUES ($1, 1, COALESCE((SELECT COUNT(*) FROM inserted_user), 0))
            ON CONFLICT (author) DO UPDATE SET
                thanks_total = public.admin_thanks_totals.thanks_total + 1,
                users_total = public.admin_thanks_totals.users_total
                    + COALESCE((SELECT COUNT(*) FROM inserted_user), 0),
                updated_at = CURRENT_TIMESTAMP
            """,
            author,
            int(user_id),
        )
