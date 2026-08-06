from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message, User


def sender_id(obj: Message | CallbackQuery) -> int | None:
    user = obj.from_user if hasattr(obj, "from_user") else None
    return user.id if isinstance(user, User) else None


async def safe_edit_text(
    msg: Any,
    text: str,
    *,
    reply_markup: Any = None,
    **kwargs: Any,
) -> bool:
    try:
        await msg.edit_text(text, reply_markup=reply_markup, **kwargs)
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return False
        raise


async def is_luxury_member(bot: Bot, user_id: int, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in {"member", "administrator", "creator"}
    except TelegramAPIError:
        return False


AUCTION_KIND_LABELS: dict[str, str] = {
    "standard": "⭐ Стандартный",
    "preorder": "🗓 Предзаказ",
    "reverse": "✨ Обратный",
    "fast": "⚡ Быстрый",
    "free": "🪶 Свободный",
    "black": "👑 Чёрный",
    "exchange": "🛍 Биржа",
}


def auction_kind_label(kind: str | None) -> str:
    normalized = (kind or "standard").strip().lower()
    return AUCTION_KIND_LABELS.get(normalized, AUCTION_KIND_LABELS["standard"])
