from __future__ import annotations

import asyncio
import sys
import types

aiogram = types.ModuleType("aiogram")
aiogram.Bot = object
sys.modules.setdefault("aiogram", aiogram)

from bot.telegram.protection import patch_bot_protect_content


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, object]]] = []

    async def send_message(self, chat_id: int, _text: str, **kwargs):
        self.calls.append(("message", chat_id, kwargs))

    async def send_video(self, chat_id: int, _video: str, **kwargs):
        self.calls.append(("video", chat_id, kwargs))

    async def copy_message(self, chat_id: int, _from_chat_id: int, _message_id: int, **kwargs):
        self.calls.append(("copy", chat_id, kwargs))


def test_content_policy_is_open_by_default_and_covers_media_copy() -> None:
    async def scenario() -> None:
        async def is_admin(user_id: int) -> bool:
            return user_id == 7

        bot = FakeBot()
        patch_bot_protect_content(bot, is_admin=is_admin)  # type: ignore[arg-type]

        await bot.send_message(42, "private")
        await bot.send_video(7, "admin-video")
        await bot.copy_message(-100, -200, 3)
        await bot.send_message(42, "explicit-open", protect_content=False)
        await bot.send_message(42, "luxury", protect_content=True)

        assert bot.calls == [
            ("message", 42, {}),
            ("video", 7, {}),
            ("copy", -100, {}),
            ("message", 42, {"protect_content": False}),
            ("message", 42, {"protect_content": True}),
        ]

    asyncio.run(scenario())


def test_content_policy_does_not_query_admin_and_patch_is_idempotent() -> None:
    async def scenario() -> None:
        checks = 0

        async def admin_check(_user_id: int) -> bool:
            nonlocal checks
            checks += 1
            return False

        bot = FakeBot()
        patch_bot_protect_content(bot, is_admin=admin_check)  # type: ignore[arg-type]
        patch_bot_protect_content(bot, is_admin=admin_check)  # type: ignore[arg-type]

        await bot.send_video(42, "private")

        assert checks == 0
        assert bot.calls == [("video", 42, {})]

    asyncio.run(scenario())
