"""Telethon event registration in the historical order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import events

from bot.core.settings import DISCUSSION_CHAT_ID
from userbot.handlers.bid_changes import on_deleted, on_edited
from userbot.handlers.new_messages import on_new_message
from userbot.handlers.schedule_admin import (
    on_schedule_admin_command,
    on_schedule_review_callback,
)
from userbot.runtime import bind_client

if TYPE_CHECKING:
    from telethon import TelegramClient


_SCHEDULE_COMMAND_PATTERN = r"^/schedule_(?:emojis|preview|status)(?:@\w+)?(?:\s|$)"
_SCHEDULE_CALLBACK_PATTERN = rb"^sched:(?:approve|reject):\d{4}-\d{2}-\d{2}$"


def register_handlers(telegram_client: TelegramClient) -> None:
    """Bind runtime state and register the three historical handlers."""

    bind_client(telegram_client)
    telegram_client.add_event_handler(
        on_new_message,
        events.NewMessage(chats=DISCUSSION_CHAT_ID),
    )
    telegram_client.add_event_handler(
        on_edited,
        events.MessageEdited(chats=DISCUSSION_CHAT_ID),
    )
    telegram_client.add_event_handler(
        on_deleted,
        events.MessageDeleted(chats=DISCUSSION_CHAT_ID),
    )


def register_schedule_handlers(telegram_client: TelegramClient) -> None:
    """Register owner commands and review callbacks for schedule publication."""

    telegram_client.add_event_handler(
        on_schedule_admin_command,
        events.NewMessage(pattern=_SCHEDULE_COMMAND_PATTERN),
    )
    telegram_client.add_event_handler(
        on_schedule_review_callback,
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
