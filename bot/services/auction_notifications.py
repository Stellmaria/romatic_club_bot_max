"""Compatibility adapter for legacy auction notification timing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Any

from aiogram import Bot

from bot import auction_notify as legacy_notifications
from bot.domain.auctions import auction_bidding_closes_at

ListAuctions = Callable[[Iterable[str]], Awaitable[list[dict[str, Any]]]]
_LEGACY_LIST_AUCTIONS: ListAuctions = legacy_notifications.list_auctions


def canonicalize_notification_deadlines(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose the exclusive close instant to the legacy notification loop.

    Auction rows persist the final accepted bidding second, for example
    ``18:30:59``. The legacy notifier compares ``now >= end_time`` directly,
    so it must receive the exclusive close instant ``18:31:00`` instead.
    """

    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        end_time = item.get("end_time")
        if isinstance(end_time, datetime):
            item["end_time"] = auction_bidding_closes_at(end_time)
        normalized.append(item)
    return normalized


async def _list_auctions_with_close_deadlines(
    statuses: Iterable[str],
) -> list[dict[str, Any]]:
    rows = await _LEGACY_LIST_AUCTIONS(statuses)
    normalized_statuses = {
        str(status).strip().lower()
        for status in statuses
        if str(status).strip()
    }
    if normalized_statuses == {"active"}:
        return canonicalize_notification_deadlines(rows)
    return rows


async def auction_notifications_loop(bot: Bot, channel_username: str) -> None:
    """Run the legacy notifier with the canonical auction-close contract."""

    original = legacy_notifications.list_auctions
    legacy_notifications.list_auctions = _list_auctions_with_close_deadlines
    try:
        await legacy_notifications.auction_notifications_loop(bot, channel_username)
    finally:
        legacy_notifications.list_auctions = original


__all__ = ["auction_notifications_loop", "canonicalize_notification_deadlines"]
