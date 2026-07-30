from __future__ import annotations

from bot.repositories.exchange_submission import ExchangeSubmissionRepository
from db.pool import get_db_pool


class ExchangeSubmissionQueries:
    """Application-facing submission lookups with an injectable repository."""

    def __init__(self, repository: ExchangeSubmissionRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ExchangeSubmissionQueries":
        return cls(ExchangeSubmissionRepository(await get_db_pool()))

    async def deck_type_for_card(self, card_id: int) -> str | None:
        return await self._repository.deck_type_for_card(int(card_id))

    async def deck_type_for_deck(self, deck_id: int) -> str | None:
        return await self._repository.deck_type_for_deck(int(deck_id))

    async def deck_type_for_card_identity(
        self,
        card_name: str,
        hero_name: str,
    ) -> str | None:
        return await self._repository.deck_type_for_card_identity(
            card_name,
            hero_name,
        )

    async def latest_resource_deck_ids(self, limit: int) -> list[int]:
        return await self._repository.latest_resource_deck_ids(int(limit))

    async def deck_name(self, deck_id: int) -> str | None:
        return await self._repository.deck_name(int(deck_id))
