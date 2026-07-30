from __future__ import annotations

import contextlib
import logging
from datetime import timedelta
from typing import Optional

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from bot.core.time import to_moscow, utc_now
from bot.domain.auctions import InvalidAuctionTransition
from bot.handlers.admin.helper.new.admin_actions import send_admin_log
from bot.handlers.admin.helper.new.formatting import format_admin_action_log
from bot.services.auction_admin import AuctionAdminService
from bot.services.auction_workflows import AuctionLifecycleService
from config import ADMINS
from db.db import (
    fetchrow,
    get_auction_by_discussion_id,
    get_lot_by_id,
    get_lot_owners,
    get_lots_by_owner,
    get_user,
    get_user_by_username,
    log_audit_action,
)

router = Router(name="auction-admin-lifecycle")
logger = logging.getLogger("auction.admin_lifecycle")
TG_MAX = 3900


async def _resolve_lot_from_reply(
    message: Message,
    max_depth: int = 7,
) -> Optional[dict]:
    """Resolve a lot from a post or any bid in its reply chain."""

    current = message.reply_to_message
    for _ in range(max_depth):
        if not current:
            break
        lot = await get_auction_by_discussion_id(current.message_id)
        if lot:
            return lot

        bid = await fetchrow(
            "SELECT auction_id FROM public.bids WHERE discussion_message_id = $1",
            current.message_id,
        )
        if bid and bid.get("auction_id"):
            lot = await get_lot_by_id(int(bid["auction_id"]))
            if lot:
                return lot
        current = current.reply_to_message
    return None


async def _answer_html_chunks(
    message: Message,
    lines: list[str],
    max_len: int = TG_MAX,
) -> None:
    buffer: list[str] = []
    size = 0
    for raw_line in lines:
        line = raw_line.rstrip()
        added = len(line) + (1 if buffer else 0)
        if buffer and size + added > max_len:
            await message.answer("\n".join(buffer), parse_mode="HTML")
            buffer, size = [line], len(line)
        else:
            if buffer:
                size += 1
            buffer.append(line)
            size += len(line)
    if buffer:
        await message.answer("\n".join(buffer), parse_mode="HTML")


@router.message(F.text.startswith("/lot_owner"), F.chat.type == "private")
async def show_lot_owners(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /lot_owner <auction_id>")
        return

    lot_id = int(parts[1])
    owners = await get_lot_owners(lot_id)
    if not owners:
        await message.answer(f"Владельцы лота {lot_id} не найдены.")
        return

    lines = []
    for owner in owners:
        user = await get_user(owner["user_id"])
        username = f"@{user['username']}" if user and user.get("username") else "-"
        user_id = user["user_id"] if user else owner["user_id"]
        lines.append(f"id: <code>{user_id}</code> | username: {username}")
    await message.answer(
        f"Владельцы лота <b>{lot_id}</b>:\n" + "\n".join(lines),
        parse_mode="HTML",
    )


@router.message(F.text.startswith("/activate_lot"), F.chat.type == "private")
async def activate_lot_cmd(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /activate_lot <auction_id>")
        return

    auction_id = int(parts[1])
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await message.answer("Лот не найден.")
        return
    try:
        lifecycle = await AuctionLifecycleService.create()
        await lifecycle.requeue_publication(auction_id)
    except InvalidAuctionTransition as exc:
        await message.answer(f"Лот нельзя вернуть в публикацию из статуса {exc.current}.")
        return

    owner_users = []
    for owner in await get_lot_owners(auction_id):
        user = await get_user(owner["user_id"])
        if user:
            owner_users.append(dict(user))
    owners_text = ", ".join(
        ("👑 " if user.get("is_luxury") else "")
        + (f"@{user['username']}" if user.get("username") else f"id:{user['user_id']}")
        for user in owner_users
    ) or "-"

    await message.answer(
        f"✅ Лот <b>{lot.get('card_name')}</b> (ID {auction_id}) возвращён в очередь публикации.",
        parse_mode="HTML",
    )
    await send_admin_log(
        message.bot,
        format_admin_action_log(
            action="force_activate_lot",
            admin={
                "id": message.from_user.id,
                "username": message.from_user.username or message.from_user.full_name,
            },
            lot=lot,
            owners_text=owners_text,
        ),
    )
    await log_audit_action(
        user_id=message.from_user.id,
        action_type="force_activate_lot",
        auction_id=auction_id,
        details="Лот вручную возвращён в очередь публикации",
    )


@router.message(F.text.startswith("/user_lots"), F.chat.type == "private")
async def show_user_lots(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.answer("Формат: /user_lots <user_id или @username>")
        return

    who = parts[1]
    user = await get_user(int(who)) if who.isdigit() else await get_user_by_username(who.lstrip("@"))
    if not user:
        await message.answer("Пользователь не найден.")
        return
    lots = await get_lots_by_owner(user["user_id"])
    if not lots:
        await message.answer("У пользователя нет лотов.")
        return

    lines = [f"Лоты пользователя <b>{user.get('username') or user['user_id']}</b>:"]
    for lot in lots:
        start = to_moscow(lot["start_time"]).strftime("%d.%m %H:%M")
        lines.append(
            f"— <b>{lot.get('card_name', '-')}</b> "
            f"(ID: <code>{lot['auction_id']}</code>, "
            f"Дата: <code>{start}</code>, "
            f"Статус: <i>{lot.get('status', '-')}</i>)"
        )
    await _answer_html_chunks(message, lines)


@router.message(F.text.lower().startswith("макс удалить"))
async def admin_delete_bid(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if not message.reply_to_message:
        await message.answer("Используй команду reply на сообщение-ставку.")
        return

    replied_id = message.reply_to_message.message_id
    service = await AuctionAdminService.create()
    bid = await service.delete_bid_with_warning(
        discussion_message_id=replied_id,
    )
    if not bid:
        await message.answer("Это не ставка или ставка не найдена.")
        return
    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await message.bot.delete_message(message.chat.id, replied_id)

    bidder = f"@{bid['username']}" if bid.get("username") else f"id{bid['bidder_id']}"
    await message.answer(
        "❌ <b>Ставка удалена админом</b>\n"
        f"{bidder}, ваша ставка удалена как ошибочная.\n"
        f"Предупреждений: {bid['warnings_count']}/4\n"
        + ("🚫 Пользователь забанен!" if bid["is_banned"] else ""),
        parse_mode="HTML",
    )


@router.message(F.text.lower().startswith("макс старт"))
async def admin_start_auction(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if not message.reply_to_message:
        await message.answer("Используйте команду reply к сообщению лота или любой ставке.")
        return
    lot = await _resolve_lot_from_reply(message)
    if not lot:
        await message.answer("Не удалось найти аукцион по reply.")
        return

    new_end_time = to_moscow(utc_now() + timedelta(minutes=30))
    try:
        lifecycle = await AuctionLifecycleService.create()
        await lifecycle.restart(int(lot["auction_id"]), end_time=new_end_time)
    except InvalidAuctionTransition as exc:
        await message.answer(f"Аукцион нельзя перезапустить из статуса {exc.current}.")
        return

    auction_msg_id = lot.get("discussion_message_id") or lot.get("message_id")
    text = (
        "⏳ <b>Аукцион возобновлён администратором!</b>\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Продлён до: <b>{new_end_time:%d.%m %H:%M}</b>\n"
        "Ставки принимаются снова!"
    )
    try:
        await message.bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            reply_to_message_id=auction_msg_id,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Could not announce auction restart: %r", exc)
    for owner in await get_lot_owners(lot["auction_id"]):
        try:
            await message.bot.send_message(
                owner["user_id"],
                f"⏳ Ваш аукцион <b>{lot['card_name']}</b> был принудительно запущен/продлён админом!\n"
                f"Новая дата окончания: <b>{new_end_time:%d.%m %H:%M}</b>",
                parse_mode="HTML",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Could not notify owner %s: %r", owner.get("user_id"), exc)
    await _send_lifecycle_animation(message, auction_msg_id)
    await message.answer(
        f"✅ Аукцион <b>{lot['card_name']}</b> запущен заново до <b>{new_end_time:%d.%m %H:%M}</b>.",
        parse_mode="HTML",
    )


@router.message(F.text.lower().startswith("макс стоп"))
async def admin_stop_auction(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if not message.reply_to_message:
        await message.answer("Используйте команду reply к сообщению лота или любой ставке.")
        return
    lot = await _resolve_lot_from_reply(message)
    if not lot:
        await message.answer("Не удалось найти аукцион по reply.")
        return

    stop_time = to_moscow(utc_now())
    try:
        lifecycle = await AuctionLifecycleService.create()
        await lifecycle.finish_now(int(lot["auction_id"]), end_time=stop_time)
    except InvalidAuctionTransition as exc:
        await message.answer(f"Аукцион нельзя остановить из статуса {exc.current}.")
        return

    auction_msg_id = lot.get("discussion_message_id") or lot.get("message_id")
    text = (
        "⏹ <b>Аукцион остановлен администратором!</b>\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Завершён в: <b>{stop_time:%d.%m %H:%M}</b>\n"
        "Ставки больше не принимаются!"
    )
    try:
        await message.bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            reply_to_message_id=auction_msg_id,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning("Could not announce auction stop: %r", exc)
    for owner in await get_lot_owners(lot["auction_id"]):
        try:
            await message.bot.send_message(
                owner["user_id"],
                f"⏹ Ваш аукцион <b>{lot['card_name']}</b> был досрочно остановлен админом!\n"
                f"Дата завершения: <b>{stop_time:%d.%m %H:%M}</b>",
                parse_mode="HTML",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Could not notify owner %s: %r", owner.get("user_id"), exc)
    await _send_lifecycle_animation(message, auction_msg_id)
    await message.answer(
        f"✅ Аукцион <b>{lot['card_name']}</b> досрочно завершён.",
        parse_mode="HTML",
    )


async def _send_lifecycle_animation(message: Message, reply_to_message_id: int | None) -> None:
    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await message.bot.send_animation(
            chat_id=message.chat.id,
            animation="https://media.stickerswiki.app/lovestory/590143.512.webp",
            reply_to_message_id=reply_to_message_id,
        )
