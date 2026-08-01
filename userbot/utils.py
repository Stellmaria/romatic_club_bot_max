from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser, InputPeerChannelFromMessage, \
    InputPeerEmpty, InputPeerSelf, InputPeerUserFromMessage

from bot.core.legacy_config import legacy_config

MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    return datetime.now(MSK)


async def get_discussion_peer(client: TelegramClient, auction: dict) -> InputPeerEmpty | InputPeerSelf | InputPeerChat | InputPeerUser | InputPeerChannel | InputPeerUserFromMessage | InputPeerChannelFromMessage:
    # В 99% случаев это константа (твой discussion chat).
    # auction здесь оставляем “на будущее”, вдруг захочешь разные чаты.
    return await client.get_input_entity(int(legacy_config.DISCUSSION_CHAT_ID))