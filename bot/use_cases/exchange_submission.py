"""Application orchestration for exchange request submission."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from bot.use_cases.common import ApplicationNotFound, ApplicationValidationError


@dataclass(frozen=True, slots=True)
class SubmitExchangeCommand:
    user_id: int
    deck_id: int
    mode: str
    currency: str
    comment: str
    proof_photo_id: str
    card_ids: tuple[int, ...]
    split_mode: str = "one"
    copies: int = 1
    explicit_price: int = 0


@dataclass(frozen=True, slots=True)
class ExchangeSubmissionItem:
    batch_id: int
    card_ids: tuple[int, ...]
    price: int


@dataclass(frozen=True, slots=True)
class SubmittedExchange:
    items: tuple[ExchangeSubmissionItem, ...]
    cards: tuple[dict[str, Any], ...]
    mode: str
    split_mode: str


class SubmitExchangeUseCase:
    def __init__(
        self,
        *,
        get_card_ids_by_deck: Callable[[int], Awaitable[list[int]]],
        get_cards: Callable[[list[int]], Awaitable[list[dict[str, Any]]]],
        price_for_card: Callable[[dict[str, Any]], int],
        price_for_deck: Callable[[int], Awaitable[int]],
        submit_many: Callable[[Iterable[dict[str, Any]]], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self._get_card_ids_by_deck = get_card_ids_by_deck
        self._get_cards = get_cards
        self._price_for_card = price_for_card
        self._price_for_deck = price_for_deck
        self._submit_many = submit_many

    async def execute(self, command: SubmitExchangeCommand) -> SubmittedExchange:
        return await self.run(command)

    async def run(self, command: SubmitExchangeCommand) -> SubmittedExchange:
        """Submit an exchange batch through the application boundary."""

        card_ids = tuple(dict.fromkeys(int(card_id) for card_id in command.card_ids))
        mode = (command.mode or "card").strip().lower()
        split_mode = (command.split_mode or "one").strip().lower()
        if not card_ids and mode in {"deck", "deck_split"}:
            card_ids = tuple(await self._get_card_ids_by_deck(command.deck_id))
        if not card_ids:
            raise ApplicationValidationError("Не выбраны карты.", code="exchange_cards_missing")
        cards = tuple(dict(card) for card in await self._get_cards(list(card_ids)))
        by_id = {int(card["card_id"]): card for card in cards if card.get("card_id") is not None}
        missing = tuple(card_id for card_id in card_ids if card_id not in by_id)
        if missing:
            raise ApplicationNotFound(
                "Часть карт не найдена.", code="exchange_cards_not_found", details={"card_ids": missing}
            )

        requests: list[dict[str, Any]] = []
        request_specs: list[tuple[tuple[int, ...], int]] = []
        common = {
            "user_id": int(command.user_id),
            "deck_id": int(command.deck_id),
            "mode": mode,
            "currency": command.currency,
            "comment": command.comment,
            "proof_photo_id": command.proof_photo_id,
        }
        if split_mode == "per_card" or mode == "deck_split":
            for card_id in card_ids:
                price = int(self._price_for_card(by_id[card_id]) or 0)
                request_specs.append(((card_id,), price))
        elif len(card_ids) == 1 and int(command.copies) > 1:
            copies = max(1, min(int(command.copies), 20))
            price = int(self._price_for_card(by_id[card_ids[0]]) or 0)
            request_specs.extend([(card_ids, price)] * copies)
        else:
            price = int(command.explicit_price or 0)
            if price <= 0:
                price = (
                    int(self._price_for_card(by_id[card_ids[0]]) or 0)
                    if mode == "card"
                    else int(await self._price_for_deck(command.deck_id) or 0)
                )
            request_specs.append((card_ids, price))

        for ids, price in request_specs:
            requests.append({**common, "card_ids": ids, "price": price})
        try:
            created = await self._submit_many(requests)
        except (TypeError, ValueError) as exc:
            raise ApplicationValidationError(
                "Заявка биржи не прошла проверку.",
                code="invalid_exchange_submission",
                details={"reason": str(exc)},
            ) from exc
        if len(created) != len(request_specs):
            raise RuntimeError("exchange persistence returned an incomplete result")
        items = tuple(
            ExchangeSubmissionItem(
                batch_id=int(batch["batch_id"]), card_ids=ids, price=price
            )
            for batch, (ids, price) in zip(created, request_specs, strict=True)
        )
        return SubmittedExchange(items=items, cards=cards, mode=mode, split_mode=split_mode)
