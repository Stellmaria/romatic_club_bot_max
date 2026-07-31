"""Compatibility hooks for the focused winner feature."""

from __future__ import annotations

from typing import Any

from aiogram import Bot

from bot.features.winner import (
    _post_rules_under_lot as _feature_post_rules_under_lot,
    announce_winner as _feature_announce_winner,
)


async def announce_winner(
    telegram_bot: Bot,
    auction: dict[str, Any],
    bids: list[Any],
    send_admin_log=None,
) -> None:
    await _feature_announce_winner(
        telegram_bot,
        auction,
        bids,
        send_admin_log=send_admin_log,
    )


async def _post_rules_under_lot(
    bot: Bot,
    auction_id: int,
    retries: int = 5,
    delay: float = 1.5,
) -> None:
    await _feature_post_rules_under_lot(
        bot,
        auction_id,
        retries=retries,
        delay=delay,
    )


__all__ = ["announce_winner", "_post_rules_under_lot"]
