"""Target resolvers shared by UID administration workflows."""

from __future__ import annotations

from aiogram import types

from bot.handlers.admin.uid_admin_shared import UID_HEX_RE, USERNAME_RE
from bot.services.uid_verification import (
    get_user_basic_info_by_username,
    get_user_by_username,
    get_user_id_by_uid_any,
    get_user_id_by_username,
    get_user_verified_uid,
    get_username_by_user_id,
)


async def _resolve_uid_from_text(text: str) -> tuple[str | None, str | None]:
    t = (text or "").strip()

    if not t:
        return None, "empty"

    # прямой UID
    if UID_HEX_RE.fullmatch(t):
        return t.lower(), None

    # user_id
    if t.isdigit():
        uid = await get_user_verified_uid(int(t))
        if not uid:
            return None, "no_uid"
        return str(uid), None

    # username
    uname = t.lstrip("@").strip()
    if not uname:
        return None, "empty"

    u = await get_user_by_username(uname)
    if not u:
        return None, "not_in_db"

    uid = await get_user_verified_uid(int(u["user_id"]))
    if not uid:
        return None, "no_uid"

    return str(uid), None


def _extract_user_id_from_message(msg: types.Message) -> int | None:
    # reply
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return int(msg.reply_to_message.from_user.id)

    # старое forward_from
    if msg.forward_from:
        return int(msg.forward_from.id)

    # новое forward_origin
    origin = getattr(msg, "forward_origin", None)
    if origin:
        sender = getattr(origin, "sender_user", None)
        if sender:
            return int(sender.id)

    return None


async def _resolve_whois_target_from_text_or_message(message: types.Message, raw: str | None = None) -> int | None:
    user_id = _extract_user_id_from_message(message)
    if user_id:
        return int(user_id)

    txt = (raw or "").strip()
    if not txt:
        return None

    if UID_HEX_RE.fullmatch(txt):
        return await get_user_id_by_uid_any(txt)

    if txt.lower().startswith("id") and txt[2:].isdigit():
        return int(txt[2:])

    if txt.isdigit():
        return int(txt)

    u = txt.lstrip("@").strip()
    if USERNAME_RE.fullmatch(u):
        info = await get_user_basic_info_by_username(username=u)
        if info:
            return int(info["user_id"])

    return None


async def _resolve_user_id_from_text(text: str) -> tuple[int | None, str | None, str | None]:
    """Возвращает (user_id, username_without_at, err)."""
    t = (text or "").strip()
    if not t:
        return None, None, "empty"

    if t.isdigit():
        return int(t), None, None

    uname = t.lstrip("@").strip()
    if not uname:
        return None, None, "empty"

    uid = await get_user_id_by_username(uname)
    if not uid:
        return None, uname, "not_in_db"
    return int(uid), uname, None


def _extract_uid_anywhere(text: str) -> str | None:
    for tok in (text or "").strip().split():
        if UID_HEX_RE.fullmatch(tok):
            return tok.lower()
    return None


def _extract_user_anywhere(text: str) -> str | None:
    # берём первый токен, который НЕ UID
    for tok in (text or "").strip().split():
        if not UID_HEX_RE.fullmatch(tok):
            return tok.strip()
    return None


async def _resolve_master_user(text: str) -> tuple[int | None, str | None, str | None]:
    """
    Возвращает (user_id, username_without_at, err).
    username можно дать только если он в БД (нажал /start), иначе просим user_id.
    """
    t = (text or "").strip()
    if not t:
        return None, None, "empty"

    if t.isdigit():
        uid = int(t)
        username = await get_username_by_user_id(uid) or ""
        return uid, (username or None), None

    uname = t.lstrip("@").strip()
    if not uname:
        return None, None, "empty"

    user_id = await get_user_id_by_username(uname)
    if not user_id:
        return None, uname, "not_in_db"

    return int(user_id), uname, None



# Public compatibility aliases. Cross-feature imports must use these names.
extract_uid_anywhere = _extract_uid_anywhere
extract_user_anywhere = _extract_user_anywhere
extract_user_id_from_message = _extract_user_id_from_message
resolve_master_user = _resolve_master_user
resolve_uid_from_text = _resolve_uid_from_text
resolve_user_id_from_text = _resolve_user_id_from_text

__all__ = ['_extract_uid_anywhere', '_extract_user_anywhere', '_extract_user_id_from_message', '_resolve_master_user', '_resolve_uid_from_text', '_resolve_user_id_from_text', '_resolve_whois_target_from_text_or_message', 'extract_uid_anywhere', 'extract_user_anywhere', 'extract_user_id_from_message', 'resolve_master_user', 'resolve_uid_from_text', 'resolve_user_id_from_text']
