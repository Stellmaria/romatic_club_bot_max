# ruff: noqa: RUF001
"""Telegram Bot API integration for the Mini App launch button."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from bot.core.mini_app_settings import MiniAppSettings


def build_mini_app_menu(settings: MiniAppSettings) -> MenuButtonWebApp | None:
    """Build the default Telegram menu button when the Mini App is enabled."""

    if not settings.enabled:
        return None
    return MenuButtonWebApp(
        text="Открыть приложение",
        web_app=WebAppInfo(url=settings.public_url),
    )


async def configure_mini_app_menu(bot_token: str, settings: MiniAppSettings) -> bool:
    """Publish the default private-chat Mini App menu button through Bot API."""

    menu_button = build_mini_app_menu(settings)
    if menu_button is None:
        return False

    bot = Bot(token=bot_token)
    try:
        return await bot.set_chat_menu_button(menu_button=menu_button)
    finally:
        await bot.session.close()


__all__ = ["build_mini_app_menu", "configure_mini_app_menu"]
