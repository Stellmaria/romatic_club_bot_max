from __future__ import annotations

import html
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from bot.core.process_restart import process_restart_coordinator
from bot.core.settings import ADMINS_OWNERS
from bot.core.supervisor_client import SupervisorUnavailable, supervisor_client
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only

router = Router(name=__name__)

_FALLBACK_TEXT = (
    "🖥 <b>Система Romatic Club</b>\n\n"
    "Server Supervisor недоступен. Можно безопасно перезапустить только основной "
    "процесс бота через Docker fallback. Обновление Git и откат временно недоступны."
)
_RESTART_CONFIRM_TEXT = (
    "♻️ <b>Перезапустить основной бот?</b>\n\n"
    "Перезапустится только сервис <code>bot</code>. PostgreSQL и userbot останутся работать."
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
    return user_id is not None and int(user_id) in {int(value) for value in ADMINS_OWNERS}


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


def _admin_main_keyboard():
    return menu_keyboard(
        ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
        ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
        ["🖥 Система"],
    )


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
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _status_text(payload: dict[str, Any]) -> tuple[str, bool]:
    bot = payload.get("bot") if isinstance(payload.get("bot"), dict) else {}
    userbot = payload.get("userbot") if isinstance(payload.get("userbot"), dict) else {}
    git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else {}
    rollback_sha = str(payload.get("rollback_sha") or "")

    bot_ok = bool(bot.get("running"))
    userbot_ok = bool(userbot.get("running"))
    bot_icon = "✅" if bot_ok else "❌"
    userbot_icon = "✅" if userbot_ok else "❌"
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
        f"Основной бот: {bot_icon} {'работает' if bot_ok else 'остановлен'} · PID <code>{html.escape(str(bot.get('pid') or '?'))}</code>",
        f"Userbot: {userbot_icon} {'работает' if userbot_ok else 'остановлен'} · PID <code>{html.escape(str(userbot.get('pid') or '?'))}</code>",
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


async def _load_system_view() -> tuple[str, InlineKeyboardMarkup]:
    if supervisor_client is None:
        return _FALLBACK_TEXT, _fallback_keyboard()
    try:
        payload = await supervisor_client.status()
    except SupervisorUnavailable:
        return _FALLBACK_TEXT, _fallback_keyboard()
    text, rollback_available = _status_text(payload)
    return text, _system_keyboard(rollback_available=rollback_available)


async def _show_system_message(message: Message) -> None:
    text, keyboard = await _load_system_view()
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(F.text.in_(["/admin", "/admin_panel"]), F.chat.type == "private")
@admin_only
async def show_admin_menu_with_system(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("↩️ Возврат в главное меню...", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        ADMIN_MESSAGES.get(
            "admin_panel_greeting",
            "Добро пожаловать в админ-панель! Выберите раздел:",
        ),
        reply_markup=_admin_main_keyboard(),
    )


@router.message(Command("system"))
@router.message(Command("supervisor"))
@router.message(F.text == "🖥 Система", F.chat.type == "private")
@admin_only
async def show_system_menu(message: Message) -> None:
    if await _require_owner(message):
        await _show_system_message(message)


@router.message(Command("restart"))
@admin_only
async def show_restart_confirmation(message: Message) -> None:
    if not await _require_owner(message):
        return
    await message.answer(
        _RESTART_CONFIRM_TEXT,
        parse_mode="HTML",
        reply_markup=_confirm_keyboard("restart", "♻️ Перезапустить"),
    )


@router.callback_query(F.data == "system:menu")
@admin_only
async def show_system_callback(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    text, keyboard = await _load_system_view()
    await _edit_or_answer(call, text, keyboard)
    await call.answer()


@router.callback_query(F.data.in_({"system:restart:ask", "system:update:ask", "system:rollback:ask"}))
@admin_only
async def show_system_confirmation(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    mapping = {
        "system:restart:ask": (_RESTART_CONFIRM_TEXT, "restart", "♻️ Перезапустить"),
        "system:update:ask": (_UPDATE_CONFIRM_TEXT, "update", "⬇️ Обновить main"),
        "system:rollback:ask": (_ROLLBACK_CONFIRM_TEXT, "rollback", "↩️ Откатить"),
    }
    text, action, label = mapping[str(call.data)]
    await _edit_or_answer(call, text, _confirm_keyboard(action, label))
    await call.answer()


async def _accept_supervisor_operation(call: CallbackQuery, action: str) -> None:
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
        f"✅ <b>Операция принята</b>\n\nID: <code>{operation_id}</code>\nСтатус можно обновить в меню системы.",
        _system_keyboard(),
    )
    await call.answer("Операция принята.")


@router.callback_query(F.data.in_({"system:restart:do", "system:update:do", "system:rollback:do"}))
@admin_only
async def run_system_operation(call: CallbackQuery) -> None:
    if not await _require_owner(call):
        return
    action = str(call.data).split(":")[1]
    await _accept_supervisor_operation(call, action)


@router.callback_query(F.data == "system:logs")
@admin_only
async def show_system_logs(call: CallbackQuery) -> None:
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
    "show_admin_menu_with_system",
    "show_system_menu",
    "show_restart_confirmation",
    "show_system_callback",
    "show_system_confirmation",
    "run_system_operation",
    "show_system_logs",
    "close_system_callback",
]
