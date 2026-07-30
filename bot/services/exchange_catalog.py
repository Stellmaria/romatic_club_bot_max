from __future__ import annotations

from typing import Any, Sequence

from bot.repositories.exchange_catalog import ExchangeCatalogRepository
from db.core import get_db_pool


class ExchangeCatalogService:
    """Application boundary for read-only exchange catalog queries."""

    CARDLIKE_MODES = ("card", "deck_split")
    WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")

    def __init__(self, repository: ExchangeCatalogRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ExchangeCatalogService":
        return cls(ExchangeCatalogRepository(await get_db_pool()))

    async def approved_decks(self, deck_ids: Sequence[int]) -> list[dict[str, Any]]:
        return await self._repository.approved_decks(deck_ids)


    async def approved_lots(self) -> list[dict[str, Any]]:
        return await self._repository.approved_lots()

    async def approved_batches_by_card(self, deck_id: int, card_id: int) -> list[int]:
        return await self._repository.approved_batches_by_card(
            deck_id,
            card_id,
            modes=self.CARDLIKE_MODES,
        )

    async def deck_cards_with_counts(self, deck_id: int) -> list[dict[str, Any]]:
        return await self._repository.deck_cards_with_counts(
            deck_id,
            modes=self.CARDLIKE_MODES,
        )

    async def has_whole_deck_lot(self, deck_id: int) -> bool:
        return await self._repository.has_whole_deck_lot(
            deck_id,
            modes=self.WHOLE_DECK_MODES,
        )

    async def whole_deck_count(self, deck_id: int) -> int:
        return await self._repository.whole_deck_count(
            deck_id,
            modes=self.WHOLE_DECK_MODES,
        )

    async def approved_whole_deck_batch_ids(self, deck_id: int) -> list[int]:
        return await self._repository.approved_whole_deck_batch_ids(
            deck_id,
            mode="deck",
        )

    async def card_info(self, card_id: int) -> dict[str, Any]:
        return await self._repository.card_info(card_id)

    async def approved_cards_by_deck(self, deck_id: int) -> list[dict[str, Any]]:
        return await self._repository.approved_cards_by_deck(
            deck_id,
            modes=self.CARDLIKE_MODES,
        )

    async def card_batches(
        self,
        deck_id: int,
        card_id: int,
        *,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        return await self._repository.card_batches(
            deck_id,
            card_id,
            modes=self.CARDLIKE_MODES,
            limit=limit,
        )

    async def whole_deck_batches(
        self,
        deck_id: int,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._repository.whole_deck_batches(
            deck_id,
            modes=self.WHOLE_DECK_MODES,
            limit=limit,
        )

    async def deck_total_cards(self, deck_id: int) -> int:
        return await self._repository.deck_total_cards(deck_id)

    async def batch_items_count(self, batch_id: int) -> int:
        return await self._repository.batch_items_count(batch_id)

    async def decks_with_approved(self, deck_ids: Sequence[int]) -> list[int]:
        return await self._repository.decks_with_approved(deck_ids)
