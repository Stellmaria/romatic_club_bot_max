from __future__ import annotations
from bot.telegram.callback_parser import rsplit_callback_data, split_callback_data

"""Exchange flow component extracted during refactoring phase 7."""

import html
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.domain.auctions import InvalidExchangeTransition
from bot.handlers.admin.action_support.compat import format_exchange_moderation_log, notify_exchange_user_moderation, send_admin_log, show_pendinglots
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.exchange_media import get_exchange_cover_media as _get_exchange_cover_media
from bot.services.exchanges import ExchangeService
from bot.services.exchange_moderation import ExchangeModerationService
from bot.telegram.media import safe_send_media
from bot.core.legacy_config import legacy_config
from bot.legacy_fsm import ModActionFSM

router = Router(name="auction_exchange_moderation")

from .common import (
    CURRENCY_EMOJI,
    UTC,
    _cur_emoji,
    _deck_id_from_row,
    _exchange_gain_for_card,
    _exchange_price_for_card,
    _fmt_dt_msk,
    _get_exchange_deck_ids,
    _get_exchange_decks_for_menu,
    _tg_clean,
    _user_link,
    tg_clean,
)

@router.callback_query(F.data.startswith("pending_menu:"))
@admin_only
async def pending_menu_pick(call: types.CallbackQuery, state: FSMContext):
    kind = split_callback_data(call.data, ":", 1)[1].strip()
    await call.answer()

    if kind == "auction":
        await show_pendinglots(call.message)
        return

    if kind == "exchange":
        await show_pending_exchange_requests(call.message)
        return

    await call.message.answer("Неизвестный тип заявок.")


@router.callback_query(F.data.startswith("exchange_proof|"))
@admin_only
async def exchange_show_proof(call: types.CallbackQuery):
    batch_id = int(split_callback_data(call.data, "|")[1])

    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
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
    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
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
            d = await moderation.deck(deck_id)
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
        items = await moderation.raw_items(batch_id)
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

    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    items = await moderation.raw_items(batch_id)
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

    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
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

        # moderation read model: deck + item count are outside the handler SQL layer
        deck_name = None
        try:
            drow = await moderation.deck(deck_id)
            if drow:
                deck_name = (drow.get("name") or "").strip() or None
        except Exception:
            deck_name = None
        deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

        try:
            items_cnt = await moderation.item_count(batch_id)
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
    moderation = await ExchangeModerationService.create()
    if not await moderation.is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await moderation.batch(batch_id)
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
    moderation = await ExchangeModerationService.create()
    if not await moderation.is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await moderation.batch(batch_id)
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
        await moderation.log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_approve",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


async def cb_exchange_reject(call: CallbackQuery):
    await call.answer()
    moderation = await ExchangeModerationService.create()
    if not await moderation.is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(rsplit_callback_data(call.data, ":", 1)[-1])
    batch = await moderation.batch(batch_id)
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
        await moderation.log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_reject",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


PENDING_EXCHANGE_PAGE_SIZE = 5


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

    moderation = await ExchangeModerationService.create()
    total = await moderation.pending_total()
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    rows = await moderation.pending_batches(limit=limit, include_luxury=True)
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
    moderation = await ExchangeModerationService.create()
    rows = await moderation.pending_batches()


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


@router.callback_query(F.data.startswith("exchange_delete_no|"))
@admin_only
async def exchange_delete_no(call: CallbackQuery):
    await call.answer("Ок, не удаляем")


@router.callback_query(F.data.startswith("exchange_delete_yes|"))
@admin_only
async def exchange_delete_yes(call: CallbackQuery, bot: Bot):
    batch_id = int(split_callback_data(call.data, "|", 1)[1])
    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
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
    moderation = await ExchangeModerationService.create()
    batch = await moderation.batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    deck = await moderation.deck(int(batch.get("deck_id") or 0))
    deck_name = str((deck or {}).get("deck_name") or f"#{batch.get('deck_id')}")

    items = await moderation.grouped_cards(batch_id)

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
