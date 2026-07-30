"""Background workers owned by the userbot process."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.core.settings import DISCUSSION_CHAT_ID
from db.auctions import list_autobids
from userbot.autobid_engine import maybe_place_autobid

if TYPE_CHECKING:
    from telethon import TelegramClient


logger = logging.getLogger("userbot")


async def autobid_watchdog(telegram_client: TelegramClient) -> None:
    while True:
        try:
            rows = await list_autobids(auction_id=None, only_active=True)
            auction_ids = {int(row["auction_id"]) for row in rows}
            for auction_id in auction_ids:
                await maybe_place_autobid(
                    telegram_client,
                    discussion_chat_id=int(DISCUSSION_CHAT_ID),
                    auction_id=auction_id,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Autobid watchdog failed")
        await asyncio.sleep(15)


__all__ = ["autobid_watchdog"]
