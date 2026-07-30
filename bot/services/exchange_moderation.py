from __future__ import annotations

from typing import Any

from bot.repositories.exchange_moderation import ExchangeModerationRepository
from db.pool import get_db_pool


class ExchangeModerationService:
    """Application boundary for exchange moderation reads and audit writes."""

    def __init__(self, repository: ExchangeModerationRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ExchangeModerationService":
        return cls(ExchangeModerationRepository(await get_db_pool()))

    async def is_admin(self, user_id: int) -> bool:
        return await self._repository.is_admin(user_id)

    async def batch(self, batch_id: int) -> dict[str, Any] | None:
        return await self._repository.batch(batch_id)

    async def deck(self, deck_id: int) -> dict[str, Any] | None:
        return await self._repository.deck(deck_id)

    async def raw_items(self, batch_id: int) -> list[dict[str, Any]]:
        return await self._repository.raw_items(batch_id)

    async def grouped_cards(self, batch_id: int) -> list[dict[str, Any]]:
        return await self._repository.grouped_cards(batch_id)

    async def item_count(self, batch_id: int) -> int:
        return await self._repository.item_count(batch_id)

    async def pending_total(self) -> int:
        return await self._repository.pending_total()

    async def pending_batches(
        self,
        *,
        limit: int | None = None,
        include_luxury: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._repository.pending_batches(
            limit=limit,
            include_luxury=include_luxury,
        )

    async def log_admin_action(
        self,
        *,
        user_id: int,
        action_type: str,
        auction_id: int | None = None,
        details: str | None = None,
    ) -> None:
        await self._repository.log_admin_action(
            user_id=user_id,
            action_type=action_type,
            auction_id=auction_id,
            details=details,
        )


class ExchangeModerationQueries(ExchangeModerationService):
    """Compatibility query surface used by the extracted moderation router."""

    async def pending(self, *, limit: int) -> list[dict[str, Any]]:
        return await self._repository.pending(limit=max(1, min(200, int(limit))))

    async def user_flags(self, user_id: int) -> dict[str, Any]:
        return await self._repository.user_flags(int(user_id))
