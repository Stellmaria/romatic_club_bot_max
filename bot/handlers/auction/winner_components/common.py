from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from typing import Any

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.time import to_moscow, utc_now
from bot.services.auction_winners import AuctionWinnerService
from config import (
    ADMIN_LOG_CHATS,
    AUCTION_CHANNEL_ID,
    AUCTION_CHANNEL_USERNAME,
    DISCUSSION_CHAT_ID,
    LOG_CHAT_ID,
)

logger = logging.getLogger("auction.winner")

TG_MAX = 3900
USERBOT_BID_MODERATION = True
AUCTION_SUPPORT_CONTACT = "@Dear_Davidik"
AUCTION_SUPPORT_CONTACT_2 = "@Dummo_loh"
AUCTION_PROBLEMS_CONTACT = "@Dear_Davidik"

CB_WIN_SEND = "win:send"
CB_WIN_SKIP = "win:skip"
CB_WIN_EDIT_AMT = "win:edit_amt"
CB_WIN_EDIT_USER = "win:edit_user"
CB_WIN_SEND_OWNER = "win:send_owner"
CB_WIN_SEND_WINNER = "win:send_winner"
CB_WIN_REFRESH = "win:refresh"
CB_WIN_MANUAL = "win:manual"
CB_WIN_SEND_BOTH = "win:send_both"
CB_WIN_EDIT_MANUAL_WINNER = "win:edit_manual_winner"
CB_WIN_EDIT_MANUAL_OWNER = "win:edit_manual_owner"
CB_WIN_EDIT_MANUAL_AMOUNT = "win:edit_manual_amount"
CB_WIN_CLEAR_MANUAL = "win:clear_manual"
CB_WIN_THANKS = "win:thanks"

WIN_REVIEW_THRESHOLD_DIAMONDS = 1000
WIN_REVIEW_THRESHOLD_CUPS = 100

WIN_DRAFTS: dict[int, dict[str, Any]] = {}
PENDING_EDIT: dict[int, dict[str, Any]] = {}
PENDING_WIN_FIELD_EDIT: dict[int, dict[str, Any]] = {}
PENDING_WIN_MANUAL: dict[int, dict[str, Any]] = {}

RULES_COMMENT = (
    "<b>📌 Правила ставок</b>\n"
    "• Ставки только цифрами, только в комментариях к этому посту.\n"
    "• Валюта как в шапке лота:\n"
    "  – 💎/🪙: кратно 10\n"
    "  – 🍵: только чётные\n"
    "• Редактирование ставки запрещено. Новая ставка = новый комментарий.\n"
    "• Флуд и оффтоп удаляются. 3 предупреждения = мут.\n"
    "• Оплата ставки — в течение месяца (если не указано иное).\n"
    "• Отказ от ставки — бан.\n"
    "• Спам и сторонние ссылки запрещены.\n\n"
    "<b>🛡️ Бот автоматически:</b>\n"
    "• ❌ удаляет невалидные ставки и выдаёт мут на 1 минуту\n"
    "• ⚠️ выдаёт предупреждение за удаление или редактирование своей ставки\n"
    "• 🧹 удаляет сообщения, не относящиеся к ставкам (флуд)\n\n"
    "За нарушение — предупреждение. 4 преда = <b>бан</b> 🚫\n"
    "Подробнее: https://teletype.in/@velassya/karty_kr_pravila"
)


async def safe_pin_pm_message(bot: Bot, user_id: int, message_id: int) -> bool:
    try:
        await bot.pin_chat_message(
            chat_id=int(user_id),
            message_id=int(message_id),
            disable_notification=True,
        )
        return True
    except Exception:
        return False


async def send_media_any(
    bot: Bot,
    chat_id: int,
    file_id: str,
    caption: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    file_id = (file_id or "").strip()
    if not file_id:
        raise ValueError("empty file_id")
    try:
        return await bot.send_photo(
            chat_id,
            photo=file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as error:
        reason = str(error)
        if "Video as Photo" in reason or "type Video as Photo" in reason:
            return await bot.send_video(
                chat_id,
                video=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
                supports_streaming=True,
            )
        if "Animation as Photo" in reason or "type Animation as Photo" in reason:
            return await bot.send_animation(
                chat_id,
                animation=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        if "Document as Photo" in reason or "type Document as Photo" in reason:
            return await bot.send_document(
                chat_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        raise


def emoji_by_currency(currency: str | None) -> str:
    normalized = (currency or "").strip().lower()
    if normalized in {"чашки", "чай", "tea", "cups"}:
        return "🍵"
    if normalized in {"сокровища", "treasure", "treasures"}:
        return "🪙"
    return "💎"


def mention(user_id: int, username: str | None) -> str:
    return f"@{username}" if username else f'<a href="tg://user?id={int(user_id)}">id{int(user_id)}</a>'


def norm_username(username: str | None) -> str | None:
    value = (username or "").strip()
    if not value:
        return None
    return value[1:] if value.startswith("@") else value


def user_links_html(user_id: int, username: str | None) -> str:
    uid = int(user_id)
    uname = norm_username(username)
    parts: list[str] = []
    if uname:
        safe = html.escape(uname)
        parts.append(f'<a href="https://t.me/{safe}">https://t.me/{safe}</a>')
    parts.append(f'<a href="tg://user?id={uid}">tg://user?id={uid}</a>')
    parts.append(f'<a href="tg://openmessage?user_id={uid}">tg://openmessage?user_id={uid}</a>')
    return " | ".join(parts)


def build_channel_link(message_id: int | None) -> str | None:
    if not message_id:
        return None
    if AUCTION_CHANNEL_USERNAME:
        return f"https://t.me/{AUCTION_CHANNEL_USERNAME.lstrip('@')}/{int(message_id)}"
    if AUCTION_CHANNEL_ID and str(AUCTION_CHANNEL_ID).startswith("-100"):
        return f"https://t.me/c/{str(AUCTION_CHANNEL_ID)[4:]}/{int(message_id)}"
    return None


def mention_soft(user_id: int | None, username: str | None) -> str:
    if username:
        return f"@{username}"
    if user_id:
        uid = int(user_id)
        return f'<a href="tg://user?id={uid}">id{uid}</a> (<a href="tg://openmessage?user_id={uid}">tg</a>)'
    return "—"


def mention_html(user_id: int, username: str | None) -> str:
    return f"@{username}" if username else f'<a href="tg://user?id={int(user_id)}">id{int(user_id)}</a>'


def admin_tag(user: types.User) -> str:
    return f"@{user.username}" if user.username else f"id{user.id}"


def winner_threshold(currency: str | None) -> int:
    normalized = (currency or "").lower()
    if normalized in {"алмазы", "diamond", "diamonds"}:
        return WIN_REVIEW_THRESHOLD_DIAMONDS
    if normalized in {"чашки", "tea", "cups"}:
        return WIN_REVIEW_THRESHOLD_CUPS
    return 0


def parse_amount_text(raw: str) -> int | None:
    text = (raw or "").strip().replace(" ", "").lower().replace("к", "k")
    if not text:
        return None
    if text.endswith("k"):
        base = text[:-1]
        return int(base) * 1000 if base.isdigit() else None
    return int(text) if text.isdigit() else None


def cb_last_int(data: str) -> int:
    return int(data.rsplit(":", 1)[1])


def msk_now() -> datetime:
    return to_moscow(utc_now())


def fmt_msk(value: datetime) -> str:
    return to_moscow(value).strftime("%d.%m %H:%M")


def iter_admin_log_chats() -> list[int]:
    values: list[int] = []
    for source in (ADMIN_LOG_CHATS, LOG_CHAT_ID):
        if isinstance(source, int):
            values.append(source)
            continue
        try:
            values.extend(item for item in source if isinstance(item, int))
        except Exception:
            continue
    return list(dict.fromkeys(values))


async def log_admin(bot: Bot, text: str) -> None:
    for chat_id in iter_admin_log_chats():
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            continue


def kb_winner_actions(auction_id: int, winner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{auction_id}:{winner_id}")],
        [
            InlineKeyboardButton(text="✎ Исправить стоимость", callback_data=f"{CB_WIN_EDIT_AMT}:{auction_id}:{winner_id}"),
            InlineKeyboardButton(text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{auction_id}:{winner_id}"),
        ],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{winner_id}")],
    ])


async def post_rules_under_lot(
    bot: Bot,
    auction_id: int,
    retries: int = 5,
    delay: float = 1.5,
) -> None:
    if USERBOT_BID_MODERATION:
        return

    service = await AuctionWinnerService.create()
    discussion_message_id = await service.discussion_message_id(auction_id)
    for _ in range(max(0, int(retries))):
        if discussion_message_id:
            break
        await asyncio.sleep(delay)
        discussion_message_id = await service.discussion_message_id(auction_id)

    if not discussion_message_id:
        await log_admin(
            bot,
            f"⚠️ Не удалось разместить правила под лотом <code>{auction_id}</code>: нет discussion_message_id.",
        )
        return

    try:
        await bot.send_message(
            DISCUSSION_CHAT_ID,
            RULES_COMMENT,
            parse_mode="HTML",
            reply_to_message_id=discussion_message_id,
        )
        await log_admin(bot, f"📌 Правила размещены под лотом <code>{auction_id}</code>.")
    except Exception as error:
        await log_admin(
            bot,
            f"⚠️ Ошибка при размещении правил по лоту <code>{auction_id}</code>: {re.escape(str(error))}",
        )
