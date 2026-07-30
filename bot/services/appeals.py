"""Application-facing user appeal operations."""

from __future__ import annotations

from typing import Any

from bot.repositories.appeals import AppealRepository
from db.pool import get_db_pool


class AppealService:
    def __init__(self, repository: AppealRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AppealService":
        return cls(AppealRepository(await get_db_pool()))

    async def create_appeal(
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
        return await self._repository.create(
            user_id=int(user_id),
            username=(username or "").strip() or None,
            topic=(topic or "").strip(),
            description=(description or "").strip(),
            participants=(participants or "").strip(),
            media_message_ids=[int(value) for value in (media_message_ids or [])],
            origin_chat_id=int(origin_chat_id),
        )

    async def get_appeal_by_id(self, appeal_id: int) -> dict[str, Any] | None:
        return await self._repository.get(int(appeal_id))

    async def get_first_pending(self) -> dict[str, Any] | None:
        return await self._repository.get_first_pending()

    async def get_next_pending(self, after_id: int) -> dict[str, Any] | None:
        return await self._repository.get_next_pending(int(after_id))

    async def set_status(
        self,
        *,
        appeal_id: int,
        status: str,
        moderator_id: int,
        moderator_username: str | None,
        comment: str | None = None,
    ) -> bool:
        normalized_comment = None if comment is None else (comment or "").strip() or None
        return await self._repository.set_status(
            appeal_id=int(appeal_id),
            status=(status or "").strip().lower(),
            moderator_id=int(moderator_id),
            moderator_username=(moderator_username or "").strip() or None,
            comment=normalized_comment,
            update_comment=comment is not None,
        )

    async def set_reply(
        self,
        appeal_id: int,
        moderator_id: int,
        moderator_username: str | None,
        reply_text: str,
    ) -> bool:
        return await self._repository.set_reply(
            appeal_id=int(appeal_id),
            moderator_id=int(moderator_id),
            moderator_username=(moderator_username or "").strip() or None,
            reply_text=(reply_text or "").strip() or None,
        )


async def _service() -> AppealService:
    return await AppealService.create()


async def create_appeal(**values: Any) -> int:
    return await (await _service()).create_appeal(**values)


async def get_appeal_by_id(appeal_id: int) -> dict[str, Any] | None:
    return await (await _service()).get_appeal_by_id(appeal_id)


async def get_first_pending() -> dict[str, Any] | None:
    return await (await _service()).get_first_pending()


async def get_next_pending(after_id: int) -> dict[str, Any] | None:
    return await (await _service()).get_next_pending(after_id)


async def set_status(**values: Any) -> bool:
    return await (await _service()).set_status(**values)


async def set_reply(
    appeal_id: int,
    moderator_id: int,
    moderator_username: str | None,
    reply_text: str,
) -> bool:
    return await (await _service()).set_reply(
        appeal_id,
        moderator_id,
        moderator_username,
        reply_text,
    )


__all__ = [
    "AppealService",
    "create_appeal",
    "get_appeal_by_id",
    "get_first_pending",
    "get_next_pending",
    "set_reply",
    "set_status",
]
