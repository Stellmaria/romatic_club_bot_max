"""Missed-mailing diagnostics and exchange print handlers."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services import winner as winner_service
from bot.telegram.states import PrintExStates

router = Router(name="auction_winner_exchange")


@router.message(Command("print_win_missed"))
@admin_only
async def cmd_print_win_missed(message: Message) -> None:
    return await winner_service.cmd_print_win_missed(message)


@router.message(Command("ex_owners"))
async def cmd_ex_owners(message: Message) -> None:
    return await winner_service.cmd_ex_owners(message)


@router.message(Command("print_ex"))
async def cmd_print_ex(message: Message, state: FSMContext) -> None:
    return await winner_service.cmd_print_ex(message, state)


@router.callback_query(F.data.startswith("pex|"))
async def cb_print_ex(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    return await winner_service.cb_print_ex(call, state, bot)


@router.message(PrintExStates.waiting_manual)
async def ex_manual_input(message: Message, state: FSMContext) -> None:
    return await winner_service.ex_manual_input(message, state)
