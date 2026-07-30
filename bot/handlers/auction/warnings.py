from __future__ import annotations

import random

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.core.legacy_config import ADMIN_LOG_CHATS, ADMINS, LOG_CHAT_ID
from bot.services.warnings import WarningService
from db.legacy import (
    add_warning,
    ban_user,
    get_user_by_username,
    get_user_id_by_username,
    get_warnings_count,
    is_user_banned,
    reset_warnings,
    unban_user,
)

router = Router(name="auction_warnings")

PRUNE_WARN_AGE_DAYS = 30
MAX_WARN_BEFORE_BAN = 4

_pending_warning_reasons: dict[int, str] = {}


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in ADMINS)


async def _deny_unless_admin(message: Message) -> bool:
    if _is_admin(message):
        return False
    await message.answer("Нет доступа.")
    return True


def _full_chat_permissions() -> types.ChatPermissions:
    return types.ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_change_info=True,
    )


def _admin_log_chat_ids() -> list[int]:
    result: list[int] = []
    for value in ADMIN_LOG_CHATS:
        if isinstance(value, int) and value not in result:
            result.append(value)
    if isinstance(LOG_CHAT_ID, int) and LOG_CHAT_ID not in result:
        result.append(LOG_CHAT_ID)
    return result


async def _log_admin(bot: Bot, text: str) -> None:
    for chat_id in _admin_log_chat_ids():
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            pass


@router.message(F.text.lower().startswith("макс размут"))
async def admin_unmute(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс размут @username")
        return

    username = parts[2].lstrip("@")
    user = await get_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден.")
        return
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            int(user["user_id"]),
            permissions=types.ChatPermissions(can_send_messages=True),
        )
        await message.answer(f"✅ Пользователь @{username} размучен.")
    except Exception as exc:
        await message.answer(f"Ошибка при размуте: {exc}")


@router.message(F.text.lower().startswith("макс мои преды"))
async def my_warnings(message: Message) -> None:
    warnings = await get_warnings_count(message.from_user.id)
    banned = await is_user_banned(message.from_user.id)
    await message.answer(
        f"👤 @{message.from_user.username or 'user'}\n"
        f"Ваших предупреждений: <b>{warnings}/4</b>\n"
        f"Статус: {'<b>ЗАБАНЕН</b> 🚫' if banned else 'Активен ✅'}",
        parse_mode="HTML",
        reply_to_message_id=message.message_id,
    )


@router.message(F.text.lower().startswith("макс фас"))
async def admin_warning_start(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        await message.answer(
            "Формат: Макс фас @username\nСледующим сообщением укажите причину."
        )
        return
    username = parts[2].lstrip("@")
    _pending_warning_reasons[message.from_user.id] = username
    await message.answer(f"Теперь пришлите причину для @{username} отдельным сообщением.")


@router.message(lambda message: message.from_user.id in _pending_warning_reasons)
async def admin_warning_reason(message: Message) -> None:
    username = _pending_warning_reasons.pop(message.from_user.id)
    user_id = await get_user_id_by_username(username)
    if not user_id:
        await message.answer(f"Пользователь @{username} не найден.")
        return

    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Причина не может быть пустой.")
        return
    await add_warning(user_id, f"admin: {reason}")
    warnings = await get_warnings_count(user_id)
    banned = await is_user_banned(user_id)
    await message.answer(
        f"@{username} получил предупреждение от администратора.\n"
        f"Причина: {reason}\n"
        f"Всего предупреждений: {warnings}/4\n"
        f"Статус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )
    if warnings >= MAX_WARN_BEFORE_BAN and not banned:
        await ban_user(user_id, reason="4 warnings (от администратора)")
        await message.answer(f"Пользователь @{username} ЗАБАНЕН за 4 предупреждения.")


@router.message(F.text.lower().startswith("макс преды"))
async def admin_check_warnings(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: Макс преды @username")
        return
    username = parts[2].lstrip("@")
    user_id = await get_user_id_by_username(username)
    if not user_id:
        await message.answer("Пользователь не найден.")
        return
    warnings = await get_warnings_count(user_id)
    banned = await is_user_banned(user_id)
    await message.answer(
        f"@{username}: {warnings}/4 предупреждений\n"
        f"Статус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )


@router.message(F.text.lower().startswith("макс амнистия"))
async def admin_unban_and_reset(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        await message.answer("Формат: макс амнистия @username")
        return
    username = parts[2].lstrip("@")
    user = await get_user_by_username(username)
    user_id = user["user_id"] if user else await get_user_id_by_username(username)
    if not user_id:
        await message.answer(f"Пользователь @{username} не найден.")
        return

    await unban_user(user_id)
    await reset_warnings(user_id)
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            user_id,
            permissions=_full_chat_permissions(),
        )
    except Exception as exc:
        await message.answer(f"Ошибка снятия ограничений: {exc}")
    banned = await is_user_banned(user_id)
    await message.answer(
        f"✅ Пользователь @{username} полностью разбанен, может отправлять любые сообщения!\n"
        f"Статус: {'<b>ЗАБАНЕН</b> 🚫' if banned else 'Активен ✅'}",
        parse_mode="HTML",
    )


@router.message(F.text.lower().startswith("макс рабан"))
async def admin_full_unrestrict(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username or f"id{user_id}"
    else:
        parts = (message.text or "").strip().split()
        if len(parts) < 3:
            await message.answer("Формат: макс рабан @username (или используйте reply)")
            return
        username = parts[2].lstrip("@")
        user = await get_user_by_username(username)
        if not user:
            await message.answer(
                f"Пользователь @{username} не найден в базе. "
                "Используйте рабан через reply, если он не писал боту."
            )
            return
        user_id = user["user_id"]

    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            user_id,
            permissions=_full_chat_permissions(),
        )
        await message.answer(
            f"✅ Пользователь @{username} полностью разблокирован и может отправлять любые сообщения."
        )
    except Exception as exc:
        await message.answer(f"Ошибка снятия ограничений: {exc}")


@router.message(F.text.lower().startswith("макс обнулить"))
async def admin_reset_warnings(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс обнулить @username")
        return
    username = parts[2].lstrip("@")
    user = await get_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден.")
        return
    await reset_warnings(user["user_id"])
    banned = await is_user_banned(user["user_id"])
    await message.answer(
        f"✅ Предупреждения @{username} обнулены. "
        f"Статус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )


@router.message(F.text.lower().startswith("макс все преды"))
async def admin_all_warnings(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    rows = await (await WarningService.create()).list_users_with_warnings()
    if not rows:
        await message.answer("Нет пользователей с предупреждениями.")
        return
    lines = ["<b>Список всех предупреждений:</b>"]
    for row in rows:
        user = f"@{row['username']}" if row["username"] else f"id{row['user_id']}"
        lines.append(f"{user} — {row['warnings_count']}/4")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text.lower().startswith("макс удалённые ставки"))
async def show_deleted_bids(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    rows = await (await WarningService.create()).list_deleted_bid_warnings(limit=50)
    if not rows:
        await message.answer("Нет удалённых ставок за последнее время.")
        return
    lines = ["<b>Последние удаления ставок:</b>"]
    for row in rows:
        user = f"@{row['username']}" if row["username"] else f"id{row['user_id']}"
        lines.append(f"{user} — {row['issued_at']:%d.%m.%Y %H:%M:%S}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text.lower().startswith("макс бан"))
async def admin_ban_user(message: Message) -> None:
    if await _deny_unless_admin(message):
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username or f"id{user_id}"
    else:
        parts = (message.text or "").strip().split()
        if len(parts) < 3:
            await message.answer("Формат: Макс бан @username (или reply на сообщение пользователя)")
            return
        username = parts[2].lstrip("@")
        user = await get_user_by_username(username)
        if not user:
            await message.answer(f"Пользователь @{username} не найден.")
            return
        user_id = user["user_id"]

    await ban_user(user_id, reason="бан через команду 'макс бан'")
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            user_id,
            permissions=types.ChatPermissions(can_send_messages=False),
        )
    except Exception as exc:
        await message.answer(f"Ошибка при бане пользователя: {exc}")
    await message.answer(
        f"@{username or 'нарушитель'}, Скажи честно, неужели ты ожидал другого исхода?"
    )


async def _resolve_user_id(value: str | None) -> int | None:
    if not value:
        return None
    return await (await WarningService.create()).resolve_user_id(value)


async def prune_old_warnings(
    *,
    target_user_id: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Delete, or count, old warnings for users who are below the ban threshold."""
    return await (await WarningService.create()).prune_old(
        maximum_warning_count=MAX_WARN_BEFORE_BAN,
        age_days=PRUNE_WARN_AGE_DAYS,
        target_user_id=target_user_id,
        dry_run=dry_run,
    )


@router.message(Command("prune_warns"), F.chat.type.in_({"private", "supergroup", "group"}))
async def cmd_prune_warnings(message: Message, bot: Bot, command: CommandObject) -> None:
    if await _deny_unless_admin(message):
        return
    arguments = (command.args or "").strip().split()
    dry_run = any(value.lower() in {"--dry", "--test", "dry"} for value in arguments)
    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = int(message.reply_to_message.from_user.id)
    if target_id is None:
        for value in arguments:
            target_id = await _resolve_user_id(value)
            if target_id is not None:
                break

    stats = await prune_old_warnings(target_user_id=target_id, dry_run=dry_run)
    if not stats:
        target = f"для пользователя <code>{target_id}</code>" if target_id else "по всем пользователям"
        await message.answer(
            f"✅ Нечего {'чистить' if dry_run else 'удалять'} {target}: "
            "подходящих предупреждений нет.",
            parse_mode="HTML",
        )
        return

    total = sum(item["removed"] for item in stats)
    lines: list[str] = []
    if target_id:
        remaining_count = await (await WarningService.create()).count_warnings(target_id)
        lines.append(
            f"{'🧪' if dry_run else '🧹'} Пользователь <code>{target_id}</code>: "
            f"{'будет удалено' if dry_run else 'удалено'} <b>{total}</b> пред(ов); "
            f"осталось: <b>{remaining_count}</b>."
        )
    else:
        lines.append(
            f"{'🧪 План очищения' if dry_run else '🧹 Очищено'}: всего <b>{total}</b> "
            f"пред(ов) у <b>{len(stats)}</b> пользовател(ей)."
        )
        lines.extend(["", "Топ по удалённым:"])
        for item in stats[:15]:
            lines.append(f" • id{item['user_id']}: {item['removed']}")
        if len(stats) > 15:
            lines.append(f" … и ещё {len(stats) - 15} пользователей.")

    await message.answer("\n".join(lines), parse_mode="HTML")
    action = "DRY-RUN" if dry_run else "DELETE"
    target_note = f" user={target_id}" if target_id else " all-users"
    await _log_admin(
        bot,
        f"🧯 <b>{action}</b> prune_warns:{target_note} — "
        f"удалено/запланировано: <b>{total}</b> пред(ов).",
    )
