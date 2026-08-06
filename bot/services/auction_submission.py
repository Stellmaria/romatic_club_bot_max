"""Catalog access used by auction submission pricing and validation."""

from __future__ import annotations

from typing import Any

from bot.repositories.auction_submission import AuctionSubmissionRepository
from db.pool import get_db_pool


class AuctionSubmissionCatalogService:
    def __init__(self, repository: AuctionSubmissionRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionSubmissionCatalogService":
        return cls(AuctionSubmissionRepository(await get_db_pool()))

    async def card(self, card_id: int) -> dict[str, Any] | None:
        return await self._repository.card(int(card_id))

    async def cards_for_deck(self, deck_id: int) -> list[dict[str, Any]]:
        return await self._repository.cards_for_deck(int(deck_id))

    async def future_empty_decks(self) -> list[dict[str, Any]]:
        return await self._repository.future_empty_decks()

    async def future_empty_deck(self, deck_id: int) -> dict[str, Any] | None:
        return await self._repository.future_empty_deck(int(deck_id))

    async def obtain_type(self, card_id: int) -> str | None:
        return await self._repository.obtain_type(int(card_id))

    async def deck_type_for_card(self, card_id: int) -> str | None:
        return await self._repository.deck_type_for_card(int(card_id))

    async def deck_type_for_deck(self, deck_id: int) -> str | None:
        return await self._repository.deck_type_for_deck(int(deck_id))

    async def deck_type_for_identity(self, *, card_name: str, hero_name: str) -> str | None:
        return await self._repository.deck_type_for_identity(
            card_name=card_name,
            hero_name=hero_name,
        )
