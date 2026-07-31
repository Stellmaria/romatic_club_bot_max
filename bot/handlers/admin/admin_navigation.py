"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.auction.exchange.catalog import (
    _kb_exchange_approved_root,
    _safe_edit_text_or_caption,
)

router = Router(name=__name__)


@router.message(F.text == "📅 Расписание", F.chat.type == "private")
@admin_only
async def schedule_button(message: Message, state: FSMContext) -> None:
    """Open the read-only schedule even when another FSM is active."""

    await start_preview_schedule(message, state)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message, state: FSMContext) -> None:
    """Open the approved exchange catalog from a clean navigation state."""

    await state.clear()
    await message.answer(
        "🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        parse_mode="HTML",
        reply_markup=_kb_exchange_approved_root(),
    )


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def exchange_approved_root(call: CallbackQuery, state: FSMContext) -> None:
    """Return to the working exchange root instead of the orphaned exinv flow."""

    await state.clear()
    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        reply_markup=_kb_exchange_approved_root(),
    )
    await call.answer()


__all__ = [
    "router",
    "schedule_button",
    "exchange_menu_button",
    "exchange_approved_root",
]
