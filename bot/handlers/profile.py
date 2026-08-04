"""Focused private profile and privacy commands before the legacy user router."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from bot.bootstrap.container import ApplicationContainer
from bot.telegram.boundary import escape_html
from db.legacy import get_user_verified_uid, is_subscribed

router = Router(name="user-profile")
logger = logging.getLogger("auction_bot.privacy_export")


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


__all__ = ["privacy_export", "router", "user_profile"]
