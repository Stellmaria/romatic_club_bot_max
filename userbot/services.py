"""Application services shared by the Telethon event handlers.

This layer coordinates Telegram operations, domain services and the userbot
repository.  It contains no SQL and never acquires a database connection
directly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon.tl.types import ChannelParticipantsAdmins

from bot.core.legacy_config import legacy_config
from bot.core.time import ensure_utc, utc_now
from bot.domain.auctions import BidFormatError, auction_bidding_closes_at
from bot.domain.auctions.rules import parse_bid_amount
from bot.services.auction_workflows import AuctionLifecycleService
from db.auctions import get_autobid_action_by_msg_id
from userbot.autobid_engine import get_local_autobid_action
from userbot.presentation import RULES_TEXT, mention, random_warning, user_link
from userbot.repositories import UserbotRepository
from userbot.runtime import (
    BOT_DELETED,
    BOT_DELETED_TTL,
    CHAT_ADMINS_CACHE,
    CHAT_ADMINS_TTL,
    require_client,
)


AUTO_DELETE_BOT_NOTICE_SEC = 0


async def _repository() -> UserbotRepository:
    return await UserbotRepository.create()


def _user_link(user_id: int, username: str | None = None) -> str:
    return user_link(user_id, username)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_thread_root_msg_id(message: Any) -> int | None:
    """Return the root post id for either a direct or nested discussion reply."""

    reply = getattr(message, "reply_to", None)
    if not reply:
        return None
    top_id = getattr(reply, "reply_to_top_id", None)
    if top_id:
        return int(top_id)
    message_id = getattr(reply, "reply_to_msg_id", None)
    return int(message_id) if message_id else None


async def reply_not_counted(event: Any, text: str) -> None:
    message = await event.reply(text)
    if AUTO_DELETE_BOT_NOTICE_SEC > 0:
        await asyncio.sleep(AUTO_DELETE_BOT_NOTICE_SEC)
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            pass


def _mention(username: str | None, user_id: int) -> str:
    return mention(username, user_id)


def _try_parse_bid_amount(text: str) -> int | None:
    try:
        return parse_bid_amount(text)
    except BidFormatError:
        return None


async def _delete_later(message_id: int, delay_sec: int = 25) -> None:
    await asyncio.sleep(int(delay_sec))
    BOT_DELETED[int(message_id)] = _now_ts() + BOT_DELETED_TTL
    try:
        await require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [int(message_id)])
    except Exception:  # noqa: BLE001
        pass


async def _send_reply_or_plain(
    text: str,
    *,
    reply_to: int | None = None,
    ttl: int | None = None,
) -> None:
    """Send an HTML reply, falling back to a plain discussion message."""

    try:
        message = await require_client().send_message(
            legacy_config.DISCUSSION_CHAT_ID,
            text,
            reply_to=reply_to,
            parse_mode="html",
            link_preview=False,
        )
    except Exception:  # noqa: BLE001
        message = await require_client().send_message(
            legacy_config.DISCUSSION_CHAT_ID,
            text,
            parse_mode="html",
            link_preview=False,
        )

    if ttl is not None and ttl > 0:
        asyncio.create_task(_delete_later(int(message.id), int(ttl)))


def _now_ts() -> float:
    return time.time()


def _is_recent_bot_delete(message_id: int) -> bool:
    expires_at = BOT_DELETED.get(int(message_id))
    if not expires_at:
        return False
    if expires_at < _now_ts():
        BOT_DELETED.pop(int(message_id), None)
        return False
    return True


def _random_warn(username: str | None, user_id: int, warnings: int) -> str:
    return random_warning(username, user_id, warnings)


async def _get_chat_admin_ids(chat_id: int) -> set[int]:
    now = _now_ts()
    cached = CHAT_ADMINS_CACHE.get(int(chat_id))
    if cached and cached[1] > now:
        return cached[0]

    admin_ids = set(legacy_config.ADMINS)
    try:
        admins = await require_client().get_participants(
            chat_id,
            filter=ChannelParticipantsAdmins,
        )
        for admin in admins:
            user_id = getattr(admin, "id", None)
            if user_id:
                admin_ids.add(int(user_id))
    except Exception:  # noqa: BLE001
        pass

    CHAT_ADMINS_CACHE[int(chat_id)] = (admin_ids, now + CHAT_ADMINS_TTL)
    return admin_ids


async def _is_chat_admin(chat_id: int, user_id: int) -> bool:
    return int(user_id) in await _get_chat_admin_ids(chat_id)


async def _mute_1m(chat_id: int, user_id: int) -> None:
    try:
        await require_client().edit_permissions(
            int(chat_id),
            int(user_id),
            send_messages=False,
            until_date=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    except Exception:  # noqa: BLE001
        pass


def _get_root_id(message: Any) -> int | None:
    root = getattr(message, "message_thread_id", None)
    if root:
        return int(root)
    reply = getattr(message, "reply_to", None)
    if reply:
        top_id = getattr(reply, "reply_to_top_id", None)
        message_id = getattr(reply, "reply_to_msg_id", None)
        return int(top_id or message_id) if (top_id or message_id) else None
    return None


def _is_direct_reply_to_root(message: Any, root_id: int) -> bool:
    reply_to = getattr(message, "reply_to_msg_id", None)
    return reply_to is None or int(reply_to) == int(root_id)


async def _fetch_auction_by_root(root_id: int) -> dict | None:
    return await (await _repository()).fetch_auction_by_root(int(root_id))


def _to_utc(value: datetime) -> datetime:
    return ensure_utc(value)


async def _is_auction_active(auction: dict) -> bool:
    start_time = auction.get("start_time")
    end_time = auction.get("end_time")
    if not start_time or not end_time:
        return False
    return (
        ensure_utc(start_time) <= utc_now()
        < auction_bidding_closes_at(ensure_utc(end_time))
    )


async def _fetch_best_bid(auction_id: int, *, lowest_wins: bool) -> int | None:
    return await (await _repository()).fetch_best_bid(
        int(auction_id),
        lowest_wins=lowest_wins,
    )


async def _fetch_best_bid_units(auction_id: int) -> int | None:
    return await (await _repository()).fetch_best_bid_units(int(auction_id))


async def _fetch_max_bid(auction_id: int) -> int | None:
    return await _fetch_best_bid(int(auction_id), lowest_wins=False)


async def _get_bid_by_msg_id(message_id: int) -> dict | None:
    return await (await _repository()).get_bid_by_message_id(int(message_id))


async def _update_bid_amount(bid_id: int, new_amount: int) -> None:
    await (await _repository()).update_bid_amount(int(bid_id), int(new_amount))


def _seconds_since(value: datetime) -> float:
    if not value:
        return 10**9
    return (utc_now() - ensure_utc(value)).total_seconds()


async def _delete_bid_by_id(bid_id: int) -> None:
    await (await _repository()).delete_bid(int(bid_id))


async def _warnings_count(user_id: int) -> int:
    return await (await _repository()).warnings_count(int(user_id))


async def _add_warning(user_id: int, reason: str, details: str = "") -> int:
    return await (await _repository()).add_warning(int(user_id), reason, details)


async def _ban_user(user_id: int, reason: str) -> None:
    await (await _repository()).ban_user(
        int(user_id),
        datetime.now() + timedelta(days=3650),
        reason,
    )
    try:
        await require_client().edit_permissions(
            legacy_config.DISCUSSION_CHAT_ID,
            int(user_id),
            send_messages=False,
            until_date=_utcnow() + timedelta(days=3650),
        )
    except Exception:  # noqa: BLE001
        pass


async def _auction_thread_root(auction_id: int) -> int | None:
    return await (await _repository()).auction_thread_root(int(auction_id))


async def _maybe_punish(
    user_id: int,
    username: str | None,
    warnings: int,
    root_id: int,
) -> None:
    if warnings >= 4:
        await _ban_user(int(user_id), "4 warnings")
        await _send_reply_or_plain(
            f"🚫 {_mention(username, user_id)} получил(а) <b>бан</b> за 4 предупреждения.",
            reply_to=root_id,
            ttl=35,
        )
        return

    if warnings == 3:
        try:
            await require_client().edit_permissions(
                legacy_config.DISCUSSION_CHAT_ID,
                int(user_id),
                send_messages=False,
                until_date=_utcnow() + timedelta(minutes=10),
            )
        except Exception:  # noqa: BLE001
            pass
        await _send_reply_or_plain(
            f"⏳ {_mention(username, user_id)}: 3 предупреждения → мут на 10 минут.",
            reply_to=root_id,
            ttl=35,
        )


async def _post_rules_under_lot(root_id: int) -> None:
    try:
        await require_client().send_message(
            entity=legacy_config.DISCUSSION_CHAT_ID,
            message=RULES_TEXT,
            reply_to=int(root_id),
            parse_mode="html",
            link_preview=False,
        )
    except Exception:  # noqa: BLE001
        try:
            await require_client().send_message(
                entity=legacy_config.DISCUSSION_CHAT_ID,
                message=RULES_TEXT,
                parse_mode="html",
                link_preview=False,
            )
        except Exception:  # noqa: BLE001
            pass


async def _fetch_auction_meta(auction_id: int) -> dict | None:
    return await (await _repository()).fetch_auction_meta(int(auction_id))


def _is_auction_closed_row(auction: dict | None) -> bool:
    if not auction:
        return False
    status = str(auction.get("status") or "").strip().lower()
    if status in {"finished", "closed", "completed", "ended", "cancelled", "canceled"}:
        return True
    end_time = auction.get("end_time")
    if not end_time:
        return False
    try:
        return utc_now() >= auction_bidding_closes_at(ensure_utc(end_time))
    except Exception:  # noqa: BLE001
        return False


def _bid_change_root_id(previous: dict | None, message: Any, auction: dict | None) -> int:
    if previous and previous.get("root_id"):
        return int(previous["root_id"])
    if auction and auction.get("discussion_message_id"):
        return int(auction["discussion_message_id"])
    return int(_get_root_id(message) or getattr(message, "reply_to_msg_id", None) or message.id)


def _looks_like_auction_post(text_low: str) -> bool:
    if not text_low or "лот" not in text_low or "цена" not in text_low:
        return False
    return "принимаются ставки" in text_low or "ставки" in text_low


def _extract_lot_id(text: str) -> int | None:
    if not text:
        return None
    import re

    match = re.search(r"(?i)\bлот\s*№\s*(\d{1,10})\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _norm_channel_id(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        rendered = str(number)
        if rendered.startswith("-100") and rendered[4:].isdigit():
            return int(rendered[4:])
        return abs(number)
    return number


async def _try_bind_root_message(message: Any) -> int | None:
    lifecycle = await AuctionLifecycleService.create()
    forwarded = getattr(message, "fwd_from", None)
    channel_post = getattr(forwarded, "channel_post", None) if forwarded else None

    if channel_post:
        source_id = getattr(getattr(forwarded, "from_id", None), "channel_id", None)
        if legacy_config.AUCTION_CHANNEL_ID and source_id:
            if _norm_channel_id(source_id) != _norm_channel_id(legacy_config.AUCTION_CHANNEL_ID):
                channel_post = None

    if channel_post:
        try:
            auction_id = await lifecycle.bind_by_channel_message(
                channel_message_id=int(channel_post),
                discussion_message_id=int(message.id),
            )
            if auction_id:
                return int(auction_id)
        except Exception:  # noqa: BLE001
            pass

    text = (getattr(message, "message", None) or "").strip()
    if not _looks_like_auction_post(text.lower()):
        return None
    lot_id = _extract_lot_id(text)
    if not lot_id:
        return None
    try:
        auction_id = await lifecycle.bind_by_auction(
            auction_id=int(lot_id),
            discussion_message_id=int(message.id),
        )
        return int(auction_id) if auction_id else None
    except Exception:  # noqa: BLE001
        return None


async def _resolve_autobid_mapping(
    message_id: int,
    *,
    wait_for_race: bool = False,
    attempts: int = 8,
    delay: float = 0.15,
) -> dict | None:
    mapped = await get_autobid_action_by_msg_id(int(message_id))
    if not mapped:
        mapped = get_local_autobid_action(int(message_id))
    if mapped or not wait_for_race:
        return mapped

    for _ in range(max(1, int(attempts))):
        await asyncio.sleep(float(delay))
        mapped = await get_autobid_action_by_msg_id(int(message_id))
        if not mapped:
            mapped = get_local_autobid_action(int(message_id))
        if mapped:
            return mapped
    return None


async def _remove_last_warnings(user_id: int, count: int = 1) -> int:
    return await (await _repository()).remove_last_warnings(int(user_id), max(1, int(count)))


async def _prune_missing_bid_messages(auction_id: int) -> int:
    repository = await _repository()
    rows = await repository.list_bid_messages(int(auction_id))
    removed = 0
    for row in rows:
        message_id = row["discussion_message_id"]
        if not message_id:
            continue
        try:
            message = await require_client().get_messages(
                legacy_config.DISCUSSION_CHAT_ID,
                ids=int(message_id),
            )
        except Exception:  # noqa: BLE001
            message = None
        if not message:
            try:
                await repository.delete_bid(int(row["bid_id"]))
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    return removed


__all__ = [
    "AUTO_DELETE_BOT_NOTICE_SEC",
    "get_thread_root_msg_id",
    "reply_not_counted",
]
