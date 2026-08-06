# fmt: off
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.domain.auctions import AuctionKind


AUCTION_KIND_TITLES = {
    AuctionKind.STANDARD: "⭐️ Стандартный",
    AuctionKind.PREORDER: "📦 Предзаказ",
    AuctionKind.REVERSE: "✨ Обратный",
    AuctionKind.FAST: "⚡️ Быстрый",
    AuctionKind.FREE: "🪶 Свободный",
    AuctionKind.BLACK: "👑 Чёрный",
    AuctionKind.EXCHANGE: "🛒 Биржа",
}


def auction_kind_keyboard(luxury_level: int) -> InlineKeyboardMarkup:
    """Build the menu from the canonical domain access policy."""
    level = max(0, int(luxury_level))
    keyboard = InlineKeyboardBuilder()
    for kind in AuctionKind:
        required = kind.minimum_luxury_level
        if level >= required:
            title = AUCTION_KIND_TITLES[kind]
            callback = f"auk_kind:{kind.value}"
        else:
            title = f"🔒 {AUCTION_KIND_TITLES[kind]} (Л{required})"
            callback = f"auk_kind_locked:{kind.value}:{required}"
        keyboard.button(text=title, callback_data=callback)

    keyboard.adjust(2)
    keyboard.row(
        InlineKeyboardButton(text="📚 Гайды от Давида", callback_data="auk_guide_menu:root"),
        InlineKeyboardButton(text="💬 Ответы от Давида", callback_data="auk_guide_menu:david"),
    )
    keyboard.row(
        InlineKeyboardButton(text="🏆 Рейтинг спасибо", callback_data="auk_guide_menu:thanks_top"),
    )
    return keyboard.as_markup()
# fmt: on
