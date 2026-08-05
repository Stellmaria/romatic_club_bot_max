"""Telethon event registration in the historical order."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from telethon import events

from bot.core.legacy_config import legacy_config
from userbot.handlers.bid_changes import on_deleted, on_edited
from userbot.handlers.new_messages import on_new_message
from userbot.handlers.schedule_admin import (
    on_schedule_admin_command,
    on_schedule_review_callback,
)
from userbot.runtime import bind_client

if TYPE_CHECKING:
    from telethon import TelegramClient

    from bot.core.settings import UserbotSettings


_SCHEDULE_COMMAND_PATTERN = r"^/schedule_(?:emojis|preview|status)(?:@\w+)?(?:\s|$)"
_SCHEDULE_CALLBACK_PATTERN = rb"^sched:(?:approve|reject):\d{4}-\d{2}-\d{2}$"


def register_handlers(telegram_client: TelegramClient) -> None:
    """Bind runtime state and register the three historical handlers."""

    bind_client(telegram_client)
    telegram_client.add_event_handler(
        on_new_message,
        events.NewMessage(chats=legacy_config.DISCUSSION_CHAT_ID),
    )
    telegram_client.add_event_handler(
        on_edited,
        events.MessageEdited(chats=legacy_config.DISCUSSION_CHAT_ID),
    )
    telegram_client.add_event_handler(
        on_deleted,
        events.MessageDeleted(chats=legacy_config.DISCUSSION_CHAT_ID),
    )


def register_schedule_handlers(
    telegram_client: TelegramClient,
    config: UserbotSettings,
) -> None:
    """Register owner commands and review callbacks with typed settings."""

    telegram_client.add_event_handler(
        partial(on_schedule_admin_command, config=config),
        events.NewMessage(pattern=_SCHEDULE_COMMAND_PATTERN),
    )
    telegram_client.add_event_handler(
        partial(on_schedule_review_callback, config=config),
        events.CallbackQuery(pattern=_SCHEDULE_CALLBACK_PATTERN),
    )


__all__ = [
    "on_deleted",
    "on_edited",
    "on_new_message",
    "on_schedule_admin_command",
    "on_schedule_review_callback",
    "register_handlers",
    "register_schedule_handlers",
]
