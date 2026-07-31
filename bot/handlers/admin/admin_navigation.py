"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.admin_menu import send_admin_main_menu
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.auction.exchange.catalog import (
    _kb_exchange_approved_root,
    _safe_edit_text_or_caption,
)

router = Router(name=__name__)

_MAIN_MENU_CALLBACKS = {
    "admin_back",
    "addadmin_cancel",
    "removeadmin_cancel",
    "givetrusted_cancel",
    "removetrusted_cancel",
    "universal_cancel",
}


@router.message(Command("admin"), F.chat.type == "private")
@router.message(Command("admin_panel"), F.chat.type == "private")
@admin_only
async def show_admin_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.message(
    F.text.lower().in_(["назад", "⬅️ назад", "отмена", "❌ отмена", "cancel"]),
    F.chat.type == "private",
)
@admin_only
async def back_to_admin_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    await send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.callback_query(F.data.in_(_MAIN_MENU_CALLBACKS))
@admin_only
async def callback_to_admin_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    message = call.message
    if isinstance(message, Message):
        try:
            await message.delete()
        except Exception:
            pass
        user = call.from_user
        await send_admin_main_menu(message, user_id=user.id if user is not None else None)
    await call.answer()


@router.message(F.text == "📅 Расписание", F.chat.type == "private")
@admin_only
async def schedule_button(message: Message, state: FSMContext) -> None:
    await start_preview_schedule(message, state)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        parse_mode="HTML",
        reply_markup=_kb_exchange_approved_root(),
    )


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def exchange_approved_root(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        reply_markup=_kb_exchange_approved_root(),
    )
    await call.answer()


__all__ = [
    "router",
    "show_admin_main_menu",
    "back_to_admin_main_menu",
    "callback_to_admin_main_menu",
    "schedule_button",
    "exchange_menu_button",
    "exchange_approved_root",
]
