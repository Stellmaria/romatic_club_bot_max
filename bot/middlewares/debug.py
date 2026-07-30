"""Opt-in diagnostic middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types


class DebugAllMessages(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, types.Message):
            logging.getLogger("auction").debug(
                "MSG chat=%s type=%s user=%s text=%r",
                getattr(event.chat, "id", "?"),
                getattr(event.chat, "type", "?"),
                getattr(event.from_user, "id", "?"),
                getattr(event, "text", None),
            )
        return await handler(event, data)
