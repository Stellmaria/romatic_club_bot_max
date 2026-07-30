"""User account state changes used by Telegram entry handlers."""

from __future__ import annotations

from bot.repositories.users import UserRepository
from db.pool import get_db_pool


class UserPrivateChatService:
    def __init__(self, repository: UserRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "UserPrivateChatService":
        return cls(UserRepository(await get_db_pool()))

    async def mark_opened(self, user_id: int) -> None:
        await self._repository.mark_private_chat_opened(int(user_id))

    async def mark_closed(self, user_id: int) -> None:
        await self._repository.mark_private_chat_closed(int(user_id))
