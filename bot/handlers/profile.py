"""Focused private profile command registered before the legacy user router."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from db.legacy import get_user_verified_uid, is_subscribed


from bot.telegram.boundary import escape_html
router = Router(name="user-profile")


@router.message(Command("profile"), F.chat.type == "private")
async def user_profile(message: types.Message) -> None:
    subscribed = await is_subscribed(message.from_user.id)
    notification_status = "Подписан ✅" if subscribed else "Не подписан"

    try:
        uid = await get_user_verified_uid(message.from_user.id)
    except Exception:
        uid = None

    verification = "❌ НЕТ ВЕРИФИКАЦИИ"
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


__all__ = ["router", "user_profile"]
