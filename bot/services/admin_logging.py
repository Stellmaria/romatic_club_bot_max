"""Telegram delivery and persistent audit workflows for admin actions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from bot.core.legacy_config import legacy_config
from bot.presentation.admin import (
    extract_auction_id,
    format_admin_action_log,
    format_delete_request_log,
    format_field_change_block,
)
from bot.repositories.admin_logs import AdminLogsRepository
from bot.services.admin_owners import get_lot_owners_text
from db.pool import get_db_pool


_MESSAGE_LOCKS: dict[int, asyncio.Lock] = {}
_MAX_SEND_ATTEMPTS = 2


async def _repository() -> AdminLogsRepository:
    return AdminLogsRepository(await get_db_pool())


def _parse_chat_id(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _iter_admin_log_chats() -> list[int]:
    """Collect configured admin log chats once, preserving their order."""
    candidates: list[object] = [
        getattr(legacy_config, "LOG_CHAT_ID", None),
        getattr(legacy_config, "LOG_CHAT_ID2", None),
    ]

    configured_chats = getattr(legacy_config, "ADMIN_LOG_CHATS", None)
    if isinstance(configured_chats, (list, tuple, set)):
        candidates.extend(configured_chats)

    raw_multiple = getattr(legacy_config, "LOG_CHAT_IDS", None)
    if isinstance(raw_multiple, str) and raw_multiple.strip():
        candidates.extend(part.strip() for part in raw_multiple.split(","))

    chats: list[int] = []
    seen: set[int] = set()
    for candidate in candidates:
        chat_id = _parse_chat_id(candidate)
        if chat_id is None or chat_id in seen:
            continue
        seen.add(chat_id)
        chats.append(chat_id)
    return chats


def _message_lock(chat_id: int) -> asyncio.Lock:
    lock = _MESSAGE_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _MESSAGE_LOCKS[chat_id] = lock
    return lock


async def send_message_safe(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
    reply_markup: Any = None,
) -> bool:
    """Send a message without letting an audit failure break its caller.

    Telegram flood-control responses are expected operational backpressure, not
    unexpected application errors. Deliveries to the same chat are serialized,
    wait for Telegram's requested delay and retry once without creating a retry
    stampede from concurrent admin actions.
    """

    async with _message_lock(chat_id):
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                    reply_markup=reply_markup,
                )
                return True
            except TelegramRetryAfter as exc:
                retry_after = max(0.0, float(exc.retry_after))
                logging.warning(
                    "send_message_safe rate limited chat_id=%s retry_after=%.3f attempt=%d/%d",
                    chat_id,
                    retry_after,
                    attempt,
                    _MAX_SEND_ATTEMPTS,
                )
                if attempt >= _MAX_SEND_ATTEMPTS:
                    return False
                await asyncio.sleep(retry_after)
            except (TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError) as exc:
                logging.warning("send_message_safe failed chat_id=%s: %s", chat_id, exc)
                return False
            except Exception as exc:  # noqa: BLE001
                logging.exception(
                    "send_message_safe unexpected error chat_id=%s: %s",
                    chat_id,
                    exc,
                )
                return False
    return False


async def send_admin_log(
    bot: Bot | None,
    *args: str,
    reply_markup: Any = None,
) -> None:
    """Send an admin log, accepting both legacy calling conventions."""
    if bot is None or not args:
        return

    text = args[0] if len(args) == 1 else args[1]
    if not text:
        return

    chats = _iter_admin_log_chats()
    if not chats:
        logging.info(
            "send_admin_log: no log chats configured "
            "(LOG_CHAT_ID/ADMIN_LOG_CHATS)"
        )
        return

    for chat_id in chats:
        await send_message_safe(bot, chat_id, text, reply_markup=reply_markup)


def _admin_dict(user: object) -> dict[str, object]:
    user_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    return {
        "id": user_id,
        "username": username or full_name or str(user_id),
    }


def short_media_id(value: object) -> str:
    """Shorten Telegram file IDs so audit messages remain readable."""
    if value is None:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    if len(text) <= 22:
        return text
    return f"{text[:12]}…{text[-8:]}"


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
    """Send one lot-edit message and persist its audit record."""
    normalized_auction_id = int(auction_id)
    owners_text = await get_lot_owners_text(normalized_auction_id)
    log_text = format_admin_action_log(
        action="edit_lot",
        admin=_admin_dict(admin_user),
        lot=lot_for_log,
        owners_text=owners_text,
    )
    for title, old_value, new_value in changes:
        log_text += format_field_change_block(title, old_value, new_value)

    await send_admin_log(bot, log_text)
    repository = await _repository()
    await repository.add_audit_action(
        user_id=getattr(admin_user, "id", None) or 0,
        action_type=audit_action_type,
        auction_id=normalized_auction_id,
        details=audit_details,
    )


def _as_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _try_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def log_delete_request(
    bot: Bot | None,
    request: Mapping[str, Any],
) -> None:
    """Resolve and deliver the audit message for a lot deletion request."""
    raw_snapshot = request.get("snapshot")
    snapshot: Mapping[str, Any] = (
        raw_snapshot if isinstance(raw_snapshot, Mapping) else {}
    )
    candidates = (
        request.get("auction_id"),
        request.get("lot_id"),
        snapshot.get("auction_id"),
        snapshot.get("lot_id"),
    )
    auction_id = next(
        (value for value in map(_try_int, candidates) if value is not None),
        None,
    )
    if auction_id is None:
        source_text = _as_str(request.get("source_text"), "")
        caption_text = _as_str(request.get("caption"), "")
        auction_id = extract_auction_id(source_text or caption_text)

    if not auction_id:
        await send_admin_log(
            bot,
            "❗️ Некорректный идентификатор лота в заявке.\n"
            "Действие: request_delete_lot через бота.",
        )
        return

    try:
        lot = await (await _repository()).get_lot(auction_id)
    except Exception:  # noqa: BLE001 - snapshot remains a valid fallback
        logging.exception("Failed to load lot %s for deletion audit", auction_id)
        lot = None
    source: Mapping[str, Any] = lot if lot else snapshot
    await send_admin_log(
        bot,
        format_delete_request_log(
            auction_id=auction_id,
            source=source,
            reason=request.get("reason"),
            lot_found=bool(lot),
        ),
    )


# Backward-compatible private aliases used by older admin modules.
_short_media_id = short_media_id
_log_lot_field_changes = send_lot_edit_log

__all__ = [
    "log_delete_request",
    "send_admin_log",
    "send_lot_edit_log",
    "send_message_safe",
    "short_media_id",
]
