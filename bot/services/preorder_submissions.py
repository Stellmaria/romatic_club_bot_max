"""Application service for future-deck preorder submissions."""

from __future__ import annotations

# ruff: noqa: RUF001

from dataclasses import dataclass
from typing import Any

from bot.domain.auctions import AuctionKind, Currency
from bot.domain.preorders import (
    build_preorder_title,
    validate_preorder_selection,
    validate_preorder_start_price,
)
from bot.repositories.preorder_submissions import PreorderSubmissionRepository
from db.pool import get_db_pool


class PreorderSubmissionError(RuntimeError):
    """Base error that can be converted into a stable Telegram response."""


class PreorderAccessDenied(PreorderSubmissionError):
    pass


class PreorderDeckUnavailable(PreorderSubmissionError):
    pass


@dataclass(frozen=True, slots=True)
class SubmittedPreorder:
    auction_id: int
    was_existing: bool
    snapshot: dict[str, Any]


class PreorderSubmissionService:
    def __init__(self, repository: PreorderSubmissionRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> PreorderSubmissionService:
        return cls(PreorderSubmissionRepository(await get_db_pool()))

    async def submit(
        self,
        *,
        owner_id: int,
        luxury_level: int,
        is_admin: bool,
        deck_id: int,
        deck_name: str,
        mode: object,
        items: dict[str, object] | None,
        request_key: str,
        start_price: object,
        currency: object,
        comment: str,
        image_id: str | None,
    ) -> SubmittedPreorder:
        kind = AuctionKind.PREORDER
        if not is_admin and int(luxury_level) < kind.minimum_luxury_level:
            raise PreorderAccessDenied(
                f"Предзаказ доступен с уровня Лакшери {kind.minimum_luxury_level}."
            )

        normalized_mode, normalized_items = validate_preorder_selection(
            mode=mode,
            items=items,
        )
        price = validate_preorder_start_price(start_price)
        parsed_currency = Currency.from_raw(currency)

        key = str(request_key or "").strip()
        if not 16 <= len(key) <= 128:
            raise ValueError("preorder request key must contain 16-128 characters")

        normalized_deck_id = int(deck_id)
        if normalized_deck_id <= 0:
            raise ValueError("preorder deck id must be positive")

        title = build_preorder_title(
            deck_id=normalized_deck_id,
            deck_name=deck_name,
            mode=normalized_mode,
            items=normalized_items,
        )
        hero_name = f"Предзаказ колоды №{normalized_deck_id}"

        try:
            created = await self._repository.create_pending(
                owner_id=int(owner_id),
                deck_id=normalized_deck_id,
                mode=normalized_mode,
                items=normalized_items,
                request_key=key,
                card_name=title,
                hero_name=hero_name,
                image_id=(image_id or "").strip() or None,
                start_price=price,
                currency=parsed_currency.value,
                comment=(comment or "").strip(),
            )
        except ValueError as exc:
            if "future preorder deck" in str(exc):
                raise PreorderDeckUnavailable(
                    "Эта будущая колода уже недоступна для предзаказа."
                ) from exc
            raise

        auction_id = int(created.get("auction_id") or 0)
        if auction_id <= 0:
            raise PreorderSubmissionError("Хранилище не вернуло номер заявки.")
        return SubmittedPreorder(
            auction_id=auction_id,
            was_existing=bool(created.get("was_existing")),
            snapshot=created,
        )

    async def list_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._repository.list_pending(limit=limit)
