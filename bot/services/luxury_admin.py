"""Application service for privileged Luxury-status maintenance."""

from __future__ import annotations

from typing import Any

from bot.repositories.luxury_admin import LuxuryAdminRepository
from db.pool import get_db_pool


class LuxuryAdminService:
    def __init__(self, repository: LuxuryAdminRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "LuxuryAdminService":
        return cls(LuxuryAdminRepository(await get_db_pool()))

    async def find_user(self, reference: str) -> dict[str, Any] | None:
        normalized = (reference or "").strip()
        if normalized.startswith("@"):
            return await self._repository.find_by_username(normalized)
        try:
            user_id = int(normalized)
        except ValueError as error:
            raise ValueError("reference must be @username or numeric user_id") from error
        return await self._repository.find_by_id(user_id)

    async def remove_luxury(self, *, user: dict[str, Any], actor_id: int) -> None:
        await self._repository.remove_luxury(
            user_id=int(user["user_id"]),
            actor_id=int(actor_id),
            username=user.get("username"),
        )
