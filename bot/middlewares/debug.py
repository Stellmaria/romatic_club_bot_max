"""Opt-in diagnostic middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types

logger = logging.getLogger("auction_bot.debug")


class DebugAllMessages(BaseMiddleware):
    """Log bounded message metadata without storing private message content."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, types.Message):
            logger.debug(
                "Telegram message metadata",
                extra={
                    "event": "telegram.message_debug",
                    "chat_id": getattr(event.chat, "id", None),
                    "chat_type": getattr(event.chat, "type", None),
                    "user_id": getattr(event.from_user, "id", None),
                    "message_id": event.message_id,
                    "has_text": bool(event.text),
                    "content_type": event.content_type,
                },
            )
        return await handler(event, data)


__all__ = ["DebugAllMessages"]
