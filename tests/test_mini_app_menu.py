# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bot.core.mini_app_settings import MiniAppSettings

aiogram_types = pytest.importorskip("aiogram.types")
mini_app = pytest.importorskip("bot.telegram.mini_app")
MenuButtonWebApp = aiogram_types.MenuButtonWebApp


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.menu_button: object | None = None

    async def set_chat_menu_button(self, *, menu_button: object) -> bool:
        self.menu_button = menu_button
        return True


def test_build_mini_app_menu_is_disabled_without_url() -> None:
    assert mini_app.build_mini_app_menu(MiniAppSettings()) is None


def test_build_mini_app_menu_uses_public_url() -> None:
    button = mini_app.build_mini_app_menu(
        MiniAppSettings(public_url="https://app.example.com")
    )

    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "Открыть приложение"
    assert button.web_app.url == "https://app.example.com"


@pytest.mark.asyncio
async def test_configure_mini_app_menu_is_noop_when_disabled() -> None:
    configured = await mini_app.configure_mini_app_menu(str(123456), MiniAppSettings())

    assert configured is False


@pytest.mark.asyncio
async def test_configure_mini_app_menu_sets_button_and_closes_session(monkeypatch) -> None:
    fake_bot = FakeBot()
    expected_token = str(123456)

    def create_bot(*, token: str) -> FakeBot:
        assert token == expected_token
        return fake_bot

    monkeypatch.setattr(mini_app, "Bot", create_bot)
    configured = await mini_app.configure_mini_app_menu(
        expected_token,
        MiniAppSettings(public_url="https://app.example.com"),
    )

    assert configured is True
    assert isinstance(fake_bot.menu_button, MenuButtonWebApp)
    assert fake_bot.session.closed is True
