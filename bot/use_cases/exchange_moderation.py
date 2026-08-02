from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bot.domain.auctions import InvalidExchangeTransition
from bot.use_cases.common import ApplicationInvalidState, ApplicationNotFound, ApplicationValidationError

Row = dict[str, Any]


async def _none() -> None:
    return None
GetBatch = Callable[[int], Awaitable[Row | None]]
GetDeck = Callable[[int], Awaitable[Row | None]]
GetItems = Callable[[int], Awaitable[list[Row]]]
Moderate = Callable[..., Awaitable[Row]]


@dataclass(frozen=True, slots=True)
class ModerateExchangeCommand:
    batch_id: int
    moderator_id: int
    moderator_username: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModeratedExchange:
    batch: Row
    deck: Row | None
    items: tuple[Row, ...]

    @property
    def item_count(self) -> int:
        return len(self.items)


class _ModerateExchangeUseCase:
    def __init__(
        self,
        *,
        get_batch: GetBatch,
        get_deck: GetDeck,
        get_items: GetItems,
        moderate: Moderate,
        target: str,
    ) -> None:
        self._get_batch = get_batch
        self._get_deck = get_deck
        self._get_items = get_items
        self._moderate = moderate
        self._target = target

    async def execute(self, command: ModerateExchangeCommand) -> ModeratedExchange:
        if int(command.batch_id) <= 0 or int(command.moderator_id) <= 0:
            raise ApplicationValidationError("batch_id and moderator_id must be positive")
        reason = (command.reason or "").strip() or None
        if self._target == "rejected" and not reason:
            raise ApplicationValidationError("rejection reason is required")

        before = await self._get_batch(int(command.batch_id))
        if not before:
            raise ApplicationNotFound("exchange batch not found")
        try:
            batch = dict(
                await self._moderate(
                    int(command.batch_id),
                    moderator_id=int(command.moderator_id),
                    moderator_username=command.moderator_username,
                    comment=reason,
                )
            )
        except InvalidExchangeTransition as exc:
            raise ApplicationInvalidState(
                "exchange batch is already processed",
                details={"current": exc.current, "target": exc.target},
            ) from exc

        deck_id = int(batch.get("deck_id") or 0)
        deck_result, items_result = await asyncio.gather(
            self._get_deck(deck_id) if deck_id else _none(),
            self._get_items(int(command.batch_id)),
            return_exceptions=True,
        )
        deck = None if isinstance(deck_result, BaseException) else deck_result
        items = [] if isinstance(items_result, BaseException) else items_result
        return ModeratedExchange(
            batch=batch,
            deck=dict(deck) if deck else None,
            items=tuple(dict(item) for item in items),
        )


class ApproveExchangeUseCase(_ModerateExchangeUseCase):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(target="approved", **kwargs)


class RejectExchangeUseCase(_ModerateExchangeUseCase):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(target="rejected", **kwargs)
