"""Compatibility facade for the Phase 9 winner workflow split.

Public imports stay stable while Telegram handlers live in focused modules under
``winner_components``.
"""
from __future__ import annotations

from typing import Any

from aiogram import Bot, Router
from aiogram.types import Message

from bot.handlers.auction.winner_components import (
    announcement_router,
    build_thanks_kb as _build_thanks_kb,
    cmd_print_win as _cmd_print_win,
    fmt_msk,
    get_admin_thanks_totals as _get_admin_thanks_totals,
    get_winner as _get_winner,
    msk_now,
    post_rules_under_lot,
    print_exchange_router,
    print_win_router,
    send_notifications,
    thanks_router,
)
from bot.handlers.auction.winner_components.announcement import announce_winner as _announce_winner

router = Router(name="auction_winner")
router.include_router(announcement_router)
router.include_router(print_win_router)
router.include_router(print_exchange_router)
router.include_router(thanks_router)


def get_winner(bids: list[Any], auction_kind: str = "standard") -> Any | None:
    return _get_winner(bids, auction_kind)


async def announce_winner(telegram_bot: Bot, auction: dict[str, Any], bids: list[Any], send_admin_log=None) -> None:
    await _announce_winner(telegram_bot, auction, bids, send_admin_log=send_admin_log)


async def cmd_print_win(message: Message, bot: Bot) -> None:
    """Compatibility entry point; the decorated handler lives in print_win.py."""
    await _cmd_print_win(message, bot)


async def _post_rules_under_lot(bot: Bot, auction_id: int, retries: int = 5, delay: float = 1.5) -> None:
    await post_rules_under_lot(bot, auction_id, retries=retries, delay=delay)


async def _send_notifications(
    bot: Bot,
    auction_id: int,
    winner_id: int,
    *,
    override_amount: int | None = None,
):
    return await send_notifications(
        bot,
        auction_id,
        winner_id,
        override_amount=override_amount,
    )


def _msk_now():
    return msk_now()


def _fmt_msk(value):
    return fmt_msk(value)


async def build_thanks_kb(any_id: int, moderator_tag: str):
    return await _build_thanks_kb(any_id, moderator_tag)


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    return await _get_admin_thanks_totals(author)
