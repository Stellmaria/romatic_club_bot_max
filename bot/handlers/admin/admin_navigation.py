"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.admin_menu import send_admin_main_menu
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.auction.exchange.catalog import (
    kb_exchange_approved_root,
    safe_edit_text_or_caption,
)
from bot.handlers.auction.exchange.moderation import (
    show_pending_exchange_requests,
    show_pending_exchange_requests_all,
)
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)

_MAIN_MENU_CALLBACKS = {
    "admin_back",
    "addadmin_cancel",
    "removeadmin_cancel",
    "givetrusted_cancel",
    "removetrusted_cancel",
    "universal_cancel",
}


def _exchange_admin_root_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🧾 Заявки на модерацию",
        callback_data="admreq|pending|exchange",
    )
    kb.button(text="✅ Принятые лоты", callback_data="ex_appr:root")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)
    return kb.as_markup()


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
    """Open the detailed read-only schedule preview."""

    await start_preview_schedule(message, state)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🛒 <b>Биржа</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=_exchange_admin_root_keyboard(),
    )


@router.callback_query(F.data == "admreq|pending|exchange")
@admin_only
async def exchange_pending_requests(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await call.answer("Открываю заявки…")
    if isinstance(call.message, Message):
        await show_pending_exchange_requests(call.message)


@router.callback_query(F.data.startswith("expend_mode|"))
@admin_only
async def exchange_pending_mode_compat(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    """Keep already-sent mode keyboards functional after the routing fix."""

    try:
        _, mode = split_callback_data(call.data or "", "|", 1)
    except ValueError:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return
    if mode not in {"all", "one"}:
        await call.answer("Неизвестный режим.", show_alert=True)
        return

    await state.clear()
    await call.answer("Открываю заявки…")
    if not isinstance(call.message, Message):
        return
    if mode == "all":
        await show_pending_exchange_requests_all(call.message)
    else:
        await show_pending_exchange_requests(call.message)


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def exchange_approved_root(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите способ просмотра принятых лотов:",
        reply_markup=kb_exchange_approved_root(),
    )
    await call.answer()


__all__ = [
    "router",
    "show_admin_main_menu",
    "back_to_admin_main_menu",
    "callback_to_admin_main_menu",
    "schedule_button",
    "exchange_menu_button",
    "exchange_pending_requests",
    "exchange_pending_mode_compat",
    "exchange_approved_root",
]
