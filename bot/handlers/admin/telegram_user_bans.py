"""Telegram user-id ban administration handlers."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.admin.uid_admin_resolvers import _resolve_user_id_from_text
from bot.handlers.admin.uid_admin_shared import _parse_user_ban_reason_and_until
from bot.services.admin_thanks import admin_tag
from bot.services.uid_verification import ban_user, list_active_user_bans, unban_user
from bot.telegram.states import ModActionFSM


router = Router(name=__name__)


@router.message(F.text == "🚫 Забанить пользователя", F.chat.type == "private")
@admin_only
async def user_ban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_user_ban_target)
    await message.answer(
        "Пришли @username или user_id пользователя, которого надо забанить.\n"
        "Если шлёшь @username — он должен быть в базе (нажал /start)."
    )


@router.message(ModActionFSM.waiting_for_user_ban_target, F.chat.type == "private")
@admin_only
async def user_ban_got_target(message: Message, state: FSMContext):
    user_id, uname, err = await _resolve_user_id_from_text(message.text or "")
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или пришлите user_id."
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(user_ban_user_id=int(user_id), user_ban_username=(uname or "").strip() or None)
    await state.set_state(ModActionFSM.waiting_for_user_ban_reason)
    await message.answer(
        "Теперь пришли причину.\n"
        "Можно указать срок в днях в начале: например <code>7 спам</code>.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_user_ban_reason, F.chat.type == "private")
@admin_only
async def user_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("user_ban_user_id") or 0)
    uname = (data.get("user_ban_username") or "").strip() or None

    if user_id <= 0:
        await state.clear()
        await message.answer("Потерял user_id в состоянии. Начни заново.")
        return

    reason, banned_until = _parse_user_ban_reason_and_until(message.text or "")

    await ban_user(
        user_id=int(user_id),
        banned_until=banned_until,
        reason=(reason or "").strip(),
    )

    until_txt = str(banned_until)
    who = f"@{uname}" if uname else f"id{user_id}"
    log_text = (
        f"🚫 <b>Пользователь забанен</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"Кого: <b>{html.escape(who)}</b> (id <code>{user_id}</code>)\n"
        f"До: <b>{html.escape(until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"✅ Готово. Забанен: <b>{html.escape(who)}</b> до <b>{html.escape(until_txt)}</b>",
        parse_mode="HTML",
    )


@router.message(F.text == "✅ Разбанить пользователя", F.chat.type == "private")
@admin_only
async def user_unban_start(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_user_unban_target)
    await message.answer("Пришли @username или user_id для разбана пользователя.")


@router.message(ModActionFSM.waiting_for_user_unban_target, F.chat.type == "private")
@admin_only
async def user_unban_got_target(message: Message, state: FSMContext):
    user_id, uname, err = await _resolve_user_id_from_text(message.text or "")
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "К сожалению, этого пользователя нет в моей базе. "
                "Попросите его нажать /start в чате с ботом @RomanticClubBot, или пришлите user_id."
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await unban_user(int(user_id))
    who = f"@{uname}" if uname else f"id{user_id}"
    log_text = (
        f"✅ <b>Пользователь разбанен</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"Кого: <b>{html.escape(who)}</b> (id <code>{int(user_id)}</code>)"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(f"✅ Разбанено: <b>{html.escape(who)}</b>", parse_mode="HTML")


@router.message(F.text == "📋 Список банов пользователей", F.chat.type == "private")
@admin_only
async def user_ban_list(message: Message):
    rows = await list_active_user_bans(limit=50)

    if not rows:
        await message.answer("Список пуст.")
        return

    lines = ["🚫 Активные баны пользователей (до 50):", ""]
    for r in rows:
        uid = int(r.get("user_id") or 0)
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id{uid}"
        until = r.get("banned_until")
        reason = (r.get("reason") or "").strip() or "—"
        lines.append(
            f"• <b>{html.escape(who)}</b> (id <code>{uid}</code>) • до: <b>{html.escape(str(until))}</b> • {html.escape(reason)}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


__all__ = ["router","user_ban_start","user_ban_got_target","user_ban_got_reason","user_unban_start","user_unban_got_target","user_ban_list"]
