from __future__ import annotations

import html
from contextlib import suppress
from typing import Any, cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.core.legacy_config import legacy_config
from bot.core.process_restart import process_restart_coordinator
from bot.core.supervisor_client import SupervisorClient, SupervisorUnavailable
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)

_FALLBACK_TEXT = (
    "🖥 <b>Система Romatic Club</b>\n\n"
    "Server Supervisor недоступен. Можно безопасно перезапустить только основной "
    "процесс бота через Docker fallback. Обновление Git, перезапуск userbot и откат "
    "временно недоступны."
)
_RESTART_CONFIRM_TEXT = (
    "♻️ <b>Перезапустить основной бот?</b>\n\n"
    "Перезапустится только сервис <code>bot</code>. PostgreSQL и userbot останутся работать."
)
_USERBOT_RESTART_CONFIRM_TEXT = (
    "🔄 <b>Перезапустить userbot?</b>\n\n"
    "Перезапустится только сервис <code>userbot</code>. "
    "PostgreSQL и основной бот останутся работать."
)
_UPDATE_CONFIRM_TEXT = (
    "⬇️ <b>Обновить Romatic Club из main?</b>\n\n"
    "Supervisor создаст и проверит резервную копию PostgreSQL, получит свежий "
    "<code>origin/main</code>, пересоберёт bot, userbot и proxy, затем выполнит smoke-проверку."
)
_ROLLBACK_CONFIRM_TEXT = (
    "↩️ <b>Откатить код?</b>\n\n"
    "Будет развёрнут предыдущий сохранённый commit через тот же backup и health gate."
)


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) in {
        int(value) for value in legacy_config.ADMINS_OWNERS
    }


async def _require_owner(target: Message | CallbackQuery) -> bool:
    user = target.from_user
    if _is_owner(user.id if user is not None else None):
        return True
    text = "Системные операции доступны только владельцу."
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text)
    return False


def _admin_main_keyboard(*, include_system: bool) -> ReplyKeyboardMarkup:
    rows = [
        ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
        ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
    ]
    if include_system:
        rows.append(["🖥 Система"])
    return menu_keyboard(*rows)


def _system_keyboard(*, rollback_available: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🔄 Обновить статус", callback_data="system:menu"),
        ],
        [
            InlineKeyboardButton(
                text="♻️ Перезапустить основной бот",
                callback_data="system:restart:ask",
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Перезапустить userbot",
                callback_data="system:userbot-restart:ask",
            )
        ],
        [
            InlineKeyboardButton(
                text="⬇️ Обновить main",
                callback_data="system:update:ask",
            ),
            InlineKeyboardButton(text="📄 Логи", callback_data="system:logs"),
        ],
    ]
    if rollback_available:
        rows.append([InlineKeyboardButton(text="↩️ Откатить", callback_data="system:rollback:ask")])
    rows.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="system:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fallback_keyboard() -> InlineKeyboardMarkup:
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


def _confirm_keyboard(action: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=f"system:{action}:do"),
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
    except Exception:  # noqa: BLE001
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _status_text(payload: dict[str, Any]) -> tuple[str, bool]:
    bot_value = payload.get("bot")
    userbot_value = payload.get("userbot")
    git_value = payload.get("git")
    operation_value = payload.get("operation")
    bot = cast(dict[str, Any], bot_value) if isinstance(bot_value, dict) else {}
    userbot = cast(dict[str, Any], userbot_value) if isinstance(userbot_value, dict) else {}
    git = cast(dict[str, Any], git_value) if isinstance(git_value, dict) else {}
    operation = cast(dict[str, Any], operation_value) if isinstance(operation_value, dict) else {}
    rollback_sha = str(payload.get("rollback_sha") or "")

    def service_view(service: dict[str, Any]) -> tuple[str, str, str]:
        running = bool(service.get("running"))
        pid = service.get("pid")
        status = str(service.get("status") or "unknown")
        health = str(service.get("health") or "")
        healthy = (
            running
            and status == "running"
            and isinstance(pid, int)
            and pid > 0
            and health in {"", "healthy"}
        )
        if healthy:
            return "✅", "работает", html.escape(str(pid))
        if running or status == "restarting":
            return "⚠️", f"не здоров · {html.escape(status)}", html.escape(str(pid or "?"))
        return "❌", "остановлен", html.escape(str(pid or "?"))

    bot_icon, bot_label, bot_pid = service_view(bot)
    userbot_icon, userbot_label, userbot_pid = service_view(userbot)
    branch = html.escape(str(git.get("branch") or "unknown"))
    commit = html.escape(str(git.get("commit") or "unknown")[:16])
    clean = "чистое" if git.get("clean") else "изменено"
    op_kind = html.escape(str(operation.get("kind") or "нет"))
    op_status = html.escape(str(operation.get("status") or "нет"))
    op_message = str(operation.get("message") or "").strip()
    if len(op_message) > 1200:
        op_message = op_message[-1200:]

    lines = [
        "🛡 <b>Romatic Club Supervisor</b>",
        "",
        f"Supervisor PID: <code>{html.escape(str(payload.get('pid') or '?'))}</code>",
        f"Основной бот: {bot_icon} {bot_label} · PID <code>{bot_pid}</code>",
        f"Userbot: {userbot_icon} {userbot_label} · PID <code>{userbot_pid}</code>",
        "",
        f"Git-ветка: <code>{branch}</code>",
        f"Commit: <code>{commit}</code>",
        f"Рабочее дерево: {clean}",
        "",
        "<b>Последняя операция</b>",
        f"<code>{op_kind} · {op_status}</code>",
    ]
    if op_message:
        lines.extend([html.escape(op_message)])
    return "\n".join(lines), bool(rollback_sha)


async def _load_system_view(
    supervisor_client: SupervisorClient | None,
) -> tuple[str, InlineKeyboardMarkup]:
    if supervisor_client is None:
        return _FALLBACK_TEXT, _fallback_keyboard()
    try:
        payload = await supervisor_client.status()
    except SupervisorUnavailable:
        return _FALLBACK_TEXT, _fallback_keyboard()
    text, rollback_available = _status_text(payload)
    return text, _system_keyboard(rollback_available=rollback_available)


async def _show_system_message(
    message: Message,
    supervisor_client: SupervisorClient | None,
) -> None:
    text, keyboard = await _load_system_view(supervisor_client)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(F.text.in_(["/admin", "/admin_panel"]), F.chat.type == "private")
@admin_only
async def show_admin_menu_with_system(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("↩️ Возврат в главное меню...", reply_markup=ReplyKeyboardRemove())
    user = message.from_user
    await message.answer(
        ADMIN_MESSAGES.get(
            "admin_panel_greeting",
            "Добро пожаловать в админ-панель! Выберите раздел:",
        ),
        reply_markup=_admin_main_keyboard(
            include_system=_is_owner(user.id if user is not None else None)
        ),
    )


@router.message(Command("system"))
@router.message(Command("supervisor"))
@router.message(F.text == "🖥 Система", F.chat.type == "private")
async def show_system_menu(
    message: Message,
    supervisor_client: SupervisorClient | None = None,
) -> None:
    if await _require_owner(message):
        await _show_system_message(message, supervisor_client)


@router.message(Command("restart"))
@router.message(Command("restart_userbot"))
async def show_restart_confirmation(message: Message) -> None:
    if not await _require_owner(message):
        return
    is_userbot = (message.text or "").split(maxsplit=1)[0].lower() == "/restart_userbot"
    text = _USERBOT_RESTART_CONFIRM_TEXT if is_userbot else _RESTART_CONFIRM_TEXT
    action = "userbot-restart" if is_userbot else "restart"
    label = "🔄 Перезапустить userbot" if is_userbot else "♻️ Перезапустить"
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_confirm_keyboard(action, label),
    )


@router.callback_query(F.data == "system:menu")
async def show_system_callback(
    call: CallbackQuery,
    supervisor_client: SupervisorClient | None = None,
) -> None:
    if not await _require_owner(call):
        return
    text, keyboard = await _load_system_view(supervisor_client)
    await _edit_or_answer(call, text, keyboard)
    await call.answer()


@router.callback_query(
    F.data.in_(
        {
            "system:restart:ask",
            "system:userbot-restart:ask",
            "system:update:ask",
            "system:rollback:ask",
        }
    )
)
async def show_system_confirmation(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    mapping = {
        "system:restart:ask": (_RESTART_CONFIRM_TEXT, "restart", "♻️ Перезапустить"),
        "system:userbot-restart:ask": (
            _USERBOT_RESTART_CONFIRM_TEXT,
            "userbot-restart",
            "🔄 Перезапустить userbot",
        ),
        "system:update:ask": (_UPDATE_CONFIRM_TEXT, "update", "⬇️ Обновить main"),
        "system:rollback:ask": (_ROLLBACK_CONFIRM_TEXT, "rollback", "↩️ Откатить"),
    }
    text, action, label = mapping[str(call.data)]
    await _edit_or_answer(call, text, _confirm_keyboard(action, label))
    await call.answer()


async def _accept_supervisor_operation(
    call: CallbackQuery,
    action: str,
    supervisor_client: SupervisorClient | None,
) -> None:
    if supervisor_client is None:
        if action != "restart":
            await call.answer("Server Supervisor недоступен.", show_alert=True)
            return
        scheduled = await process_restart_coordinator.request()
        if not scheduled:
            await call.answer("Перезапуск уже запущен.", show_alert=True)
            return
        await _edit_or_answer(call, "♻️ <b>Перезапуск принят</b>", None)
        await call.answer("Перезапуск принят.")
        return

    try:
        if action == "restart":
            result = await supervisor_client.restart()
        elif action == "userbot-restart":
            result = await supervisor_client.restart_userbot()
        elif action == "update":
            result = await supervisor_client.update()
        else:
            result = await supervisor_client.rollback()
    except SupervisorUnavailable as exc:
        await call.answer(f"Supervisor недоступен: {str(exc)[:120]}", show_alert=True)
        return

    operation_id = html.escape(str(result.get("operation_id") or "принята"))
    await _edit_or_answer(
        call,
        f"✅ <b>Операция принята</b>\n\nID: <code>{operation_id}</code>\n"
        "Статус можно обновить в меню системы.",
        _system_keyboard(),
    )
    await call.answer("Операция принята.")


@router.callback_query(
    F.data.in_(
        {
            "system:restart:do",
            "system:userbot-restart:do",
            "system:update:do",
            "system:rollback:do",
        }
    )
)
async def run_system_operation(
    call: CallbackQuery,
    supervisor_client: SupervisorClient | None = None,
) -> None:
    if not await _require_owner(call):
        return
    action = split_callback_data(str(call.data), ":")[1]
    await _accept_supervisor_operation(call, action, supervisor_client)


@router.callback_query(F.data == "system:logs")
async def show_system_logs(
    call: CallbackQuery,
    supervisor_client: SupervisorClient | None = None,
) -> None:
    if not await _require_owner(call):
        return
    if supervisor_client is None:
        await call.answer("Server Supervisor недоступен.", show_alert=True)
        return
    try:
        payload = await supervisor_client.logs()
    except SupervisorUnavailable as exc:
        await call.answer(f"Логи недоступны: {str(exc)[:120]}", show_alert=True)
        return
    output = str(payload.get("logs") or "Логи пусты.")
    if len(output) > 3500:
        output = output[-3500:]
    await _edit_or_answer(
        call,
        f"📄 <b>Последние логи</b>\n\n<pre>{html.escape(output)}</pre>",
        _system_keyboard(rollback_available=bool(payload.get("rollback_sha"))),
    )
    await call.answer()


@router.callback_query(F.data == "system:close")
async def close_system_callback(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    if isinstance(call.message, Message):
        with suppress(Exception):
            await call.message.delete()
    await call.answer()


__all__ = [
    "close_system_callback",
    "router",
    "run_system_operation",
    "show_admin_menu_with_system",
    "show_restart_confirmation",
    "show_system_callback",
    "show_system_confirmation",
    "show_system_logs",
    "show_system_menu",
]
