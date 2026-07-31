"""Priority admin navigation that must bypass unfinished FSM conversations."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.action_support.forms import start_preview_schedule
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
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


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) in {int(value) for value in ADMINS_OWNERS}


def _admin_main_keyboard(*, include_system: bool):
    rows = [
        ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
        ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
        ["📅 Расписание", "🛒 Биржа"],
    ]
    if include_system:
        rows.append(["🖥 Система"])
    return menu_keyboard(*rows)


async def _send_admin_main_menu(message: Message, *, user_id: int | None) -> None:
    await message.answer("↩️ Возврат в главное меню...", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        ADMIN_MESSAGES.get(
            "admin_panel_greeting",
            "Добро пожаловать в админ-панель! Выберите раздел:",
        ),
        reply_markup=_admin_main_keyboard(include_system=_is_owner(user_id)),
    )


@router.message(Command("admin"))
@router.message(Command("admin_panel"))
@admin_only
async def show_admin_main_menu(message: Message, state: FSMContext) -> None:
    """Open the complete admin menu before the legacy system router can answer."""

    await state.clear()
    user = message.from_user
    await _send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.message(
    F.text.lower().in_(["назад", "⬅️ назад", "отмена"]),
    F.chat.type == "private",
)
@admin_only
async def back_to_admin_main_menu(message: Message, state: FSMContext) -> None:
    """Return from any legacy/FSM submenu to the complete admin keyboard."""

    await state.clear()
    user = message.from_user
    await _send_admin_main_menu(message, user_id=user.id if user is not None else None)


@router.callback_query(F.data.in_(_MAIN_MENU_CALLBACKS))
@admin_only
async def callback_to_admin_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    """Keep inline back/cancel actions on the same complete admin keyboard."""

    await state.clear()
    message = call.message
    if isinstance(message, Message):
        try:
            await message.delete()
        except Exception:
            pass
        user = call.from_user
        await _send_admin_main_menu(message, user_id=user.id if user is not None else None)
    await call.answer()


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
    "show_admin_main_menu",
    "back_to_admin_main_menu",
    "callback_to_admin_main_menu",
    "schedule_button",
    "exchange_menu_button",
    "exchange_approved_root",
]
