"""Application-facing access to administrative diagnostic read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bot.repositories.admin_diagnostics import AdminDiagnosticsRepository
from db.pool import get_db_pool


class AdminDiagnosticsQueries:
    def __init__(self, repository: AdminDiagnosticsRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AdminDiagnosticsQueries":
        return cls(AdminDiagnosticsRepository(await get_db_pool()))

    async def delayed_luxury_lots(self) -> list[dict[str, Any]]:
        return await self._repository.delayed_luxury_lots()

    async def database_overview(self) -> tuple[dict[str, Any], int]:
        metadata = await self._repository.database_metadata()
        count = await self._repository.delayed_luxury_count()
        return metadata, count

    async def owners_with_multiple_future_lots(
        self,
        *,
        after: datetime,
    ) -> list[dict[str, Any]]:
        return await self._repository.owners_with_multiple_future_lots(after=after)
