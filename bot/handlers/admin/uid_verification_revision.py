# ruff: noqa: RUF001
"""The UID-verification revision FSM and callbacks."""

from __future__ import annotations

import html
import logging
from contextlib import suppress

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.uid_admin_presentation import (
    REV_ALLOWED,
    kb_uidv_revision,
    rev_flags_to_lines,
    sort_rev_flags,
)
from bot.services import uid_verification as uid_verification_service
from bot.services.admin_thanks import admin_tag
from bot.telegram.callback_parser import split_callback_data
from bot.telegram.states import UIDVerificationRevisionFSM

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def _callback_message(call: types.CallbackQuery) -> types.Message | None:
    message = call.message
    return message if isinstance(message, types.Message) else None


@router.callback_query(F.data.startswith("uidv|rev|"))
@admin_only
async def uidv_revision_start(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    service = await uid_verification_service.UIDVerificationService.create()
    req = await service.get_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение заявки недоступно.", show_alert=True)
        return

    await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
    await state.update_data(uidv_rev_req_id=req_id, uidv_rev_flags=[], uidv_rev_reason="")

    txt = (
        f"🔧 <b>На доработку</b> (заявка <b>#{req_id}</b>)\n\n"
        f"Отметь, что нужно исправить, добавь причину, затем отправь пользователю."
    )
    try:
        await message.edit_text(txt, reply_markup=kb_uidv_revision(req_id, [], ""))
    except TelegramBadRequest:
        await message.answer(txt, reply_markup=kb_uidv_revision(req_id, [], ""))
    await call.answer()


@router.callback_query(F.data.startswith("uidv|rev_toggle|"))
@admin_only
async def uidv_revision_toggle(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = split_callback_data(call.data or "", "|")

    # поддержка обоих форматов:
    # новый: uidv|rev_toggle|<id>|<flag>  (len=4)
    # старый/кривой: uidv|rev_toggle|<id>|X|<flag> (len>=5)
    if len(parts) < 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение заявки недоступно.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    flag = (parts[3] if len(parts) == 4 else parts[4] if len(parts) > 4 else "").strip()

    data = await state.get_data()
    if int(data.get("uidv_rev_req_id") or 0) != req_id:
        await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
        await state.update_data(
            uidv_rev_req_id=req_id,
            uidv_rev_flags=[],
            uidv_rev_reason="",
        )
        data = await state.get_data()

    chosen = set(data.get("uidv_rev_flags") or [])
    reason = str(data.get("uidv_rev_reason") or "")

    if flag in REV_ALLOWED:
        if flag in chosen:
            chosen.remove(flag)
        else:
            chosen.add(flag)

    chosen_list = sort_rev_flags(chosen)
    await state.update_data(uidv_rev_flags=chosen_list)

    await call.answer()

    with suppress(TelegramBadRequest):
        await message.edit_reply_markup(
            reply_markup=kb_uidv_revision(req_id, chosen_list, reason)
        )


@router.callback_query(F.data.startswith("uidv|rev_reason|"))
@admin_only
async def uidv_revision_reason(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение заявки недоступно.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    await state.set_state(UIDVerificationRevisionFSM.waiting_reason)
    await state.update_data(uidv_rev_req_id=req_id)

    await call.answer()

    await message.answer(
        "✏️ Напиши причину/комментарий, что именно не так и что нужно исправить.\n"
        "Можно коротко, но по делу.",
    )


@router.message(UIDVerificationRevisionFSM.waiting_reason, F.chat.type == "private")
@admin_only
async def uidv_revision_reason_msg(message: types.Message, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    data = await state.get_data()
    req_id = int(data.get("uidv_rev_req_id") or 0)
    chosen = sort_rev_flags(data.get("uidv_rev_flags") or [])

    if not reason:
        await message.answer("Причина не может быть пустой.")
        return

    await state.set_state(UIDVerificationRevisionFSM.choosing_flags)
    await state.update_data(uidv_rev_reason=reason)

    txt = f"🔧 <b>На доработку</b> (заявка <b>#{req_id}</b>)\n\nПричина сохранена."
    await message.answer(txt, reply_markup=kb_uidv_revision(req_id, chosen, reason))


@router.callback_query(F.data.startswith("uidv|rev_send|"))
@admin_only
async def uidv_revision_send(
    call: types.CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[2] or 0)
    data = await state.get_data()
    if int(data.get("uidv_rev_req_id") or 0) != req_id:
        await call.answer("Сессия устарела. Открой доработку заново.", show_alert=True)
        return

    chosen = sort_rev_flags(data.get("uidv_rev_flags") or [])
    reason = (data.get("uidv_rev_reason") or "").strip()

    if not chosen:
        await call.answer("Сначала отметь, что исправлять.", show_alert=True)
        return
    if not reason:
        await call.answer("Нужна причина (кнопка «Причина»).", show_alert=True)
        return

    service = await uid_verification_service.UIDVerificationService.create()
    req = await service.get_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    admin_u = call.from_user
    admin_id = call.from_user.id
    admin_username = call.from_user.username or call.from_user.full_name

    ok = await uid_verification_service.set_uid_verification_request_revision(  # type: ignore[attr-defined]
        req_id,
        moderator_id=admin_id,
        moderator_username=admin_username,
        reason=reason,
        flags=chosen,
    )

    if not ok:
        await call.answer("Не удалось обновить заявку.", show_alert=True)
        return

    user_id = int(req.get("user_id") or 0)
    moderator = admin_tag(admin_u)
    lines = "\n".join(rev_flags_to_lines(chosen))

    kb = InlineKeyboardBuilder()
    kb.button(text="🔧 Исправить заявку", callback_data=f"uidv_fix|{req_id}")
    kb.button(text="📌 Показать мою заявку", callback_data="uidv|start")
    kb.adjust(1)

    text_user = (
        "🔧 Заявка на верификацию требует доработки\n\n"
        f"Заявка: #{req_id}\n"
        f"Модератор: {moderator}\n\n"
        f"Нужно исправить:\n{lines}\n\n"
        f"Причина:\n{html.escape(reason)}\n\n"
        "Нажми «🔧 Исправить заявку» и досылай только то, что отмечено."
    )

    delivered = True
    try:
        await bot.send_message(
            user_id,
            text_user,
            reply_markup=kb.as_markup(),
            protect_content=False,
        )
    except TelegramAPIError:
        delivered = False
        logger.exception(
            "Failed to notify user about UID verification revision",
            extra={"request_id": req_id, "user_id": user_id},
        )

    await state.clear()

    if delivered:
        await call.answer("Отправлено ✅")
    else:
        await call.answer(
            "Заявка переведена на доработку, но сообщение пользователю не доставлено.",
            show_alert=True,
        )


__all__ = [
    "router",
    "uidv_revision_reason",
    "uidv_revision_reason_msg",
    "uidv_revision_send",
    "uidv_revision_start",
    "uidv_revision_toggle",
]
