"""Pending-lot, deletion-request and rejection moderation workflows."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from bot.core.legacy_config import legacy_config
from bot.core.time import to_moscow
from bot.handlers.admin.action_support.exchange import (
    _send_exchange_batch_card_admin,
    build_exchange_pending_keyboard,
    safe_answer_photo,
    tg_clean,
)
from bot.handlers.admin.action_support.transport import (
    _human_wait,
    _resolve_bot_from_message,
    _safe_strip,
    _to_msk,
    parse_datetime_field,
    send_lot_card_safe,
)
from bot.handlers.admin.helper.admin_constants import CURRENCY_EMOJI, RARITY_EMOJI, SYSTEM_MESSAGES
from bot.handlers.admin.helper.new.formatting import format_pending_lot
from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard
from bot.presentation.admin import format_owners_block
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text, get_lot_owners_with_levels
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from db.admin import log_admin_action
from db.auctions import get_lot_by_id, get_lot_owners, get_pending_auctions, list_pending_delete_requests
from db.exchange import get_pending_exchange_batches

MAX_DEBUG_LEN = 3500
MSK_TZ = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

async def show_pendinglots(message: Message, kind: str | None = None) -> None:
    """Показывает заявки на модерацию (аукционы + биржа).

    kind:
      - None -> всё
      - "exchange" -> только биржа
      - иначе -> фильтр по auction_kind (standard/reverse/...)
    """

    # В callback message.from_user = BOT, поэтому проверяем chat.id
    actor_id = message.chat.id if message.chat else None
    if actor_id not in legacy_config.ADMINS:
        return

    kind = (kind or "").strip().lower() or None

    pending_lots: list[dict] = []
    pending_exchange: list[dict] = []

    if kind == "exchange":
        pending_exchange = await get_pending_exchange_batches(limit=30, offset=0)
    else:
        pending_lots = await get_pending_auctions(auction_kind=kind, limit=50, offset=0)
        # если нет фильтра, показываем ещё и биржу
        if kind is None:
            pending_exchange = await get_pending_exchange_batches(limit=30, offset=0)

    if not pending_lots and not pending_exchange:
        await message.answer("✅ Нет заявок на модерацию.")
        return

    # 1) Аукционы
    for lot in pending_lots:
        # get_pending_auctions в db.py не выбирает status, а клавиатуре он нужен
        lot = dict(lot)
        lot.setdefault("status", "pending")

        owners = await get_lot_owners_with_levels(message.bot, int(lot["auction_id"]))
        text = format_pending_lot(lot, owners)

        kb = build_lot_keyboard(lot, role="admin", show_proof=True)

        await send_lot_card_safe(message, lot, text, kb)

    # 2) Биржа
    if pending_exchange:
        currency_emoji = {"алмазы": "💎", "чашки": "☕", "сокровища": "🪙"}

        for b in pending_exchange:
            batch_id = int(b.get("batch_id") or 0)
            if not batch_id:
                continue

            uname = (b.get("username") or "").strip()
            who = f"@{uname}" if uname else str(b.get("user_id"))

            deck_name = b.get("deck_name") or f"#{b.get('deck_id')}"
            em = currency_emoji.get((b.get("currency") or "").lower(), "💰")
            items_cnt = int(b.get("items_count") or 0)

            created_msk = _to_msk(b.get("created_at"))
            created_block = ""
            if created_msk:
                sent_str = created_msk.strftime("%d.%m.%Y %H:%M")
                wait_str = _human_wait(datetime.now(MSK_TZ) - created_msk)
                created_block = (
                    f"⏱ <b>Отправлено:</b> {html.escape(sent_str)} (МСК)\n"
                    f"🕒 <b>На модерации:</b> {html.escape(wait_str)}\n"
                )

            text = (
                "📦 <b>Заявка на биржу</b>\n"
                f"🆔 Batch: <code>{batch_id}</code>\n"
                f"{created_block}"
                f"👤 Пользователь: {html.escape(who)}\n"
                f"🗂 Колода: {html.escape(str(deck_name))}\n"
                f"⚙️ Режим: {html.escape(str(b.get('mode') or '-'))}\n"
                f"💵 Цена: {html.escape(str(b.get('price')))} {em} ({html.escape(str(b.get('currency') or ''))})\n"
                f"🃏 Карт: {items_cnt}\n"
                f"📝 Комментарий: {tg_clean(b.get('comment') or '-')}\n"
            )

            proof = (b.get("proof_photo_id") or "").strip()
            has_proof = bool(proof) and proof.upper() != "NO_PROOF"
            kb = build_exchange_pending_keyboard(batch_id, has_proof=has_proof)

            await _send_exchange_batch_card_admin(
                message,
                batch_id=batch_id,
                text=text,
                kb=kb,
                proof_id=proof,
                has_proof=has_proof,
            )


def _delete_row_lot_id(item: Any) -> Optional[int]:
    def _get_val(obj: Any, key: str) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key)
        try:
            return obj[key]
        except (KeyError, TypeError, IndexError):
            return None

    raw = _get_val(item, "lot_id")
    if raw is None:
        raw = _get_val(item, "auction_id")
    return _to_int(raw)


def _delete_request_created_str(row: Mapping[str, Any]) -> str:
    created_at = parse_datetime_field(row.get("created_at"))
    return (
        created_at.strftime("%d.%m.%Y %H:%M")
        if isinstance(created_at, datetime)
        else str(created_at)
    )


def _delete_request_keyboard(row_id: Any) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить удаление",
                    callback_data=f"approve_delete|{row_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить с причиной",
                    callback_data=f"reject_delete|{row_id}",
                )
            ],
        ]
    )


def _clip_caption(text: str, limit: int = 950) -> str:
    # caption у фото ограничен, так что не устраиваем “Bad Request: message is too long”
    if not isinstance(text, str):
        text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _build_channel_link(message_id: int | None) -> str | None:
    if not message_id:
        return None
    if legacy_config.AUCTION_CHANNEL_USERNAME:
        return f"https://t.me/{legacy_config.AUCTION_CHANNEL_USERNAME.lstrip('@')}/{message_id}"
    if legacy_config.AUCTION_CHANNEL_ID and str(legacy_config.AUCTION_CHANNEL_ID).startswith("-100"):
        return f"https://t.me/c/{str(legacy_config.AUCTION_CHANNEL_ID)[4:]}/{message_id}"
    return None


def _build_discussion_link(message_id: int | None) -> str | None:
    if not message_id or not legacy_config.DISCUSSION_CHAT_ID:
        return None
    cid = str(legacy_config.DISCUSSION_CHAT_ID)
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    return f"https://t.me/c/{cid}/{message_id}"


def _currency_label(currency: object) -> str:
    cur = str(currency or "").strip().lower()
    return CURRENCY_EMOJI.get(cur, cur or "—")


def _rarity_label(rarity: object) -> str:
    r = str(rarity or "").strip().lower()
    if not r:
        return "—"
    em = RARITY_EMOJI.get(r, "")
    return (f"{em} {r}".strip()).replace("  ", " ")


def _gift_line(lot: Mapping[str, Any]) -> str:
    try:
        ot = str(lot.get("obtain_type") or "").strip().lower()
        amt = int(lot.get("obtain_amount") or 0)
        if ot and amt > 0:
            em = {"diamonds": "💎", "cups": "🍵", "treasures": "🪙", "spins": "🎰"}.get(ot, "💰")
            return f"🎁 <b>При дарении:</b> +{amt} {em}\n"
    except Exception:
        pass
    return ""


def _delete_request_text(
        lot: Mapping[str, Any],
        owners_text: str,
        date_time_info: str,
        row: Mapping[str, Any],
        created_str: str,
) -> str:
    auction_id = html.escape(str(lot.get("auction_id", "-")))
    hero = html.escape(str(lot.get("hero_name", "") or "").strip())
    card = html.escape(str(lot.get("card_name", "-") or "-").strip())

    title = f"{hero} — {card}" if hero and card and hero != card else (card or hero or "-")

    kind = html.escape(str(lot.get("auction_kind", "standard") or "standard"))
    status = html.escape(str(lot.get("status", "-") or "-"))
    start_price = html.escape(str(lot.get("start_price", "-") or "-"))
    cur_label = html.escape(_currency_label(lot.get("currency")))

    deck_id = lot.get("deck_id")
    deck_name = str(lot.get("deck_name") or "").strip()
    deck_line = "—"
    if deck_id and deck_name:
        deck_line = f"№{html.escape(str(deck_id))} — {html.escape(deck_name)}"
    elif deck_id:
        deck_line = f"№{html.escape(str(deck_id))}"
    elif deck_name:
        deck_line = html.escape(deck_name)

    rarity_line = html.escape(_rarity_label(lot.get("rarity")))
    card_id = lot.get("card_id")
    card_num = lot.get("card_num")
    card_meta = "—"
    if card_id and card_num is not None:
        card_meta = f"id={html.escape(str(card_id))} / №{html.escape(str(card_num))}"
    elif card_id:
        card_meta = f"id={html.escape(str(card_id))}"

    comment = tg_clean(str(lot.get("comment") or "-"))
    if len(comment) > 250:
        comment = comment[:247] + "…"
    comment = html.escape(comment)

    reason = tg_clean(str(row.get("reason") or "-"))
    if len(reason) > 250:
        reason = reason[:247] + "…"
    reason = html.escape(reason)

    msg_id = lot.get("message_id")
    disc_id = lot.get("discussion_message_id")

    post_link = _build_channel_link(int(msg_id)) if msg_id else None
    disc_link = _build_discussion_link(int(disc_id)) if disc_id else None

    links: list[str] = []
    if post_link:
        links.append(f"📣 <b>Пост:</b> <a href='{post_link}'>открыть</a>")
    if disc_link:
        links.append(f"💬 <b>Обсуждение:</b> <a href='{disc_link}'>перейти</a>")

    links_block = ("\n".join(links) + "\n") if links else ""

    return (
        f"🗑️ <b>Заявка на удаление лота №{auction_id}</b>\n"
        f"<b>Лот:</b> {title}\n"
        f"⚙️ <b>Тип:</b> {kind}\n"
        f"📌 <b>Статус:</b> {status}\n"
        f"💰 <b>Старт:</b> {start_price} ({cur_label})\n"
        f"🗂 <b>Колода:</b> {deck_line}\n"
        f"✨ <b>Редкость:</b> {rarity_line}\n"
        f"🃏 <b>Карта:</b> {card_meta}\n"
        f"{_gift_line(lot)}"
        f"<b>Владелец(ы):</b> {owners_text}\n"
        f"{date_time_info}"
        f"💬 <b>Комментарий лота:</b> {comment}\n"
        f"❗️ <b>Причина:</b> {reason}\n"
        f"🕒 <b>Создана:</b> {html.escape(created_str)}\n"
        f"{links_block}"
        f"<i>Одобрите или отклоните удаление.</i>"
    )


async def show_delete_requests_for_moderation(message: Message, kind: str | None = None) -> None:
    rows = await list_pending_delete_requests(kind=kind)
    if not rows:
        await message.answer("Нет заявок на удаление.")
        return

    for row in rows:
        lot_id = _delete_row_lot_id(row)
        if lot_id is None:
            payload = dict(row) if isinstance(row, Mapping) else row
            snippet = html.escape(str(payload), quote=False)[:MAX_DEBUG_LEN]
            await message.answer(
                "❗️ Некорректный идентификатор лота в заявке.\n"
                f"<code>{snippet}</code>",
                parse_mode="HTML",
            )
            continue

        lot = await get_lot_by_id(int(lot_id))
        if not lot:
            await message.answer(f"❗️ Лот <code>{lot_id}</code> не найден.", parse_mode="HTML")
            continue

        owners = await get_lot_owners(int(lot_id))
        owners_text = format_owners_block(owners)

        start_dt = parse_datetime_field(lot.get("start_time"))
        end_dt = parse_datetime_field(lot.get("end_time"))
        date_time_info = ""
        if start_dt and end_dt:
            start_dt = to_moscow(start_dt)
            end_dt = to_moscow(end_dt)
            date_time_info = f"<b>Время:</b> {start_dt:%d.%m.%Y %H:%M}–{end_dt:%H:%M} (МСК)\n"

        created_str = _delete_request_created_str(row)
        text = _delete_request_text(lot, owners_text, date_time_info, row, created_str)

        # фото лота: сначала “что реально показывается в посте”, потом fallback на карточное
        photo_id = lot.get("image_id") or lot.get("card_image_id")

        if photo_id:
            await safe_answer_photo(
                message,
                str(photo_id),
                caption=_clip_caption(text),
                parse_mode="HTML",
                reply_markup=_delete_request_keyboard(row["id"]),
            )
        else:
            await message.answer(
                text,
                parse_mode="HTML",
                reply_markup=_delete_request_keyboard(row["id"]),
            )

        # опционально: фото подтверждения отдельным сообщением (если есть и оно не совпало с картинкой лота)
        proof = lot.get("proof_photo_id")
        if proof and str(proof) != str(photo_id):
            await safe_answer_photo(
                message,
                str(proof),
                caption="📎 <b>Фото подтверждения</b>",
                parse_mode="HTML",
            )


def _extract_reason_text(message: Message) -> str:
    return _safe_strip(getattr(message, "text", None))


async def _get_obj_row_lot(
        state: FSMContext,
        obj_id_key: str,
        get_row_fn: Callable[[int], Awaitable[Dict[str, Any]]],
        get_lot_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        message: Message,
) -> Optional[Tuple[int, int, Dict[str, Any], Dict[str, Any]]]:
    data = await state.get_data()
    obj_id = _to_int(data.get(obj_id_key))
    if obj_id is None:
        await message.answer(SYSTEM_MESSAGES["operation_failed"])
        return None
    row = await get_row_fn(obj_id)
    if not row:
        await message.answer(SYSTEM_MESSAGES["user_not_found"])
        return None
    lot = await get_lot_fn(row)
    if not lot:
        await message.answer(SYSTEM_MESSAGES["user_not_found_id"])
        return None
    lot_id = _to_int(lot.get("auction_id"))
    if lot_id is None:
        await message.answer("❗️ У лота отсутствует корректный auction_id.")
        return None
    return obj_id, lot_id, row, lot


async def _log_reject_admin_action(
        message: Message,
        admin_action_type: Optional[str],
        lot_id: int,
        reason: str,
) -> None:
    fu = getattr(message, "from_user", None)
    if admin_action_type and isinstance(fu, User):
        await log_admin_action(
            user_id=fu.id,
            action_type=admin_action_type,
            auction_id=lot_id,
            details=f"Отклонена заявка. Причина: {reason}",
        )


def _to_int(v) -> Optional[int]:
    try:
        if isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


async def _notify_lot_owners(
        bot: Optional[Bot],
        owners: Sequence[Mapping[str, Any]],
        text: str,
        *,
        lot: Optional[Mapping[str, Any]] = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    if bot is None:
        return

    photo_id = None
    if lot:
        photo_id = lot.get("image_id") or lot.get("card_image_id")

    for o in owners:
        uid = _to_int(o.get("user_id")) if isinstance(o, Mapping) else None
        if uid is None:
            continue
        try:
            if photo_id and photo_id != "DEFAULT_PHOTO_ID":
                try:
                    await bot.send_photo(
                        uid,
                        photo=str(photo_id),
                        caption=text,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                except TelegramBadRequest as e:
                    s = str(e)
                    if (
                            "Video as Photo" in s
                            or "type Video" in s
                            or "can't use file of type Video as Photo" in s
                    ):
                        # это видео, шлём как видео
                        try:
                            await bot.send_video(
                                uid,
                                video=str(photo_id),
                                caption=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                supports_streaming=True,
                            )
                        except Exception:
                            # на крайний случай анимация
                            await bot.send_animation(
                                uid,
                                animation=str(photo_id),
                                caption=text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                            )
                    else:
                        raise
            else:
                await bot.send_message(
                    uid,
                    text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
        except TelegramAPIError as e:
            await send_admin_log(bot, f"[Ошибка уведомления владельца] {e}")


async def process_reject_action(
        message: Message,
        state: FSMContext,
        *,
        obj_id_key: str,
        get_row_fn: Callable[[int], Awaitable[Dict[str, Any]]],
        get_lot_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        update_status_fn: Callable[[int, str], Awaitable[None]],
        admin_log_text_builder: Callable[
            [Dict[str, Any], str, Dict[str, Any], str, Message], str
        ],
        user_notify_builder: Callable[[Dict[str, Any], Dict[str, Any], str], str],
        status_value: str = "rejected",
        admin_action_type: Optional[str] = None,
        send_to_owners: bool = True,
) -> None:
    reason = _extract_reason_text(message)
    obj = await _get_obj_row_lot(
        state, obj_id_key, get_row_fn, get_lot_fn, message
    )
    if obj is None:
        return
    obj_id, lot_id, row, lot = obj

    owners_text = await get_lot_owners_text(lot_id)
    owners = await get_lot_owners(lot_id)

    await update_status_fn(obj_id, status_value)

    log_text = admin_log_text_builder(lot, owners_text, row, reason, message)
    bot = _resolve_bot_from_message(message)
    if bot is not None:
        await send_admin_log(bot, log_text)

    await _log_reject_admin_action(message, admin_action_type, lot_id, reason)

    if send_to_owners and owners:
        moderator = admin_tag(message.from_user)
        kb = await build_thanks_kb(int(lot_id), moderator)

        notify_text = user_notify_builder(lot, row, reason)
        notify_text = f"{notify_text}\n\n<b>Модератор:</b> {html.escape(moderator)}"

        await _notify_lot_owners(bot, owners, notify_text, lot=lot, reply_markup=kb)

    await message.answer(
        SYSTEM_MESSAGES.get("operation_success", "Отказ отправлен владельцу.")
    )
    await state.clear()


__all__ = (
    'MAX_DEBUG_LEN',
    'MSK_TZ',
    'UTC',
    'show_pendinglots',
    '_delete_row_lot_id',
    '_delete_request_created_str',
    '_delete_request_keyboard',
    '_clip_caption',
    '_build_channel_link',
    '_build_discussion_link',
    '_currency_label',
    '_rarity_label',
    '_gift_line',
    '_delete_request_text',
    'show_delete_requests_for_moderation',
    '_extract_reason_text',
    '_get_obj_row_lot',
    '_log_reject_admin_action',
    '_to_int',
    '_notify_lot_owners',
    'process_reject_action',
)
