"""User private-chat delivery state."""

from __future__ import annotations

from db.core import execute


async def mark_user_private_chat_opened(user_id: int) -> None:
    await execute(
        """
        UPDATE public.users
        SET pm_opened = TRUE,
            first_pm_at = COALESCE(first_pm_at, NOW()),
            last_pm_at = NOW()
        WHERE user_id = $1
        """,
        int(user_id),
    )


async def mark_user_private_chat_closed(user_id: int) -> None:
    await execute(
        """
        UPDATE public.users
        SET pm_opened = FALSE,
            last_pm_at = NOW()
        WHERE user_id = $1
        """,
        int(user_id),
    )


__all__ = ["mark_user_private_chat_opened", "mark_user_private_chat_closed"]
