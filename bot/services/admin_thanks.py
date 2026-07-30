from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User

from bot.services.auction_winners import AuctionWinnerService

CB_WIN_THANKS = "win:thanks"


def admin_tag(user: User) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else f"id{user.id}"


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    service = await AuctionWinnerService.create()
    return await service.admin_thanks_totals(author)


async def build_thanks_kb(auction_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    total, users = await get_admin_thanks_totals(moderator_tag)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"{CB_WIN_THANKS}:{int(auction_id)}:{moderator_tag}",
        )
    ]])
