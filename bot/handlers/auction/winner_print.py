"""Winner preview, notification, manual-result and feedback handlers."""

from __future__ import annotations

from aiogram import Bot, F, Router, types
from aiogram.types import Message

from bot.services import winner as winner_service

router = Router(name="auction_winner_print")


@router.message(F.text.startswith("/print_win"))
async def cmd_print_win(message: Message, bot: Bot):
    return await winner_service.cmd_print_win(message, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_EDIT_AMT}:"))
async def cb_win_edit_amt(call: types.CallbackQuery):
    return await winner_service.cb_win_edit_amt(call)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_EDIT_USER}:"))
async def cb_win_edit_user(call: types.CallbackQuery):
    return await winner_service.cb_win_edit_user(call)


@router.message(
    lambda message: message.from_user.id in winner_service.PENDING_EDIT
)
async def handle_pending_edit(message: types.Message, bot: Bot):
    return await winner_service.handle_pending_edit(message, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_SEND}:"))
async def cb_winner_send(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_winner_send(call, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_SKIP}:"))
async def cb_winner_skip(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_winner_skip(call, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_REFRESH}:"))
async def cb_print_win_refresh(call: types.CallbackQuery):
    return await winner_service.cb_print_win_refresh(call)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_SEND_OWNER}:"))
async def cb_print_win_send_owner(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_print_win_send_owner(call, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_SEND_WINNER}:"))
async def cb_print_win_send_winner(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_print_win_send_winner(call, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_SEND_BOTH}:"))
async def cb_print_win_send_both(call: types.CallbackQuery, bot: Bot):
    return await winner_service.cb_print_win_send_both(call, bot)


@router.callback_query(F.data.startswith(f"{winner_service.CB_WIN_MANUAL}:"))
async def cb_print_win_manual(call: types.CallbackQuery):
    return await winner_service.cb_print_win_manual(call)


@router.message(
    lambda message: message.from_user
    and message.from_user.id in winner_service.PENDING_WIN_MANUAL
)
async def msg_print_win_manual(message: types.Message, bot: Bot):
    return await winner_service.msg_print_win_manual(message, bot)


@router.callback_query(F.data.startswith("win:thanks:"))
async def cb_win_thanks(call: types.CallbackQuery) -> None:
    return await winner_service.cb_win_thanks(call)


@router.callback_query(F.data.startswith("win:edit_manual_comment:"))
async def cb_print_win_edit_manual_comment(call: types.CallbackQuery):
    return await winner_service.cb_print_win_edit_manual_comment(call)
