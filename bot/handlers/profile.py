# ruff: noqa: RUF001
"""Canonical private profile, privacy, and user lookup entrypoints."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.bootstrap.container import ApplicationContainer
from bot.legacy_fsm import PublicWhoFSM
from bot.services.privacy_requests import PrivacyRequestConflict
from bot.telegram.boundary import escape_html
from db.legacy import get_user_verified_uid, is_subscribed

router = Router(name="user-profile")
logger = logging.getLogger("auction_bot.privacy")


def build_profile_keyboard(*, verified: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not verified:
        builder.row(
            InlineKeyboardButton(
                text="🆔 Пройти UID-верификацию",
                callback_data="user_profile|verify_uid",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔔 Настроить уведомления",
            callback_data="user_profile|notifications",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔎 Проверить пользователя",
            callback_data="user_profile|who",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔐 Данные и приватность",
            callback_data="user_profile|privacy",
        )
    )
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="user_menu|home"))
    return builder.as_markup()


def build_privacy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📥 Скачать мои данные",
            callback_data="user_privacy|export",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Управление удалением",
            callback_data="user_privacy|delete",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📋 Статус удаления",
            callback_data="user_privacy|status",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ К профилю", callback_data="user_privacy|back"))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="user_menu|home"))
    return builder.as_markup()


def build_delete_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⚠️ Подать запрос на удаление",
            callback_data="user_privacy|delete_confirm",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📋 Проверить статус",
            callback_data="user_privacy|status",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отменить запрос",
            callback_data="user_privacy|cancel",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="user_profile|privacy"))
    return builder.as_markup()


def _callback_message(call: types.CallbackQuery) -> types.Message | None:
    message = call.message
    return message if isinstance(message, types.Message) else None


async def _edit_or_answer(
    message: types.Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:  # noqa: BLE001 - stale callback messages must still recover
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def show_profile_menu(
    message: types.Message,
    *,
    user: types.User,
    edit: bool = False,
) -> None:
    subscribed = await is_subscribed(user.id)
    notification_status = "Подписан ✅" if subscribed else "Не подписан"

    try:
        uid = await get_user_verified_uid(user.id)
    except Exception:  # noqa: BLE001 - profile remains available if UID lookup fails
        uid = None

    verification = "❌ НЕТ ВЕРИФИКАЦИИ"
    uid_line = ""
    if uid:
        value = str(uid)
        verification = "✅ UID верифицирован"
        uid_line = f"\nUID: <code>{value[:3]}***{value[-3:]}</code>"

    text = (
        "<b>👤 Профиль</b>\n"
        f"Имя: {escape_html(user.full_name)}\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"Статус уведомлений: {notification_status}\n"
        f"Верификация: {verification}"
        f"{uid_line}"
    )
    keyboard = build_profile_keyboard(verified=bool(uid))
    if edit:
        await _edit_or_answer(message, text=text, reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _export_self(
    message: types.Message,
    *,
    user_id: int,
    application_container: ApplicationContainer,
) -> None:
    result = await application_container.privacy_export.export_self(
        actor_user_id=user_id,
        subject_user_id=user_id,
    )
    await message.answer_document(
        BufferedInputFile(result.payload, filename=result.filename),
        caption=(
            "Экспорт персональных данных сформирован. "
            f"Записей: {result.exported_rows}. "
            f"Идентификатор: <code>{result.correlation_id}</code>"
        ),
        parse_mode="HTML",
    )
    logger.info(
        "Privacy export delivered",
        extra={
            "event": "privacy.export.delivered",
            "correlation_id": str(result.correlation_id),
            "exported_rows": result.exported_rows,
        },
    )


async def _request_delete_self(
    message: types.Message,
    *,
    user_id: int,
    application_container: ApplicationContainer,
) -> None:
    try:
        record = await application_container.privacy_request.request_self(
            actor_user_id=user_id,
            subject_user_id=user_id,
        )
    except PrivacyRequestConflict:
        await message.answer(
            "У вас уже есть активный запрос на анонимизацию. "
            "Статус можно проверить в разделе «Данные и приватность»."
        )
        return
    await message.answer(
        "Запрос на анонимизацию принят и ожидает проверку обязательных исключений.\n"
        f"Идентификатор: <code>{record.request_id}</code>\n\n"
        "Настройки и необязательные идентификаторы будут удалены. "
        "Минимальная связь с историей ставок, модерации и безопасности может быть сохранена.",
        parse_mode="HTML",
    )


async def _status_delete_self(
    message: types.Message,
    *,
    user_id: int,
    application_container: ApplicationContainer,
) -> None:
    record = await application_container.privacy_request.status_self(
        actor_user_id=user_id,
        subject_user_id=user_id,
    )
    if record is None:
        await message.answer("Запросов на анонимизацию нет.")
        return
    holds = ", ".join(record.retained_holds) if record.retained_holds else "нет"
    await message.answer(
        f"Статус: <code>{record.status}</code>\n"
        f"Идентификатор: <code>{record.request_id}</code>\n"
        f"Сохранённые исключения: <code>{escape_html(holds)}</code>",
        parse_mode="HTML",
    )


async def _cancel_delete_self(
    message: types.Message,
    *,
    user_id: int,
    application_container: ApplicationContainer,
) -> None:
    try:
        record = await application_container.privacy_request.cancel_self(
            actor_user_id=user_id,
            subject_user_id=user_id,
        )
    except (LookupError, PrivacyRequestConflict):
        await message.answer("Нет ожидающего запроса, который можно отменить.")
        return
    await message.answer(
        f"Запрос <code>{record.request_id}</code> отменён.",
        parse_mode="HTML",
    )


@router.message(Command("profile"), F.chat.type == "private")
async def user_profile(message: types.Message) -> None:
    if message.from_user is None:
        return
    await show_profile_menu(message, user=message.from_user)


@router.callback_query(F.data == "user_profile|privacy")
async def profile_privacy(call: types.CallbackQuery) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await _edit_or_answer(
        message,
        text=(
            "🔐 <b>Данные и приватность</b>\n\n"
            "Здесь можно скачать копию своих данных и управлять запросом на анонимизацию."
        ),
        reply_markup=build_privacy_keyboard(),
    )


@router.callback_query(F.data == "user_profile|who")
async def profile_who(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await state.clear()
    await state.set_state(PublicWhoFSM.waiting_for_who_target)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="user_profile|who_cancel"))
    await message.answer(
        "🔎 <b>Проверка пользователя</b>\n\n"
        "Пришлите @username, Telegram ID, UID либо перешлите сообщение пользователя.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "user_profile|who_cancel")
async def profile_who_cancel(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    await call.answer("Отменено")
    if message is None:
        return
    await state.clear()
    await show_profile_menu(message, user=call.from_user)


@router.callback_query(F.data == "user_privacy|back")
async def privacy_back(call: types.CallbackQuery) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await show_profile_menu(message, user=call.from_user, edit=True)


@router.callback_query(F.data == "user_privacy|delete")
async def privacy_delete_menu(call: types.CallbackQuery) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await _edit_or_answer(
        message,
        text=(
            "🗑 <b>Удаление данных</b>\n\n"
            "Запрос запускает проверяемую анонимизацию. История ставок, модерации и безопасности "
            "может частично сохраняться там, где это обязательно для целостности сервиса."
        ),
        reply_markup=build_delete_keyboard(),
    )


@router.callback_query(F.data == "user_privacy|export")
async def privacy_export_callback(
    call: types.CallbackQuery,
    application_container: ApplicationContainer,
) -> None:
    message = _callback_message(call)
    await call.answer("Формирую экспорт…")
    if message is None:
        return
    await _export_self(
        message,
        user_id=call.from_user.id,
        application_container=application_container,
    )


@router.callback_query(F.data == "user_privacy|delete_confirm")
async def privacy_delete_request_callback(
    call: types.CallbackQuery,
    application_container: ApplicationContainer,
) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await _request_delete_self(
        message,
        user_id=call.from_user.id,
        application_container=application_container,
    )


@router.callback_query(F.data == "user_privacy|status")
async def privacy_delete_status_callback(
    call: types.CallbackQuery,
    application_container: ApplicationContainer,
) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await _status_delete_self(
        message,
        user_id=call.from_user.id,
        application_container=application_container,
    )


@router.callback_query(F.data == "user_privacy|cancel")
async def privacy_delete_cancel_callback(
    call: types.CallbackQuery,
    application_container: ApplicationContainer,
) -> None:
    message = _callback_message(call)
    await call.answer()
    if message is None:
        return
    await _cancel_delete_self(
        message,
        user_id=call.from_user.id,
        application_container=application_container,
    )


@router.message(Command("privacy_export"), F.chat.type == "private")
async def privacy_export(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    """Export only the authenticated Telegram user's allowlisted personal data."""

    if message.from_user is None:
        return
    await _export_self(
        message,
        user_id=message.from_user.id,
        application_container=application_container,
    )


@router.message(Command("privacy_delete_request"), F.chat.type == "private")
async def privacy_delete_request(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    """Create a reviewed self-service anonymization request."""

    if message.from_user is None:
        return
    await _request_delete_self(
        message,
        user_id=message.from_user.id,
        application_container=application_container,
    )


@router.message(Command("privacy_delete_status"), F.chat.type == "private")
async def privacy_delete_status(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    if message.from_user is None:
        return
    await _status_delete_self(
        message,
        user_id=message.from_user.id,
        application_container=application_container,
    )


@router.message(Command("privacy_delete_cancel"), F.chat.type == "private")
async def privacy_delete_cancel(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    if message.from_user is None:
        return
    await _cancel_delete_self(
        message,
        user_id=message.from_user.id,
        application_container=application_container,
    )


__all__ = [
    "build_profile_keyboard",
    "privacy_delete_cancel",
    "privacy_delete_request",
    "privacy_delete_status",
    "privacy_export",
    "router",
    "show_profile_menu",
    "user_profile",
]
