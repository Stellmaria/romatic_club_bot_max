"""Moderator feedback counters and thanks callbacks."""

from __future__ import annotations

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

from .common import PENDING_WIN_FIELD_EDIT, _cb_last_int


async def _thanks_kb(auction_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    total, users = await get_admin_thanks_totals(moderator_tag)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🙏 Спасибо: {total} | 👥 {users}",
                    callback_data=f"{CB_WIN_THANKS}:{auction_id}:{moderator_tag}",
                )
            ]
        ]
    )


CB_WIN_THANKS = "win:thanks"


def _norm_author(author: str) -> str:
    return winner_repository.normalize_author(author)


async def _ensure_admin_thanks_tables() -> None:
    await winner_repository.ensure_thanks_tables(await get_db_pool())


async def _inc_admin_thanks(author: str, user_id: int) -> tuple[int, int]:
    """
    +1 к "спасибо" модератору.
    - thanks_total увеличивается всегда
    - users_total увеличивается только если это первый клик этого user_id по данному author
    """
    return await winner_repository.increment_thanks(
        await get_db_pool(),
        author,
        user_id,
    )


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    return await winner_repository.get_thanks_totals(
        await get_db_pool(),
        author,
    )


async def build_thanks_kb(any_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    # оставлено для совместимости, чтобы не чинить импорты в других файлах
    return await _thanks_kb(int(any_id), moderator_tag)


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

    # быстро отвечаем, чтобы не ловить "query is too old"
    try:
        await call.answer("Спасибо учтено ✅")
    except Exception:
        pass

    await _inc_admin_thanks(author, int(call.from_user.id))

    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=await build_thanks_kb(any_id, author))
    except Exception:
        pass


async def cb_print_win_edit_manual_comment(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "comment",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "💬 <b>Комментарий от модератора</b>\n\n"
        "Пришли текст (он будет добавлен в рассылку победителю/владельцу).\n"
        "Чтобы очистить комментарий — пришли <code>-</code>.",
        parse_mode="HTML",
    )
