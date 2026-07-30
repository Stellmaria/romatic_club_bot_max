from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def user_deck_keyboard():
    kb = [
        [InlineKeyboardButton(text=f"Колода {i}", callback_data=f"user_deck_{i}")]
        for i in range(1, 11)
    ]
    kb.append([InlineKeyboardButton(text="Свой вариант", callback_data="user_custom")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def currency_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍵"), KeyboardButton(text="💎")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirmation_keyboard(yes="Да", no="Нет"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=yes), KeyboardButton(text=no)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
