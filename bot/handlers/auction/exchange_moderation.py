from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from html import escape as _h
from typing import Any, Optional

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.domain.auctions import InvalidExchangeTransition
from bot.handlers.admin.action_support.exchange import (
    format_exchange_moderation_log,
    notify_exchange_user_moderation,
)
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.admin_logging import send_admin_log
from bot.services.exchange_media import get_exchange_cover_media as _get_exchange_cover_media
from bot.services.exchange_moderation import ExchangeModerationQueries
from bot.services.exchanges import ExchangeService
from bot.services.luxury import get_user_luxury_level, is_luxury_member
from bot.telegram.media import answer_media_any as _answer_media_any, safe_send_media
from bot.core.legacy_config import legacy_config
from db.auctions import (
    count_sold_by_card_id,
    count_sold_same_card,
    show_pending_auction_lots,
)
from db.cards import get_deck_by_id
from db.exchange import (
    get_exchange_batch,
    get_exchange_batch_by_id,
    get_exchange_cards_for_batch,
    get_exchange_items_by_batch_id,
)
from db.admin import (
    is_admin,
    log_admin_action,
)
from bot.telegram.states import ExchangeFSM, ModActionFSM, UserAddLotFSM

from bot.features.exchange.contracts import (
    ANNOUNCE_TZ,
    CURRENCY_EMOJI,
    GUIDE_UID_CRAFT_PHOTO_ID,
    GUIDE_UID_CRAFT_TEXT,
    UTC,
    _BR_RE,
    _cur_emoji,
    _deck_id_from_row,
    _exchange_gain_for_card,
    _exchange_gift_for_card,
    _exchange_price_for_card,
    _get_exchange_deck_ids,
    _get_exchange_decks_for_menu,
    _gift_emoji,
    _rarity_badge,
    _rarity_norm,
    currency_to_emoji,
    h,
    tg_clean,
)
from bot.telegram.callback_parser import rsplit_callback_data, split_callback_data

router = Router(name="auction_exchange_moderation")


@router.callback_query(F.data.startswith("pending_menu:"))
@admin_only
async def pending_menu_pick(call: types.CallbackQuery, state: FSMContext):
    kind = split_callback_data(call.data, ":", 1)[1].strip()
    await call.answer()

    if kind == "auction":
        await show_pending_auction_lots(call.message)
        return

    if kind == "exchange":
        await show_pending_exchange_requests(call.message)
        return

    await call.message.answer("Неизвестный тип заявок.")


@router.callback_query(F.data.startswith("exchange_proof|"))
@admin_only
async def exchange_show_proof(call: types.CallbackQuery):
    batch_id = int(split_callback_data(call.data, "|")[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    proof_id = (batch.get("proof_photo_id") or "").strip()
    if not proof_id or proof_id.upper() == "NO_PROOF":
        await call.answer("Пруфа нет", show_alert=True)
        return

    # ✅ отправляем ОДИН раз
    try:
        await call.message.answer_photo(
            proof_id,
            caption=f"📸 Пруф заявки (биржа) • #{batch_id}",
            protect_content=False,  # если хочешь разрешить пересылку/скрины
        )
    except Exception:
        await call.answer("Пруф битый (file_id неверный).", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("exchange_approve|"))
@admin_only
async def exchange_approve(call: types.CallbackQuery):
    batch_id = int(split_callback_data(call.data, "|")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    try:
        service = await ExchangeService.create()
        batch = await service.approve(
            batch_id,
            moderator_id=call.from_user.id,
            moderator_username=call.from_user.username or call.from_user.full_name,
        )
    except InvalidExchangeTransition as exc:
        await call.answer(
            f"Заявка уже обработана (статус: {exc.current}).",
            show_alert=True,
        )
        return

    # ---------- данные для лога ----------
    when_msk = _fmt_dt_msk(datetime.now(timezone.utc))

    admin_html = _user_link(call.from_user.id, call.from_user.username)

    user_id = int(batch.get("user_id") or 0)
    user_html = _user_link(user_id, batch.get("username")) if user_id else "—"

    deck_id = int(batch.get("deck_id") or 0)
    deck_name = None
    try:
        if deck_id:
            d = await get_deck_by_id(deck_id)
            deck_name = (d.get("name") or "").strip() if d else None
    except Exception:
        deck_name = None
    deck_title = deck_name or (f"{deck_id}" if deck_id else "—")

    mode = (batch.get("mode") or "").strip()
    currency = str(batch.get("currency") or "алмазы").strip()
    price = batch.get("price")
    comment = (batch.get("comment") or "").strip() or "-"

    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

    # состав (короткое превью до 10 строк)
    items_cnt = 0
    preview: list[str] = []
    try:
        items = await get_exchange_items_by_batch_id(batch_id)
        items_cnt = len(items)
        for i, it in enumerate(items[:10], start=1):
            card_name = tg_clean(str(it.get("card_name") or "-"))
            hero_name = tg_clean(str(it.get("hero_name") or "-"))
            preview.append(f"{i}. <b>{card_name}</b> — {hero_name}")
        more = items_cnt - min(items_cnt, 10)
        if more > 0:
            preview.append(f"…и ещё <b>{more}</b> шт.")
    except Exception:
        items_cnt = int(batch.get("items_count") or 0) if batch.get("items_count") is not None else 0
        preview = []

    # ---------- отправка лога в лог-чаты ----------
    try:
        log_text = format_exchange_approved_log(
            created_at_msk=when_msk,
            batch_id=batch_id,
            admin_html=admin_html,
            user_html=user_html,
            deck_title=deck_title,
            mode=mode,
            items_count=items_cnt,
            price=int(price) if price is not None else None,
            currency=currency,
            has_proof=has_proof,
            comment=comment,
            items_preview=preview,
        )
        await send_admin_log(call.bot, log_text)
    except Exception:
        pass

    # ---------- уведомление пользователю (как раньше, можно оставить твою версию) ----------
    moderator_tag_str = admin_tag(call.from_user)
    thanks_kb = await build_thanks_kb(int(batch_id), moderator_tag_str)

    mode_key = (mode or "").strip().lower()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, mode or "—")

    cur_emoji = _cur_emoji(currency.lower())
    price_line = f"{int(price)} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    notify_text = (
        "✅ <b>Ваша заявка на биржу одобрена и добавлена в биржу!</b>\n"
        f"🆔 Batch: <code>{batch_id}</code>\n\n"
        f"📚 Колода: <b>{html.escape(deck_title)}</b>\n"
        f"🎛 Режим: <b>{html.escape(str(mode_ru))}</b>\n"
        f"🃏 Карт: <b>{items_cnt}</b>\n"
        f"💰 Цена: <b>{html.escape(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{html.escape(comment)}</i>\n\n"
        f"<b>Модератор:</b> {admin_html}"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n\n"
    )

    media_id = None
    kind = "photo"
    try:
        cover_id, cover_kind = await _get_exchange_cover_media(batch_id)
        if cover_id:
            media_id = cover_id
            kind = cover_kind
    except Exception:
        media_id = None

    if not media_id and has_proof:
        media_id = proof_id
        kind = "photo"

    try:
        if user_id:
            if media_id:
                await safe_send_media(
                    call.bot,
                    chat_id=user_id,
                    file_id=str(media_id),
                    caption=notify_text,
                    reply_markup=thanks_kb,
                    parse_mode="HTML",
                    protect_content=False,
                )
            else:
                await call.bot.send_message(
                    user_id,
                    notify_text,
                    parse_mode="HTML",
                    reply_markup=thanks_kb,
                    disable_web_page_preview=True,
                )
    except Exception:
        pass

    # обновим кнопки у админа
    try:
        await call.message.edit_reply_markup(reply_markup=_approved_kb(batch_id, has_proof=has_proof))
    except Exception:
        pass

    await call.answer("Одобрено ✅", show_alert=False)


@router.callback_query(F.data.startswith("exchange_items|"))
@admin_only
async def exchange_items(call: types.CallbackQuery):
    batch_id = int(split_callback_data(call.data, "|")[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    items = await get_exchange_items_by_batch_id(batch_id)
    if not items:
        await call.message.answer(
            f"🃏 <b>Состав заявки</b> • ID <code>{batch_id}</code>\n— пусто",
            parse_mode="HTML",
        )
        await call.answer()
        return

    lines: list[str] = []
    hard_limit = 60
    for i, it in enumerate(items[:hard_limit], start=1):
        name = html.escape(str(it.get("card_name") or "-"))
        hero = html.escape(str(it.get("hero_name") or "-"))
        cid = it.get("card_id")
        tail = f" (id={cid})" if cid else ""
        lines.append(f"{i}. <b>{name}</b> — {hero}{tail}")

    more = len(items) - min(len(items), hard_limit)
    more_line = f"\n…и ещё <b>{more}</b> шт." if more > 0 else ""

    await call.message.answer(
        f"🃏 <b>Состав заявки</b> • ID <code>{batch_id}</code>\n\n" + "\n".join(lines) + more_line,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data.startswith("exchange_reject|"))
@admin_only
async def exchange_reject_start(call: types.CallbackQuery, state: FSMContext):
    batch_id = int(split_callback_data(call.data, "|")[1])
    await state.update_data(exchange_batch_id=batch_id)
    await state.set_state(ModActionFSM.waiting_for_reject_exchange_reason)
    await call.message.answer("Напиши причину отклонения заявки на биржу:")
    await call.answer()


@router.message(ModActionFSM.waiting_for_reject_exchange_reason, F.chat.type == "private")
@admin_only
async def exchange_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    batch_id = int(data.get("exchange_batch_id") or 0)
    reason = (message.text or "").strip()

    if not batch_id or not reason:
        await message.answer("Нужна причина текстом.")
        return

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer("Заявка не найдена или уже обработана.")
        await state.clear()
        return

    try:
        service = await ExchangeService.create()
        batch = await service.reject(
            batch_id,
            moderator_id=message.from_user.id,
            moderator_username=message.from_user.username or message.from_user.full_name,
            comment=reason,
        )
    except InvalidExchangeTransition as exc:
        await message.answer(f"Заявка уже обработана (статус: {exc.current}).")
        await state.clear()
        return

    # 1) уведомим пользователя — КРАСИВО, как у аукциона
    try:
        await notify_exchange_user_moderation(
            message.bot,
            batch=batch,
            admin_user=message.from_user,
            title="отклонена",
            reason=reason,
        )
    except Exception:
        pass

    # 2) лог в лог-чат — единый стиль, как у обычной заявки
    try:
        deck_id = int(batch.get("deck_id") or 0)

        # deck_name (опционально)
        deck_name = None
        try:
            queries = await ExchangeModerationQueries.create()
            deck_name = await queries.deck_name(deck_id)
        except Exception:
            deck_name = None
        deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

        # items count
        items_cnt = 0
        try:
            queries = await ExchangeModerationQueries.create()
            items_cnt = await queries.batch_items_count(batch_id)
        except Exception:
            items_cnt = 0

        proof_id = (batch.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

        when_msk = _fmt_dt_msk(datetime.now(timezone.utc))

        log_text = format_exchange_moderation_log(
            action_title="Отклонена заявка на биржу",
            action_code="exchange_reject через бота",
            when_msk=when_msk,
            admin_user=message.from_user,
            batch_id=batch_id,
            sender_username=batch.get("username"),
            sender_id=batch.get("user_id"),
            deck_name=deck_title,
            deck_id=deck_id,
            mode=str(batch.get("mode") or "—"),
            items_count=items_cnt,
            price=int(batch["price"]) if batch.get("price") is not None else None,
            currency=str(batch.get("currency") or "алмазы"),
            has_proof=has_proof,
            comment=str(batch.get("comment") or ""),
            moderator_comment=reason,
        )
        await send_admin_log(message.bot, log_text)
    except Exception:
        pass

    await message.answer(f"Отклонено ❌ (Batch {batch_id})")
    await state.clear()


EX_CB_PROOF = "ex:proof"
EX_CB_APPROVE = "ex:approve"
EX_CB_REJECT = "ex:reject"


def _ex_admin_kb(batch_id: int, has_proof: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"{EX_CB_APPROVE}:{batch_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{EX_CB_REJECT}:{batch_id}"),
        ]
    ]
    if has_proof:
        rows.append([InlineKeyboardButton(text="📸 Фото подтверждения", callback_data=f"{EX_CB_PROOF}:{batch_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_exchange_batch_text(batch: dict, items: list[dict]) -> str:
    username = f"@{batch['username']}" if batch.get("username") else ""
    user_line = f"{batch['user_id']} {username}".strip()
    deck = batch.get("deck_name") or f"ID {batch.get('deck_id')}"
    comment = batch.get("comment") or "—"

    items_lines = []
    for it in items:
        hero = (it.get("hero_name") or "").strip()
        card = (it.get("card_name") or "").strip()
        if hero and card:
            items_lines.append(f"• {hero} — {card}")
        elif hero or card:
            items_lines.append(f"• {hero or card}")
    items_block = "\n".join(items_lines) if items_lines else "—"

    return (
        f"🛒 Заявка на Биржу #{batch['batch_id']}\n"
        f"👤 {user_line}\n"
        f"🗂 Колода: {deck}\n"
        f"🎛 Режим: {batch.get('mode')}\n"
        f"💰 Валюта: {batch.get('currency')} | Цена: {batch.get('price')}\n"
        f"💬 Комментарий: {comment}\n"
        f"🃏 Карты:\n{items_block}\n"
        f"🕒 Создано: {batch.get('created_at')}\n"
        f"📌 Статус: {batch.get('status')}"
    )


@router.callback_query(F.data.startswith(f"{EX_CB_PROOF}:"))
async def cb_exchange_proof(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    proof = batch.get("proof_photo_id")
    if not proof:
        await call.message.answer("Фото подтверждения не прикреплено.")
        return

    await call.message.answer_photo(proof, caption=f"📸 Фото подтверждения (Биржа #{batch_id})")


async def cb_exchange_approve(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    try:
        service = await ExchangeService.create()
        batch = await service.approve(
            batch_id,
            moderator_id=call.from_user.id,
            moderator_username=call.from_user.username or call.from_user.full_name,
        )
    except InvalidExchangeTransition as exc:
        await call.message.answer(f"Заявка уже обработана: {exc.current}.")
        return
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Биржа-заявка #{batch_id} принята.")

    # ✅ логи
    try:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_text = (
            "🛒 <b>Биржа: одобрено</b>\n"
            f"🕒 {now_str} (МСК)\n"
            f"Batch: <code>{batch_id}</code>\n"
            f"Админ: {_user_link(call.from_user.id, call.from_user.username)}\n"
            f"Пользователь: {_user_link(int(batch.get('user_id')), batch.get('username'))}\n"
            "Действие: exchange_approve через бота."
        )
        await send_admin_log(call.bot, log_text)
        await log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_approve",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


async def cb_exchange_reject(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    try:
        service = await ExchangeService.create()
        batch = await service.reject(
            batch_id,
            moderator_id=call.from_user.id,
            moderator_username=call.from_user.username or call.from_user.full_name,
        )
    except InvalidExchangeTransition as exc:
        await call.message.answer(f"Заявка уже обработана: {exc.current}.")
        return
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"❌ Биржа-заявка #{batch_id} отклонена.")

    # ✅ логи
    try:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_text = (
            "🛒 <b>Биржа: отклонено</b>\n"
            f"🕒 {now_str} (МСК)\n"
            f"Batch: <code>{batch_id}</code>\n"
            f"Админ: {_user_link(call.from_user.id, call.from_user.username)}\n"
            f"Пользователь: {_user_link(int(batch.get('user_id')), batch.get('username'))}\n"
            "Действие: exchange_reject через бота."
        )
        await send_admin_log(call.bot, log_text)
        await log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_reject",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


PENDING_EXCHANGE_PAGE_SIZE = 5


def _fmt_dt_msk(dt: Any) -> str:
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(ANNOUNCE_TZ).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)


def _user_ref(username: Optional[str], user_id: Any) -> str:
    uid = str(user_id) if user_id is not None else "?"
    uname = (username or "").strip()
    if uname and not uname.startswith("@"):
        uname = "@" + uname
    return f"{html.escape(uname)} <code>{uid}</code>" if uname else f"<code>{uid}</code>"


async def _edit_or_send(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup]) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


def _fmt_age_short(created_at: Any) -> str:
    """Сколько заявка висит на модерации: 0м / 1ч 12м / 2д 3ч."""
    if not isinstance(created_at, datetime):
        return "—"
    try:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt.astimezone(UTC)
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0

        mins = seconds // 60
        hours = mins // 60
        days = hours // 24
        mins = mins % 60
        hours = hours % 24

        if days > 0:
            return f"{days}д {hours}ч"
        if hours > 0:
            return f"{hours}ч {mins}м"
        return f"{mins}м"
    except Exception:
        return "—"


def format_pending_exchange_batch_card(batch: dict, *, items_count: int) -> str:
    batch_id = int(batch.get("batch_id") or 0)
    created = batch.get("created_at")
    age = _fmt_age_short(created)

    username = (batch.get("username") or "").strip()
    user_id = batch.get("user_id")
    user_line = _user_ref(username, user_id)

    deck_id = batch.get("deck_id")
    deck_name = (batch.get("deck_name") or "").strip()
    deck_title = deck_name or (f"ID {deck_id}" if deck_id else "-")

    mode_labels = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }
    mode = (batch.get("mode") or "").strip()
    mode_title = mode_labels.get(mode, mode or "-")

    currency = (batch.get("currency") or "алмазы").strip().lower()
    cur_emoji = _cur_emoji(currency)

    price = batch.get("price")
    comment = (batch.get("comment") or "").strip() or "-"

    created_str = _fmt_dt_msk(created)

    # стиль "как заявка на аукцион": короткие строки + иконки
    return (
        "🧾 <b>Заявка на биржу</b>\n"
        f"🆔 <b>ID Batch:</b> <code>{batch_id}</code>\n"
        f"🕒 <b>Отправлено:</b> {html.escape(created_str)} (МСК)\n"
        f"⏳ <b>На модерации:</b> {html.escape(age)}\n"
        f"👤 <b>Пользователь:</b> {user_line}\n"
        f"📚 <b>Колода:</b> <b>{html.escape(deck_title)}</b>\n"
        f"🎛 <b>Режим:</b> <b>{html.escape(mode_title)}</b>\n"
        f"💰 <b>Цена:</b> <b>{html.escape(str(price))} {cur_emoji}</b> ({html.escape(currency)})\n"
        f"🃏 <b>Карт:</b> <b>{items_count}</b>\n"
        f"💬 <b>Комментарий:</b> {html.escape(comment)}"
    )


def pending_exchange_kb(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    row1: list[InlineKeyboardButton] = []
    if has_proof:
        row1.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    row1.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    b.row(*row1)

    b.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"exchange_approve|{batch_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"exchange_reject|{batch_id}"),
    )

    b.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}"))
    return b.as_markup()




def _format_exchange_user_notice(
        *,
        batch: dict,
        deck_name: str,
        items_count: int,
        currency: str,
        price: int | None,
        has_proof: bool,
        comment: str,
        title: str,  # "отклонена" / "удалена"
        reason: str | None,
        moderator_html: str,
) -> str:
    batch_id = int(batch["batch_id"])
    cur_emoji = _cur_emoji(currency)
    price_line = f"{price} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    created_at = batch.get("created_at")
    created_at_msk = _fmt_msk_dt(created_at) if created_at else "—"

    # сколько висело на модерации
    try:
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            waited = _human_wait(int((datetime.now(timezone.utc) - created_at).total_seconds()))
        else:
            waited = "—"
    except Exception:
        waited = "—"

    cmt = (comment or "").strip() or "-"

    lines = [
        f"❌ <b>Ваша заявка на биржу {html.escape(title)}</b>",
        f"🕒 Отправлено: {created_at_msk} (МСК)",
        f"⏳ На модерации: {waited}",
        f"🆔 Batch: <code>{batch_id}</code>",
        "",
        f"📚 Колода: <b>{html.escape(deck_name)}</b>",
        f"🎛 Режим: <b>{html.escape(str(batch.get('mode') or '—'))}</b>",
        f"🃏 Карт: <b>{items_count}</b>",
        f"💰 Цена: <b>{price_line}</b>",
        f"📸 Пруф: <b>{proof_line}</b>",
        f"💬 Комментарий: <i>{html.escape(cmt)}</i>",
    ]

    if reason is not None:
        rsn = (reason or "").strip() or "—"
        lines += [
            f"🔒 Причина: <i>{html.escape(rsn)}</i>",
        ]

    lines += [
        "",
        "Если есть вопросы — обратитесь к администрации.",
        f"Модератор: {moderator_html}",
        "",
        "Если хочешь, можешь сказать спасибо ниже ❤️\n",
    ]
    return "\n".join(lines)


async def show_pending_exchange_requests_all(message: types.Message, limit: int = 50) -> None:
    """Показывает ВСЕ (точнее первые limit) заявки биржи одной лентой."""
    limit = max(1, min(int(limit or 50), 200))

    queries = await ExchangeModerationQueries.create()
    total = await queries.pending_count()
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    rows = await queries.pending(limit=limit)
    shown = len(rows)

    head = (
        "🛒 <b>Заявки на биржу</b>\n"
        f"Всего: <b>{total}</b>\n"
        f"Показываю: <b>{shown}</b>"
    )
    if total > shown:
        head += f"\n\n⚠️ Заявок больше, чем лимит. Остальные удобнее листать в режиме «По одному»."

    await message.answer(head, parse_mode="HTML")

    for r in rows:
        batch_id = int(r.get("batch_id") or 0)

        proof_id = (r.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
        items_count = int(r.get("items_count") or 0)

        status_line = "👑 <b>Статус пользователя:</b> " + ("Лакшери" if bool(r.get("is_luxury")) else "Обычный")
        text = status_line + "\n\n" + format_pending_exchange_batch_card(r, items_count=items_count)

        kb = pending_exchange_kb(batch_id, has_proof=has_proof)

        cover_id, cover_kind = await _get_exchange_cover_media(batch_id)
        media_id = cover_id or (proof_id if has_proof else None)
        kind = cover_kind if cover_id else "photo"

        if media_id:
            try:
                if kind == "video":
                    await message.answer_video(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
                elif kind == "animation":
                    await message.answer_animation(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
                else:
                    await message.answer_photo(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                kind2 = _media_kind_from_error(e) or "photo"
                try:
                    if kind2 == "video":
                        await message.answer_video(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
                    elif kind2 == "animation":
                        await message.answer_animation(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
                    else:
                        await message.answer_photo(media_id, caption=text, parse_mode="HTML", reply_markup=kb)
                except Exception:
                    await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


async def show_pending_exchange_requests(message: types.Message, page: int = 0) -> None:
    # page намеренно игнорируем: показываем всё сразу, без навигации
    queries = await ExchangeModerationQueries.create()
    rows = await queries.all_pending()

    if not rows:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    await message.answer(
        f"🛒 <b>Заявки на биржу</b>\n"
        f"Всего: <b>{len(rows)}</b>\n\n"
        "Ниже все заявки одной лентой (без страниц).",
        parse_mode="HTML",
    )

    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        proof_id = (r.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

        items_count = int(r.get("items_count") or 0)
        text = format_pending_exchange_batch_card(r, items_count=items_count)

        # ✅ только кнопки действий, без “Обновить/стрелки”
        kb = pending_exchange_kb_simple(batch_id=batch_id, has_proof=has_proof)

        # ✅ без фото-карты вообще, просто текстом
        await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def pending_exchange_kb_simple(*, batch_id: int, has_proof: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # верхний ряд: показать пруф / состав (если у тебя есть кнопка состава)
    if has_proof:
        kb.button(text="📸 Пруф", callback_data=f"exchange_proof|{batch_id}")
    kb.button(text="🧾 Состав", callback_data=f"exchange_items|{batch_id}")

    # второй ряд: одобрить / отклонить
    kb.button(text="✅ Одобрить", callback_data=f"exchange_approve|{batch_id}")
    kb.button(text="❌ Отклонить", callback_data=f"exchange_reject|{batch_id}")

    # третий ряд: удалить
    kb.button(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}")

    # раскладка
    kb.adjust(2, 2, 1)
    return kb.as_markup()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data == "craft_uid:help")
async def addlot_craft_uid_help(call: CallbackQuery):
    await call.answer()

    await call.message.answer_photo(
        GUIDE_UID_CRAFT_PHOTO_ID,
        caption="🆔 <b>Гайд</b>: крафт по UID",
        parse_mode="HTML",
    )
    await call.message.answer(
        GUIDE_UID_CRAFT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data.startswith("craft_uid:"))
async def addlot_craft_uid_answer(call: CallbackQuery, state: FSMContext):
    raw = split_callback_data(call.data or "", ":", 1)[-1].strip().lower()
    craft_ok = raw in {"yes", "1", "true", "да"}

    await state.update_data(craft_uid_possible=craft_ok)

    # убираем кнопки, чтобы не тыкали повторно
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    d = await state.get_data()
    currency = d.get("currency", "алмазы")
    emoji = _cur_emoji(currency)

    craft_text = "✅ Да" if craft_ok else "❌ Нет"
    comment = (d.get("comment") or "").strip()

    preview = (
        f"<b>Лот:</b> {html.escape(str(d.get('card_name') or '-'))}\n"
        f"Валюта: {emoji}\n"
        f"Минимальная ставка: {d.get('start_price')} {emoji}\n"
        f"Крафт на UID: {craft_text}\n"
        f"Комментарий: {html.escape(comment or '-')}\n"
        "Всё верно? Отправить заявку на модерацию?"
    )

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Подтвердить"), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await call.message.answer(preview, reply_markup=kb, parse_mode="HTML")
    await state.set_state(UserAddLotFSM.waiting_for_confirmation)
    await call.answer()














def _tg_clean(text: str) -> str:
    return _BR_RE.sub("\n", text or "")


# Public bridge used by the lot-creation flow.  Keeping this small surface
# explicit prevents auctions.py from reaching into exchange implementation
# details while Phase 7 splits this module further.
exchange_deck_id_from_row = _deck_id_from_row
get_exchange_deck_ids = _get_exchange_deck_ids
get_exchange_decks_for_menu = _get_exchange_decks_for_menu
exchange_price_for_card = _exchange_price_for_card
exchange_gain_for_card = _exchange_gain_for_card
clean_telegram_text = _tg_clean


def _media_kind_from_error(e: Exception) -> str | None:
    s = str(e).lower()
    if "video as photo" in s:
        return "video"
    if "animation as photo" in s or "gif as photo" in s:
        return "animation"
    return None


def _fmt_msk_dt(dt: object) -> str:
    # если у тебя уже есть _fmt_dt_msk — используй его вместо этого
    try:
        return _fmt_dt_msk(dt)  # type: ignore[name-defined]
    except Exception:
        # fallback: просто локальный формат, без TZ магии
        if isinstance(dt, datetime):
            return dt.strftime("%d.%m.%Y %H:%M")
        return "—"


def _human_wait(delta_sec: int) -> str:
    if delta_sec < 0:
        delta_sec = 0
    days, sec = divmod(delta_sec, 86400)
    hours, sec = divmod(sec, 3600)
    mins, _ = divmod(sec, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


def _user_link(user_id: int, username: Optional[str]) -> str:
    label = f"@{username}" if username else f"id:{user_id}"
    return f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'


def _items_block(items: list[dict], *, max_lines: int = 30) -> str:
    lines: list[str] = []
    for it in items[:max_lines]:
        hero = html.escape(str(it.get("hero_name") or "—"))
        card = html.escape(str(it.get("card_name") or "—"))
        qty = int(it.get("qty") or 1)
        lines.append(f"• {hero} — {card} × {qty}")
    if len(items) > max_lines:
        lines.append(f"…и ещё {len(items) - max_lines} шт.")
    return "\n".join(lines) if lines else "—"


def _format_exchange_channel_post(batch: dict, deck_name: str, items: list[dict]) -> str:
    batch_id = int(batch["batch_id"])
    price = batch.get("price")
    currency = (batch.get("currency") or "").lower()
    em = CURRENCY_EMOJI.get(currency, "💰")
    comment = (batch.get("comment") or "").strip()

    parts = [
        f"🛒 <b>Биржа</b> • <code>{batch_id}</code>",
        f"🗂 <b>Колода:</b> {html.escape(deck_name)}",
        "",
        "🃏 <b>Состав:</b>",
        _items_block(items, max_lines=35),
        "",
        f"💵 <b>Цена:</b> {html.escape(str(price))} {em} ({html.escape(currency or '—')})",
    ]
    if comment:
        parts.append(f"📝 <b>Комментарий:</b> {html.escape(comment)}")

    return "\n".join(parts)


def _approved_kb(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    row1: list[InlineKeyboardButton] = []
    if has_proof:
        row1.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    row1.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    b.row(*row1)

    b.row(
        InlineKeyboardButton(text="📣 Рассылка", callback_data=f"exchange_broadcast|{batch_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}"),
    )
    return b.as_markup()


def _delete_confirm_kb(batch_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"exchange_delete_yes|{batch_id}"),
        InlineKeyboardButton(text="⬅️ Нет", callback_data=f"exchange_delete_no|{batch_id}"),
    )
    return b.as_markup()



# Removed unreachable duplicate handler: exchange_items.



# Removed unreachable duplicate handler: exchange_approve.



# Removed unreachable duplicate handler: exchange_reject_start.





# Removed unreachable duplicate handler: exchange_reject_finish.


@router.callback_query(F.data.startswith("exchange_delete|"))
@admin_only
async def exchange_delete_ask(call: CallbackQuery):
    batch_id = int(split_callback_data(call.data, "|", 1)[1])
    await call.message.answer(
        f"Точно удалить заявку биржи <code>{batch_id}</code>?",
        parse_mode="HTML",
        reply_markup=_delete_confirm_kb(batch_id),
    )
    await call.answer()


@router.callback_query(ExchangeFSM.waiting_for_copies, F.data.startswith("ex_copies:"))
async def ex_copies_selected(call: CallbackQuery, state: FSMContext) -> None:
    payload = split_callback_data(call.data or "", ":", 1)[1].strip()

    if payload == "other":
        await call.message.answer("Введи число (например 2). Минимум 1, максимум 50.")
        await call.answer()
        return

    try:
        copies = int(payload)
    except Exception:
        await call.answer("Некорректное число.", show_alert=True)
        return

    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await call.message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await call.answer()


@router.message(ExchangeFSM.waiting_for_copies)
async def ex_copies_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    text_low = text.lower()

    # выход
    if text_low in {"🏠 меню", "меню", "/start", "🛒 биржа", "биржа", "📦 аукцион", "аукцион"}:
        await state.clear()
        await message.answer("Ок, выхожу из оформления заявки биржи.")
        return

    # команды не жрём FSM-ом
    if text.startswith("/"):
        raise SkipHandler()

    if not text.isdigit():
        await message.answer("Нужно число. Например: 2")
        return

    copies = int(text)
    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(F.data.startswith("exchange_delete_no|"))
@admin_only
async def exchange_delete_no(call: CallbackQuery):
    await call.answer("Ок, не удаляем")


@router.callback_query(F.data.startswith("exchange_delete_yes|"))
@admin_only
async def exchange_delete_yes(call: CallbackQuery, bot: Bot):
    batch_id = int(split_callback_data(call.data, "|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    admin = call.from_user

    service = await ExchangeService.create()
    await service.delete(
        batch_id,
        moderator_id=admin.id,
        moderator_username=admin.username or admin.full_name,
        comment="deleted",
    )

    # Внешний side effect выполняется после фиксации soft-delete. Если Telegram
    # недоступен, повторное нажатие безопасно дочистит опубликованный пост.
    posted_chat_id = batch.get("posted_chat_id")
    posted_message_id = batch.get("posted_message_id")
    if posted_chat_id and posted_message_id:
        try:
            await bot.delete_message(int(posted_chat_id), int(posted_message_id))
        except Exception:
            pass

    # уведомим пользователя (мягко)
    try:
        await bot.send_message(
            int(batch["user_id"]),
            f"🗑 Ваша заявка на биржу <code>{batch_id}</code> удалена модератором.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # лог
    log_text = (
        "🛒 <b>Биржа: удалено</b>\n"
        f"Batch: <code>{batch_id}</code>\n"
        f"Админ: {_user_link(admin.id, admin.username)}\n"
        f"Пользователь: {_user_link(int(batch['user_id']), batch.get('username'))}\n"
    )
    await send_admin_log(bot, log_text)

    await call.answer("Удалено")


@router.callback_query(F.data.startswith("exchange_broadcast|"))
@admin_only
async def exchange_broadcast(call: CallbackQuery, bot: Bot):
    batch_id = int(split_callback_data(call.data, "|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    deck = await get_deck_by_id(int(batch.get("deck_id") or 0))
    deck_name = str((deck or {}).get("deck_name") or f"#{batch.get('deck_id')}")

    items = await get_exchange_cards_for_batch(batch_id)

    text = _format_exchange_channel_post(batch, deck_name, items)

    proof = (batch.get("proof_photo_id") or "").strip()
    service = await ExchangeService.create()
    try:
        batch = await service.claim_for_post(batch_id)
    except InvalidExchangeTransition as exc:
        await call.answer(
            f"Нельзя опубликовать заявку со статусом {exc.current}.",
            show_alert=True,
        )
        return
    try:
        if proof and proof.upper() != "NO_PROOF":
            msg = await bot.send_photo(
                legacy_config.AUCTION_CHANNEL_ID,
                photo=proof,
                caption=text[:1024],
                parse_mode="HTML",
            )
        else:
            msg = await bot.send_message(legacy_config.AUCTION_CHANNEL_ID, text, parse_mode="HTML")
    except Exception:
        try:
            await service.release_post_claim(batch_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "exchange publication claim release failed: %s", batch_id
            )
        await call.answer("Не удалось отправить в канал.", show_alert=True)
        return

    try:
        await service.mark_posted(
            batch_id,
            chat_id=int(legacy_config.AUCTION_CHANNEL_ID),
            message_id=int(msg.message_id),
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "exchange post delivered but not recorded: batch_id=%s message_id=%s",
            batch_id,
            msg.message_id,
        )
        await call.answer(
            "Пост отправлен, но фиксация не завершилась. Нужна ручная проверка.",
            show_alert=True,
        )
        return

    link = ""
    if legacy_config.AUCTION_CHANNEL_USERNAME:
        link = f"\n🔗 https://t.me/{legacy_config.AUCTION_CHANNEL_USERNAME}/{msg.message_id}"

    await send_admin_log(
        bot,
        "🛒 <b>Биржа: рассылка</b>\n"
        f"Batch: <code>{batch_id}</code>\n"
        f"Канал msg_id: <code>{msg.message_id}</code>{html.escape(link)}\n",
    )

    await call.answer("Отправлено")



# _default_step_for_currency moved to bot.handlers.auction.autobid.



# cmd_autobid_set moved to bot.handlers.auction.autobid.



# cmd_autobid_stop moved to bot.handlers.auction.autobid.



# cmd_autobid_list moved to bot.handlers.auction.autobid.


async def _uid_verification_badge(user_id: int) -> str:
    try:
        from db.uid import (
            get_user_verified_uid,
            is_user_uid_banned,
        )
        if await is_user_uid_banned(int(user_id)):
            return "⛔️ UID в ЧС"
        uid = await get_user_verified_uid(int(user_id))
        return "✅ UID верифицирован" if uid else "❌ НЕТ ВЕРИФИКАЦИИ"
    except Exception:
        return "❌ НЕТ ВЕРИФИКАЦИИ"


async def _format_user_status(bot: Bot, user_id: int) -> str:
    # 1) админ
    try:
        if await is_admin(int(user_id)):
            return "🛡 Админ"
    except Exception:
        pass

    # 2) лакшери по чатам (самый надёжный источник)
    try:
        if legacy_config.LUXURY_CHAT_ID_LVL2 and await is_luxury_member(bot, user_id, legacy_config.LUXURY_CHAT_ID_LVL2):
            return "👑 Лакшери 2"
        if legacy_config.LUXURY_CHAT_ID and await is_luxury_member(bot, user_id, legacy_config.LUXURY_CHAT_ID):
            return "👑 Лакшери"
    except Exception:
        pass

    # 3) fallback на БД
    try:
        queries = await ExchangeModerationQueries.create()
        row = await queries.user_flags(int(user_id))
        if row:
            if bool(row.get("is_luxury")):
                return "👑 Лакшери"
            if bool(row.get("is_trusted")):
                return "🤝 Доверенный"
    except Exception:
        pass

    badge = await _uid_verification_badge(int(user_id))
    return f"👤 Обычный • {badge}"


# auctions.py

async def _send_user_exchange_confirmation(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = _h(preview.get("hero_name") or "—")
    card_name = _h(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            deck_line = f"🧩 {int(deck_id)} колода — {name}" if name else f"🧩 {int(deck_id)} колода"
        except Exception:
            deck_line = f"🧩 {int(deck_id)} колода"

    # редкость
    rn = _rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    # подарок/профит
    obtain_type, obtain_amount = _exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = _h(preview.get("story") or "—")
    quote = _h(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅ можно пересылать/скринить
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_multi(
        message: Message,
        *,
        user_id: int,
        created: list[dict],  # [{"batch_id": int, "card": dict, "price": int, "gain": int}]
        currency: str,
        comment: str,
        deck_id: int | None,
        mode: str,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # режим по-русски
    mode_key = (mode or "").strip().lower()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, mode or "—")

    # колода
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            if name:
                if name.lower().startswith(str(int(deck_id))):
                    deck_line = f"🧩 {h(name)}"
                else:
                    deck_line = f"🧩 {int(deck_id)} колода — {h(name)}"
        except Exception:
            pass

    # превью для медиа
    preview_card = (created[0].get("card") or {}) if created else {}
    file_id = (preview_card.get("image_id") or "").strip()

    # определяем: это “копии одной карты”?
    same_card = False
    if created:
        c0 = created[0].get("card") or {}
        cid0 = c0.get("card_id")
        same_card = all(((x.get("card") or {}).get("card_id") == cid0) for x in created)

    caption = (
        "✅ <b>Заявки отправлены на модерацию</b>\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Режим: <b>{_h(mode_ru)}</b>\n\n"
    )

    if same_card and created:
        c = created[0]["card"]
        hero = _h(c.get("hero_name"))
        name = _h(c.get("card_name"))
        price = int(created[0].get("price") or 0)
        caption += (
                f"Карта: <b>{hero} — {name}</b>\n"
                f"Экземпляров: <b>{len(created)}</b>\n"
                f"Стоимость (фикс.) за 1: <b>{price}</b> {cur_emoji}\n\n"
                "IDs лотов: " + ", ".join(f"<code>{int(x['batch_id'])}</code>" for x in created) + "\n"
        )
    else:
        caption += f"Создано лотов: <b>{len(created)}</b>\n\n"
        for x in created:
            bid = int(x["batch_id"])
            c = x.get("card") or {}
            hero = _h(c.get("hero_name"))
            name = _h(c.get("card_name"))
            rn = _rarity_norm(c.get("rarity") or c.get("rarity_norm"))
            price = int(x.get("price") or 0)
            caption += f"• <b>{hero} — {name}</b> ({_h(rn)}) → №<code>{bid}</code> • <b>{price}</b> {cur_emoji}\n"

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_copies(
        message: Message,
        *,
        batch_ids: list[int],
        user_id: int,
        card: dict,
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    status_line = await _format_user_status(message.bot, int(user_id))

    hero = h(card.get("hero_name") or "—")
    name = h(card.get("card_name") or "—")

    rn = _rarity_norm(card.get("rarity") or card.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {h(rn or '—')}"

    sold = "—"
    try:
        if card.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(card["card_id"])) or 0))
    except Exception:
        pass

    ot, oa = _exchange_gain_for_card(card)
    gift_line = f"🎁 +{int(oa)} {_gift_emoji(ot)}" if oa else "—"

    story = h(card.get("story") or "—")
    quote = h(card.get("quote") or "—")

    # колода красиво
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            nm = (d.get("name") or "").strip() if d else ""
            if nm:
                deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                    str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
        except Exception:
            pass

    ids_line = ", ".join(str(int(x)) for x in batch_ids)

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лоты биржи №<b>{h(ids_line)}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {name}\n"
        f"Экземпляров: <b>{len(batch_ids)}</b>\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {h(sold)}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {h(comment.strip())}"

    file_id = (card.get("image_id") or "").strip()
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )
        if sent:
            return

    await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_deck_split(
        message: Message,
        *,
        created: list[tuple[int, dict, int]],  # (batch_id, card, price)
        user_id: int,
        deck_id: int,
) -> None:
    status_line = await _format_user_status(message.bot, int(user_id))

    deck_line = f"🧩 {int(deck_id)} колода"
    try:
        d = await get_deck_by_id(int(deck_id))
        nm = (d.get("name") or "").strip() if d else ""
        if nm:
            deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
    except Exception:
        pass

    lines = [
        "✅ <b>Заявки отправлены на модерацию</b>\n",
        f"Статус пользователя: {status_line}",
        f"Колода: {deck_line}",
        "Режим: <b>Разбор колоды</b>\n",
        f"Создано лотов: <b>{len(created)}</b>\n",
    ]

    for bid, c, price in created:
        hero = h(c.get("hero_name") or "—")
        name = h(c.get("card_name") or "—")
        rn = _rarity_norm(c.get("rarity") or c.get("rarity_norm"))
        ot, oa = _exchange_gain_for_card(c)
        gain = f"+{int(oa)}{_gift_emoji(ot)}" if oa else "—"
        lines.append(f"• №<code>{int(bid)}</code> {hero} — {name} ({h(rn)}) • <b>{int(price)}</b>💎 • {gain}")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _send_user_exchange_confirmation_card(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    # Это твоя старая логика “по карте” (Ливий, редкость, цитата, история…)
    # Можно просто перенести сюда код из старого дубля, который сейчас у тебя в auctions.py.
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = _h(preview.get("hero_name") or "—")
    card_name = _h(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя
    try:
        lux = int(await get_user_luxury_level(message.bot, user_id) or 0)
    except Exception:
        lux = 0
    status_line = f"👑 Лакшери {lux}" if lux > 0 else "👤 Обычный"

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            if d and d.get("name"):
                deck_line = f"🧩 {deck_id} колода — {d['name']}"
            else:
                deck_line = f"🧩 {deck_id} колода"
        except Exception:
            deck_line = f"🧩 {deck_id} колода"

    # редкость
    rn = _rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    obtain_type, obtain_amount = _exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = _h(preview.get("story") or "—")
    quote = _h(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        try:
            sent = await _answer_media_any(message, file_id, caption=caption, reply_markup=None)
        except Exception:
            sent = None

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# =======================
# 🆔 file_id helper (reply to media)
# =======================
@router.message(Command("fileid"), F.chat.type == "private")
async def cmd_fileid(message: Message):
    """Админская команда: ответь на медиа и получи file_id/unique_id.
    Работает для video/animation/photo/document/voice/video_note/sticker.
    """
    if not await is_admin(int(message.from_user.id)):
        return

    rep = message.reply_to_message
    if not rep:
        await message.answer("Ответь на сообщение с медиа (видео/фото/гиф/документ) и напиши /fileid.")
        return

    kind = None
    file_id = None
    unique_id = None

    if rep.video:
        kind = "video"
        file_id = rep.video.file_id
        unique_id = rep.video.file_unique_id
    elif rep.animation:
        kind = "animation"
        file_id = rep.animation.file_id
        unique_id = rep.animation.file_unique_id
    elif rep.photo:
        kind = "photo"
        ph = rep.photo[-1]
        file_id = ph.file_id
        unique_id = ph.file_unique_id
    elif rep.document:
        kind = "document"
        file_id = rep.document.file_id
        unique_id = rep.document.file_unique_id
    elif rep.voice:
        kind = "voice"
        file_id = rep.voice.file_id
        unique_id = rep.voice.file_unique_id
    elif rep.video_note:
        kind = "video_note"
        file_id = rep.video_note.file_id
        unique_id = rep.video_note.file_unique_id
    elif rep.sticker:
        kind = "sticker"
        file_id = rep.sticker.file_id
        unique_id = rep.sticker.file_unique_id

    if not file_id:
        await message.answer("Не вижу медиа в ответе. Нужен reply на видео/фото/гиф/документ.")
        return

    await message.answer(
        f"✅ <b>{kind}</b>\n"
        f"<b>file_id:</b> <code>{h(file_id, '')}</code>\n"
        f"<b>unique_id:</b> <code>{h(unique_id, '')}</code>",
        parse_mode="HTML",
    )


# admin_actions.py

def format_exchange_approved_log(*,
                                 created_at_msk: str,
                                 batch_id: int,
                                 admin_html: str,
                                 user_html: str,
                                 deck_title: str,
                                 mode: str,
                                 items_count: int,
                                 price: int | None,
                                 currency: str,
                                 has_proof: bool,
                                 comment: str | None,
                                 items_preview: list[str] | None = None) -> str:
    mode_key = (mode or "").strip().lower()
    mode_lbl = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, (mode or "—"))

    cur_print = (currency or "алмазы").strip()
    cur = cur_print.lower()
    cur_emoji = _cur_emoji(cur)

    proof_line = "✅ Да" if has_proof else "❌ Нет"
    price_line = f"{int(price)} {cur_emoji} ({tg_clean(cur_print)})" if price is not None else f"— {cur_emoji} ({tg_clean(cur_print)})"

    cmt = (comment or "").strip()
    if not cmt:
        cmt = "-"

    preview_lines = items_preview or []
    items_block = ""
    if preview_lines:
        items_block = "\n\n🃏 <b>Состав (превью):</b>\n" + "\n".join(preview_lines)

    return (
        "✅ <b>Биржа: заявка одобрена</b>\n"
        f"🕒 {tg_clean(created_at_msk)} (МСК)\n"
        f"🧑‍💼 Админ: {admin_html}\n"
        f"👤 Пользователь: {user_html}\n"
        f"🆔 Batch: <code>{int(batch_id)}</code>\n\n"
        f"📚 Колода: <b>{tg_clean(deck_title)}</b>\n"
        f"🎛 Режим: <b>{tg_clean(mode_lbl)}</b>\n"
        f"🃏 Карт: <b>{int(items_count)}</b>\n"
        f"💰 Цена: <b>{tg_clean(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{tg_clean(cmt)}</i>"
        f"{items_block}\n\n"
        "Действие: <code>exchange_approve</code> через бота"
    )

# Public compatibility aliases. Cross-feature imports must use these names.
fmt_dt_msk = _fmt_dt_msk
user_link = _user_link
