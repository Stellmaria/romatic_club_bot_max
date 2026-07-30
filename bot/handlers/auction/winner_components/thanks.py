from __future__ import annotations

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.auction_winners import AuctionWinnerService

from .common import CB_WIN_THANKS

router = Router(name="auction_winner_thanks")


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    service = await AuctionWinnerService.create()
    return await service.admin_thanks_totals(author)


async def build_thanks_kb(any_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    total, users = await get_admin_thanks_totals(moderator_tag)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"{CB_WIN_THANKS}:{int(any_id)}:{moderator_tag}",
        )
    ]])


@router.callback_query(F.data.startswith(f"{CB_WIN_THANKS}:"))
async def cb_win_thanks(call: types.CallbackQuery) -> None:
    parts = (call.data or "").split(":")
    if len(parts) < 4:
        try:
            await call.answer("Кривые данные.", show_alert=True)
        except Exception:
            pass
        return
    try:
        any_id = int(parts[2])
    except ValueError:
        any_id = 0
    author = ":".join(parts[3:]).strip()
    try:
        await call.answer("Спасибо учтено ✅")
    except Exception:
        pass

    service = await AuctionWinnerService.create()
    await service.increment_admin_thanks(author, int(call.from_user.id))
    try:
        if call.message:
            await call.message.edit_reply_markup(
                reply_markup=await build_thanks_kb(any_id, author),
            )
    except Exception:
        pass
