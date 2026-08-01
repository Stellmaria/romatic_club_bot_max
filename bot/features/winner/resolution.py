"""Winner resolution, auction completion announcement, and lot rules."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.legacy_config import legacy_config
from bot.domain.auctions import AuctionKind
from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

from .common import (
    CB_WIN_EDIT_AMT,
    CB_WIN_EDIT_USER,
    CB_WIN_SEND,
    CB_WIN_SKIP,
    USERBOT_BID_MODERATION,
    _build_channel_link,
    _emoji_by_currency,
    _fmt_msk,
    _get_owners,
    _is_user_uid_verified,
    _mention,
    _msk_now,
    _users_uid_verification_counts,
    get_autobid_action_by_msg_id,
    get_user,
    get_winner,
)

WIN_REVIEW_THRESHOLD_DIAMONDS = 1000


WIN_REVIEW_THRESHOLD_CUPS = 100


def _winner_threshold(currency: str | None) -> int:
    cur = (currency or "").lower()
    if cur in {"алмазы", "diamond", "diamonds"}:
        return WIN_REVIEW_THRESHOLD_DIAMONDS
    if cur in {"чашки", "cups"}:
        return WIN_REVIEW_THRESHOLD_CUPS
    return 0


async def _winner_preview_text(auction_id: int, amount: int, winner_id: int) -> str:
    a = (
        await winner_repository.get_auction_summary(
            await get_db_pool(),
            auction_id,
        )
        or {}
    )
    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (
        f" — {a.get('card_name')}" if a.get("card_name") else ""
    )

    w = await get_user(winner_id) or {}
    wname = _mention(winner_id, w.get("username"))

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    w_verified = await _is_user_uid_verified(int(winner_id))
    w_verif_line = "✅" if w_verified else "❌"

    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_user_ids)
    if s_total:
        s_verif_line = "✅" if sellers_all_verified else "❌"
    else:
        s_verif_line = "—"

    return (
        "Привет!\n\n"
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amount} {cur_emoji}\n"
        f"Победитель: {wname} ({w_verif_line})\n"
        f"Продавец вериф.: {s_verif_line}\n"
        f"Владелец карты: {owners_mentions}"
    )


async def _autobid_win_note(
    *,
    auction_id: int,
    winner_bid: dict | object,
    wid: int,
    max_amt: int,
    wname: str,
) -> str:
    """
    Если победная ставка была сделана через автоставку (платно),
    вернём строку для добавления в итог.
    """
    # 1) пытаемся взять discussion_message_id прямо из winner_bid
    win_msg_id = None
    try:
        if isinstance(winner_bid, dict):
            win_msg_id = winner_bid.get("discussion_message_id")
        else:
            win_msg_id = getattr(winner_bid, "discussion_message_id", None)
    except Exception:
        win_msg_id = None

    # 2) если его нет — найдём по БД (последняя ставка победителя на эту сумму)
    if not win_msg_id:
        win_msg_id = await winner_repository.get_bid_discussion_message_id(
            await get_db_pool(),
            auction_id,
            bidder_id=wid,
            amount=max_amt,
        )

    if not win_msg_id:
        return ""

    mapped = await get_autobid_action_by_msg_id(int(win_msg_id))
    if not mapped:
        return ""

    return f"\n🤖 <i>Платная автоставка для {wname}</i>"


async def announce_winner(telegram_bot, auction, bids, send_admin_log=None):
    """
    Готовит сводку по победителю, постит финальный комментарий под лотом
    и шлёт в админ-лог карточку с дедлайном, превью и кнопками:
      — Отправить уведомления
      — Исправить ставку
      — Исправить победителя
      — Не отправлять
    """
    auction_id = int(auction["auction_id"])
    currency = (auction.get("currency") or "").lower()
    cur_emoji = _emoji_by_currency(currency)
    kind = AuctionKind.from_raw(auction.get("auction_kind"))

    if kind is AuctionKind.FREE:
        reply_to_id = auction.get("discussion_message_id") or auction.get("message_id")
        text = (
            "⏰ <b>Свободный аукцион завершён!</b>\n"
            "🪶 <i>Итог определит модератор после ручной проверки комментариев.</i>"
        )
        try:
            await telegram_bot.send_message(
                legacy_config.DISCUSSION_CHAT_ID,
                text,
                parse_mode="HTML",
                reply_to_message_id=reply_to_id,
            )
        except Exception:
            await telegram_bot.send_message(legacy_config.DISCUSSION_CHAT_ID, text, parse_mode="HTML")
        for chat_id in legacy_config.ADMIN_LOG_CHATS:
            try:
                await telegram_bot.send_message(
                    chat_id,
                    f"🪶 Лот {auction_id}: требуется ручное определение итога свободного аукциона.",
                )
            except Exception:
                pass
        return

    winner_bid = get_winner(bids or [], kind.value)
    max_amt = None
    if winner_bid is not None:
        try:
            max_amt = int(
                winner_bid["amount"] if isinstance(winner_bid, dict) else winner_bid.amount
            )
        except Exception:
            winner_bid = None

    reply_to_id = auction.get("discussion_message_id") or auction.get("message_id")
    if not winner_bid:
        txt = "⏰ <b>Аукцион завершён!</b>\n❌ <i>Победителей нет, ставок не было.</i>"
        try:
            await telegram_bot.send_message(
                legacy_config.DISCUSSION_CHAT_ID, txt, parse_mode="HTML", reply_to_message_id=reply_to_id
            )
        except Exception:
            await telegram_bot.send_message(legacy_config.DISCUSSION_CHAT_ID, txt, parse_mode="HTML")
        for chat_id in legacy_config.ADMIN_LOG_CHATS:
            try:
                await telegram_bot.send_message(
                    chat_id, f"🏁 Лот {auction_id}: ставок не было.", parse_mode="HTML"
                )
            except Exception:
                pass
        return

    win_msg_id = None
    try:
        if isinstance(winner_bid, dict):
            win_msg_id = winner_bid.get("discussion_message_id")
        else:
            win_msg_id = getattr(winner_bid, "discussion_message_id", None)
    except Exception:
        win_msg_id = None

    winner_bidder_id = None
    if not win_msg_id:
        top = await winner_repository.get_top_bid(
            await get_db_pool(),
            auction_id,
            lowest_wins=kind.lowest_bid_wins,
        )
        if top:
            winner_bidder_id = int(top["bidder_id"])
            try:
                max_amt = int(top["amount"])
            except Exception:
                pass
            win_msg_id = top.get("discussion_message_id")
    else:
        winner_bidder_id = int(
            winner_bid["bidder_id"] if isinstance(winner_bid, dict) else winner_bid.bidder_id
        )

    mapped = None
    try:
        if win_msg_id:
            mapped = await get_autobid_action_by_msg_id(int(win_msg_id))
    except Exception:
        mapped = None

    if mapped and mapped.get("target_user_id"):
        wid = int(mapped["target_user_id"])
    else:
        wid = int(winner_bidder_id)

    wuser = await get_user(wid) or {}
    wname = _mention(wid, wuser.get("username"))

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    # верификация
    winner_verified = await _is_user_uid_verified(wid)
    winner_verif_tag = "✅" if winner_verified else "❌"

    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_user_ids)
    if s_total:
        seller_verif_line = (
            "🔒 Лот от верифицированного продавца ✅"
            if sellers_all_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌"
        )
    else:
        seller_verif_line = "🔒 Верификация продавца: —"

    deck_tag = ""
    try:
        row = await winner_repository.get_auction_deck(
            await get_db_pool(),
            auction_id,
        )
        if row:
            if row.get("deck_id"):
                deck_tag = f" Колода №{row['deck_id']}"
            elif row.get("deck_name"):
                deck_tag = f" Колода «{row['deck_name']}»"
    except Exception:
        pass

    try:
        dmsg_id = await _get_discussion_msg_id(auction_id)
        auto_note = await _autobid_win_note(
            auction_id=auction_id,
            winner_bid=winner_bid,
            wid=wid,
            max_amt=max_amt,
            wname=wname,
        )

        end_text = (
            "🏁 <b>Аукцион завершён</b>\n"
            f"Победитель: {wname} ({winner_verif_tag})\n"
            f"Ставка: <b>{max_amt} {cur_emoji}</b>"
            f"{auto_note}\n"
            f"{seller_verif_line}"
        )

        if dmsg_id:
            await telegram_bot.send_message(
                legacy_config.DISCUSSION_CHAT_ID, end_text, parse_mode="HTML", reply_to_message_id=dmsg_id
            )
        else:
            await telegram_bot.send_message(legacy_config.DISCUSSION_CHAT_ID, end_text, parse_mode="HTML")
    except Exception:
        pass

    link = _build_channel_link(auction.get("message_id"))
    now_msk = _msk_now()
    deadline_msk = now_msk + timedelta(minutes=int(legacy_config.WINNER_NOTIFY_DEADLINE_MINUTES or 10))
    rel_minutes = int((deadline_msk - now_msk).total_seconds() // 60)

    lot_title = f"{(auction.get('hero_name') or '-')}" + (
        f" — {auction.get('card_name')}" if auction.get("card_name") else ""
    )
    preview_dm = await _winner_preview_text(auction_id, max_amt, wid)

    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    threshold = 0 if kind is AuctionKind.REVERSE else _winner_threshold(currency)
    need_review = threshold and max_amt >= threshold
    review_line = (
        f"\n⚠️ <b>Сумма ≥ порога проверки ({threshold} {cur_emoji}). Рекомендуется сверка ставок.</b>\n"
        if need_review
        else ""
    )

    admin_text = (
        "🏁 <b>Аукцион завершён</b>\n\n"
        f"Лот: <b>{lot_title}</b>{deck_tag}\n"
        f"Стоимость: <b>{max_amt} {cur_emoji}</b>\n"
        f"Победитель: {wname} ({winner_verif_tag})\n"
        f"Владелец(ы): {owners_mentions}\n"
        f"{seller_verif_line}\n"
        f"{'Ссылка: ' + link if link else ''}\n\n"
        "🧾 <b>Отправка уведомлений</b>\n"
        f"• Рекомендуем отправить <b>до {_fmt_msk(deadline_msk)} МСК</b> "
        f"(через ~{rel_minutes} мин)."
        f"{review_line}\n"
        "<i>Превью ЛС (как получит победитель/владельцы):</i>\n"
        "<code>" + preview_dm.replace("<", "‹").replace(">", "›") + "</code>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить уведомления",
                    callback_data=f"{CB_WIN_SEND}:{auction_id}:{wid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✎ Исправить ставку", callback_data=f"{CB_WIN_EDIT_AMT}:{auction_id}:{wid}"
                ),
                InlineKeyboardButton(
                    text="👤 Исправить победителя",
                    callback_data=f"{CB_WIN_EDIT_USER}:{auction_id}:{wid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{wid}"
                )
            ],
        ]
    )

    for chat_id in legacy_config.ADMIN_LOG_CHATS:
        try:
            await telegram_bot.send_message(
                chat_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception:
            pass


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


async def _get_discussion_msg_id(auction_id: int) -> int | None:
    return await winner_repository.get_discussion_message_id(
        await get_db_pool(),
        auction_id,
    )


async def _post_rules_under_lot(
    bot: Bot, auction_id: int, retries: int = 5, delay: float = 1.5
) -> None:
    """
    Пытается найти discussion_message_id и отправить «Правила» реплаем.
    Делаем несколько попыток, пока Telethon-юзербот не привяжет обсуждение.
    """
    if USERBOT_BID_MODERATION:
        return

    dmsg_id = await _get_discussion_msg_id(auction_id)
    for _ in range(retries):
        if dmsg_id:
            break
        await asyncio.sleep(delay)
        dmsg_id = await _get_discussion_msg_id(auction_id)

    if not dmsg_id:
        # не нашли — логируем и сдаёмся, без истерик
        for chat_id in legacy_config.ADMIN_LOG_CHATS:
            try:
                await bot.send_message(
                    chat_id,
                    f"⚠️ Не удалось разместить правила под лотом <code>{auction_id}</code>: нет discussion_message_id.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    try:
        await bot.send_message(
            legacy_config.DISCUSSION_CHAT_ID, RULES_COMMENT, parse_mode="HTML", reply_to_message_id=dmsg_id
        )
        # опционально: короткий лог
        for chat_id in legacy_config.ADMIN_LOG_CHATS:
            try:
                await bot.send_message(
                    chat_id,
                    f"📌 Правила размещены под лотом <code>{auction_id}</code>.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    except Exception as e:
        for chat_id in legacy_config.ADMIN_LOG_CHATS:
            try:
                await bot.send_message(
                    chat_id,
                    f"⚠️ Ошибка при размещении правил по лоту <code>{auction_id}</code>: {re.escape(str(e))}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
