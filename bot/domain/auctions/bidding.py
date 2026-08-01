from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from bot.domain.auctions.enums import Currency, normalize_currency_choices
from bot.domain.auctions.exceptions import BidFormatError, BidStepError, BidTooHigh

TEA_TO_DIAMONDS = 10

_BID_OFFER_RE = re.compile(
    r"^\s*([\d\s_]+)\s*([кk])?\s*"
    r"(💎|🍵|☕️?|алмаз(?:ы|ов)?|diamond(?:s)?|чай|чая|чаш(?:ка|ки|ек)?|tea|cups?)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BidOffer:
    amount: int
    currency: Currency


def auction_bidding_closes_at(end_time: datetime) -> datetime:
    """Return the exclusive deadline after the displayed ending minute.

    A displayed end time of 18:30 accepts bids through 18:30:59.999999 and
    closes at 18:31:00.  Stored seconds are deliberately ignored because the
    public contract is minute-based.
    """

    return end_time.replace(second=0, microsecond=0) + timedelta(minutes=1)


def comparison_multiplier(currency: Currency) -> int:
    return TEA_TO_DIAMONDS if currency is Currency.CUPS else 1


def comparison_units(amount: int, currency: Currency) -> int:
    return int(amount) * comparison_multiplier(currency)


def amount_from_comparison_units(units: int, currency: Currency) -> int:
    amount = max(0, int(units)) // comparison_multiplier(currency)
    step = currency.bid_step
    return amount - amount % step


def _currency_from_marker(marker: str | None) -> Currency | None:
    if not marker:
        return None
    value = marker.strip().lower()
    if value.startswith("алмаз") or value.startswith("diamond") or "💎" in value:
        return Currency.DIAMONDS
    if value.startswith("ча") or value.startswith("cup") or value == "tea" or "🍵" in value or "☕" in value:
        return Currency.CUPS
    return None


def parse_bid_offer(
    text: str,
    *,
    accepted_currencies: Iterable[Currency] | object | None,
    fallback: Currency,
) -> BidOffer:
    match = _BID_OFFER_RE.fullmatch(text or "")
    if not match:
        raise BidFormatError(
            "Ставка должна быть числом. Для лота с двумя валютами укажите валюту: "
            "например, 12 чай или 120 алмазов."
        )

    digits, suffix, marker = match.groups()
    normalized = digits.replace(" ", "").replace("_", "")
    try:
        amount = int(normalized)
    except ValueError as exc:
        raise BidFormatError() from exc
    if suffix:
        amount *= 1000
    if amount <= 0:
        raise BidFormatError("Ставка должна быть больше нуля.")

    choices = normalize_currency_choices(accepted_currencies, fallback=fallback)
    selected = _currency_from_marker(marker)
    if selected is None:
        if len(choices) > 1:
            raise BidFormatError(
                "У этого лота две валюты. Укажите валюту после суммы: "
                "например, 12 чай или 120 алмазов."
            )
        selected = choices[0] if choices else fallback
    if choices and selected not in choices:
        allowed = " или ".join(f"{item.emoji} {item.value}" for item in choices)
        raise BidFormatError(f"Эта валюта не принимается. Доступно: {allowed}.")
    return BidOffer(amount=amount, currency=selected)


def validate_reverse_offer(
    *,
    amount: int,
    currency: Currency,
    start_price: int,
    base_currency: Currency,
    current_best_units: int | None,
) -> int:
    """Validate a descending bid and return the maximum in its own currency."""

    amount_i = int(amount)
    step = currency.bid_step
    if step > 1 and amount_i % step:
        raise BidStepError(amount=amount_i, start_price=0, step=step)

    ceiling_units = comparison_units(int(start_price), base_currency)
    if ceiling_units <= 0:
        raise BidTooHigh(maximum=0, current_best=None)

    if current_best_units is None:
        maximum_units = ceiling_units
    else:
        maximum_units = int(current_best_units) - comparison_units(step, currency)

    maximum = amount_from_comparison_units(maximum_units, currency)
    if amount_i <= 0 or maximum <= 0 or amount_i > maximum:
        raise BidTooHigh(maximum=max(0, maximum), current_best=current_best_units)
    return maximum
