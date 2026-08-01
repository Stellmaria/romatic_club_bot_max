from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.repositories.users import UserRepository
from bot.core.legacy_config import legacy_config
from db.pool import get_db_pool


async def is_luxury_member(bot: Bot, user_id: int, chat_id: int) -> bool:
    """Check membership without leaking Telegram errors into callers."""
    try:
        member = await bot.get_chat_member(int(chat_id), int(user_id))
        return member.status in {"member", "administrator", "creator"}
    except TelegramAPIError:
        return False


async def get_user_luxury_level(bot: Bot, user_id: int) -> int:
    """Resolve the highest effective Luxury level from chats and legacy DB."""
    if legacy_config.LUXURY_CHAT_ID_LVL2 and await is_luxury_member(
        bot,
        int(user_id),
        int(legacy_config.LUXURY_CHAT_ID_LVL2),
    ):
        return 2
    if legacy_config.LUXURY_CHAT_ID and await is_luxury_member(
        bot,
        int(user_id),
        int(legacy_config.LUXURY_CHAT_ID),
    ):
        return 1
    repository = UserRepository(await get_db_pool())
    return 1 if await repository.is_luxury(int(user_id)) else 0
