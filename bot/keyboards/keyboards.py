from aiogram.types import InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup


def back_to_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Меню")]],
        resize_keyboard=True
    )


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
