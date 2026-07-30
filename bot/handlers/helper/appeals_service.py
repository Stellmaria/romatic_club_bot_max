from __future__ import annotations

from typing import Any, List, Optional

from db.legacy import execute, fetchrow


def _row_to_dict(row: Any) -> dict[Any, Any] | dict[str, Any] | dict[str, str] | dict[bytes, bytes] | None:
    return dict(row) if row else None


def _rows_affected(execute_result: str) -> int:
    """
    asyncpg conn.execute(...) обычно возвращает строку вида:
      - "UPDATE 1"
      - "INSERT 0 1"
    Нам для bool-успеха достаточно понять, сколько строк затронуто.
    """
    try:
        # чаще всего последний токен это число
        return int((execute_result or "").split()[-1])
    except Exception:
        return 0


async def create_appeal(
        *,
        user_id: int,
        username: Optional[str],
        topic: str,
        description: str,
        participants: str,
        media_message_ids: List[int],
        origin_chat_id: int,
) -> int:
    """
    Создаёт обращение пользователя и возвращает id.

    media_message_ids: список message_id медиа в исходном чате (обычно private).
    origin_chat_id: чат-источник медиа (чтобы админы могли copy_message).
    """
    topic = (topic or "").strip()
    description = (description or "").strip()
    participants = (participants or "").strip()
    uname = (username or "").strip() or None
    media_ids = [int(x) for x in (media_message_ids or [])]

    row = await fetchrow(
        """
        INSERT INTO public.user_appeals
        (user_id, username, topic, description, participants, media_message_ids, origin_chat_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        int(user_id),
        uname,
        topic,
        description,
        participants,
        media_ids,
        int(origin_chat_id),
    )
    if not row:
        raise RuntimeError("create_appeal: INSERT не вернул id")
    return int(row["id"])


async def get_appeal_by_id(appeal_id: int) -> dict[Any, Any] | dict[str, Any] | dict[str, str] | dict[
    bytes, bytes] | None:
    row = await fetchrow(
        "SELECT * FROM public.user_appeals WHERE id = $1",
        int(appeal_id),
    )
    return _row_to_dict(row)


async def get_first_pending() -> dict[Any, Any] | dict[str, Any] | dict[str, str] | dict[bytes, bytes] | None:
    row = await fetchrow(
        """
        SELECT *
        FROM public.user_appeals
        WHERE status = 'pending'
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """
    )
    return _row_to_dict(row)


async def get_next_pending(after_id: int) -> dict[Any, Any] | dict[str, Any] | dict[str, str] | dict[
    bytes, bytes] | None:
    row = await fetchrow(
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
    return _row_to_dict(row)


async def set_status(
        *,
        appeal_id: int,
        status: str,
        moderator_id: int,
        moderator_username: str,
        comment: Optional[str] = None,
) -> bool:
    """
    Обновляет статус + модератора.
    Если comment=None, комментарий НЕ трогаем.
    Если comment задан (включая пустую строку), перезаписываем moderator_comment.
    """
    st = (status or "").strip().lower()
    mod_u = (moderator_username or "").strip() or None

    if comment is None:
        res = await execute(
            """
            UPDATE public.user_appeals
            SET status             = $2,
                moderator_id       = $3,
                moderator_username = $4
            WHERE id = $1
            """,
            int(appeal_id),
            st,
            int(moderator_id),
            mod_u,
        )
        return _rows_affected(res) > 0

    comment_clean = (comment or "").strip() or None
    res = await execute(
        """
        UPDATE public.user_appeals
        SET status             = $2,
            moderator_id       = $3,
            moderator_username = $4,
            moderator_comment  = $5
        WHERE id = $1
        """,
        int(appeal_id),
        st,
        int(moderator_id),
        mod_u,
        comment_clean,
    )
    return _rows_affected(res) > 0


async def set_reply(
        appeal_id: int,
        moderator_id: int,
        moderator_username: Optional[str],
        reply_text: str,
) -> bool:
    """
    Сохраняет текст ответа/комментария модератора.
    Статус здесь НЕ меняем (это делает set_status).
    """
    mod_u = (moderator_username or "").strip() or None
    reply_clean = (reply_text or "").strip() or None

    res = await execute(
        """
        UPDATE public.user_appeals
        SET moderator_id       = $2,
            moderator_username = $3,
            moderator_comment  = $4
        WHERE id = $1
        """,
        int(appeal_id),
        int(moderator_id),
        mod_u,
        reply_clean,
    )
    return _rows_affected(res) > 0


__all__ = [
    "create_appeal",
    "get_appeal_by_id",
    "get_first_pending",
    "get_next_pending",
    "set_status",
    "set_reply",
]
