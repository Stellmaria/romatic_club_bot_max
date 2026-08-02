"""Application orchestration for creating auction applications."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from bot.use_cases.common import ApplicationPermissionDenied, ApplicationValidationError


@dataclass(frozen=True, slots=True)
class SubmitAuctionCommand:
    owner_id: int
    card_id: int | None
    hero_name: str | None
    card_name: str | None
    start_price: int
    currency: str
    accepted_currencies: tuple[str, ...]
    custom_offer_terms: str | None
    comment: str
    image_id: str | None
    auction_kind: str
    proof_photo_id: str | None
    craft_uid_possible: bool | None


@dataclass(frozen=True, slots=True)
class SubmittedAuction:
    auction_id: int
    lot: dict[str, Any]
    luxury_level: int


class SubmitAuctionUseCase:
    def __init__(
        self,
        *,
        get_luxury_level: Callable[[int], Awaitable[int]],
        submit: Callable[..., Awaitable[dict[str, Any]]],
        access_denied_errors: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._get_luxury_level = get_luxury_level
        self._submit = submit
        self._access_denied_errors = access_denied_errors

    async def execute(self, command: SubmitAuctionCommand) -> SubmittedAuction:
        try:
            luxury_level = int(await self._get_luxury_level(command.owner_id))
            created = await self._submit(
                owner_id=int(command.owner_id),
                luxury_level=luxury_level,
                card_id=command.card_id,
                hero_name=command.hero_name or "",
                card_name=command.card_name or "",
                start_price=int(command.start_price),
                currency=command.currency,
                accepted_currencies=list(command.accepted_currencies),
                custom_offer_terms=command.custom_offer_terms,
                comment=command.comment,
                image_id=command.image_id,
                auction_kind=command.auction_kind,
                proof_photo_id=command.proof_photo_id,
                craft_uid_possible=command.craft_uid_possible,
            )
        except self._access_denied_errors as exc:
            raise ApplicationPermissionDenied(
                "Этот тип аукциона недоступен для уровня Лакшери.",
                code="auction_kind_forbidden",
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ApplicationValidationError(
                "Данные заявки не прошли проверку.",
                code="invalid_auction_draft",
                details={"reason": str(exc)},
            ) from exc
        auction_id = int(created.get("auction_id") or 0)
        if auction_id <= 0:
            raise ApplicationValidationError(
                "Хранилище не вернуло идентификатор заявки.",
                code="auction_id_missing",
            )
        return SubmittedAuction(auction_id=auction_id, lot=dict(created), luxury_level=luxury_level)
