from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:100]!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


write(
    "bot/domain/auctions/bidding.py",
    '''from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from bot.domain.auctions.enums import Currency, normalize_currency_choices
from bot.domain.auctions.exceptions import BidFormatError, BidStepError, BidTooHigh

TEA_TO_DIAMONDS = 10

_BID_OFFER_RE = re.compile(
    r"^\\s*([\\d\\s_]+)\\s*([кk])?\\s*"
    r"(💎|🍵|☕️?|алмаз(?:ы|ов)?|diamond(?:s)?|чай|чая|чаш(?:ка|ки|ек)?|tea|cups?)?\\s*$",
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
''',
)

replace(
    "bot/domain/auctions/models.py",
    "from datetime import datetime\n",
    "from datetime import datetime\n",
)
replace(
    "bot/domain/auctions/models.py",
    "from bot.domain.auctions.enums import AuctionKind, AuctionStatus, Currency\n",
    "from bot.domain.auctions.bidding import auction_bidding_closes_at\n"
    "from bot.domain.auctions.enums import (\n"
    "    AuctionKind,\n"
    "    AuctionStatus,\n"
    "    Currency,\n"
    "    normalize_currency_choices,\n"
    ")\n",
)
replace(
    "bot/domain/auctions/models.py",
    "    auction_kind: AuctionKind = AuctionKind.STANDARD\n",
    "    auction_kind: AuctionKind = AuctionKind.STANDARD\n"
    "    accepted_currencies: tuple[Currency, ...] = ()\n",
)
replace(
    "bot/domain/auctions/models.py",
    "            auction_kind=AuctionKind.from_raw(row.get(\"auction_kind\")),\n",
    "            auction_kind=AuctionKind.from_raw(row.get(\"auction_kind\")),\n"
    "            accepted_currencies=normalize_currency_choices(\n"
    "                row.get(\"accepted_currencies\"),\n"
    "                fallback=row.get(\"currency\"),\n"
    "            ),\n",
)
replace(
    "bot/domain/auctions/models.py",
    "            if comparable_now >= self.end_time:\n                return False\n",
    "            if comparable_now >= auction_bidding_closes_at(self.end_time):\n                return False\n",
)
replace(
    "bot/domain/auctions/models.py",
    "        return _compatible_now(now, self.end_time) >= self.end_time\n",
    "        return (\n"
    "            _compatible_now(now, self.end_time)\n"
    "            >= auction_bidding_closes_at(self.end_time)\n"
    "        )\n",
)
replace(
    "bot/domain/auctions/models.py",
    "    created_at: datetime | None\n",
    "    created_at: datetime | None\n"
    "    currency: Currency = Currency.DIAMONDS\n",
)
replace(
    "bot/domain/auctions/models.py",
    "            created_at=row.get(\"created_at\"),\n",
    "            created_at=row.get(\"created_at\"),\n"
    "            currency=Currency.from_raw(row.get(\"currency\") or \"алмазы\"),\n",
)

replace(
    "bot/domain/auctions/rules.py",
    '_BID_RE = re.compile(r"^\\s*([\\d\\s_]+)\\s*([кk])?\\s*$", re.IGNORECASE)\n',
    '_BID_RE = re.compile(\n'
    '    r"^\\s*([\\d\\s_]+)\\s*([кk])?\\s*"\n'
    '    r"(?:💎|🍵|☕️?|алмаз(?:ы|ов)?|diamond(?:s)?|чай|чая|чаш(?:ка|ки|ек)?|tea|cups?)?\\s*$",\n'
    '    re.IGNORECASE,\n'
    ')\n',
)
replace(
    "bot/domain/auctions/rules.py",
    "    # У обратного аукциона нет фиксированной верхней границы. Первая ставка\n"
    "    # может быть любой положительной суммой с корректным шагом. После неё\n"
    "    # каждая следующая ставка должна быть ниже текущей лучшей минимум на шаг.\n"
    "    if current_best is None:\n"
    "        if amount_i < step:\n"
    "            raise BidTooLow(minimum=step, current_max=None)\n"
    "        if step > 1 and amount_i % step != 0:\n"
    "            raise BidStepError(amount=amount_i, start_price=0, step=step)\n"
    "        return amount_i\n\n"
    "    maximum = int(current_best) - step\n",
    "    # Стартовая цена обратного аукциона является верхним потолком.\n"
    "    # Первая ставка не может быть выше него, последующие снижаются на шаг.\n"
    "    maximum = int(start_price) if current_best is None else int(current_best) - step\n",
)

replace(
    "bot/domain/auctions/__init__.py",
    "from bot.domain.auctions.models import Auction, Autobid, Bid, BidPlacement, BidRevision\n",
    "from bot.domain.auctions.bidding import (\n"
    "    BidOffer,\n"
    "    TEA_TO_DIAMONDS,\n"
    "    auction_bidding_closes_at,\n"
    "    comparison_units,\n"
    "    parse_bid_offer,\n"
    "    validate_reverse_offer,\n"
    ")\n"
    "from bot.domain.auctions.models import Auction, Autobid, Bid, BidPlacement, BidRevision\n",
)
replace(
    "bot/domain/auctions/__init__.py",
    '    "BidOwnershipError",\n',
    '    "BidOwnershipError",\n'
    '    "BidOffer",\n',
)
replace(
    "bot/domain/auctions/__init__.py",
    '    "Currency",\n',
    '    "Currency",\n'
    '    "TEA_TO_DIAMONDS",\n'
    '    "auction_bidding_closes_at",\n'
    '    "comparison_units",\n'
    '    "parse_bid_offer",\n'
    '    "validate_reverse_offer",\n',
)

replace(
    "bot/repositories/auction_bids.py",
    "    currency,\n    start_price,\n",
    "    currency,\n    accepted_currencies,\n    start_price,\n",
)
insert_point = "    async def get_max_bid(self, auction_id: int, *, excluding_bid_id: int | None = None) -> int | None:\n"
replace(
    "bot/repositories/auction_bids.py",
    insert_point,
    "    async def get_best_bid_units(\n"
    "        self,\n"
    "        auction_id: int,\n"
    "        *,\n"
    "        excluding_bid_id: int | None = None,\n"
    "    ) -> int | None:\n"
    "        exclusion = \"\" if excluding_bid_id is None else \"AND bid_id <> $2\"\n"
    "        parameters = (int(auction_id),) if excluding_bid_id is None else (int(auction_id), int(excluding_bid_id))\n"
    "        value = await self.connection.fetchval(\n"
    "            f\"\"\"\n"
    "            SELECT MIN(\n"
    "                CASE lower(COALESCE(currency, 'алмазы'))\n"
    "                    WHEN 'чашки' THEN amount * 10\n"
    "                    ELSE amount\n"
    "                END\n"
    "            )\n"
    "            FROM public.bids\n"
    "            WHERE auction_id = $1 {exclusion}\n"
    "            \"\"\",\n"
    "            *parameters,\n"
    "        )\n"
    "        return int(value) if value is not None else None\n\n"
    + insert_point,
)
replace(
    "bot/repositories/auction_bids.py",
    "        amount: int,\n        discussion_message_id: int,\n",
    "        amount: int,\n        currency: str,\n        discussion_message_id: int,\n",
)
replace(
    "bot/repositories/auction_bids.py",
    "                    amount,\n                    discussion_message_id\n                )\n                VALUES ($1, $2, $3, $4)\n                RETURNING bid_id, auction_id, bidder_id, amount,\n                          discussion_message_id, placed_at, created_at\n",
    "                    amount,\n                    currency,\n                    discussion_message_id\n                )\n                VALUES ($1, $2, $3, $4, $5)\n                RETURNING bid_id, auction_id, bidder_id, amount, currency,\n                          discussion_message_id, placed_at, created_at\n",
)
replace(
    "bot/repositories/auction_bids.py",
    "                int(amount),\n                int(discussion_message_id),\n",
    "                int(amount),\n                str(currency),\n                int(discussion_message_id),\n",
)
replace(
    "bot/repositories/auction_bids.py",
    "            SELECT bid_id, auction_id, bidder_id, amount,\n                   discussion_message_id, placed_at, created_at\n",
    "            SELECT bid_id, auction_id, bidder_id, amount, currency,\n                   discussion_message_id, placed_at, created_at\n",
    count=1,
)
replace(
    "bot/repositories/auction_bids.py",
    "    async def update_bid_amount(self, bid_id: int, amount: int) -> Bid:\n",
    "    async def update_bid_amount(\n"
    "        self, bid_id: int, amount: int, *, currency: str | None = None\n"
    "    ) -> Bid:\n",
)
replace(
    "bot/repositories/auction_bids.py",
    "            SET amount = $2\n            WHERE bid_id = $1\n            RETURNING bid_id, auction_id, bidder_id, amount,\n                      discussion_message_id, placed_at, created_at\n",
    "            SET amount = $2,\n                currency = COALESCE($3, currency)\n            WHERE bid_id = $1\n            RETURNING bid_id, auction_id, bidder_id, amount, currency,\n                      discussion_message_id, placed_at, created_at\n",
)
replace(
    "bot/repositories/auction_bids.py",
    "            int(amount),\n        )\n        if not row:\n            raise BidNotFound(f\"bid {bid_id} not found\")\n        return Bid.from_record(row)\n\n    async def delete_bid",
    "            int(amount),\n            str(currency) if currency is not None else None,\n        )\n        if not row:\n            raise BidNotFound(f\"bid {bid_id} not found\")\n        return Bid.from_record(row)\n\n    async def delete_bid",
)
replace(
    "bot/repositories/auction_bids.py",
    "            RETURNING bid_id, auction_id, bidder_id, amount,\n                      discussion_message_id, placed_at, created_at\n",
    "            RETURNING bid_id, auction_id, bidder_id, amount, currency,\n                      discussion_message_id, placed_at, created_at\n",
    count=1,
)

replace(
    "bot/services/auction_bids.py",
    "from bot.domain.auctions.rules import parse_bid_amount, validate_bid_for_kind\n",
    "from bot.domain.auctions.bidding import (\n"
    "    parse_bid_offer,\n"
    "    validate_reverse_offer,\n"
    ")\n"
    "from bot.domain.auctions.rules import validate_bid_for_kind\n",
)
replace(
    "bot/services/auction_bids.py",
    "        amount = int(explicit_amount) if explicit_amount is not None else parse_bid_amount(bid_text)\n"
    "        previous_max = await tx.get_best_bid(\n"
    "            auction.auction_id,\n"
    "            lowest_wins=auction.lowest_bid_wins,\n"
    "        )\n"
    "        minimum = validate_bid_for_kind(\n"
    "            amount=amount,\n"
    "            currency=auction.currency,\n"
    "            start_price=auction.start_price,\n"
    "            current_best=previous_max,\n"
    "            auction_kind=auction.auction_kind,\n"
    "        )\n",
    "        if explicit_amount is not None:\n"
    "            amount = int(explicit_amount)\n"
    "            bid_currency = auction.currency\n"
    "        else:\n"
    "            offer = parse_bid_offer(\n"
    "                bid_text,\n"
    "                accepted_currencies=auction.accepted_currencies,\n"
    "                fallback=auction.currency,\n"
    "            )\n"
    "            amount = offer.amount\n"
    "            bid_currency = offer.currency\n"
    "\n"
    "        if auction.lowest_bid_wins:\n"
    "            previous_max = await tx.get_best_bid_units(auction.auction_id)\n"
    "            minimum = validate_reverse_offer(\n"
    "                amount=amount,\n"
    "                currency=bid_currency,\n"
    "                start_price=auction.start_price,\n"
    "                base_currency=auction.currency,\n"
    "                current_best_units=previous_max,\n"
    "            )\n"
    "        else:\n"
    "            previous_max = await tx.get_best_bid(\n"
    "                auction.auction_id,\n"
    "                lowest_wins=False,\n"
    "            )\n"
    "            minimum = validate_bid_for_kind(\n"
    "                amount=amount,\n"
    "                currency=bid_currency,\n"
    "                start_price=auction.start_price,\n"
    "                current_best=previous_max,\n"
    "                auction_kind=auction.auction_kind,\n"
    "            )\n",
)
replace(
    "bot/services/auction_bids.py",
    "            amount=amount,\n            discussion_message_id=bid_message_id,\n",
    "            amount=amount,\n            currency=bid_currency.value,\n            discussion_message_id=bid_message_id,\n",
)
replace(
    "bot/services/auction_bids.py",
    "            amount = parse_bid_amount(new_bid_text or \"\")\n"
    "            other_max = await tx.get_best_bid(\n"
    "                auction.auction_id,\n"
    "                lowest_wins=auction.lowest_bid_wins,\n"
    "                excluding_bid_id=bid.bid_id,\n"
    "            )\n"
    "            minimum = validate_bid_for_kind(\n"
    "                amount=amount,\n"
    "                currency=auction.currency,\n"
    "                start_price=auction.start_price,\n"
    "                current_best=other_max,\n"
    "                auction_kind=auction.auction_kind,\n"
    "            )\n"
    "            updated = await tx.update_bid_amount(bid.bid_id, amount)\n",
    "            offer = parse_bid_offer(\n"
    "                new_bid_text or \"\",\n"
    "                accepted_currencies=auction.accepted_currencies,\n"
    "                fallback=bid.currency,\n"
    "            )\n"
    "            amount = offer.amount\n"
    "            if auction.lowest_bid_wins:\n"
    "                other_max = await tx.get_best_bid_units(\n"
    "                    auction.auction_id, excluding_bid_id=bid.bid_id\n"
    "                )\n"
    "                minimum = validate_reverse_offer(\n"
    "                    amount=amount,\n"
    "                    currency=offer.currency,\n"
    "                    start_price=auction.start_price,\n"
    "                    base_currency=auction.currency,\n"
    "                    current_best_units=other_max,\n"
    "                )\n"
    "            else:\n"
    "                other_max = await tx.get_best_bid(\n"
    "                    auction.auction_id,\n"
    "                    lowest_wins=False,\n"
    "                    excluding_bid_id=bid.bid_id,\n"
    "                )\n"
    "                minimum = validate_bid_for_kind(\n"
    "                    amount=amount,\n"
    "                    currency=offer.currency,\n"
    "                    start_price=auction.start_price,\n"
    "                    current_best=other_max,\n"
    "                    auction_kind=auction.auction_kind,\n"
    "                )\n"
    "            updated = await tx.update_bid_amount(\n"
    "                bid.bid_id, amount, currency=offer.currency.value\n"
    "            )\n",
)

replace(
    "bot/services/auction_workflows.py",
    "        if kind in {AuctionKind.REVERSE, AuctionKind.FREE}:\n"
    "            if price < 0:\n"
    "                raise ValueError(\"start_price must not be negative\")\n"
    "        elif price <= 0:\n",
    "        if kind is AuctionKind.REVERSE:\n"
    "            if price <= 0:\n"
    "                raise ValueError(\"reverse start_price must be greater than zero\")\n"
    "        elif kind is AuctionKind.FREE:\n"
    "            if price < 0:\n"
    "                raise ValueError(\"start_price must not be negative\")\n"
    "        elif price <= 0:\n",
)

replace(
    "bot/handlers/auction/submission.py",
    "    if is_reverse or is_free:\n"
    "        await state.update_data(start_price=0, min_start=None, max_start=None)\n"
    "        await message.answer(\n"
    "            USER_MESSAGES.get(\n"
    "                \"add_comment\",\n"
    "                \"Введите комментарий к лоту или '-' если не нужен:\",\n"
    "            ),\n"
    "            reply_markup=ReplyKeyboardRemove(),\n"
    "        )\n"
    "        await state.set_state(UserAddLotFSM.waiting_for_comment)\n"
    "        return\n",
    "    if is_free:\n"
    "        await state.update_data(start_price=0, min_start=None, max_start=None)\n"
    "        await message.answer(\n"
    "            USER_MESSAGES.get(\n"
    "                \"add_comment\",\n"
    "                \"Введите комментарий к лоту или '-' если не нужен:\",\n"
    "            ),\n"
    "            reply_markup=ReplyKeyboardRemove(),\n"
    "        )\n"
    "        await state.set_state(UserAddLotFSM.waiting_for_comment)\n"
    "        return\n"
    "\n"
    "    if is_reverse:\n"
    "        min_allowed, max_allowed, hint = await compute_start_price_limits(state, currency)\n"
    "        max_allowed = max(min_allowed, max_allowed)\n"
    "        emoji = _cur_emoji(currency)\n"
    "        step = _cur_step(currency)\n"
    "        mixed_note = (\n"
    "            \"\\nДля смешанного лота это потолок в чае; \"\n"
    "            \"ставки сравниваются по курсу 1 🍵 = 10 💎.\"\n"
    "            if len(accepted_currencies) > 1\n"
    "            else \"\"\n"
    "        )\n"
    "        await message.answer(\n"
    "            f\"Стартовый потолок обратного аукциона: \"\n"
    "            f\"<b>{min_allowed}–{max_allowed} {emoji}</b>\\n\"\n"
    "            f\"({hint})\\nШаг: {step}.{mixed_note}\\n\\n\"\n"
    "            \"Введите стартовый потолок целым числом:\",\n"
    "            parse_mode=\"HTML\",\n"
    "            reply_markup=ReplyKeyboardRemove(),\n"
    "        )\n"
    "        await state.update_data(min_start=min_allowed, max_start=max_allowed)\n"
    "        await state.set_state(UserAddLotFSM.waiting_for_start_price)\n"
    "        return\n",
)
replace(
    "bot/handlers/auction/submission.py",
    "        if kind_key == AuctionKind.REVERSE.value:\n"
    "            price_line = (\n"
    "                f\"Валюта ставок: {accepted_label}\\n\"\n"
    "                \"Побеждает минимальная ставка.\\n\"\n"
    "            )\n",
    "        if kind_key == AuctionKind.REVERSE.value:\n"
    "            price_line = (\n"
    "                f\"Валюта ставок: {accepted_label}\\n\"\n"
    "                f\"Стартовый потолок: {d.get('start_price')} {emoji}\\n\"\n"
    "                \"Побеждает минимальная ставка.\\n\"\n"
    "            )\n",
)
replace(
    "bot/handlers/auction/submission.py",
    "    if kind_key == AuctionKind.REVERSE.value:\n"
    "        price_preview = (\n"
    "            f\"Валюта ставок: <b>{html.escape(currencies_preview)}</b>\\n\"\n"
    "            \"Ставки идут на понижение\"\n"
    "        )\n",
    "    if kind_key == AuctionKind.REVERSE.value:\n"
    "        price_preview = (\n"
    "            f\"Валюта ставок: <b>{html.escape(currencies_preview)}</b>\\n\"\n"
    "            f\"Стартовый потолок: <b>{int(start_price)}</b> {_emoji_by_currency(currency)}\\n\"\n"
    "            \"Ставки идут на понижение\"\n"
    "        )\n",
)
replace(
    "bot/handlers/auction/submission.py",
    "        if kind_key == AuctionKind.REVERSE.value:\n"
    "            price_log_line = (\n"
    "                f\"💱 Валюта ставок: <b>{accepted_label}</b>\\n\"\n"
    "                \"📉 Побеждает минимальная ставка.\\n\"\n"
    "            )\n",
    "        if kind_key == AuctionKind.REVERSE.value:\n"
    "            price_log_line = (\n"
    "                f\"💱 Валюта ставок: <b>{accepted_label}</b>\\n\"\n"
    "                f\"💰 Стартовый потолок: <b>{int(start_price)} {cur_emoji}</b>\\n\"\n"
    "                \"📉 Побеждает минимальная ставка.\\n\"\n"
    "            )\n",
)

replace(
    "bot/handlers/admin/helper/admin_constants.py",
    "    if kind_key == \"reverse\":\n"
    "        price_line = (\n"
    "            f\"Валюта ставок: {accepted_label}\\n\"\n"
    "            \"Ставки идут на понижение. Побеждает минимальная ставка.\\n\\n\"\n"
    "        )\n"
    "        rules_line = \"Ставки только цифрами в комментариях к этому посту!\"\n",
    "    if kind_key == \"reverse\":\n"
    "        price_line = (\n"
    "            f\"Валюта ставок: {accepted_label}\\n\"\n"
    "            f\"Стартовый потолок: {start_price} {emoji}\\n\"\n"
    "            \"Ставки идут на понижение. Побеждает минимальная ставка.\\n\\n\"\n"
    "        )\n"
    "        rules_line = (\n"
    "            \"Ставки указывайте суммой и валютой, если доступны и чай, и алмазы!\"\n"
    "        )\n",
)

replace(
    "userbot/repositories.py",
    "                       currency,\n                       start_time,\n",
    "                       currency,\n                       accepted_currencies,\n                       start_time,\n",
)
replace(
    "userbot/services.py",
    "from bot.domain.auctions import BidFormatError\n",
    "from bot.domain.auctions import BidFormatError, auction_bidding_closes_at\n",
)
replace(
    "userbot/services.py",
    "    return ensure_utc(start_time) <= utc_now() <= ensure_utc(end_time)\n",
    "    return (\n"
    "        ensure_utc(start_time) <= utc_now()\n"
    "        < auction_bidding_closes_at(ensure_utc(end_time))\n"
    "    )\n",
)
replace(
    "userbot/services.py",
    "        return utc_now() > ensure_utc(end_time)\n",
    "        return utc_now() >= auction_bidding_closes_at(ensure_utc(end_time))\n",
)

replace(
    "userbot/handlers/new_messages.py",
    "    Currency,\n    UnsupportedCurrency,\n",
    "    Currency,\n"
    "    UnsupportedCurrency,\n"
    "    normalize_currency_choices,\n"
    "    parse_bid_offer,\n",
)
replace(
    "userbot/handlers/new_messages.py",
    "            f\"{revision.auction.currency.emoji}.\",\n",
    "            f\"{revision.bid.currency.emoji}.\",\n",
)
replace(
    "userbot/handlers/new_messages.py",
    "    # Единые правила валюты и ставок используются и bot, и userbot.\n"
    "    try:\n"
    "        currency = Currency.from_raw(auction.get(\"currency\"))\n"
    "    except UnsupportedCurrency:\n",
    "    # Единые правила валюты и ставок используются и bot, и userbot.\n"
    "    accepted_currencies = normalize_currency_choices(\n"
    "        auction.get(\"accepted_currencies\"), fallback=auction.get(\"currency\")\n"
    "    )\n"
    "    try:\n"
    "        offer = parse_bid_offer(\n"
    "            text_raw,\n"
    "            accepted_currencies=accepted_currencies,\n"
    "            fallback=Currency.from_raw(auction.get(\"currency\")),\n"
    "        )\n"
    "        currency = offer.currency\n"
    "    except BidFormatError:\n"
    "        offer = None\n"
    "        currency = Currency.from_raw(auction.get(\"currency\"))\n"
    "    except UnsupportedCurrency:\n",
)
replace(
    "userbot/handlers/new_messages.py",
    "    amount = int(mapped[\"amount\"]) if is_autobid_msg else _try_parse_bid_amount(text_raw)\n",
    "    amount = (\n"
    "        int(mapped[\"amount\"])\n"
    "        if is_autobid_msg\n"
    "        else (offer.amount if offer is not None else _try_parse_bid_amount(text_raw))\n"
    "    )\n",
)
replace(
    "userbot/handlers/new_messages.py",
    "        \"auction_id\": int(placement.auction.auction_id),\n",
    "        \"auction_id\": int(placement.auction.auction_id),\n"
    "        \"currency\": placement.bid.currency.value,\n",
)

replace(
    "bot/repositories/auctions.py",
    "                          AND end_time <= $1\n",
    "                          AND date_trunc('minute', end_time)\n"
    "                              + INTERVAL '1 minute' <= $1\n",
)
replace(
    "bot/handlers/auction_comments.py",
    "    if end_time and now >= end_time:\n",
    "    if end_time and now >= end_time.replace(second=0, microsecond=0) + timedelta(minutes=1):\n",
)

replace(
    "bot/repositories/auction_winners.py",
    "                SELECT bidder_id, amount, discussion_message_id, placed_at\n",
    "                SELECT bidder_id, amount, currency, discussion_message_id, placed_at\n",
)
replace(
    "bot/repositories/auction_winners.py",
    "                ORDER BY amount {direction}, placed_at ASC, bid_id ASC\n",
    "                ORDER BY\n"
    "                    CASE WHEN {str(lowest_wins).upper()} THEN\n"
    "                        CASE lower(COALESCE(currency, 'алмазы'))\n"
    "                            WHEN 'чашки' THEN amount * 10\n"
    "                            ELSE amount\n"
    "                        END\n"
    "                    END {direction},\n"
    "                    amount {direction}, placed_at ASC, bid_id ASC\n",
)
replace(
    "bot/repositories/auction_winners.py",
    "                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse'\n                        THEN b.amount END ASC,\n",
    "                    CASE WHEN lower(COALESCE(a.auction_kind, 'standard')) = 'reverse'\n"
    "                        THEN CASE lower(COALESCE(b.currency, a.currency))\n"
    "                            WHEN 'чашки' THEN b.amount * 10\n"
    "                            ELSE b.amount\n"
    "                        END END ASC,\n",
)

replace(
    "bot/handlers/auction/winner_components/announcement.py",
    "from bot.domain.auctions import AuctionKind\n",
    "from bot.domain.auctions import AuctionKind, Currency, comparison_units\n",
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    "    direction = 1 if kind.lowest_bid_wins else -1\n"
    "    return sorted(\n"
    "        bids,\n"
    "        key=lambda bid: (\n"
    "            direction * int(bid[\"amount\"] if isinstance(bid, dict) else bid.amount),\n"
    "            bid[\"placed_at\"] if isinstance(bid, dict) else bid.placed_at,\n"
    "        ),\n"
    "    )[0]\n",
    "    direction = 1 if kind.lowest_bid_wins else -1\n"
    "\n"
    "    def value(bid: Any) -> int:\n"
    "        amount = int(bid[\"amount\"] if isinstance(bid, dict) else bid.amount)\n"
    "        if not kind.lowest_bid_wins:\n"
    "            return amount\n"
    "        raw_currency = (\n"
    "            bid.get(\"currency\") if isinstance(bid, dict) else getattr(bid, \"currency\", None)\n"
    "        )\n"
    "        return comparison_units(amount, Currency.from_raw(raw_currency or \"алмазы\"))\n"
    "\n"
    "    return sorted(\n"
    "        bids,\n"
    "        key=lambda bid: (\n"
    "            direction * value(bid),\n"
    "            bid[\"placed_at\"] if isinstance(bid, dict) else bid.placed_at,\n"
    "        ),\n"
    "    )[0]\n",
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    "    final_amount = int(amount or 0)\n",
    "    final_amount = int(amount or 0)\n"
    "    winning_currency = Currency.from_raw(\n"
    "        (winner_bid.get(\"currency\") if isinstance(winner_bid, dict) else getattr(winner_bid, \"currency\", None))\n"
    "        or currency\n"
    "    )\n"
    "    currency_emoji = winning_currency.emoji\n",
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    "        amount = int(top_bid[\"amount\"]) if top_bid and top_bid.get(\"amount\") is not None else 0\n",
    "        amount = int(top_bid[\"amount\"]) if top_bid and top_bid.get(\"amount\") is not None else 0\n"
    "        if top_bid and top_bid.get(\"currency\"):\n"
    "            currency_emoji = Currency.from_raw(top_bid[\"currency\"]).emoji\n",
)

write(
    "db/migrations/012_bid_currency_and_deadline_contract.sql",
    '''ALTER TABLE public.bids
    ADD COLUMN IF NOT EXISTS currency TEXT;

UPDATE public.bids AS b
SET currency = a.currency
FROM public.auctions AS a
WHERE a.auction_id = b.auction_id
  AND (b.currency IS NULL OR btrim(b.currency) = '');

CREATE OR REPLACE FUNCTION public.fill_bid_currency()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.currency IS NULL OR btrim(NEW.currency) = '' THEN
        SELECT a.currency INTO NEW.currency
        FROM public.auctions AS a
        WHERE a.auction_id = NEW.auction_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_fill_bid_currency ON public.bids;
CREATE TRIGGER trg_fill_bid_currency
BEFORE INSERT OR UPDATE OF auction_id, currency ON public.bids
FOR EACH ROW EXECUTE FUNCTION public.fill_bid_currency();

ALTER TABLE public.bids
    ALTER COLUMN currency SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bids_auction_currency_amount
    ON public.bids (auction_id, currency, amount, placed_at, bid_id);
''',
)

replace(
    ".github/workflows/ci.yml",
    "assert items[-1].filename == '011_schedule_setup_master.sql'",
    "assert items[-1].filename == '012_bid_currency_and_deadline_contract.sql'",
)

replace(
    "tests/test_auction_domain.py",
    "from bot.domain.auctions import Auction, BidFormatError, BidStepError, BidTooLow, Currency\n",
    "from bot.domain.auctions import (\n"
    "    Auction,\n"
    "    BidFormatError,\n"
    "    BidStepError,\n"
    "    BidTooLow,\n"
    "    Currency,\n"
    "    auction_bidding_closes_at,\n"
    "    comparison_units,\n"
    "    parse_bid_offer,\n"
    ")\n",
)
replace(
    "tests/test_auction_domain.py",
    "    assert ended.is_active_at(now) is False\n    assert ended.has_ended_at(now) is True\n",
    "    assert ended.is_active_at(now) is True\n"
    "    assert ended.has_ended_at(now) is False\n"
    "    closes_at = auction_bidding_closes_at(now)\n"
    "    assert ended.is_active_at(closes_at - timedelta(microseconds=1)) is True\n"
    "    assert ended.is_active_at(closes_at) is False\n"
    "    assert ended.has_ended_at(closes_at) is True\n"
    "\n"
    "\n"
    "def test_mixed_currency_offer_requires_marker_and_uses_project_rate() -> None:\n"
    "    accepted = (Currency.CUPS, Currency.DIAMONDS)\n"
    "    with pytest.raises(BidFormatError):\n"
    "        parse_bid_offer(\"12\", accepted_currencies=accepted, fallback=Currency.CUPS)\n"
    "    tea = parse_bid_offer(\"12 чай\", accepted_currencies=accepted, fallback=Currency.CUPS)\n"
    "    diamonds = parse_bid_offer(\"120 алмазов\", accepted_currencies=accepted, fallback=Currency.CUPS)\n"
    "    assert comparison_units(tea.amount, tea.currency) == 120\n"
    "    assert comparison_units(diamonds.amount, diamonds.currency) == 120\n",
)

write(
    "tests/test_reverse_auction_no_ceiling.py",
    '''from __future__ import annotations

from pathlib import Path

import pytest

from bot.domain.auctions import BidTooHigh, Currency, validate_reverse_offer

ROOT = Path(__file__).resolve().parents[1]


def test_reverse_first_bid_respects_starting_ceiling() -> None:
    assert validate_reverse_offer(
        amount=100,
        currency=Currency.DIAMONDS,
        start_price=10,
        base_currency=Currency.CUPS,
        current_best_units=None,
    ) == 100
    with pytest.raises(BidTooHigh) as exc:
        validate_reverse_offer(
            amount=110,
            currency=Currency.DIAMONDS,
            start_price=10,
            base_currency=Currency.CUPS,
            current_best_units=None,
        )
    assert exc.value.maximum == 100


def test_reverse_mixed_currency_bid_must_improve_in_common_units() -> None:
    assert validate_reverse_offer(
        amount=8,
        currency=Currency.CUPS,
        start_price=20,
        base_currency=Currency.CUPS,
        current_best_units=100,
    ) == 8
    assert validate_reverse_offer(
        amount=90,
        currency=Currency.DIAMONDS,
        start_price=20,
        base_currency=Currency.CUPS,
        current_best_units=100,
    ) == 90


def test_submission_routes_reverse_to_starting_ceiling() -> None:
    source = (ROOT / "bot/handlers/auction/submission.py").read_text(encoding="utf-8")
    assert "if is_reverse:" in source
    assert "Стартовый потолок обратного аукциона" in source
    assert "if is_reverse or is_free" not in source


def test_finalizer_waits_until_next_minute() -> None:
    source = (ROOT / "bot/repositories/auctions.py").read_text(encoding="utf-8")
    assert "date_trunc('minute', end_time)" in source
    assert "+ INTERVAL '1 minute' <= $1" in source


def test_bid_currency_migration_is_packaged() -> None:
    migration = ROOT / "db/migrations/012_bid_currency_and_deadline_contract.sql"
    source = migration.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS currency TEXT" in source
    assert "CREATE TRIGGER trg_fill_bid_currency" in source
''',
)

# The staging workflow and this script must not survive the generated commit.
(ROOT / ".github/workflows/apply-auction-contract-fix.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
