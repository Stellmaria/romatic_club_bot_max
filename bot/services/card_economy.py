"""Application service for card-economy read models."""

from __future__ import annotations

from typing import Any

from bot.repositories.card_economy import CardEconomyRepository
from db.pool import get_db_pool


class CardEconomyService:
    def __init__(self, repository: CardEconomyRepository):
        self._repository = repository

    @classmethod
    async def from_runtime(cls) -> "CardEconomyService":
        return cls(CardEconomyRepository(await get_db_pool()))

    async def luxury_top(
        self,
        *,
        limit: int,
        offset: int,
        rarity: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repository.luxury_top(
            limit=limit,
            offset=offset,
            rarity=rarity,
        )

    async def auction_core(self, auction_id: int) -> dict[str, Any] | None:
        return await self._repository.auction_core(auction_id)

    async def auction_owner_usernames(self, auction_id: int) -> list[str]:
        return await self._repository.auction_owner_usernames(auction_id)

    async def fallback_winner(
        self,
        auction_id: int,
    ) -> tuple[str | None, int | None]:
        return await self._repository.fallback_winner(auction_id)


__all__ = ["CardEconomyService"]
