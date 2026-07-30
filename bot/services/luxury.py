from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import LUXURY_CHAT_ID, LUXURY_CHAT_ID_LVL2
from db.db import is_luxury_user


async def is_luxury_member(bot: Bot, user_id: int, chat_id: int) -> bool:
    """Check membership without leaking Telegram errors into callers."""
    try:
        member = await bot.get_chat_member(int(chat_id), int(user_id))
        return member.status in {"member", "administrator", "creator"}
    except TelegramAPIError:
        return False


async def get_user_luxury_level(bot: Bot, user_id: int) -> int:
    """Resolve the highest effective Luxury level from chats and legacy DB."""
    if LUXURY_CHAT_ID_LVL2 and await is_luxury_member(
        bot,
        int(user_id),
        int(LUXURY_CHAT_ID_LVL2),
    ):
        return 2
    if LUXURY_CHAT_ID and await is_luxury_member(
        bot,
        int(user_id),
        int(LUXURY_CHAT_ID),
    ):
        return 1
    return 1 if await is_luxury_user(int(user_id)) else 0
