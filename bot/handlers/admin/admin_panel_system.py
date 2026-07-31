from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.core.process_restart import process_restart_coordinator
from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.helper.new.wrapper import admin_only

router = Router(name=__name__)

_SYSTEM_TEXT = (
    "🖥 <b>Система Romatic Club</b>\n\n"
    "Основной бот работает под Docker Compose. Перезапуск завершит только "
    "текущий процесс основного бота; PostgreSQL и отдельный userbot останутся "
    "работать. Docker автоматически поднимет новую копию."
)
_CONFIRM_TEXT = (
    "♻️ <b>Перезапустить основной бот?</b>\n\n"
    "Текущий процесс завершится, после чего Docker Compose автоматически "
    "запустит новую копию. Данные в PostgreSQL не затрагиваются."
)
_ACCEPTED_TEXT = (
    "♻️ <b>Перезапуск принят</b>\n\n"
    "Основной бот завершает текущий процесс. Новая копия обычно возвращается "
    "в течение нескольких секунд."
)


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) in {int(value) for value in ADMINS_OWNERS}


async def _require_owner(target: Message | CallbackQuery) -> bool:
    user = target.from_user
    if _is_owner(user.id if user is not None else None):
        return True
    if isinstance(target, CallbackQuery):
        await target.answer("Системные операции доступны только владельцу.", show_alert=True)
    else:
        await target.answer("Системные операции доступны только владельцу.")
    return False


def _system_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Перезапустить основной бот",
                    callback_data="system:restart:ask",
                )
            ],
            [InlineKeyboardButton(text="✖ Закрыть", callback_data="system:close")],
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Перезапустить",
                    callback_data="system:restart:do",
                ),
                InlineKeyboardButton(text="✖ Отмена", callback_data="system:menu"),
            ]
        ]
    )


async def _edit_or_answer(
    call: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    message = call.message
    if not isinstance(message, Message):
        return
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("system"))
@router.message(F.text == "🖥 Система", F.chat.type == "private")
@admin_only
async def show_system_menu(message: Message) -> None:
    if not await _require_owner(message):
        return
    await message.answer(
        _SYSTEM_TEXT,
        parse_mode="HTML",
        reply_markup=_system_keyboard(),
    )


@router.message(Command("restart"))
@admin_only
async def show_restart_confirmation(message: Message) -> None:
    if not await _require_owner(message):
        return
    await message.answer(
        _CONFIRM_TEXT,
        parse_mode="HTML",
        reply_markup=_confirm_keyboard(),
    )


@router.callback_query(F.data.in_({"system:menu", "system:restart:ask"}))
@admin_only
async def show_system_callback(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    if call.data == "system:restart:ask":
        await _edit_or_answer(call, _CONFIRM_TEXT, _confirm_keyboard())
    else:
        await _edit_or_answer(call, _SYSTEM_TEXT, _system_keyboard())
    await call.answer()


@router.callback_query(F.data == "system:restart:do")
@admin_only
async def restart_system_callback(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    scheduled = await process_restart_coordinator.request()
    if not scheduled:
        await call.answer("Перезапуск уже запущен.", show_alert=True)
        return
    await _edit_or_answer(call, _ACCEPTED_TEXT, None)
    await call.answer("Перезапуск принят.")


@router.callback_query(F.data == "system:close")
@admin_only
async def close_system_callback(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    if isinstance(call.message, Message):
        try:
            await call.message.delete()
        except Exception:
            pass
    await call.answer()


__all__ = [
    "router",
    "show_system_menu",
    "show_restart_confirmation",
    "show_system_callback",
    "restart_system_callback",
    "close_system_callback",
]
