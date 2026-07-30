"""Moderation of edited and manually deleted bid messages."""

from __future__ import annotations

from telethon import events

from bot.core.settings import DISCUSSION_CHAT_ID
from userbot.runtime import ACCEPTED_BIDS
from userbot.services import (
    _add_warning,
    _auction_thread_root,
    _bid_change_root_id,
    _delete_bid_by_id,
    _fetch_auction_meta,
    _get_bid_by_msg_id,
    _is_auction_closed_row,
    _is_chat_admin,
    _is_recent_bot_delete,
    _maybe_punish,
    _mention,
    _random_warn,
    _send_reply_or_plain,
)


async def on_edited(event: events.MessageEdited.Event) -> None:
    message = event.message
    sender_id = getattr(message, "sender_id", None)
    if not sender_id or getattr(message, "out", False):
        return
    if getattr(message, "sender_chat", None) is not None:
        return

    key = (int(event.chat_id), int(message.id))
    previous = ACCEPTED_BIDS.get(key)
    new_text = (message.message or "").strip()
    bid = await _get_bid_by_msg_id(int(message.id))
    if not previous and not bid:
        return
    if previous and new_text == (previous.get("text") or ""):
        return

    is_admin = await _is_chat_admin(int(event.chat_id), int(sender_id))
    sender = await event.get_sender()
    username = getattr(sender, "username", None)

    auction_id = None
    if bid and bid.get("auction_id"):
        auction_id = int(bid["auction_id"])
    elif previous and previous.get("auction_id"):
        auction_id = int(previous["auction_id"])

    auction = await _fetch_auction_meta(auction_id) if auction_id else None
    auction_closed = _is_auction_closed_row(auction)
    root_id = _bid_change_root_id(previous, message, auction)

    if bid and not auction_closed:
        try:
            await _delete_bid_by_id(int(bid["bid_id"]))
        except Exception:  # noqa: BLE001
            pass

    ACCEPTED_BIDS.pop(key, None)

    if is_admin:
        action_text = (
            "была отредактирована после завершения аукциона, запись в БД сохранена"
            if auction_closed
            else "была отредактирована и <b>не засчитана</b>"
        )
        await _send_reply_or_plain(
            f"⚠️ {_mention(username, sender_id)}: ставка {action_text}.",
            reply_to=root_id,
        )
        return

    details = f"msg_id={message.id}"
    if bid:
        details += f" amount={bid.get('amount')} auction_id={bid.get('auction_id')}"
    if auction_closed:
        details += " closed=1"

    warnings = await _add_warning(int(sender_id), "edit_bid", details)
    if auction_closed:
        await _send_reply_or_plain(
            f"⛔ {_mention(username, sender_id)}: редактирование ставки после завершения "
            "аукциона запрещено.\n"
            "Ставка в БД сохранена, предупреждение выдано.\n"
            f"{_random_warn(username, sender_id, warnings)}",
            reply_to=root_id,
        )
    else:
        await _send_reply_or_plain(
            f"⛔ {_mention(username, sender_id)}: редактирование ставки запрещено.\n"
            f"{_random_warn(username, sender_id, warnings)}",
            reply_to=root_id,
        )
    await _maybe_punish(int(sender_id), username, warnings, int(root_id))


async def on_deleted(event: events.MessageDeleted.Event) -> None:
    for message_id in event.deleted_ids:
        if _is_recent_bot_delete(int(message_id)):
            continue

        bid = await _get_bid_by_msg_id(int(message_id))
        if not bid:
            continue

        bidder_id = int(bid["bidder_id"])
        auction_id = int(bid["auction_id"])
        auction = await _fetch_auction_meta(auction_id)
        auction_closed = _is_auction_closed_row(auction)

        if not auction_closed:
            try:
                await _delete_bid_by_id(int(bid["bid_id"]))
            except Exception:  # noqa: BLE001
                pass

        ACCEPTED_BIDS.pop((int(DISCUSSION_CHAT_ID), int(message_id)), None)
        if await _is_chat_admin(DISCUSSION_CHAT_ID, bidder_id):
            continue

        warnings = await _add_warning(
            bidder_id,
            "delete_bid",
            f"msg_id={message_id} amount={bid.get('amount')} auction_id={auction_id} "
            f"closed={int(auction_closed)}",
        )
        thread_root_id = int(
            (auction or {}).get("discussion_message_id")
            or await _auction_thread_root(auction_id)
            or int(message_id)
        )

        if auction_closed:
            await _send_reply_or_plain(
                f"⚠️ {_mention(None, bidder_id)}: предупреждение за удаление ставки после "
                "завершения аукциона.\n"
                f"Ставка в БД сохранена. (предов: {warnings}/4)",
                reply_to=thread_root_id,
            )
        else:
            await _send_reply_or_plain(
                f"⚠️ {_mention(None, bidder_id)}: предупреждение за удаление ставки.\n"
                f"(предов: {warnings}/4)",
                reply_to=thread_root_id,
            )
        await _maybe_punish(bidder_id, None, warnings, int(thread_root_id))


__all__ = ["on_deleted", "on_edited"]
