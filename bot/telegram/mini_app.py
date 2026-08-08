# ruff: noqa: RUF001
"""Telegram Bot API integration for the Mini App launch button."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonWebApp, WebAppInfo

from bot.core.mini_app_settings import MiniAppSettings

logger = logging.getLogger("auction_bot.mini_app")


def build_mini_app_menu(settings: MiniAppSettings) -> MenuButtonWebApp | None:
    """Build the default Telegram menu button when the Mini App is enabled."""

    if not settings.enabled:
        return None
    return MenuButtonWebApp(
        text="Открыть приложение",
        web_app=WebAppInfo(url=settings.public_url),
    )


async def configure_mini_app_menu(bot_token: str, settings: MiniAppSettings) -> bool:
    """Publish the default Mini App button without making bot startup depend on it."""

    menu_button = build_mini_app_menu(settings)
    if menu_button is None:
        return False

    bot = Bot(token=bot_token)
    try:
        try:
            return await bot.set_chat_menu_button(menu_button=menu_button)
        except TelegramAPIError:
            logger.exception("Failed to configure Telegram Mini App menu button")
            return False
    finally:
        await bot.session.close()


__all__ = ["build_mini_app_menu", "configure_mini_app_menu"]
