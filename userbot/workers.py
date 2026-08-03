"""Background workers owned by the userbot process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from bot.core.legacy_config import legacy_config
from db.auctions import list_autobids
from userbot.autobid_engine import maybe_place_autobid
from userbot.schedule_publication import schedule_announcement_watchdog

if TYPE_CHECKING:
    from telethon import TelegramClient


logger = logging.getLogger("userbot")
Heartbeat = Callable[[], None]


async def autobid_watchdog(
    telegram_client: TelegramClient,
    *,
    heartbeat: Heartbeat | None = None,
) -> None:
    while True:
        try:
            rows = await list_autobids(auction_id=None, only_active=True)
            auction_ids = {int(row["auction_id"]) for row in rows}
            for auction_id in auction_ids:
                await maybe_place_autobid(
                    telegram_client,
                    discussion_chat_id=int(legacy_config.DISCUSSION_CHAT_ID),
                    auction_id=auction_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Autobid watchdog iteration failed")
            raise
        if heartbeat is not None:
            heartbeat()
        await asyncio.sleep(15)


__all__ = ["autobid_watchdog", "schedule_announcement_watchdog"]
