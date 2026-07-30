"""PostgreSQL persistence for user appeals."""

from __future__ import annotations

from typing import Any

import asyncpg


def _affected_rows(command_status: str) -> int:
    """Extract asyncpg's affected-row count from a command status string."""

    try:
        return int((command_status or "").split()[-1])
    except (IndexError, TypeError, ValueError):
        return 0


class AppealRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(
        self,
        *,
        user_id: int,
        username: str | None,
        topic: str,
        description: str,
        participants: str,
        media_message_ids: list[int],
        origin_chat_id: int,
    ) -> int:
        async with self._pool.acquire() as connection:
            appeal_id = await connection.fetchval(
                """
                INSERT INTO public.user_appeals (
                    user_id,
                    username,
                    topic,
                    description,
                    participants,
                    media_message_ids,
                    origin_chat_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                int(user_id),
                username,
                topic,
                description,
                participants,
                media_message_ids,
                int(origin_chat_id),
            )
        if appeal_id is None:
            raise RuntimeError("create_appeal: INSERT did not return an id")
        return int(appeal_id)

    async def get(self, appeal_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM public.user_appeals WHERE id = $1",
                int(appeal_id),
            )
        return dict(row) if row else None

    async def get_first_pending(self) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM public.user_appeals
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            )
        return dict(row) if row else None

    async def get_next_pending(self, after_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM public.user_appeals
                WHERE status = 'pending'
                  AND id > $1
                ORDER BY id ASC
                LIMIT 1
                """,
                int(after_id),
            )
        return dict(row) if row else None

    async def set_status(
        self,
        *,
        appeal_id: int,
        status: str,
        moderator_id: int,
        moderator_username: str | None,
        comment: str | None = None,
        update_comment: bool = False,
    ) -> bool:
        async with self._pool.acquire() as connection:
            if not update_comment:
                result = await connection.execute(
                    """
                    UPDATE public.user_appeals
                    SET status = $2,
                        moderator_id = $3,
                        moderator_username = $4
                    WHERE id = $1
                    """,
                    int(appeal_id),
                    status,
                    int(moderator_id),
                    moderator_username,
                )
            else:
                result = await connection.execute(
                    """
                    UPDATE public.user_appeals
                    SET status = $2,
                        moderator_id = $3,
                        moderator_username = $4,
                        moderator_comment = $5
                    WHERE id = $1
                    """,
                    int(appeal_id),
                    status,
                    int(moderator_id),
                    moderator_username,
                    comment,
                )
        return _affected_rows(result) > 0

    async def set_reply(
        self,
        *,
        appeal_id: int,
        moderator_id: int,
        moderator_username: str | None,
        reply_text: str | None,
    ) -> bool:
        async with self._pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE public.user_appeals
                SET moderator_id = $2,
                    moderator_username = $3,
                    moderator_comment = $4
                WHERE id = $1
                """,
                int(appeal_id),
                int(moderator_id),
                moderator_username,
                reply_text,
            )
        return _affected_rows(result) > 0


__all__ = ["AppealRepository"]
