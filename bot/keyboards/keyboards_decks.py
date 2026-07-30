from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.admin_constants import BUTTONS


def deck_keyboard(decks):
    builder = InlineKeyboardBuilder()
    for deck in decks:
        text = f"Колода {deck['deck_id']} — {deck['deck_name']}"
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"admin_deck_{deck['deck_id']}"
            )
        )
    return builder.as_markup()


def build_decks_keyboard(prefix="admin_cards_deck_"):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Колода {i}", callback_data=f"{prefix}{i}")]
            for i in range(1, 11)
        ]
    )


def cards_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К списку колод", callback_data="admin_cards_back")]
        ]
    )


def card_keyboard(cards):
    return InlineKeyboardMarkup(
        inline_keyboard=[
                            [InlineKeyboardButton(
                                text=f"{c['num']}. {c['hero_name']} ({c['rarity']})",
                                callback_data=f"subscribe_card_{c['card_id']}")
                            ] for c in cards
                        ] + [[InlineKeyboardButton(text=BUTTONS["menu"], callback_data="back_to_menu")]]
    )


def card_action_keyboard(card_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BUTTONS["edit"], callback_data=f"admin_edit_card_{card_id}")],
            [InlineKeyboardButton(text=BUTTONS["delete"], callback_data=f"admin_delete_card_{card_id}")]
        ]
    )


def card_edit_keyboard():
    fields = [
        ("Название", "name"),
        ("Имя героя", "hero"),
        ("Номер", "num"),
        ("Фото", "image"),
        ("Редкость", "rarity"),
        ("История", "story"),
        ("Цитата", "quote"),
        ("Отмена", "cancel"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"edit_card_{field}")]
            for label, field in fields
        ]
    )


def delete_card_keyboard(card_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BUTTONS["confirm"], callback_data=f"confirm_delete_card_{card_id}")],
            [InlineKeyboardButton(text=BUTTONS["cancel"], callback_data="cancel_delete_card")]
        ]
    )


def rarity_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="бронзовая")],
            [KeyboardButton(text="серебряная")],
            [KeyboardButton(text="золотая")],
            [KeyboardButton(text="алмазная")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirm_card_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="addcard_confirm_yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data="addcard_confirm_no")
            ]
        ]
    )


def subscriptions_keyboard(subs):
    if not subs:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"❌ {sub['card_name']} ({sub['hero_name']})",
                callback_data=f"unsubscribe_{sub['id']}"
            )] for sub in subs
        ]
    )


def admin_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTONS["stats"]), KeyboardButton(text=BUTTONS["logs"])],
            [KeyboardButton(text=BUTTONS["moderation"]), KeyboardButton(text=BUTTONS["users"])],
            [KeyboardButton(text=BUTTONS["admin_menu"])]
        ],
        resize_keyboard=True
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="addcard_back")]]
    )
