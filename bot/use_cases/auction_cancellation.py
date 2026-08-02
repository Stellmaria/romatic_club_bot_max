"""Application use cases for owner and moderator auction cancellation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from bot.use_cases.common import (
    ApplicationInvalidState,
    ApplicationNotFound,
    ApplicationPermissionDenied,
)


@dataclass(frozen=True, slots=True)
class CancelOwnedAuctionCommand:
    auction_id: int
    owner_id: int


@dataclass(frozen=True, slots=True)
class CancelAuctionCommand:
    auction_id: int
    moderator_id: int


@dataclass(frozen=True, slots=True)
class CancelledAuction:
    lot: dict[str, Any]
    owners: tuple[dict[str, Any], ...]
    owners_text: str


class CancelOwnedAuctionUseCase:
    def __init__(
        self,
        *,
        get_owned: Callable[..., Awaitable[dict[str, Any]]],
        cancel_owned: Callable[..., Awaitable[dict[str, Any]]],
        get_owners_text: Callable[[int], Awaitable[str]],
    ) -> None:
        self._get_owned = get_owned
        self._cancel_owned = cancel_owned
        self._get_owners_text = get_owners_text

    async def execute(self, command: CancelOwnedAuctionCommand) -> CancelledAuction:
        try:
            lot = await self._get_owned(command.auction_id, owner_id=command.owner_id)
        except LookupError as exc:
            raise ApplicationPermissionDenied(
                "Лот не найден или недоступен владельцу.", code="auction_not_owned"
            ) from exc
        if str(lot.get("status")) != "pending":
            raise ApplicationInvalidState(
                "Владелец может отменить только ожидающую модерации заявку.",
                code="owner_cancel_invalid_status",
                details={"status": lot.get("status")},
            )
        try:
            cancelled = await self._cancel_owned(command.auction_id, owner_id=command.owner_id)
        except LookupError as exc:
            raise ApplicationPermissionDenied(
                "Лот уже изменён или недоступен владельцу.", code="auction_not_owned"
            ) from exc
        try:
            owners_text = await self._get_owners_text(command.auction_id)
        except Exception:
            owners_text = ""
        return CancelledAuction(dict(cancelled or lot), (), owners_text)


class CancelAuctionUseCase:
    def __init__(
        self,
        *,
        get_lot: Callable[[int], Awaitable[dict[str, Any] | None]],
        cancel: Callable[[int], Awaitable[dict[str, Any]]],
        get_owners: Callable[[int], Awaitable[list[dict[str, Any]]]],
        get_owners_text: Callable[[int], Awaitable[str]],
    ) -> None:
        self._get_lot = get_lot
        self._cancel = cancel
        self._get_owners = get_owners
        self._get_owners_text = get_owners_text

    async def execute(self, command: CancelAuctionCommand) -> CancelledAuction:
        lot = await self._get_lot(command.auction_id)
        if not lot:
            raise ApplicationNotFound("Лот не найден.", code="auction_not_found")
        try:
            cancelled = await self._cancel(command.auction_id)
        except LookupError as exc:
            raise ApplicationNotFound("Лот не найден.", code="auction_not_found") from exc
        except ValueError as exc:
            raise ApplicationInvalidState(
                "Лот нельзя отменить из текущего состояния.",
                code="auction_cancel_invalid_status",
                details={"reason": str(exc)},
            ) from exc
        owners: tuple[dict[str, Any], ...] = ()
        owners_text = ""
        try:
            owners = tuple(dict(item) for item in await self._get_owners(command.auction_id))
        except Exception:
            pass
        try:
            owners_text = await self._get_owners_text(command.auction_id)
        except Exception:
            pass
        return CancelledAuction(dict(cancelled or lot), owners, owners_text)
