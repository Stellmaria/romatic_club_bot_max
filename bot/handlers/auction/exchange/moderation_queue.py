"""Continue the one-by-one exchange queue after moderation actions."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.telegram.callback_parser import split_callback_data

logger = logging.getLogger(__name__)


class ContinuePendingExchangeQueueMiddleware(BaseMiddleware):
    """Advance the saved one-by-one queue after a successful approval."""

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        callback_data = event.data or ""
        if not callback_data.startswith("exchange_approve|"):
            return await handler(event, data)

        try:
            _, raw_batch_id = split_callback_data(callback_data, "|", 1)
            batch_id = int(raw_batch_id)
        except (TypeError, ValueError):
            return await handler(event, data)

        result = await handler(event, data)

        state = data.get("state")
        if not isinstance(event.message, Message) or not isinstance(
            state,
            FSMContext,
        ):
            return result

        try:
            from bot.handlers.admin.presentation.exchange_pending_view import (
                continue_pending_exchange_request_one,
            )

            await continue_pending_exchange_request_one(
                event.message,
                state,
                processed_batch_id=batch_id,
            )
        except Exception:
            logger.exception(
                "Could not continue exchange moderation queue after batch_id=%s",
                batch_id,
            )

        return result


__all__ = ["ContinuePendingExchangeQueueMiddleware"]
