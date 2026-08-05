"""Focused private profile and privacy commands before the legacy user router."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from bot.bootstrap.container import ApplicationContainer
from bot.services.privacy_requests import PrivacyRequestConflict
from bot.telegram.boundary import escape_html
from db.legacy import get_user_verified_uid, is_subscribed

router = Router(name="user-profile")
logger = logging.getLogger("auction_bot.privacy")


@router.message(Command("profile"), F.chat.type == "private")
async def user_profile(message: types.Message) -> None:
    if message.from_user is None:
        return
    subscribed = await is_subscribed(message.from_user.id)
    notification_status = "Подписан ✅" if subscribed else "Не подписан"  # noqa: RUF001

    try:
        uid = await get_user_verified_uid(message.from_user.id)
    except Exception:  # noqa: BLE001 - profile remains available if UID lookup fails
        uid = None

    verification = "❌ НЕТ ВЕРИФИКАЦИИ"  # noqa: RUF001
    uid_line = ""
    if uid:
        value = str(uid)
        verification = "✅ UID верифицирован"
        uid_line = f"\nUID: <code>{value[:3]}***{value[-3:]}</code>"

    await message.answer(
        "<b>Профиль</b>\n"
        f"👤 {escape_html(message.from_user.full_name)}\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Статус уведомлений: {notification_status}\n"
        f"Верификация: {verification}"
        f"{uid_line}",
        parse_mode="HTML",
    )


@router.message(Command("privacy_export"), F.chat.type == "private")
async def privacy_export(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    """Export only the authenticated Telegram user's allowlisted personal data."""

    if message.from_user is None:
        return
    result = await application_container.privacy_export.export_self(
        actor_user_id=message.from_user.id,
        subject_user_id=message.from_user.id,
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


@router.message(Command("privacy_delete_request"), F.chat.type == "private")
async def privacy_delete_request(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    """Create a reviewed self-service anonymization request."""

    if message.from_user is None:
        return
    try:
        record = await application_container.privacy_request.request_self(
            actor_user_id=message.from_user.id,
            subject_user_id=message.from_user.id,
        )
    except PrivacyRequestConflict:
        await message.answer(
            "У вас уже есть активный запрос на анонимизацию. " "Статус: /privacy_delete_status"
        )
        return
    await message.answer(
        "Запрос на анонимизацию принят и ожидает проверку обязательных исключений.\n"
        f"Идентификатор: <code>{record.request_id}</code>\n\n"
        "Настройки и необязательные идентификаторы будут удалены. "
        "Минимальная связь с историей ставок, модерации и безопасности может быть сохранена.",
        parse_mode="HTML",
    )


@router.message(Command("privacy_delete_status"), F.chat.type == "private")
async def privacy_delete_status(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    if message.from_user is None:
        return
    record = await application_container.privacy_request.status_self(
        actor_user_id=message.from_user.id,
        subject_user_id=message.from_user.id,
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


@router.message(Command("privacy_delete_cancel"), F.chat.type == "private")
async def privacy_delete_cancel(
    message: types.Message,
    application_container: ApplicationContainer,
) -> None:
    if message.from_user is None:
        return
    try:
        record = await application_container.privacy_request.cancel_self(
            actor_user_id=message.from_user.id,
            subject_user_id=message.from_user.id,
        )
    except (LookupError, PrivacyRequestConflict):
        await message.answer("Нет ожидающего запроса, который можно отменить.")
        return
    await message.answer(
        f"Запрос <code>{record.request_id}</code> отменён.",
        parse_mode="HTML",
    )


__all__ = [
    "privacy_delete_cancel",
    "privacy_delete_request",
    "privacy_delete_status",
    "privacy_export",
    "router",
    "user_profile",
]
