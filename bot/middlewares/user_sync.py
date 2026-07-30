from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from db.db import add_user


class UserSyncMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: Optional[User] = data.get("event_from_user") or getattr(event, "from_user", None)

        if user and not user.is_bot:
            username = (user.username or "").strip().lstrip("@")  # если нет username -> ""
            full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()

            await add_user(
                user_id=user.id,
                username=username,
                full_name=full_name,
            )

        return await handler(event, data)
