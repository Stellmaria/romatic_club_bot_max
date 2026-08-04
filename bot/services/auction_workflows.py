from __future__ import annotations

from datetime import datetime
from typing import Any

from bot.domain.auctions import AuctionKind, Currency, normalize_currency_choices
from bot.domain.auctions.rules import assert_kind_access
from bot.domain.auctions.workflows import AuctionDraft, PublicationFailure
from bot.core.time import ensure_utc
from bot.repositories.auction_workflows import AuctionWorkflowRepository
from db.core import get_db_pool


class AuctionCreationService:
    def __init__(self, repository: AuctionWorkflowRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionCreationService":
        return cls(AuctionWorkflowRepository(await get_db_pool()))

    async def submit(
        self,
        *,
        owner_id: int,
        luxury_level: int,
        start_price: int,
        currency: str,
        auction_kind: str,
        accepted_currencies: list[str] | tuple[str, ...] | None = None,
        custom_offer_terms: str | None = None,
        card_id: int | None = None,
        card_name: str | None = None,
        hero_name: str | None = None,
        image_id: str | None = None,
        comment: str = "",
        proof_photo_id: str | None = None,
        craft_uid_possible: bool | None = None,
    ) -> dict[str, Any]:
        kind = AuctionKind.from_raw(auction_kind)
        if kind is AuctionKind.EXCHANGE:
            raise ValueError("exchange requests must use ExchangeService")
        assert_kind_access(kind, luxury_level)
        parsed_currency = Currency.from_raw(currency)
        choices = normalize_currency_choices(accepted_currencies, fallback=parsed_currency)
        if not choices:
            choices = (parsed_currency,)
        custom_terms = (custom_offer_terms or "").strip()[:500] or None
        if kind in {AuctionKind.FREE, AuctionKind.REVERSE}:
            allowed = {Currency.CUPS, Currency.DIAMONDS}
            if any(choice not in allowed for choice in choices):
                raise ValueError("reverse/free auctions accept only tea and/or diamonds")
            if len(choices) > 2:
                raise ValueError("reverse/free auctions accept at most two currencies")
            if kind is AuctionKind.REVERSE and custom_terms:
                raise ValueError("custom combo is available only for free auctions")
        elif len(choices) != 1:
            raise ValueError("this auction type accepts one currency")

        price = int(start_price)
        if kind is AuctionKind.REVERSE:
            if price <= 0:
                raise ValueError("reverse start_price must be greater than zero")
        elif kind is AuctionKind.FREE:
            if price < 0:
                raise ValueError("start_price must not be negative")
        elif price <= 0:
            raise ValueError("start_price must be greater than zero")

        draft = AuctionDraft(
            owner_id=int(owner_id),
            start_price=price,
            currency=parsed_currency,
            accepted_currencies=choices,
            custom_offer_terms=custom_terms,
            auction_kind=kind,
            card_id=int(card_id) if card_id is not None else None,
            card_name=(card_name or "").strip() or None,
            hero_name=(hero_name or "").strip() or None,
            image_id=(image_id or "").strip() or None,
            comment=(comment or "").strip()[:2000],
            proof_photo_id=(proof_photo_id or "").strip() or None,
            craft_uid_possible=craft_uid_possible,
        )
        return await self._repository.create_pending(draft)


class AuctionModerationService:
    def __init__(self, repository: AuctionWorkflowRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionModerationService":
        return cls(AuctionWorkflowRepository(await get_db_pool()))

    async def schedule(
        self,
        auction_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        return await self._repository.schedule(
            int(auction_id),
            start_time=ensure_utc(start_time),
            end_time=ensure_utc(end_time),
        )

    async def reject(self, auction_id: int) -> dict[str, Any]:
        return await self._repository.reject(int(auction_id))

    async def cancel(self, auction_id: int) -> dict[str, Any]:
        return await self._repository.cancel_by_moderator(int(auction_id))

    async def reschedule(
        self,
        auction_id: int,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, Any]:
        return await self._repository.reschedule(
            int(auction_id),
            start_time=ensure_utc(start_time),
            end_time=ensure_utc(end_time),
        )

    async def update_field(
        self,
        auction_id: int,
        *,
        field: str,
        value: Any,
    ) -> dict[str, Any]:
        return await self.update_fields(
            auction_id,
            changes={field: value},
        )

    @staticmethod
    def _normalize_field(field: str, value: Any) -> Any:
        if field == "start_price":
            normalized_value = int(value)
            if normalized_value <= 0:
                raise ValueError("start_price must be greater than zero")
            return normalized_value
        if field == "currency":
            return Currency.from_raw(value).value
        if field == "auction_kind":
            kind = AuctionKind.from_raw(value)
            if kind is AuctionKind.EXCHANGE:
                raise ValueError("exchange requests are not auction lots")
            return kind.value
        if field == "comment":
            return str(value or "").strip()[:2000]
        if field == "image_id":
            return str(value or "").strip() or None
        if field == "craft_uid_possible":
            if value is not None and not isinstance(value, bool):
                raise TypeError("craft_uid_possible must be bool or None")
            return value
        raise ValueError(f"field is not moderatable: {field}")

    async def update_fields(
        self,
        auction_id: int,
        *,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = {
            (field or "").strip(): self._normalize_field((field or "").strip(), value)
            for field, value in changes.items()
        }
        return await self._repository.update_moderatable_fields(
            int(auction_id),
            changes=normalized,
        )


class AuctionOwnerService:
    """Owner-scoped edits for pending applications."""

    def __init__(self, repository: AuctionWorkflowRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionOwnerService":
        return cls(AuctionWorkflowRepository(await get_db_pool()))

    async def update_field(
        self,
        auction_id: int,
        *,
        owner_id: int,
        field: str,
        value: Any,
    ) -> dict[str, Any]:
        return await self.update_fields(
            auction_id,
            owner_id=owner_id,
            changes={field: value},
        )

    async def get_owned(self, auction_id: int, *, owner_id: int) -> dict[str, Any]:
        return await self._repository.get_owned(
            int(auction_id),
            owner_id=int(owner_id),
        )

    async def cancel(self, auction_id: int, *, owner_id: int) -> dict[str, Any]:
        return await self._repository.cancel_by_owner(
            int(auction_id),
            owner_id=int(owner_id),
        )

    async def update_fields(
        self,
        auction_id: int,
        *,
        owner_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = {
            (field or "").strip(): AuctionModerationService._normalize_field(
                (field or "").strip(),
                value,
            )
            for field, value in changes.items()
        }
        return await self._repository.update_owner_fields(
            int(auction_id),
            owner_id=int(owner_id),
            changes=normalized,
        )


class AuctionLifecycleService:
    """Explicit administrative and Telegram-discussion lifecycle operations."""

    def __init__(self, repository: AuctionWorkflowRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionLifecycleService":
        return cls(AuctionWorkflowRepository(await get_db_pool()))

    async def requeue_publication(self, auction_id: int) -> dict[str, Any]:
        return await self._repository.requeue_publication(int(auction_id))

    async def restart(self, auction_id: int, *, end_time: datetime) -> dict[str, Any]:
        return await self._repository.restart(
            int(auction_id),
            end_time=ensure_utc(end_time),
        )

    async def finish_now(self, auction_id: int, *, end_time: datetime) -> dict[str, Any]:
        return await self._repository.finish_now(
            int(auction_id),
            end_time=ensure_utc(end_time),
        )

    async def bind_by_channel_message(
        self,
        *,
        channel_message_id: int,
        discussion_message_id: int,
    ) -> int | None:
        return await self._repository.bind_discussion_by_message(
            channel_message_id=int(channel_message_id),
            discussion_message_id=int(discussion_message_id),
        )

    async def bind_by_auction(
        self,
        *,
        auction_id: int,
        discussion_message_id: int,
    ) -> int | None:
        return await self._repository.bind_discussion_by_auction(
            auction_id=int(auction_id),
            discussion_message_id=int(discussion_message_id),
        )


class AuctionPublicationService:
    def __init__(self, repository: AuctionWorkflowRepository):
        self._repository = repository

    @classmethod
    async def create(cls) -> "AuctionPublicationService":
        return cls(AuctionWorkflowRepository(await get_db_pool()))

    async def recover_stale(self) -> list[int]:
        return await self._repository.fail_stale_publications(
            older_than_minutes=15
        )

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self._repository.claim_due(
            now=ensure_utc(now),
            limit=limit,
        )

    async def claim_one(self, auction_id: int) -> dict[str, Any]:
        return await self._repository.claim_one(int(auction_id))

    async def mark_published(self, auction_id: int, *, message_id: int) -> bool:
        return await self._repository.mark_published(
            int(auction_id),
            message_id=int(message_id),
        )

    async def mark_deferred(self, auction_id: int) -> bool:
        return await self._repository.mark_deferred(int(auction_id))

    async def confirm_deferred_publication(
        self,
        auction_id: int,
        *,
        channel_message_id: int,
        discussion_message_id: int | None = None,
    ) -> dict[str, Any]:
        return await self._repository.confirm_deferred_publication(
            int(auction_id),
            channel_message_id=int(channel_message_id),
            discussion_message_id=(
                int(discussion_message_id) if discussion_message_id is not None else None
            ),
        )

    async def mark_failed(
        self,
        auction_id: int,
        *,
        error: str,
    ) -> PublicationFailure:
        return await self._repository.mark_publication_failed(
            int(auction_id),
            error=error,
        )
