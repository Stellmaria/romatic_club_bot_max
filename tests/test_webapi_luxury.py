from __future__ import annotations

from types import SimpleNamespace

import pytest

import webapi.luxury as luxury
from bot.core.settings import DatabaseSettings
from webapi.settings import WebAppSettings


class FakeBot:
    def __init__(self, memberships: dict[int, str]) -> None:
        self.memberships = memberships

    async def get_chat_member(self, chat_id: int, user_id: int):
        del user_id
        return SimpleNamespace(status=self.memberships.get(chat_id, "left"))


def settings() -> WebAppSettings:
    return WebAppSettings(
        bot_token="123456:" + "test-token",
        database=DatabaseSettings(url="postgresql://example.invalid/app"),
        luxury_chat_id=-1001,
        luxury_chat_id_lvl2=-1002,
    )


@pytest.mark.asyncio
async def test_luxury_level_prefers_level_two_membership() -> None:
    bot = FakeBot({-1001: "member", -1002: "member"})

    assert await luxury.resolve_luxury_level(bot, settings(), 42) == 2


@pytest.mark.asyncio
async def test_luxury_level_uses_level_one_membership() -> None:
    bot = FakeBot({-1001: "member", -1002: "left"})

    assert await luxury.resolve_luxury_level(bot, settings(), 42) == 1


@pytest.mark.asyncio
async def test_luxury_level_is_zero_when_membership_checks_succeed() -> None:
    bot = FakeBot({-1001: "left", -1002: "left"})

    assert await luxury.resolve_luxury_level(bot, settings(), 42) == 0
