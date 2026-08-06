"""Canonical user-menu facade with current UX policy overrides."""

from __future__ import annotations

import html
from typing import Any

from aiogram.types import Message

from bot.handlers import user_menu_impl as _impl


def user_main_text(*, full_name: str | None = None, status_line: str | None = None) -> str:
    parts = ["✨ <b>Клуб Романтики • Аукционы</b>"]
    if full_name:
        parts.append(f"Привет, <b>{html.escape(full_name)}</b>!")
    if status_line:
        parts.append(status_line.strip())
    parts.extend(
        [
            "",
            "Здесь всё работает через кнопки. Выберите нужный раздел ниже.",
        ]
    )
    return "\n".join(parts)


async def show_notifications_menu(message: Message, *, user_id: int) -> None:
    settings = await _impl.get_settings(user_id) or {}
    subscribed = await _impl.is_subscribed(user_id)
    await _impl._edit_or_answer(
        message,
        text=(
            "🔔 <b>Уведомления</b>\n\n"
            "Выберите, какие сообщения бот должен присылать:\n\n"
            "📨 <b>Общие уведомления</b> — главный переключатель. "
            "Когда он выключен, остальные уведомления не отправляются.\n"
            "🔔 <b>О начале аукциона</b> — сообщение при старте подходящего аукциона.\n"
            "⏰ <b>За минуту до конца</b> — напоминание перед завершением аукциона.\n"
            "🏁 <b>О завершении</b> — результат после окончания аукциона.\n"
            "📅 <b>Анонс дня в 00:00</b> — список аукционов на новый день.\n\n"
            "✅ — включено, ❌ — выключено.\n"
            f"Сейчас общие уведомления: <b>{'включены' if subscribed else 'выключены'}</b>."
        ),
        reply_markup=_impl.build_notifications_keyboard(settings, subscribed=subscribed),
    )


async def show_exchange_menu(message: Message) -> None:
    await _impl._edit_or_answer(
        message,
        text=(
            "🛍 <b>Биржа карт</b>\n\n"
            "Отдельного пользовательского раздела биржи больше нет. "
            "Чтобы подать биржевой лот, нажмите «🎴 Подать лот» и выберите нужный тип заявки."
        ),
        reply_markup=_impl.build_user_main_keyboard(),
    )


async def show_exchange_browser(message: Message) -> None:
    await message.answer(
        "Просмотр предложений биржи доступен только администраторам.",
        reply_markup=_impl.build_user_main_keyboard(),
    )


def help_text() -> str:
    return (
        "ℹ️ <b>Как пользоваться ботом</b>\n\n"
        "🎴 <b>Подать лот</b> — оформление аукционного или биржевого лота.\n"
        "📦 <b>Мои лоты</b> — актуальные, завершённые, выплаты и архив.\n"
        "📅 <b>Сегодня</b> — аукционы на текущий день.\n"
        "🔔 <b>Уведомления</b> — все переключатели и их пояснения.\n"
        "🃏 <b>Подписки</b> — подписки на карты, колоды и пресеты.\n"
        "👤 <b>Профиль</b> — статус уведомлений и UID-верификация.\n"
        "👑 <b>Лакшери</b> — расширенное расписание и поиск.\n"
        "🆘 <b>Поддержка</b> — обращение администрации с вложениями."
    )


# Aiogram registered the handlers while importing the implementation module.
# Those handlers resolve these helpers through their module globals at runtime,
# so replacing the globals keeps one router while applying the current UX policy.
_impl.user_main_text = user_main_text
_impl.show_notifications_menu = show_notifications_menu
_impl.show_exchange_menu = show_exchange_menu
_impl.show_exchange_browser = show_exchange_browser
_impl.help_text = help_text

router = _impl.router
UserMenuFSM = _impl.UserMenuFSM
build_exchange_keyboard = _impl.build_exchange_keyboard
build_notifications_keyboard = _impl.build_notifications_keyboard
build_schedule_keyboard = _impl.build_schedule_keyboard
send_user_main_menu = _impl.send_user_main_menu
show_day_schedule = _impl.show_day_schedule
show_schedule_menu = _impl.show_schedule_menu


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))


__all__ = sorted(
    set(getattr(_impl, "__all__", ()))
    | {
        "UserMenuFSM",
        "build_exchange_keyboard",
        "build_notifications_keyboard",
        "build_schedule_keyboard",
        "help_text",
        "router",
        "send_user_main_menu",
        "show_day_schedule",
        "show_exchange_browser",
        "show_exchange_menu",
        "show_notifications_menu",
        "show_schedule_menu",
        "user_main_text",
    }
)
