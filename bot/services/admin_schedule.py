"""Application service for schedule-specific administrative reads."""

from __future__ import annotations

from bot.repositories.admin_schedule import AdminScheduleRepository
from db.pool import get_db_pool


class AdminScheduleQueries:
    def __init__(self, repository: AdminScheduleRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AdminScheduleQueries":
        return cls(AdminScheduleRepository(await get_db_pool()))

    async def last_nonempty_deck_id(self) -> int:
        return await self._repository.last_nonempty_deck_id()
