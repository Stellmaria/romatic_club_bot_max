"""Canonical administrator root menu and delivery helpers."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES
from bot.handlers.admin.helper.new.keyboards import menu_keyboard


def is_owner_user(user_id: int | None) -> bool:
    """Return whether the Telegram user is configured as an owner."""

    return user_id is not None and int(user_id) in {
        int(value) for value in ADMINS_OWNERS
    }


def build_admin_main_keyboard(
    *,
    user_id: int | None = None,
    include_system: bool | None = None,
) -> ReplyKeyboardMarkup:
    """Build the supported root keyboard for administrators."""

    rows = [
        ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
        ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
        ["📅 Расписание", "🛒 Биржа"],
    ]
    show_system = is_owner_user(user_id) if include_system is None else include_system
    if show_system:
        rows.append(["🖥 Система"])
    return menu_keyboard(*rows)


def admin_main_text(prefix: str | None = None) -> str:
    """Return the root-menu greeting, optionally preceded by a status message."""

    greeting = ADMIN_MESSAGES.get(
        "admin_panel_greeting",
        "Добро пожаловать в админ-панель! Выберите раздел:",
    )
    clean_prefix = str(prefix or "").strip()
    return f"{clean_prefix}\n\n{greeting}" if clean_prefix else greeting


async def send_admin_main_menu(
    message: Message,
    *,
    user_id: int | None = None,
    prefix: str | None = None,
    remove_previous_keyboard: bool = True,
) -> None:
    """Replace any stale reply keyboard with the canonical administrator menu."""

    if user_id is None and message.from_user is not None:
        user_id = message.from_user.id
    if remove_previous_keyboard:
        await message.answer(
            "↩️ Возврат в главное меню...",
            reply_markup=ReplyKeyboardRemove(),
        )
    await message.answer(
        admin_main_text(prefix),
        reply_markup=build_admin_main_keyboard(user_id=user_id),
    )


async def send_admin_main_menu_to_chat(
    bot: Bot,
    *,
    chat_id: int,
    user_id: int | None,
    prefix: str | None = None,
) -> None:
    """Send the canonical administrator menu when only bot/chat context exists."""

    await bot.send_message(
        chat_id=chat_id,
        text=admin_main_text(prefix),
        reply_markup=build_admin_main_keyboard(user_id=user_id),
    )


__all__ = [
    "admin_main_text",
    "build_admin_main_keyboard",
    "is_owner_user",
    "send_admin_main_menu",
    "send_admin_main_menu_to_chat",
]
