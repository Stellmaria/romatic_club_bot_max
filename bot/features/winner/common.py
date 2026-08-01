"""Shared constants, repository gateways, and Telegram-safe utilities."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.legacy_config import legacy_config
from bot.core.time import to_moscow, utc_now
from bot.domain.auctions import AuctionKind
from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

logger = logging.getLogger("auction.winner")


TG_MAX = 3900


USERBOT_BID_MODERATION = True


AUCTION_SUPPORT_CONTACT = "@Dear_Davidik"


AUCTION_SUPPORT_CONTACT_2 = "@Dummo_loh"


AUCTION_PROBLEMS_CONTACT = "@Dear_Davidik"


_MSK = ZoneInfo("Europe/Moscow")


async def get_user(user_id: int) -> dict | None:
    return await winner_repository.get_user(await get_db_pool(), user_id)


async def get_user_by_username(username: str) -> dict | None:
    return await winner_repository.get_user_by_username(
        await get_db_pool(),
        username,
    )


async def _is_user_uid_verified(user_id: int | None) -> bool:
    return await winner_repository.is_user_uid_verified(
        await get_db_pool(),
        user_id,
    )


async def _users_uid_verification_counts(
    user_ids: list[int] | None,
) -> tuple[int, int, bool]:
    return await winner_repository.users_uid_verification_counts(
        await get_db_pool(),
        user_ids,
    )


async def get_autobid_action_by_msg_id(message_id: int) -> dict | None:
    return await winner_repository.get_autobid_action_by_message_id(
        await get_db_pool(),
        message_id,
    )


async def get_exchange_batches_for_card(
    card_id: int,
    *,
    status: str = "approved",
) -> list[dict]:
    return await winner_repository.get_exchange_batches_for_card(
        await get_db_pool(),
        card_id,
        status=status,
    )


async def get_exchange_batch_by_id(batch_id: int) -> dict | None:
    return await winner_repository.get_exchange_batch(await get_db_pool(), batch_id)


async def get_exchange_cards_for_batch(batch_id: int) -> list[dict]:
    return await winner_repository.get_exchange_cards(await get_db_pool(), batch_id)


async def get_exchange_print_stats(batch_id: int) -> dict | None:
    return await winner_repository.get_exchange_print_stats(
        await get_db_pool(),
        batch_id,
    )


async def reset_exchange_print_stats(
    batch_id: int,
    *,
    updated_by: int | None = None,
) -> None:
    await winner_repository.reset_exchange_print_stats(
        await get_db_pool(),
        batch_id,
        updated_by=updated_by,
    )


async def upsert_exchange_print_stats(batch_id: int, **changes) -> None:
    await winner_repository.upsert_exchange_print_stats(
        await get_db_pool(),
        batch_id,
        **changes,
    )


async def get_print_win_missed_for_day(target_date: date) -> list[dict]:
    return await winner_repository.get_print_win_missed_for_day(
        await get_db_pool(),
        target_date,
    )


async def _safe_pin_pm_message(bot: Bot, user_id: int, message_id: int) -> bool:
    try:
        await bot.pin_chat_message(
            chat_id=user_id,
            message_id=message_id,
            disable_notification=True,
        )
        return True
    except Exception:
        return False


async def _send_media_any(
    bot: Bot,
    chat_id: int,
    file_id: str,
    caption: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """
    Пытается отправить file_id как фото.
    Если Telegram ругается, что это видео/анимация/документ — пробуем соответствующий метод.
    """
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
    except TelegramBadRequest as e:
        msg = str(e)
        if "Video as Photo" in msg or "type Video as Photo" in msg:
            return await bot.send_video(
                chat_id,
                video=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
                supports_streaming=True,
            )
        if "Animation as Photo" in msg or "type Animation as Photo" in msg:
            return await bot.send_animation(
                chat_id,
                animation=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        if "Document as Photo" in msg or "type Document as Photo" in msg:
            return await bot.send_document(
                chat_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        raise


async def _resolve_winner(text: str) -> tuple[int | None, str | None]:
    s = (text or "").strip()
    if not s:
        return None, None

    if s.isdigit():
        return int(s), None

    username = s[1:] if s.startswith("@") else s
    username = username.strip()
    if not username:
        return None, None

    u = await get_user_by_username(username)
    if u and u.get("user_id"):
        return int(u["user_id"]), username

    # пользователь не найден в БД (скорее всего не нажимал /start)
    return None, username


CB_WIN_SEND = "win:send"


CB_WIN_SKIP = "win:skip"


def _emoji_by_currency(currency: str | None) -> str:
    c = (currency or "").strip().lower()
    if c in ("чашки", "чай", "tea", "cups"):
        return "🍵"
    if c in ("сокровища", "treasure", "treasures"):
        return "🪙"
    return "💎"


def _mention(user_id: int, username: str | None) -> str:
    return f"@{username}" if username else f'<a href="tg://user?id={user_id}">id{user_id}</a>'


def _norm_username(username: str | None) -> str | None:
    u = (username or "").strip()
    if not u:
        return None
    return u[1:] if u.startswith("@") else u


def _user_links_html(user_id: int, username: str | None) -> str:
    """
    Возвращает кликабельные ссылки:
      - https://t.me/<username> (если есть username)
      - tg://user?id=<id>
      - tg://openmessage?user_id=<id>
    """
    uid = int(user_id)
    uname = _norm_username(username)

    parts: list[str] = []
    if uname:
        safe = html.escape(uname)
        parts.append(f'<a href="https://t.me/{safe}">https://t.me/{safe}</a>')

    parts.append(f'<a href="tg://user?id={uid}">tg://user?id={uid}</a>')
    parts.append(f'<a href="tg://openmessage?user_id={uid}">tg://openmessage?user_id={uid}</a>')
    return " | ".join(parts)


def _build_channel_link(message_id: int | None) -> str | None:
    if not message_id:
        return None
    if legacy_config.AUCTION_CHANNEL_USERNAME:
        return f"https://t.me/{legacy_config.AUCTION_CHANNEL_USERNAME.lstrip('@')}/{message_id}"
    if legacy_config.AUCTION_CHANNEL_ID and str(legacy_config.AUCTION_CHANNEL_ID).startswith("-100"):
        return f"https://t.me/c/{str(legacy_config.AUCTION_CHANNEL_ID)[4:]}/{message_id}"
    return None


async def _get_owners(auction_id: int) -> list[dict]:
    return await winner_repository.get_owners(await get_db_pool(), auction_id)


def _kb_winner_action(auction_id: int, winner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить уведомления",
                    callback_data=f"{CB_WIN_SEND}:{auction_id}:{winner_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{winner_id}"
                )
            ],
        ]
    )


def get_winner(bids, auction_kind: str = "standard"):
    if not bids:
        return None
    kind = AuctionKind.from_raw(auction_kind)
    if not kind.is_automatic_bidding:
        return None
    amount_sign = 1 if kind.lowest_bid_wins else -1
    sorted_bids = sorted(
        bids,
        key=lambda x: (amount_sign * int(x["amount"]), x["placed_at"]),
    )
    return sorted_bids[0]


CB_WIN_EDIT_AMT = "win:edit_amt"


CB_WIN_EDIT_USER = "win:edit_user"


WIN_DRAFTS: dict[int, dict] = {}


PENDING_EDIT: dict[int, dict] = {}


CB_WIN_SEND_OWNER = "win:send_owner"


CB_WIN_SEND_WINNER = "win:send_winner"


CB_WIN_REFRESH = "win:refresh"


CB_WIN_MANUAL = "win:manual"


CB_WIN_SEND_BOTH = "win:send_both"


CB_WIN_EDIT_MANUAL_WINNER = "win:edit_manual_winner"


CB_WIN_EDIT_MANUAL_OWNER = "win:edit_manual_owner"


CB_WIN_EDIT_MANUAL_AMOUNT = "win:edit_manual_amount"


CB_WIN_CLEAR_MANUAL = "win:clear_manual"


PENDING_WIN_FIELD_EDIT: dict[int, dict] = {}


PENDING_WIN_MANUAL: dict[int, dict] = {}


def _admin_tag(user: types.User) -> str:
    return f"@{user.username}" if user.username else f"id{user.id}"


def _msk_now() -> datetime:
    return to_moscow(utc_now())


def _fmt_msk(dt: datetime) -> str:
    return to_moscow(dt).strftime("%d.%m %H:%M")


def _kb_winner_actions(aid: int, wid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{aid}:{wid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✎ Исправить стоимость", callback_data=f"{CB_WIN_EDIT_AMT}:{aid}:{wid}"
                ),
                InlineKeyboardButton(
                    text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{aid}:{wid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{aid}:{wid}"
                )
            ],
        ]
    )


async def _log_admin(bot: Bot, text: str) -> None:
    for chat_id in _iter_admin_log_chats():
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


def _iter_admin_log_chats() -> list[int]:
    out = []
    try:
        for x in legacy_config.ADMIN_LOG_CHATS:
            if isinstance(x, int):
                out.append(x)
    except Exception:
        pass
    try:
        if isinstance(legacy_config.LOG_CHAT_ID, int):
            out.append(legacy_config.LOG_CHAT_ID)
    except Exception:
        pass
    # уникализируем
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _cb_last_int(data: str) -> int:
    # Берём число после последнего двоеточия: win:send_owner:4676 -> 4676
    return int(data.rsplit(":", 1)[1])


async def safe_edit_text(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise
