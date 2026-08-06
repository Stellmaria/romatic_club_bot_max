from typing import Optional
from typing import Union

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, User


def sender_id(obj: Union[Message, CallbackQuery]) -> Optional[int]:
    u = obj.from_user if hasattr(obj, "from_user") else None
    return u.id if isinstance(u, User) else None

from aiogram.exceptions import TelegramBadRequest

async def safe_edit_text(msg, text: str, *, reply_markup=None, **kwargs) -> bool:
    try:
        await msg.edit_text(text, reply_markup=reply_markup, **kwargs)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return False  # это НЕ ошибка, просто нечего менять
        raise


async def is_luxury_member(bot: Bot, user_id: int, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
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
    k = (kind or "standard").strip().lower()
    return AUCTION_KIND_LABELS.get(k, AUCTION_KIND_LABELS["standard"])
