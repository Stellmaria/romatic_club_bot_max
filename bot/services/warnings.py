"""Warning reporting and retention use cases."""

from __future__ import annotations

from typing import Any

from bot.repositories.warnings import WarningRepository
from db.pool import get_db_pool


class WarningService:
    def __init__(self, repository: WarningRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "WarningService":
        return cls(WarningRepository(await get_db_pool()))

    async def list_users_with_warnings(self) -> list[dict[str, Any]]:
        return await self._repository.list_users_with_warnings()

    async def list_deleted_bid_warnings(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repository.list_deleted_bid_warnings(limit=limit)

    async def resolve_user_id(self, value: str | None) -> int | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if normalized.startswith("@"):
            username = normalized.lstrip("@").strip()
            if not username:
                return None
            return await self._repository.find_user_id_by_username(username)
        return int(normalized) if normalized.isdigit() else None

    async def prune_old(
        self,
        *,
        maximum_warning_count: int,
        age_days: int,
        target_user_id: int | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, int]]:
        return await self._repository.prune_old(
            maximum_warning_count=maximum_warning_count,
            age_days=age_days,
            target_user_id=target_user_id,
            dry_run=dry_run,
        )

    async def count_warnings(self, user_id: int) -> int:
        return await self._repository.count_warnings(user_id)


__all__ = ["WarningService"]
