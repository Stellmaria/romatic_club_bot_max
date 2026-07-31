"""Telethon event registration in the historical order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import events

from bot.core.settings import DISCUSSION_CHAT_ID
from userbot.handlers.bid_changes import on_deleted, on_edited
from userbot.handlers.new_messages import on_new_message
from userbot.handlers.schedule_admin import on_schedule_admin_command
from userbot.runtime import bind_client

if TYPE_CHECKING:
    from telethon import TelegramClient


_SCHEDULE_COMMAND_PATTERN = (
    r"^/schedule_(?:emojis|preview|status)(?:@\w+)?(?:\s|$)"
)


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
    """Register private owner commands for Premium schedule announcements."""

    telegram_client.add_event_handler(
        on_schedule_admin_command,
        events.NewMessage(pattern=_SCHEDULE_COMMAND_PATTERN),
    )


__all__ = [
    "on_deleted",
    "on_edited",
    "on_new_message",
    "on_schedule_admin_command",
    "register_handlers",
    "register_schedule_handlers",
]
