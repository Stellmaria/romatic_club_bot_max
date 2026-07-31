from functools import wraps
from typing import Callable, Awaitable, Any

from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.helper.admin_constants import NO_ACCESS_MSG
from bot.handlers.admin.helper.new.utils import sender_id
from bot.security.admin_access import is_admin_user
from bot.telegram.callbacks import safe_callback_answer


def admin_only(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(handler)
    async def wrapper(message_or_call, *args, **kwargs):
        uid = sender_id(message_or_call)
        if not await is_admin_user(uid):
            if isinstance(message_or_call, CallbackQuery):
                await safe_callback_answer(message_or_call, NO_ACCESS_MSG, show_alert=True)
            elif isinstance(message_or_call, Message):
                await message_or_call.answer(NO_ACCESS_MSG)
            return None
        return await handler(message_or_call, *args, **kwargs)

    return wrapper
