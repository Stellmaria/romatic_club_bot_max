"""Administrative commands related to auction luxury access."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.luxury_admin import LuxuryAdminService

router = Router(name=__name__)


@router.message(Command("unlux"), F.chat.type == "private")
@admin_only
async def cmd_remove_luxury(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование:\n<code>/unlux @username</code>\n<code>/unlux user_id</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()
    service = await LuxuryAdminService.create()
    try:
        user = await service.find_user(raw)
    except ValueError:
        await message.answer("Укажи корректный @username или numeric user_id.")
        return

    if not user:
        await message.answer("Пользователь не найден в базе.")
        return

    user_id = int(user["user_id"])
    username = user.get("username")
    full_name = user.get("full_name") or "—"

    if not bool(user.get("is_luxury")):
        await message.answer(
            f"У пользователя уже нет лакшери-статуса.\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Username: @{username}"
            if username
            else f"ID: <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return

    await service.remove_luxury(user=user, actor_id=message.from_user.id)

    lines = [
        "✅ Лакшери-статус снят.",
        f"ID: <code>{user_id}</code>",
        f"Имя: {full_name}",
    ]
    if username:
        lines.append(f"Username: @{username}")

    lines.append(
        "\n⚠️ Если пользователь всё ещё состоит в лакшери-чате, "
        "следующий refresh может вернуть статус обратно."
    )

    await message.answer("\n".join(lines), parse_mode="HTML")
