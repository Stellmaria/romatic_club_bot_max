"""UID hash-ban administration handlers."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.admin.uid_admin_resolvers import resolve_uid_from_text
from bot.handlers.admin.uid_admin_shared import mask_uid, parse_ban_reason_and_until
from bot.services.admin_thanks import admin_tag
from bot.services.uid_verification import list_uid_bans, remove_uid_ban, upsert_uid_ban
from bot.telegram.states import ModActionFSM
from bot.uid_crypto import mask_uid, mask_uid_by_last4


router = Router(name=__name__)


@router.message(F.text == "⛔ UID-бан", F.chat.type == "private")
@admin_only
async def uid_ban_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⛔ UID-блокировки:",
        reply_markup=menu_keyboard(
            ["💣 Мастер-бан", "🧹 Мастер-разбан"],
            ["⛔ Забанить UID", "✅ Разбанить UID"],
            ["🚫 Забанить пользователя", "✅ Разбанить пользователя"],
            ["📋 Список UID-банов", "📋 Список банов пользователей"],
            ["⬅️ Назад"],
        ),
    )


@router.message(F.text == "⛔ Забанить UID", F.chat.type == "private")
@admin_only
async def uid_ban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_uid_ban_target)
    await message.answer(
        "Пришли UID (24 hex) или @username / user_id.\n"
        "Если шлёшь @username — он должен быть в базе (нажал /start)."
    )


@router.message(ModActionFSM.waiting_for_uid_ban_target, F.chat.type == "private")
@admin_only
async def uid_ban_got_target(message: Message, state: FSMContext):
    uid, err = await resolve_uid_from_text(message.text or "")
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    await state.update_data(uid_ban_uid=uid)
    await state.set_state(ModActionFSM.waiting_for_uid_ban_reason)
    await message.answer(
        "Теперь пришли причину.\n"
        "Можно указать срок в днях в начале: например\n"
        "<code>7 мошенник</code>\n"
        "Или просто текст причины.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_uid_ban_reason, F.chat.type == "private")
@admin_only
async def uid_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = str(data.get("uid_ban_uid") or "").strip()
    if not uid:
        await state.clear()
        await message.answer("Потерял UID в состоянии. Начни заново.")
        return

    reason, banned_until = parse_ban_reason_and_until(message.text or "")

    row = await upsert_uid_ban(
        uid,
        banned_by=message.from_user.id,
        reason=reason,
        banned_until=banned_until,
    )

    # лог: UID не палим полностью
    until_txt = "навсегда" if not row.get("banned_until") else str(row["banned_until"])
    log_text = (
        f"⛔ <b>UID добавлен в ЧС</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"UID: <code>{mask_uid(uid)}</code>\n"
        f"До: <b>{html.escape(until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(f"✅ Готово. UID в ЧС: <code>{mask_uid(uid)}</code>", parse_mode="HTML")


@router.message(F.text == "✅ Разбанить UID", F.chat.type == "private")
@admin_only
async def uid_unban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_uid_unban_target)
    await message.answer("Пришли UID (24 hex) или @username / user_id для разбана.")


@router.message(ModActionFSM.waiting_for_uid_unban_target, F.chat.type == "private")
@admin_only
async def uid_unban_got_target(message: Message, state: FSMContext):
    uid, err = await resolve_uid_from_text(message.text or "")
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    ok = await remove_uid_ban(uid)

    if ok:
        log_text = (
            f"✅ <b>UID удалён из ЧС</b>\n"
            f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
            f"UID: <code>{mask_uid(uid)}</code>"
        )
        await send_admin_log(message.bot, log_text)

        await message.answer(f"✅ Разбанено: <code>{mask_uid(uid)}</code>", parse_mode="HTML")
    else:
        await message.answer("UID не найден в ЧС.")

    await state.clear()


@router.message(F.text == "📋 Список UID-банов", F.chat.type == "private")
@admin_only
async def uid_ban_list(message: Message):
    items = await list_uid_bans(limit=50, offset=0, only_active=True)
    if not items:
        await message.answer("Список пуст.")
        return

    lines = ["⛔ Активные UID-баны (до 50):", ""]
    for it in items:
        last4 = it.get("uid_last4") or ""
        until = it.get("banned_until")
        reason = it.get("reason") or "—"
        until_txt = "навсегда" if not until else str(until)
        lines.append(f"• <code>{mask_uid_by_last4(last4)}</code> • до: <b>{html.escape(until_txt)}</b> • {html.escape(reason)}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("uidban"), F.chat.type == "private")
@admin_only
async def cmd_uidban(message: Message):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Формат: /uidban <uid|@username|user_id> [текст причины | '7 причина']")
        return

    uid, err = await resolve_uid_from_text(parts[1])
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    reason_raw = parts[2] if len(parts) >= 3 else "-"
    reason, banned_until = parse_ban_reason_and_until(reason_raw)

    await upsert_uid_ban(uid, banned_by=message.from_user.id, reason=reason, banned_until=banned_until)
    await message.answer(f"✅ UID в ЧС: <code>{mask_uid(uid)}</code>", parse_mode="HTML")


@router.message(Command("uidunban"), F.chat.type == "private")
@admin_only
async def cmd_uidunban(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /uidunban <uid|@username|user_id>")
        return

    uid, err = await resolve_uid_from_text(parts[1])
    if not uid:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого."
            )
        else:
            await message.answer("Нужно прислать UID (24 hex) или @username / user_id.")
        return

    ok = await remove_uid_ban(uid)
    await message.answer("✅ Разбанено." if ok else "UID не найден в ЧС.")


@router.message(Command("uidbans"), F.chat.type == "private")
@admin_only
async def cmd_uidbans(message: Message):
    items = await list_uid_bans(limit=50, offset=0, only_active=True)
    if not items:
        await message.answer("Список пуст.")
        return

    lines = ["⛔ Активные UID-баны (до 50):", ""]
    for it in items:
        last4 = it.get("uid_last4") or ""
        until = it.get("banned_until")
        reason = it.get("reason") or "—"
        until_txt = "навсегда" if not until else str(until)
        lines.append(
            f"• <code>{mask_uid_by_last4(last4)}</code> • до: <b>{html.escape(until_txt)}</b> • {html.escape(reason)}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


__all__ = ["router","uid_ban_menu","uid_ban_start","uid_ban_got_target","uid_ban_got_reason","uid_unban_start","uid_unban_got_target","uid_ban_list","cmd_uidban","cmd_uidunban","cmd_uidbans"]
