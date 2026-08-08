"""Auction-home read model for the Telegram Mini App."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from bot.core.time import ensure_utc, serialize_timestamp, to_moscow, utc_now
from db.auctions import (
    get_auctions_by_date,
    get_lot_by_id,
    get_lot_owners,
    get_occupied_slots,
    get_top_bid_for_auction,
)

PUBLIC_AUCTION_STATUSES = frozenset({"scheduled", "publishing", "active"})
UPCOMING_LIMIT = 5
SLOT_DURATION = timedelta(minutes=30)
LUXURY_DAY_START = time(11, 0)
LUXURY_DAY_LAST_START = time(22, 0)


def parse_selected_date(raw: str | None, *, today: date) -> date:
    if raw is None or not raw.strip():
        return today
    try:
        selected = date.fromisoformat(raw.strip())
    except ValueError as error:
        raise ValueError("date must use YYYY-MM-DD format") from error
    if selected < today:
        raise ValueError("past dates are not available")
    return selected


async def build_auction_home(
    selected_date: date,
    *,
    channel_username: str,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = ensure_utc(now or utc_now())
    rows = await get_auctions_by_date(selected_date)
    visible = [
        row for row in rows if str(row.get("status") or "").casefold() in PUBLIC_AUCTION_STATUSES
    ]

    active_row = _select_active(visible, current_time)
    upcoming_rows = _select_upcoming(
        visible,
        current_time,
        active_id=_row_id(active_row) if active_row is not None else None,
    )

    active = (
        await _enrich_auction(
            active_row,
            channel_username=channel_username,
            include_current_bid=True,
        )
        if active_row is not None
        else None
    )
    upcoming = [
        await _enrich_auction(
            row,
            channel_username=channel_username,
            include_current_bid=False,
        )
        for row in upcoming_rows
    ]

    return {
        "date": selected_date.isoformat(),
        "active": active,
        "upcoming": upcoming,
        "generated_at": serialize_timestamp(current_time),
    }


async def list_free_slots(
    selected_date: date,
    *,
    now: datetime | None = None,
) -> list[str]:
    occupied = await get_occupied_slots(selected_date)
    slots: list[str] = []
    current = datetime.combine(selected_date, LUXURY_DAY_START)
    last = datetime.combine(selected_date, LUXURY_DAY_LAST_START)
    business_now = to_moscow(ensure_utc(now or utc_now()))

    while current <= last:
        slot_time = current.time()
        is_past_today = selected_date == business_now.date() and slot_time <= business_now.time()
        if not is_past_today and not any(start <= slot_time < end for start, end in occupied):
            slots.append(slot_time.strftime("%H:%M"))
        current += SLOT_DURATION
    return slots


def _select_active(
    rows: Sequence[Mapping[str, Any]],
    now: datetime,
) -> Mapping[str, Any] | None:
    active = [row for row in rows if str(row.get("status") or "") == "active"]
    if active:
        return min(active, key=_start_sort_key)

    publishing = [
        row
        for row in rows
        if str(row.get("status") or "") == "publishing" and _contains_time(row, now)
    ]
    return min(publishing, key=_start_sort_key) if publishing else None


def _select_upcoming(
    rows: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    active_id: int | None,
) -> list[Mapping[str, Any]]:
    upcoming: list[Mapping[str, Any]] = []
    for row in rows:
        if active_id is not None and _row_id(row) == active_id:
            continue
        start_time = row.get("start_time")
        if not isinstance(start_time, datetime):
            continue
        if ensure_utc(start_time) <= now:
            continue
        upcoming.append(row)
    return sorted(upcoming, key=_start_sort_key)[:UPCOMING_LIMIT]


def _contains_time(row: Mapping[str, Any], now: datetime) -> bool:
    start_time = row.get("start_time")
    end_time = row.get("end_time")
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        return False
    return ensure_utc(start_time) <= now < ensure_utc(end_time)


def _start_sort_key(row: Mapping[str, Any]) -> datetime:
    value = row.get("start_time")
    if not isinstance(value, datetime):
        return datetime.max.replace(tzinfo=UTC)
    return ensure_utc(value)


def _row_id(row: Mapping[str, Any] | None) -> int | None:
    if row is None:
        return None
    value = row.get("auction_id")
    return int(value) if value is not None else None


async def _enrich_auction(
    row: Mapping[str, Any],
    *,
    channel_username: str,
    include_current_bid: bool,
) -> dict[str, object]:
    auction_id = int(row["auction_id"])
    details = await get_lot_by_id(auction_id) or dict(row)
    owners = await get_lot_owners(auction_id)
    current_bid = await _current_bid(auction_id) if include_current_bid else None
    return _serialize_auction(
        details,
        owners=owners,
        current_bid=current_bid,
        channel_username=channel_username,
    )


async def _current_bid(auction_id: int) -> int | None:
    amount, _ = await get_top_bid_for_auction(auction_id)
    return amount


def _serialize_auction(
    row: Mapping[str, Any],
    *,
    owners: Sequence[Mapping[str, Any]],
    current_bid: int | None,
    channel_username: str,
) -> dict[str, object]:
    card_id = _optional_int(row.get("card_id"))
    start_time = row.get("start_time")
    end_time = row.get("end_time")
    start_price = int(row.get("start_price") or 0)
    seller = _public_seller(owners[0]) if owners else None

    return {
        "id": int(row["auction_id"]),
        "status": str(row.get("status") or ""),
        "auction_kind": str(row.get("auction_kind") or "standard"),
        "start_price": start_price,
        "current_bid": current_bid,
        "display_price": current_bid if current_bid is not None else start_price,
        "currency": str(row.get("currency") or ""),
        "start_time": _timestamp_or_none(start_time),
        "end_time": _timestamp_or_none(end_time),
        "telegram_url": _telegram_post_url(channel_username, row.get("message_id")),
        "seller": seller,
        "card": {
            "id": card_id,
            "name": str(row.get("card_name") or ""),
            "hero_name": str(row.get("hero_name") or ""),
            "num": _optional_int(row.get("card_num")),
            "deck_id": _optional_int(row.get("deck_id")),
            "deck_name": str(row.get("deck_name") or ""),
            "rarity": str(row.get("rarity") or ""),
            "story": str(row.get("story") or ""),
            "quote": str(row.get("quote") or ""),
            "obtain_type": str(row.get("obtain_type") or ""),
            "obtain_amount": int(row.get("obtain_amount") or 0),
            "image_url": f"/api/webapp/cards/{card_id}/image" if card_id else None,
        },
    }


def _public_seller(owner: Mapping[str, Any]) -> dict[str, object]:
    username = str(owner.get("username") or "").strip().lstrip("@")
    full_name = str(owner.get("full_name") or "").strip()
    display_name = full_name or (f"@{username}" if username else "Seller")
    return {
        "display_name": display_name,
        "verified": bool(owner.get("is_trusted")),
    }


def _telegram_post_url(channel_username: str, message_id: object) -> str | None:
    username = channel_username.strip().lstrip("@")
    if not username or message_id is None:
        return None
    try:
        numeric_message_id = int(message_id)
    except (TypeError, ValueError):
        return None
    if numeric_message_id <= 0:
        return None
    return f"https://t.me/{username}/{numeric_message_id}"


def _timestamp_or_none(value: object) -> str | None:
    return serialize_timestamp(value) if isinstance(value, datetime) else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


__all__ = [
    "build_auction_home",
    "list_free_slots",
    "parse_selected_date",
]
