from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Awaitable, Callable

from aiogram.types import CallbackQuery, Message

from bot.core.errors import PersistenceError
from bot.handlers.admin.helper.admin_constants import NO_ACCESS_MSG
from bot.handlers.admin.helper.new.utils import sender_id
from bot.security.admin_access import is_admin_user
from bot.telegram.callbacks import safe_callback_answer

logger = logging.getLogger(__name__)


def admin_only(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(handler)
    async def wrapper(message_or_call, *args, **kwargs):
        uid = sender_id(message_or_call)
        try:
            authorized = await is_admin_user(uid)
        except PersistenceError as exc:
            logger.warning(
                "Admin authorization failed because persistence is unavailable",
                extra={
                    "handler": handler.__qualname__,
                    "user_id": uid,
                    "error_code": exc.error_code,
                },
            )
            if isinstance(message_or_call, CallbackQuery):
                await safe_callback_answer(
                    message_or_call,
                    exc.user_message,
                    show_alert=True,
                )
            elif isinstance(message_or_call, Message):
                await message_or_call.answer(exc.user_message)
            return None

        if not authorized:
            if isinstance(message_or_call, CallbackQuery):
                await safe_callback_answer(message_or_call, NO_ACCESS_MSG, show_alert=True)
            elif isinstance(message_or_call, Message):
                await message_or_call.answer(NO_ACCESS_MSG)
            return None
        return await handler(message_or_call, *args, **kwargs)

    return wrapper
