from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


USER_MENU_ADD_LOT = "🎴 Подать лот"
USER_MENU_MY_LOTS = "📦 Мои лоты"
USER_MENU_TODAY = "📅 Сегодня"
# Retained only to reject buttons from already-sent keyboards.
USER_MENU_SCHEDULE = "📆 Расписание"
USER_MENU_EXCHANGE = "🛍 Биржа"
USER_MENU_NOTIFICATIONS = "🔔 Уведомления"
USER_MENU_SUBSCRIPTIONS = "🃏 Подписки"
USER_MENU_PROFILE = "👤 Профиль"
USER_MENU_LUXURY = "👑 Лакшери"
USER_MENU_SUPPORT = "🆘 Поддержка"
USER_MENU_HELP = "ℹ️ Помощь"
USER_MENU_HOME = "🏠 Меню"

USER_MENU_LAYOUT: tuple[tuple[str, ...], ...] = (
    (USER_MENU_ADD_LOT, USER_MENU_MY_LOTS),
    (USER_MENU_TODAY, USER_MENU_NOTIFICATIONS),
    (USER_MENU_SUBSCRIPTIONS, USER_MENU_PROFILE),
    (USER_MENU_LUXURY, USER_MENU_SUPPORT),
    (USER_MENU_HELP, USER_MENU_HOME),
)


def build_user_main_keyboard() -> ReplyKeyboardMarkup:
    """Build the canonical persistent keyboard for private users."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=label) for label in row]
            for row in USER_MENU_LAYOUT
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел меню",
    )


def back_to_menu_keyboard() -> ReplyKeyboardMarkup:
    """Compatibility alias used by legacy flows after completing an action."""

    return build_user_main_keyboard()


def currency_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Алмазы", callback_data="user_edit_currency|алмазы")],
        [InlineKeyboardButton(text="🍵 Чашки", callback_data="user_edit_currency|чашки")],
        [InlineKeyboardButton(text="🪙 Сокровища", callback_data="user_edit_currency|сокровища")],
    ])


def craft_uid_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="craft_uid:yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="craft_uid:no"),
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Что такое крафт на UID?",
                    callback_data="craft_uid:help",
                )
            ],
        ]
    )


__all__ = [
    "USER_MENU_ADD_LOT",
    "USER_MENU_EXCHANGE",
    "USER_MENU_HELP",
    "USER_MENU_HOME",
    "USER_MENU_LAYOUT",
    "USER_MENU_LUXURY",
    "USER_MENU_MY_LOTS",
    "USER_MENU_NOTIFICATIONS",
    "USER_MENU_PROFILE",
    "USER_MENU_SCHEDULE",
    "USER_MENU_SUBSCRIPTIONS",
    "USER_MENU_SUPPORT",
    "USER_MENU_TODAY",
    "back_to_menu_keyboard",
    "build_user_main_keyboard",
    "craft_uid_kb",
    "currency_choice_keyboard",
]
