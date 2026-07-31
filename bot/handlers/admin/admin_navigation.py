"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.admin_menu import send_admin_main_menu
from bot.handlers.admin.helper.new.keyboards import period_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.helper.user_helpers import (
    build_grouped_schedule_lines_with_prefixes,
)
from bot.handlers.auction.exchange.catalog import (
    _kb_exchange_approved_root,
    _safe_edit_text_or_caption,
)
from bot.telegram.states import PreviewScheduleFSM
from db.auctions import get_auctions_by_date_with_owners

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
    """Open the grouped read-only schedule preview."""

    await start_preview_schedule(message, state)


@router.callback_query(F.data.startswith("preview_schedule|"))
@admin_only
async def preview_schedule_navigation(
    call: CallbackQuery,
    state: FSMContext,
) -> None:
    """Handle month/day selection and render the whole day in one message."""

    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение с расписанием недоступно.", show_alert=True)
        return

    try:
        _, raw_date = (call.data or "").split("|", 1)
        parts = raw_date.split("-")
    except ValueError:
        await call.answer("Некорректная дата.", show_alert=True)
        return

    if len(parts) == 2:
        try:
            year, month = map(int, parts)
            month_start = datetime(year, month, 1)
        except (TypeError, ValueError):
            await call.answer("Некорректный месяц.", show_alert=True)
            return

        await state.update_data(preview_year=year, preview_month=month)
        await state.set_state(PreviewScheduleFSM.choosing_day)
        await message.answer(
            "Выберите день для просмотра расписания:",
            reply_markup=period_keyboard(
                period="day",
                prefix="preview_schedule",
                base_date=month_start,
            ),
        )
        await call.answer()
        return

    if len(parts) == 3:
        try:
            year, month, day = map(int, parts)
            selected_date = date(year, month, day)
        except (TypeError, ValueError):
            await call.answer("Некорректный день.", show_alert=True)
            return

        auctions = await get_auctions_by_date_with_owners(selected_date)
        if auctions:
            lines = await build_grouped_schedule_lines_with_prefixes(
                auctions,
                {"card_name": ""},
                current_owner_ids=None,
            )
            schedule_text = "\n".join(lines)
        else:
            schedule_text = "Нет запланированных лотов на этот день."

        await message.answer(
            f"📅 <b>Расписание на {selected_date.strftime('%d.%m.%Y')}</b>\n\n"
            f"{schedule_text}",
            parse_mode="HTML",
        )
        await state.clear()
        await call.answer()
        return

    await call.answer("Некорректный формат даты.", show_alert=True)


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
    "preview_schedule_navigation",
    "exchange_menu_button",
    "exchange_approved_root",
]
