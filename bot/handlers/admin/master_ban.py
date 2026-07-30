"""Atomic master ban and unban workflows for UID and Telegram identity."""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.admin.uid_admin_resolvers import (
    _extract_uid_anywhere,
    _extract_user_anywhere,
    _resolve_master_user,
)
from bot.handlers.admin.uid_admin_shared import _mask_uid, _parse_master_reason
from bot.services.admin_thanks import admin_tag
from bot.services.uid_verification import apply_master_ban, apply_master_unban
from bot.telegram.states import ModActionFSM


router = Router(name=__name__)


@router.message(F.text == "💣 Мастер-бан", F.chat.type == "private")
@admin_only
async def master_ban_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ModActionFSM.waiting_for_master_ban_user)
    await message.answer(
        "💣 <b>Мастер-бан</b>\n\n"
        "Шаг 1/3: пришли <b>@username</b> или <b>user_id</b>.\n"
        "Можно одной строкой: <code>UID @username</code> или <code>UID user_id</code> (через пробел).",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_user, F.chat.type == "private")
@admin_only
async def master_ban_got_user(message: Message, state: FSMContext):
    text = message.text or ""

    uid = _extract_uid_anywhere(text)
    user_token = _extract_user_anywhere(text)

    # если прислали одной строкой "UID @user" / "UID 123"
    if uid and user_token:
        user_id, username, err = await _resolve_master_user(user_token)
        if not user_id:
            if err == "not_in_db":
                await message.answer(
                    "Этого @username нет в базе.\n"
                    "Пусть нажмёт /start у бота или пришли <b>user_id</b>.",
                    parse_mode="HTML",
                )
            else:
                await message.answer("Нужно прислать @username или user_id.")
            return

        await state.update_data(master_user_id=int(user_id), master_username=username, master_uid=uid)
        await state.set_state(ModActionFSM.waiting_for_master_ban_reason)
        await message.answer(
            "Шаг 3/3: причина.\n"
            "Можно со сроком: <code>7 мошенник</code>\n"
            "Или просто текст.\n"
            "Если без причины: <code>-</code>",
            parse_mode="HTML",
        )
        return

    # обычный режим: ждём только TG на шаге 1
    user_id, username, err = await _resolve_master_user(text)
    if not user_id:
        if err == "not_in_db":
            await message.answer(
                "Этого @username нет в базе.\n"
                "Пусть нажмёт /start у бота или пришли <b>user_id</b>.",
                parse_mode="HTML",
            )
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(master_user_id=int(user_id), master_username=username)
    await state.set_state(ModActionFSM.waiting_for_master_ban_uid)
    await message.answer(
        "Шаг 2/3: теперь пришли <b>UID</b> (24 hex).\n"
        "Можно просто UID, без лишних слов.",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_uid, F.chat.type == "private")
@admin_only
async def master_ban_got_uid(message: Message, state: FSMContext):
    uid = _extract_uid_anywhere(message.text or "")
    if not uid:
        await message.answer("UID должен быть ровно 24 hex символа. Пришли UID ещё раз.")
        return

    await state.update_data(master_uid=uid)
    await state.set_state(ModActionFSM.waiting_for_master_ban_reason)
    await message.answer(
        "Шаг 3/3: причина.\n"
        "Можно со сроком: <code>7 мошенник</code>\n"
        "Или просто текст.\n"
        "Если без причины: <code>-</code>",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_ban_reason, F.chat.type == "private")
@admin_only
async def master_ban_got_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("master_user_id") or 0)
    username = (data.get("master_username") or "").strip() or None
    uid = (data.get("master_uid") or "").strip().lower()

    if user_id <= 0 or not uid:
        await state.clear()
        await message.answer("Потерял данные (user/uid) в состоянии. Начни заново.")
        return

    reason, days = _parse_master_reason(message.text or "")

    # UID-ban: если days нет -> навсегда
    uid_until = None if days is None else (datetime.now(ZoneInfo("UTC")) + timedelta(days=int(days)))

    # TG-ban: если days нет -> 10 лет (как у тебя в банах пользователя)
    user_until = (datetime.now() + timedelta(days=int(days))) if days is not None else (datetime.now() + timedelta(days=365 * 10))

    result = await apply_master_ban(
        uid=uid,
        user_id=int(user_id),
        banned_by=message.from_user.id,
        reason=reason,
        uid_banned_until=uid_until,
        user_banned_until=user_until,
    )

    # (опционально) проверка владельца UID и предупреждение в лог
    owner_user_id = result.owner_user_id
    mismatch_note = ""
    if owner_user_id and owner_user_id != user_id:
        mismatch_note = f"\n⚠️ UID принадлежит другому user_id: <code>{owner_user_id}</code>"

    who = f"@{username}" if username else f"id{user_id}"
    uid_txt = _mask_uid(uid)
    uid_until_txt = "навсегда" if uid_until is None else str(uid_until)

    log_text = (
        f"💣 <b>МАСТЕР-БАН</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"TG: <b>{html.escape(who)}</b> (id <code>{user_id}</code>)\n"
        f"UID: <code>{html.escape(uid_txt)}</code>\n"
        f"TG до: <b>{html.escape(str(user_until))}</b>\n"
        f"UID до: <b>{html.escape(uid_until_txt)}</b>\n"
        f"Причина: <b>{html.escape(reason or '—')}</b>"
        f"{mismatch_note}"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"✅ Готово.\nTG забанен: <b>{html.escape(who)}</b>\nUID забанен: <code>{html.escape(uid_txt)}</code>",
        parse_mode="HTML",
    )


@router.message(F.text == "🧹 Мастер-разбан", F.chat.type == "private")
@admin_only
async def master_unban_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ModActionFSM.waiting_for_master_unban_user)
    await message.answer(
        "🧹 <b>Мастер-разбан</b>\n\n"
        "Шаг 1/2: пришли <b>@username</b> или <b>user_id</b>.\n"
        "Можно одной строкой: <code>UID @username</code> или <code>UID user_id</code> (через пробел).",
        parse_mode="HTML",
    )


@router.message(ModActionFSM.waiting_for_master_unban_user, F.chat.type == "private")
@admin_only
async def master_unban_got_user(message: Message, state: FSMContext):
    text = message.text or ""
    uid = _extract_uid_anywhere(text)
    user_token = _extract_user_anywhere(text)

    if uid and user_token:
        user_id, username, err = await _resolve_master_user(user_token)
        if not user_id:
            await message.answer("Нужно прислать @username или user_id.")
            return
        await state.update_data(master_user_id=int(user_id), master_username=username, master_uid=uid)
        # сразу делаем разбан
        await _do_master_unban(message, state)
        return

    user_id, username, err = await _resolve_master_user(text)
    if not user_id:
        if err == "not_in_db":
            await message.answer("Этого @username нет в базе. Пришли user_id или UID.")
        else:
            await message.answer("Нужно прислать @username или user_id.")
        return

    await state.update_data(master_user_id=int(user_id), master_username=username)
    await state.set_state(ModActionFSM.waiting_for_master_unban_uid)
    await message.answer("Шаг 2/2: пришли UID (24 hex).", parse_mode="HTML")


@router.message(ModActionFSM.waiting_for_master_unban_uid, F.chat.type == "private")
@admin_only
async def master_unban_got_uid(message: Message, state: FSMContext):
    uid = _extract_uid_anywhere(message.text or "")
    if not uid:
        await message.answer("UID должен быть 24 hex. Пришли UID ещё раз.")
        return

    await state.update_data(master_uid=uid)
    await _do_master_unban(message, state)


async def _do_master_unban(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = int(data.get("master_user_id") or 0)
    username = (data.get("master_username") or "").strip() or None
    uid = (data.get("master_uid") or "").strip().lower()

    result = await apply_master_unban(
        uid=uid or None,
        user_id=int(user_id) if user_id > 0 else None,
    )
    did_uid = result.uid_removed
    did_user = result.user_removed

    who = f"@{username}" if username else f"id{user_id}"
    uid_txt = _mask_uid(uid) if uid else "—"

    log_text = (
        f"🧹 <b>МАСТЕР-РАЗБАН</b>\n"
        f"Админ: <b>{admin_tag(message.from_user)}</b> (id {message.from_user.id})\n"
        f"TG: <b>{html.escape(who)}</b> ({'✅' if did_user else '⚠️'})\n"
        f"UID: <code>{html.escape(uid_txt)}</code> ({'✅' if did_uid else '⚠️'})"
    )
    await send_admin_log(message.bot, log_text)

    await state.clear()
    await message.answer(
        f"Готово.\nTG: {'✅' if did_user else '⚠️'}\nUID: {'✅' if did_uid else '⚠️'}"
    )


__all__ = ["router","master_ban_start","master_ban_got_user","master_ban_got_uid","master_ban_got_reason","master_unban_start","master_unban_got_user","master_unban_got_uid","_do_master_unban"]
