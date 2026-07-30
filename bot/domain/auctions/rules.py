from __future__ import annotations

import re

from bot.domain.auctions.enums import AuctionKind, AuctionStatus, Currency
from bot.domain.auctions.exceptions import (
    AuctionAccessDenied,
    AuctionKindNotBiddable,
    BidFormatError,
    BidStepError,
    BidTooHigh,
    BidTooLow,
    InvalidAuctionTransition,
)

_BID_RE = re.compile(r"^\s*([\d\s_]+)\s*([кk])?\s*$", re.IGNORECASE)


def parse_bid_amount(text: str) -> int:
    """Parse Telegram bid syntax: 300, 1 000, 1_000, 10k/10к."""
    match = _BID_RE.fullmatch(text or "")
    if not match:
        raise BidFormatError(
            "Ставка должна быть числом. Допустимы пробелы и суффикс K/К, например 300 или 10к."
        )

    digits, suffix = match.groups()
    normalized = digits.replace(" ", "").replace("_", "")
    try:
        amount = int(normalized)
    except ValueError as exc:
        raise BidFormatError() from exc

    if suffix:
        amount *= 1000
    if amount <= 0:
        raise BidFormatError("Ставка должна быть больше нуля.")
    return amount


def minimum_next_bid(*, start_price: int, current_max: int | None, step: int) -> int:
    start = max(0, int(start_price))
    if current_max is None:
        return start
    return max(start, int(current_max)) + max(1, int(step))


def validate_bid_amount(
    *,
    amount: int,
    currency: Currency,
    start_price: int,
    current_max: int | None,
) -> int:
    step = currency.bid_step
    minimum = minimum_next_bid(
        start_price=start_price,
        current_max=current_max,
        step=step,
    )
    if int(amount) < minimum:
        raise BidTooLow(minimum=minimum, current_max=current_max)
    if step > 1 and (int(amount) - int(start_price)) % step != 0:
        raise BidStepError(amount=amount, start_price=start_price, step=step)
    return minimum


def validate_bid_for_kind(
    *,
    amount: int,
    currency: Currency,
    start_price: int,
    current_best: int | None,
    auction_kind: AuctionKind,
) -> int:
    """Validate a bid using the winner policy of the selected auction kind."""
    if not auction_kind.is_automatic_bidding:
        raise AuctionKindNotBiddable(auction_kind.value)

    if not auction_kind.lowest_bid_wins:
        return validate_bid_amount(
            amount=amount,
            currency=currency,
            start_price=start_price,
            current_max=current_best,
        )

    step = currency.bid_step
    amount_i = int(amount)

    # У обратного аукциона нет фиксированной верхней границы. Первая ставка
    # может быть любой положительной суммой с корректным шагом. После неё
    # каждая следующая ставка должна быть ниже текущей лучшей минимум на шаг.
    if current_best is None:
        if amount_i < step:
            raise BidTooLow(minimum=step, current_max=None)
        if step > 1 and amount_i % step != 0:
            raise BidStepError(amount=amount_i, start_price=0, step=step)
        return amount_i

    maximum = int(current_best) - step
    if amount_i <= 0 or amount_i > maximum:
        raise BidTooHigh(maximum=max(1, maximum), current_best=current_best)
    if step > 1 and amount_i % step != 0:
        raise BidStepError(amount=amount_i, start_price=0, step=step)
    return maximum


def assert_kind_access(kind: AuctionKind, luxury_level: int) -> None:
    actual = max(0, int(luxury_level))
    if actual < kind.minimum_luxury_level:
        raise AuctionAccessDenied(
            required_level=kind.minimum_luxury_level,
            actual_level=actual,
        )


_ALLOWED_TRANSITIONS: dict[AuctionStatus, frozenset[AuctionStatus]] = {
    AuctionStatus.DRAFT: frozenset({AuctionStatus.MODERATION, AuctionStatus.CANCELLED}),
    AuctionStatus.MODERATION: frozenset(
        {AuctionStatus.SCHEDULED, AuctionStatus.REJECTED, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.PENDING: frozenset(
        {AuctionStatus.SCHEDULED, AuctionStatus.REJECTED, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.APPROVED: frozenset(
        {AuctionStatus.SCHEDULED, AuctionStatus.REJECTED, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.SCHEDULED: frozenset(
        {AuctionStatus.PUBLISHING, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.PUBLISHING: frozenset(
        {
            AuctionStatus.ACTIVE,
            AuctionStatus.SCHEDULED,
            AuctionStatus.PUBLICATION_FAILED,
        }
    ),
    AuctionStatus.PUBLICATION_FAILED: frozenset(
        {AuctionStatus.SCHEDULED, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.ACTIVE: frozenset(
        {AuctionStatus.FINALIZING, AuctionStatus.FINISHED, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.FINALIZING: frozenset(
        {AuctionStatus.FINISHED, AuctionStatus.FINALIZATION_FAILED}
    ),
    AuctionStatus.FINALIZATION_FAILED: frozenset(
        {AuctionStatus.FINALIZING, AuctionStatus.ACTIVE, AuctionStatus.CANCELLED}
    ),
    AuctionStatus.FINISHED: frozenset({AuctionStatus.ACTIVE}),
}


def assert_status_transition(current: object, target: object) -> None:
    current_status = AuctionStatus.from_raw(current)
    target_status = AuctionStatus.from_raw(target)
    if current_status is None or target_status is None:
        raise InvalidAuctionTransition(current=str(current), target=str(target))
    if target_status not in _ALLOWED_TRANSITIONS.get(current_status, frozenset()):
        raise InvalidAuctionTransition(
            current=current_status.value,
            target=target_status.value,
        )
