"""Telethon event registration in the historical order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import events

from bot.core.settings import DISCUSSION_CHAT_ID
from userbot.handlers.bid_changes import on_deleted, on_edited
from userbot.handlers.new_messages import on_new_message
from userbot.runtime import bind_client

if TYPE_CHECKING:
    from telethon import TelegramClient


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


__all__ = ["on_deleted", "on_edited", "on_new_message", "register_handlers"]
