"""Administrative WHOIS handlers for Telegram users and UID bindings."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.uid_admin_presentation import _render_whois, _render_whois_by_uid
from bot.handlers.admin.uid_admin_resolvers import _extract_user_id_from_message
from bot.handlers.admin.uid_admin_shared import UID_HEX_RE, USERNAME_RE
from bot.services.uid_verification import get_user_basic_info_by_username
from bot.telegram.states import ModActionFSM


router = Router(name=__name__)


@router.message(Command("whois"), F.chat.type == "private")
@admin_only
async def cmd_whois(message: types.Message, state: FSMContext):
    await state.clear()

    # 1) reply/forward
    user_id = _extract_user_id_from_message(message)

    # 2) аргумент
    arg = None
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        arg = parts[1].strip()

    # если передан UID hex — отдельный режим
    if not user_id and arg and UID_HEX_RE.fullmatch(arg):
        await _render_whois_by_uid(message, arg.lower())
        return

    # обычный поиск по id / username
    if not user_id and arg:
        if arg.lower().startswith("id") and arg[2:].isdigit():
            user_id = int(arg[2:])
        elif arg.isdigit():
            user_id = int(arg)
        else:
            u = arg.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    user_id = int(info["user_id"])

    if user_id:
        await _render_whois(message, user_id)
        return

    await state.set_state(ModActionFSM.waiting_for_whois_target)
    await message.answer(
        "Перешли сообщение пользователя (или ответь на него), либо пришли @username / user_id / UID.\n"
        "Отмена: /cancel"
    )


@router.message(ModActionFSM.waiting_for_whois_target, F.chat.type == "private")
@admin_only
async def whois_waiting_target(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    if txt.lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer("Ок, отменено.")
        return

    user_id = _extract_user_id_from_message(message)

    # UID hex
    if not user_id and UID_HEX_RE.fullmatch(txt):
        await state.clear()
        await _render_whois_by_uid(message, txt.lower())
        return

    if not user_id and txt:
        if txt.lower().startswith("id") and txt[2:].isdigit():
            user_id = int(txt[2:])
        elif txt.isdigit():
            user_id = int(txt)
        else:
            u = txt.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    user_id = int(info["user_id"])

    if not user_id:
        await message.answer(
            "Не смог определить пользователя.\n"
            "Нужен reply/forward, @username, user_id или UID.\n"
            "Отмена: /cancel"
        )
        return

    await state.clear()
    await _render_whois(message, user_id)


__all__ = ["router","cmd_whois","whois_waiting_target"]
