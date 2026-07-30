import logging
from typing import List, Optional, Set

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError

import config as cfg


def _parse_chat_id(value: object) -> Optional[int]:
    """Принимаем int или строку вида '-100..., -100...'."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _iter_admin_log_chats() -> List[int]:
    """Собирает все возможные лог-чаты из config (один или несколько)."""
    candidates: List[object] = [
        getattr(cfg, "LOG_CHAT_ID", None),
        getattr(cfg, "LOG_CHAT_ID2", None),
    ]

    admin_log_chats = getattr(cfg, "ADMIN_LOG_CHATS", None)
    if isinstance(admin_log_chats, (list, tuple, set)):
        candidates.extend(list(admin_log_chats))

    # Поддержка строки с несколькими id
    raw_multi = getattr(cfg, "LOG_CHAT_IDS", None)
    if isinstance(raw_multi, str) and raw_multi.strip():
        candidates.extend([p.strip() for p in raw_multi.split(",")])

    out: List[int] = []
    seen: Set[int] = set()
    for c in candidates:
        cid = _parse_chat_id(c)
        if cid is None:
            continue
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


async def send_message_safe(
        bot: Bot,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup=None,
) -> bool:
    """Безопасная отправка текста (не валим хендлер из-за логов)."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )
        return True
    except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError) as e:
        logging.warning("send_message_safe failed chat_id=%s: %s", chat_id, e)
        return False
    except Exception as e:  # noqa: BLE001
        logging.exception("send_message_safe unexpected error chat_id=%s: %s", chat_id, e)
        return False


async def send_admin_log(bot: Bot, *args: str, reply_markup=None) -> None:
    """
    Поддерживает оба формата:
      - send_admin_log(bot, text)
      - send_admin_log(bot, category, text)  # category игнорируется
    + поддержка reply_markup
    """
    if not args:
        return

    text = args[0] if len(args) == 1 else args[1]
    if not text:
        return

    chats = _iter_admin_log_chats()
    if not chats:
        logging.info("send_admin_log: no log chats configured (LOG_CHAT_ID/ADMIN_LOG_CHATS)")
        return

    for cid in chats:
        await send_message_safe(bot, cid, text, reply_markup=reply_markup)

def _admin_dict(user: object) -> dict:
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    return {"id": user_id, "username": username or full_name or str(user_id)}


def short_media_id(v: object) -> str:
    """Чтобы логи не превращались в простыню из file_id на 300 символов."""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    if len(s) <= 22:
        return s
    return f"{s[:12]}…{s[-8:]}"


async def send_lot_edit_log(
        bot: Bot,
        *,
        admin_user: object,
        auction_id: int,
        lot_for_log: dict,
        changes: list[tuple[str, object, object]],
        audit_action_type: str,
        audit_details: str,
) -> None:
    """
    Единый стиль логов:
      log_text = format_admin_action_log(..., owners_text=owners_text)
      log_text += format_field_change_block(...)
      await send_admin_log(...)
      await log_audit_action(...)
    """
    # Ленивая загрузка, чтобы не ловить циклические импорты.
    from bot.handlers.admin.helper.new.admin_actions import get_lot_owners_text
    from bot.handlers.admin.helper.new.formatting import (
        format_admin_action_log,
        format_field_change_block,
    )
    from db.db import log_audit_action

    owners_text = await get_lot_owners_text(int(auction_id))

    log_text = format_admin_action_log(
        action="edit_lot",
        admin=_admin_dict(admin_user),
        lot=lot_for_log,
        owners_text=owners_text,
    )
    for title, old_v, new_v in changes:
        log_text += format_field_change_block(title, old_v, new_v)

    await send_admin_log(bot, log_text)

    await log_audit_action(
        user_id=getattr(admin_user, "id", None) or 0,
        action_type=audit_action_type,
        auction_id=int(auction_id),
        details=audit_details,
    )


def _admin_dict(user: object) -> dict:
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    return {"id": user_id, "username": username or full_name or str(user_id)}


def _short_media_id(v: object) -> str:
    """Чтобы логи не превращались в простыню из file_id на 300 символов."""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    if len(s) <= 22:
        return s
    return f"{s[:12]}…{s[-8:]}"


async def _log_lot_field_changes(
        bot,
        *,
        admin_user: object,
        auction_id: int,
        lot_for_log: dict,
        changes: list[tuple[str, object, object]],
        audit_action_type: str,
        audit_details: str,
) -> None:
    """
    Единый стиль логов:
      log_text = format_admin_action_log(..., owners_text=owners_text)
      log_text += format_field_change_block(...)
      await send_admin_log(...)
      await log_audit_action(...)
    """
    # ⚠️ ВАЖНО: импорты внутри функции = нет циклических импортов при старте бота
    from bot.handlers.admin.helper.new.admin_actions import get_lot_owners_text
    from bot.handlers.admin.helper.new.formatting import (
        format_admin_action_log,
        format_field_change_block,
    )
    from db.db import log_audit_action

    owners_text = await get_lot_owners_text(int(auction_id))

    log_text = format_admin_action_log(
        action="edit_lot",
        admin=_admin_dict(admin_user),
        lot=lot_for_log,
        owners_text=owners_text,
    )
    for title, old_v, new_v in changes:
        log_text += format_field_change_block(title, old_v, new_v)

    await send_admin_log(bot, log_text)

    await log_audit_action(
        user_id=getattr(admin_user, "id", None) or 0,
        action_type=audit_action_type,
        auction_id=int(auction_id),
        details=audit_details,
    )


# Если хочешь импортировать наружу (из admin_panel.py), добавь “публичные” алиасы:
short_media_id = _short_media_id
send_lot_edit_log = _log_lot_field_changes
