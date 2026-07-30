"""Thin compatibility entrypoint for the Telethon userbot process."""

from __future__ import annotations

import asyncio
import logging

from bot.core.environment import load_project_environment

load_project_environment()

from bot.core.settings import ADMINS, AUCTION_CHANNEL_ID, DISCUSSION_CHAT_ID
from userbot.application import create_userbot_client, run_userbot_application
from userbot.handlers import on_deleted, on_edited, on_new_message, register_handlers
from userbot.handlers.new_messages import OOPS_EDIT_WINDOW_SEC
from userbot.presentation import RULES_TEXT, WARN_TEXTS
from userbot.runtime import ACCEPTED_BIDS
from userbot.services import (
    AUTO_DELETE_BOT_NOTICE_SEC,
    get_thread_root_msg_id,
    reply_not_counted,
)
from userbot.workers import autobid_watchdog


logger = logging.getLogger("userbot")


async def main() -> None:
    """Compatibility coroutine delegating to the application lifecycle."""

    await run_userbot_application()


def run() -> int:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Userbot stopped by operator")
    return 0


__all__ = [
    "ACCEPTED_BIDS",
    "ADMINS",
    "AUCTION_CHANNEL_ID",
    "AUTO_DELETE_BOT_NOTICE_SEC",
    "DISCUSSION_CHAT_ID",
    "OOPS_EDIT_WINDOW_SEC",
    "RULES_TEXT",
    "WARN_TEXTS",
    "autobid_watchdog",
    "create_userbot_client",
    "get_thread_root_msg_id",
    "main",
    "on_deleted",
    "on_edited",
    "on_new_message",
    "register_handlers",
    "reply_not_counted",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(run())
