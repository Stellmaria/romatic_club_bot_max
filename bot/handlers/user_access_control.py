# ruff: noqa: RUF001
"""Access guards for privileged schedule entry points."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.core.time import to_moscow, utc_now
from bot.handlers.user_menu import show_day_schedule
from bot.keyboards.keyboards import (
    USER_MENU_HELP,
    USER_MENU_SCHEDULE,
    USER_MENU_TODAY,
    build_user_main_keyboard,
)
from db.legacy import is_admin, is_luxury_user

router = Router(name="user-access-control")


async def _has_schedule_access(user_id: int) -> bool:
    return await is_admin(user_id) or await is_luxury_user(user_id)


async def _show_today(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_day_schedule(message, to_moscow(utc_now()).date())


async def _deny_schedule_message(message: Message) -> None:
    if await is_luxury_user(message.from_user.id):
        text = "Расписание для Лакшери-пользователей открывается через раздел " "«👑 Лакшери»."
    else:
        text = "Расписание доступно только администраторам и Лакшери-пользователям."
    await message.answer(text, reply_markup=build_user_main_keyboard())


async def _deny_schedule_callback(call: CallbackQuery) -> None:
    if await is_luxury_user(call.from_user.id):
        text = "Откройте расписание через раздел «👑 Лакшери»."
    else:
        text = "Расписание доступно только администраторам и Лакшери-пользователям."
    await call.answer(text, show_alert=True)


@router.message(Command("today"), F.chat.type == "private")
@router.message(F.text == USER_MENU_TODAY, F.chat.type == "private")
async def public_today(message: Message, state: FSMContext) -> None:
    await _show_today(message, state)


@router.message(Command("day"), F.chat.type == "private")
async def guard_legacy_schedule_commands(message: Message) -> None:
    if await _has_schedule_access(message.from_user.id):
        raise SkipHandler
    await _deny_schedule_message(message)


@router.message(F.text == USER_MENU_SCHEDULE, F.chat.type == "private")
async def guard_stale_schedule_button(message: Message) -> None:
    if await is_admin(message.from_user.id):
        raise SkipHandler
    await _deny_schedule_message(message)


@router.callback_query(
    F.data.startswith("user_schedule|") | F.data.startswith("user_day|"),
)
async def guard_schedule_callbacks(call: CallbackQuery) -> None:
    if await is_admin(call.from_user.id):
        raise SkipHandler
    await _deny_schedule_callback(call)


@router.message(Command("help"), F.chat.type == "private")
@router.message(F.text.in_([USER_MENU_HELP, "help"]), F.chat.type == "private")
async def guarded_user_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "ℹ️ <b>Как пользоваться ботом</b>\n\n"
        "🎴 <b>Подать лот</b> — пошаговое оформление заявки.\n"
        "📦 <b>Мои лоты</b> — актуальные, завершённые, выплаты и архив.\n"
        "📅 <b>Сегодня</b> — аукционы, которые идут в течение текущего дня.\n"
        "🛍 <b>Биржа</b> — выставление карт и просмотр принятых предложений.\n"
        "🔔 <b>Уведомления</b> — настройка оповещений.\n"
        "🃏 <b>Подписки</b> — подписки на карты, колоды и пресеты.\n"
        "👤 <b>Профиль</b> — статус уведомлений и UID-верификация.\n"
        "👑 <b>Лакшери</b> — расписание, свободные слоты и поиск карт.\n"
        "🆘 <b>Поддержка</b> — обращение администрации с вложениями.\n\n"
        "Расписание по другим дням доступно администраторам и Лакшери-пользователям.\n"
        "Кнопка «🏠 Меню» отменяет текущий ввод и возвращает на главный экран.",
        parse_mode="HTML",
        reply_markup=build_user_main_keyboard(),
    )


__all__ = ["router"]
