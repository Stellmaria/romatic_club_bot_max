"""Application operations for card and preset subscriptions."""

from __future__ import annotations

from typing import Any

from bot.repositories.card_subscriptions import CardSubscriptionsRepository
from db.pool import get_db_pool


class CardSubscriptionsService:
    """Keep subscription persistence and Telegram presentation separated."""

    def __init__(self, repository: CardSubscriptionsRepository):
        self._repository = repository

    @classmethod
    async def from_runtime(cls) -> "CardSubscriptionsService":
        return cls(CardSubscriptionsRepository(await get_db_pool()))

    async def list_presets(self) -> list[dict[str, Any]]:
        return await self._repository.list_presets()

    async def toggle_preset(self, user_id: int, key: str) -> tuple[bool, str]:
        state = await self._repository.toggle_preset(user_id, key)
        if state is None:
            return False, "Пресет не найден"
        if state:
            return True, "Подключено"
        return False, "Отключено"

    async def unsubscribe_preset(self, user_id: int, key: str) -> bool:
        return await self._repository.unsubscribe_preset_by_key(user_id, key)

    async def card_metadata(
        self,
        card_ids: list[int],
    ) -> dict[int, dict[str, Any]]:
        return await self._repository.card_metadata(card_ids)

    async def confirm_all(self, user_id: int) -> int:
        return await self._repository.confirm_all(user_id)


__all__ = ["CardSubscriptionsService"]
