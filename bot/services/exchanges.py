from __future__ import annotations

from typing import Any, Iterable

from bot.domain.auctions import Currency
from bot.domain.auctions.workflows import ExchangeDraft
from bot.repositories.exchanges import ExchangeRepository
from db.core import get_db_pool


class ExchangeService:
    _MODES = frozenset({"card", "deck", "deck_split"})

    def __init__(self, repository: ExchangeRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ExchangeService":
        return cls(ExchangeRepository(await get_db_pool()))

    async def submit(
        self,
        *,
        user_id: int,
        deck_id: int,
        mode: str,
        currency: str,
        price: int,
        card_ids: Iterable[int],
        comment: str = "",
        proof_photo_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_mode = (mode or "card").strip().lower()
        if normalized_mode not in self._MODES:
            raise ValueError(f"unsupported exchange mode: {normalized_mode}")
        normalized_ids = tuple(int(card_id) for card_id in card_ids)
        if not normalized_ids:
            raise ValueError("exchange batch requires at least one card")
        amount = int(price)
        if amount < 0:
            raise ValueError("exchange price cannot be negative")

        draft = ExchangeDraft(
            user_id=int(user_id),
            deck_id=int(deck_id),
            mode=normalized_mode,
            currency=Currency.from_raw(currency),
            price=amount,
            card_ids=normalized_ids,
            comment=(comment or "").strip()[:2000],
            proof_photo_id=(proof_photo_id or "").strip() or "NO_PROOF",
        )
        return await self._repository.create(draft)

    async def approve(
        self,
        batch_id: int,
        *,
        moderator_id: int,
        moderator_username: str | None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        return await self._repository.moderate(
            int(batch_id),
            target_status="approved",
            moderator_id=int(moderator_id),
            moderator_username=moderator_username,
            moderator_comment=comment,
        )

    async def reject(
        self,
        batch_id: int,
        *,
        moderator_id: int,
        moderator_username: str | None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        return await self._repository.moderate(
            int(batch_id),
            target_status="rejected",
            moderator_id=int(moderator_id),
            moderator_username=moderator_username,
            moderator_comment=comment,
        )

    async def mark_posted(
        self,
        batch_id: int,
        *,
        chat_id: int,
        message_id: int,
    ) -> dict[str, Any]:
        return await self._repository.mark_posted(
            int(batch_id),
            chat_id=int(chat_id),
            message_id=int(message_id),
        )

    async def claim_for_post(self, batch_id: int) -> dict[str, Any]:
        return await self._repository.claim_for_post(int(batch_id))

    async def release_post_claim(self, batch_id: int) -> bool:
        return await self._repository.release_post_claim(int(batch_id))

    async def delete(
        self,
        batch_id: int,
        *,
        moderator_id: int | None = None,
        moderator_username: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        return await self._repository.soft_delete(
            int(batch_id),
            moderator_id=moderator_id,
            moderator_username=moderator_username,
            moderator_comment=comment,
        )

    async def get(self, batch_id: int) -> dict[str, Any]:
        return await self._repository.get(int(batch_id))

    async def items(self, batch_id: int) -> list[dict[str, Any]]:
        return await self._repository.items(int(batch_id))

    async def pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repository.pending(limit=limit)
