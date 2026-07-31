from __future__ import annotations

from typing import Any

from bot.domain.auctions import Currency
from bot.repositories.exchange_editor import ApprovedExchangeEditorRepository
from db.core import get_db_pool


class ApprovedExchangeEditorService:
    _MODES = frozenset({"card", "deck", "deck_split"})

    def __init__(self, repository: ApprovedExchangeEditorRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "ApprovedExchangeEditorService":
        return cls(ApprovedExchangeEditorRepository(await get_db_pool()))

    async def get(self, batch_id: int) -> dict[str, Any]:
        return await self._repository.get(int(batch_id))

    async def set_mode(self, batch_id: int, mode: str) -> dict[str, Any]:
        normalized = str(mode or "").strip().lower()
        if normalized not in self._MODES:
            raise ValueError("Неизвестный режим биржевого лота.")

        if normalized == "deck":
            batch = await self.get(int(batch_id))
            deck_id = int(batch.get("deck_id") or 0)
            expected = await self._repository.deck_card_count(deck_id)
            actual = await self._repository.batch_card_count(int(batch_id))
            if expected <= 0 or actual != expected:
                raise ValueError(
                    "Нельзя включить режим «колода целиком»: "
                    f"в лоте {actual} карт из {expected}."
                )

        return await self._repository.update_field(
            int(batch_id),
            field="mode",
            value=normalized,
        )

    async def set_price(self, batch_id: int, price: int) -> dict[str, Any]:
        amount = int(price)
        if amount < 0:
            raise ValueError("Цена не может быть отрицательной.")
        return await self._repository.update_field(
            int(batch_id),
            field="price",
            value=amount,
        )

    async def set_currency(self, batch_id: int, currency: str) -> dict[str, Any]:
        normalized = Currency.from_raw(currency).value
        return await self._repository.update_field(
            int(batch_id),
            field="currency",
            value=normalized,
        )

    async def set_comment(self, batch_id: int, comment: str) -> dict[str, Any]:
        normalized = str(comment or "").strip()[:2000]
        return await self._repository.update_field(
            int(batch_id),
            field="comment",
            value=normalized,
        )

    async def set_proof(
        self,
        batch_id: int,
        proof_photo_id: str | None,
    ) -> dict[str, Any]:
        normalized = str(proof_photo_id or "").strip() or "NO_PROOF"
        return await self._repository.update_field(
            int(batch_id),
            field="proof_photo_id",
            value=normalized,
        )
