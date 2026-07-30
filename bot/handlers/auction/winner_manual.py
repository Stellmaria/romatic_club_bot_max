"""Manual winner-result field editor handlers.

This router is registered before the exchange/missed-mailing router, matching
the order in the former monolithic handler.
"""

from __future__ import annotations

from aiogram import Bot, F, Router, types

from bot.services import winner as winner_service

router = Router(name="auction_winner_manual")


@router.callback_query(
    F.data.startswith(f"{winner_service.CB_WIN_EDIT_MANUAL_WINNER}:")
)
async def cb_print_win_edit_manual_winner(call: types.CallbackQuery):
    return await winner_service.cb_print_win_edit_manual_winner(call)


@router.callback_query(
    F.data.startswith(f"{winner_service.CB_WIN_EDIT_MANUAL_OWNER}:")
)
async def cb_print_win_edit_manual_owner(call: types.CallbackQuery):
    return await winner_service.cb_print_win_edit_manual_owner(call)


@router.callback_query(
    F.data.startswith(f"{winner_service.CB_WIN_EDIT_MANUAL_AMOUNT}:")
)
async def cb_print_win_edit_manual_amount(call: types.CallbackQuery):
    return await winner_service.cb_print_win_edit_manual_amount(call)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_CLEAR_MANUAL}:"))
async def cb_print_win_clear_manual(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_print_win_clear_manual(call, bot)


@router.message(
    lambda message: message.from_user
    and message.from_user.id in winner_service.PENDING_WIN_FIELD_EDIT
)
async def msg_print_win_edit_single_field(message: types.Message, bot: Bot):
    return await winner_service.msg_print_win_edit_single_field(message, bot)
