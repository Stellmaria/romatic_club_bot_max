from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bot.domain.auctions import AuctionNotFound, AuctionOwnerPermissionDenied, InvalidAuctionTransition
from bot.use_cases.common import (
    ApplicationInvalidState,
    ApplicationNotFound,
    ApplicationPermissionDenied,
    ApplicationValidationError,
)

Row = dict[str, Any]
UpdateOwned = Callable[..., Awaitable[Row]]
OwnersText = Callable[[int], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class EditOwnedLotCommand:
    auction_id: int
    owner_id: int
    changes: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EditedOwnedLot:
    lot: Row
    owners_text: str


class EditOwnedLotUseCase:
    def __init__(self, *, update_owned: UpdateOwned, owners_text: OwnersText) -> None:
        self._update_owned = update_owned
        self._owners_text = owners_text

    async def execute(self, command: EditOwnedLotCommand) -> EditedOwnedLot:
        if int(command.auction_id) <= 0 or int(command.owner_id) <= 0:
            raise ApplicationValidationError("auction_id and owner_id must be positive")
        changes = dict(command.changes)
        if not changes:
            raise ApplicationValidationError("at least one change is required")
        try:
            lot = dict(
                await self._update_owned(
                    int(command.auction_id),
                    owner_id=int(command.owner_id),
                    changes=changes,
                )
            )
        except AuctionNotFound as exc:
            raise ApplicationNotFound("auction not found") from exc
        except AuctionOwnerPermissionDenied as exc:
            raise ApplicationPermissionDenied("auction does not belong to user") from exc
        except InvalidAuctionTransition as exc:
            raise ApplicationInvalidState(
                "auction is not editable",
                details={"current": exc.current, "target": exc.target},
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ApplicationValidationError(str(exc)) from exc
        try:
            owners_text = await self._owners_text(int(command.auction_id))
        except Exception:
            owners_text = "-"
        return EditedOwnedLot(lot=lot, owners_text=owners_text or "-")
