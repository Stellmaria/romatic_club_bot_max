from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, types
from aiogram.exceptions import TelegramBadRequest

from bot.telegram.callbacks import is_expired_callback_error

logger = logging.getLogger("auction_bot.callback")


class ExpiredCallbackMiddleware(BaseMiddleware):
    """Suppress only Telegram errors caused by an already expired callback query."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as error:
            if not is_expired_callback_error(error):
                raise

            callback_data = None
            user_id = None
            if isinstance(event, types.Update) and event.callback_query is not None:
                callback_data = event.callback_query.data
                if event.callback_query.from_user is not None:
                    user_id = event.callback_query.from_user.id

            logger.info(
                "Expired callback query ignored: user_id=%s data=%r",
                user_id,
                callback_data,
            )
            return None
