"""Luxury access resolution for the Telegram Mini App."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from db.users import is_luxury_user
from webapi.settings import WebAppSettings

_MEMBER_STATUSES = frozenset({"member", "administrator", "creator"})


async def _membership(bot: Bot, user_id: int, chat_id: int) -> bool | None:
    if not chat_id:
        return None
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return None
    return str(member.status) in _MEMBER_STATUSES


async def resolve_luxury_level(
    bot: Bot,
    settings: WebAppSettings,
    user_id: int,
) -> int:
    configured = False
    lookup_failed = False

    if settings.luxury_chat_id_lvl2:
        configured = True
        level2 = await _membership(bot, user_id, settings.luxury_chat_id_lvl2)
        if level2 is True:
            return 2
        lookup_failed = lookup_failed or level2 is None

    if settings.luxury_chat_id:
        configured = True
        level1 = await _membership(bot, user_id, settings.luxury_chat_id)
        if level1 is True:
            return 1
        lookup_failed = lookup_failed or level1 is None

    if not configured or lookup_failed:
        return 1 if await is_luxury_user(user_id) else 0
    return 0


@dataclass(slots=True)
class LuxuryLevelCache:
    ttl_seconds: float = 300.0
    _items: dict[int, tuple[float, int]] = field(default_factory=dict)

    async def get(
        self,
        bot: Bot,
        settings: WebAppSettings,
        user_id: int,
    ) -> int:
        now = time.monotonic()
        cached = self._items.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]

        level = await resolve_luxury_level(bot, settings, user_id)
        self._items[user_id] = (now + self.ttl_seconds, level)
        return level


__all__ = ["LuxuryLevelCache", "resolve_luxury_level"]
