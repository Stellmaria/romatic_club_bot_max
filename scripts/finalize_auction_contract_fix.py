from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(
            f"{path}: expected {count} occurrence(s), found {actual}: {old[:120]!r}"
        )
    target.write_text(text.replace(old, new, count), encoding="utf-8")


replace(
    "userbot/services.py",
    '''async def _fetch_best_bid(auction_id: int, *, lowest_wins: bool) -> int | None:
    return await (await _repository()).fetch_best_bid(
        int(auction_id),
        lowest_wins=lowest_wins,
    )


async def _fetch_max_bid(auction_id: int) -> int | None:
''',
    '''async def _fetch_best_bid(auction_id: int, *, lowest_wins: bool) -> int | None:
    return await (await _repository()).fetch_best_bid(
        int(auction_id),
        lowest_wins=lowest_wins,
    )


async def _fetch_best_bid_units(auction_id: int) -> int | None:
    return await (await _repository()).fetch_best_bid_units(int(auction_id))


async def _fetch_max_bid(auction_id: int) -> int | None:
''',
)

replace(
    "userbot/handlers/new_messages.py",
    '''    normalize_currency_choices,
    parse_bid_offer,
)
''',
    '''    normalize_currency_choices,
    parse_bid_offer,
    reverse_maximum_for_currency,
)
''',
)
replace(
    "userbot/handlers/new_messages.py",
    '''    _fetch_auction_by_root,
    _fetch_best_bid,
    _fetch_max_bid,
''',
    '''    _fetch_auction_by_root,
    _fetch_best_bid,
    _fetch_best_bid_units,
    _fetch_max_bid,
''',
)
replace(
    "userbot/handlers/new_messages.py",
    '''    step = currency.bid_step
    emoji = currency.emoji
    start_price = int(auction.get("start_price") or 0)
    best_bid = await _fetch_best_bid(
        int(auction["auction_id"]),
        lowest_wins=auction_kind.lowest_bid_wins,
    )
    if auction_kind.lowest_bid_wins:
        min_required = start_price if best_bid is None else max(1, int(best_bid) - step)
        bid_limit_label = "Максимум"
    else:
        min_required = minimum_next_bid(
            start_price=start_price,
            current_max=best_bid,
            step=step,
        )
        bid_limit_label = "Минимум"

    amount = (
        int(mapped["amount"])
        if is_autobid_msg
        else (offer.amount if offer is not None else _try_parse_bid_amount(text_raw))
    )
''',
    '''    step = currency.bid_step
    emoji = currency.emoji
    start_price = int(auction.get("start_price") or 0)
    amount = (
        int(mapped["amount"])
        if is_autobid_msg
        else (offer.amount if offer is not None else _try_parse_bid_amount(text_raw))
    )

    if auction_kind.lowest_bid_wins:
        best_bid_units = await _fetch_best_bid_units(int(auction["auction_id"]))
        reverse_maximum = reverse_maximum_for_currency(
            currency=currency,
            start_price=start_price,
            base_currency=Currency.from_raw(auction.get("currency")),
            current_best_units=best_bid_units,
        )
        min_required = (
            int(reverse_maximum)
            if reverse_maximum is not None
            else max(step, int(amount or step))
        )
        bid_limit_label = "Максимум"
    else:
        best_bid = await _fetch_best_bid(
            int(auction["auction_id"]),
            lowest_wins=False,
        )
        min_required = minimum_next_bid(
            start_price=start_price,
            current_max=best_bid,
            step=step,
        )
        bid_limit_label = "Минимум"
''',
)
replace(
    "userbot/handlers/new_messages.py",
    '''            if cached is not None:
                cached["amount"] = int(revision.bid.amount)
''',
    '''            if cached is not None:
                cached["amount"] = int(revision.bid.amount)
                cached["currency"] = revision.bid.currency.value
''',
)

replace(
    "bot/handlers/auction/winner_components/announcement.py",
    '''async def _winner_preview_text(
    service: AuctionWinnerService,
    auction_id: int,
    amount: int,
    winner_id: int,
) -> str:
    auction = await service.auction(auction_id) or {}
    currency_emoji = emoji_by_currency(auction.get("currency"))
''',
    '''async def _winner_preview_text(
    service: AuctionWinnerService,
    auction_id: int,
    amount: int,
    winner_id: int,
    currency_emoji: str,
) -> str:
    auction = await service.auction(auction_id) or {}
''',
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    '''        if top_bid:
            winner_bidder_id = int(top_bid["bidder_id"])
            amount = int(top_bid["amount"])
            win_message_id = top_bid.get("discussion_message_id")
''',
    '''        if top_bid:
            winner_bid = top_bid
            winner_bidder_id = int(top_bid["bidder_id"])
            amount = int(top_bid["amount"])
            win_message_id = top_bid.get("discussion_message_id")
''',
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    '''    preview = await _winner_preview_text(service, auction_id, final_amount, winner_id)
''',
    '''    preview = await _winner_preview_text(
        service,
        auction_id,
        final_amount,
        winner_id,
        currency_emoji,
    )
''',
)
replace(
    "bot/handlers/auction/winner_components/announcement.py",
    '''    if override_amount is not None:
        amount = int(override_amount)
    else:
        kind = AuctionKind.from_raw(auction.get("auction_kind"))
        top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
        amount = int(top_bid["amount"]) if top_bid and top_bid.get("amount") is not None else 0
        if top_bid and top_bid.get("currency"):
            currency_emoji = Currency.from_raw(top_bid["currency"]).emoji
''',
    '''    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    top_bid = await service.top_bid(auction_id, lowest_wins=kind.lowest_bid_wins)
    if top_bid and top_bid.get("currency"):
        currency_emoji = Currency.from_raw(top_bid["currency"]).emoji
    if override_amount is not None:
        amount = int(override_amount)
    else:
        amount = int(top_bid["amount"]) if top_bid and top_bid.get("amount") is not None else 0
''',
)

# This script is a temporary repository editing aid, not runtime code.
Path(__file__).unlink(missing_ok=True)
