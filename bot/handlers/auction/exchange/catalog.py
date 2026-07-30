from __future__ import annotations

"""Exchange flow component extracted during refactoring phase 7."""

import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.handlers.admin.helper.new.admin_actions import send_admin_log
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.exchange_media import get_exchange_cover_media as _get_exchange_cover_media
from bot.services.exchanges import ExchangeService
from bot.services.exchange_catalog import ExchangeCatalogService
from bot.telegram.media import safe_send_media
from db.db import get_deck_by_id, get_exchange_batch_by_id, get_exchange_items_by_batch_id, is_admin, is_luxury_user

router = Router(name="auction_exchange_catalog")

from .common import EX_MODE_CARD, EX_MODE_DECK, EX_MODE_DECK_SPLIT, _cur_emoji, _currency_emoji, _currency_label, _fmt_dt_msk, _get_exchange_deck_ids, _user_link, currency_to_emoji

def _kb_exchange_approved_root() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 По колодам", callback_data="ex_appr:decks")
    kb.button(text="📄 Списком (все лоты)", callback_data="ex_appr:list:all:0")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


async def _q_exchange_approved_decks() -> list[dict]:
    deck_ids = await _get_exchange_deck_ids()
    service = await ExchangeCatalogService.create()
    return await service.approved_decks(deck_ids)


def _kb_exchange_approved_decks(decks: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for d in decks:
        deck_id = int(d.get("deck_id") or 0)
        name = (d.get("deck_name") or "").strip()
        cnt = int(d.get("cnt") or 0)

        title = f"{deck_id} колода" + (f" — {name}" if name else "")
        kb.button(
            text=f"📚 {title} • {cnt}",
            callback_data=f"ex_appr:deck:{deck_id}"
        )

    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(1)
    return kb.as_markup()


def _kb_exchange_approved_cards(deck_id: int, cards: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in cards:
        card_id = int(c.get("card_id") or 0)
        card_name = (c.get("card_name") or "—").strip()
        hero_name = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)
        kb.button(
            text=f"🃏 {card_name} — {hero_name} • {cnt}",
            callback_data=f"ex_appr:card:{deck_id}:{card_id}",
        )
    kb.button(text="⬅️ Назад", callback_data="ex_appr:decks")
    kb.adjust(1)
    return kb.as_markup()


async def _q_exchange_approved_batches_by_card(deck_id: int, card_id: int) -> list[int]:
    service = await ExchangeCatalogService.create()
    return await service.approved_batches_by_card(deck_id, card_id)


async def _q_exchange_deck_cards_with_counts(deck_id: int) -> list[dict]:
    service = await ExchangeCatalogService.create()
    return await service.deck_cards_with_counts(deck_id)


def _kb_exchange_approved_batches(deck_id: int, card_id: int, batch_ids: list[int], page: int) -> InlineKeyboardMarkup:
    page = max(0, int(page or 0))
    per_page = 12
    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)
    page = min(page, last)

    start = page * per_page
    chunk = batch_ids[start:start + per_page]

    kb = InlineKeyboardBuilder()

    # кнопки лотов по batch_id
    for bid in chunk:
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{bid}")

    # навигация по списку batch_id
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:card:{deck_id}:{card_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:card:{deck_id}:{card_id}:{page + 1}")

    # режимы просмотра
    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="📄 Показать списком", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:0")
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}")
    kb.adjust(3, 3, 3, 3, 1, 1)
    return kb.as_markup()


def _kb_exchange_approved_lot_actions(*, batch_id: int, back_cb: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Редактировать", callback_data=f"ex_edit:{batch_id}")
    kb.button(text="🗑 Удалить", callback_data=f"ex_del:{batch_id}")
    kb.button(text="⬅️ Назад", callback_data=back_cb)
    kb.adjust(2, 1)
    return kb.as_markup()


async def _format_exchange_approved_lot_caption(batch_id: int) -> str:
    b = await get_exchange_batch_by_id(int(batch_id))
    if not b:
        return f"🛒 <b>Биржа</b>\n\nBatch: <code>{int(batch_id)}</code>\n⚠️ Лот не найден."

    # базовые поля
    deck_id = int(b.get("deck_id") or 0)
    d = await get_deck_by_id(deck_id)
    deck_name = (d.get("name") or "").strip() if d else ""
    deck_line = deck_name or (f"{deck_id} колода" if deck_id else "—")

    mode = (b.get("mode") or EX_MODE_CARD).strip().lower()
    if mode == EX_MODE_DECK_SPLIT:
        mode = EX_MODE_CARD

    mode_ru = {
        EX_MODE_CARD: "Одна карта",
        EX_MODE_DECK: "Колода целиком",
    }.get(mode, mode or "—")

    currency = (b.get("currency") or "алмазы").strip()
    cur_emoji = currency_to_emoji(currency) or "💎"
    price = b.get("price")
    price_line = f"{int(price)} {cur_emoji}" if price is not None else f"— {cur_emoji}"

    proof_id = (b.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    comment = (b.get("comment") or "").strip() or "—"

    # статус пользователя (обычный/лакшери)
    user_id = int(b.get("user_id") or 0)
    lux = False
    try:
        lux = bool(await is_luxury_user(user_id))
    except Exception:
        lux = False
    user_status = "👑 Лакшери" if lux else "👤 Обычный"
    # кликабельный пользователь (username или ID)
    uname = (b.get("username") or "").strip().lstrip("@")
    if uname:
        user_label = f"@{uname}"
    elif user_id:
        user_label = f"ID {user_id}"
    else:
        user_label = "—"

    if user_id and user_label != "—":
        user_link = f'<a href="tg://user?id={user_id}">{html.escape(user_label)}</a>'
    elif uname:
        # на всякий случай, если user_id вдруг пустой
        user_link = f'<a href="https://t.me/{html.escape(uname)}">{html.escape(user_label)}</a>'
    else:
        user_link = html.escape(user_label)

    created_at = b.get("created_at")
    try:
        if isinstance(created_at, datetime):
            msk = ZoneInfo("Europe/Moscow")
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
            created_msk = created_at.astimezone(msk).strftime("%d.%m.%Y %H:%M (МСК)")
        else:
            created_msk = "—"
    except Exception:
        created_msk = "—"

    # состав + дополнительные поля по первой карте
    items = []
    try:
        items = await get_exchange_items_by_batch_id(int(batch_id))
    except Exception:
        items = []

    items_count = len(items)
    first = items[0] if items else {}

    hero = (first.get("hero_name") or "").strip()
    card_name = (first.get("card_name") or "").strip()

    rarity = (first.get("rarity") or first.get("rarity_norm") or "").strip()
    if rarity:
        rarity_line = f"{rarity}"
    else:
        rarity_line = "—"

    story = (first.get("story") or "").strip() or "—"
    quote = (first.get("quote") or "").strip() or "—"

    # подарок/экономика (если есть поля)
    gift_line = "—"
    try:
        obtain_type = (first.get("obtain_type") or "").strip()
        obtain_amount = first.get("obtain_amount")
        if obtain_type and obtain_amount is not None:
            gift_line = f"+{int(obtain_amount)} {currency_to_emoji(obtain_type) or '💎'}"
    except Exception:
        pass

    # состав строками
    comp_lines: list[str] = []
    if items:
        for i, it in enumerate(items[:12], start=1):
            hn = (it.get("hero_name") or "—").strip()
            cn = (it.get("card_name") or "—").strip()
            comp_lines.append(f"{i}. {hn} — {cn}")
        if len(items) > 12:
            comp_lines.append(f"…и ещё {len(items) - 12}")
    comp_block = "\n".join(comp_lines) if comp_lines else "—"

    header = "✅ <b>Биржа • Лот принят</b>"
    if hero or card_name:
        header = f"✅ <b>Биржа • Лот принят</b>\n{html.escape(hero)} — {html.escape(card_name)}" if hero else f"✅ <b>Биржа • Лот принят</b>\n{html.escape(card_name)}"

    text = (
        f"{header}\n"
        f"🆔 Batch: <code>{int(batch_id)}</code>\n"
        f"🕒 Дата заявки: {html.escape(created_msk)}\n\n"
        f"📚 Колода: <b>{html.escape(deck_line)}</b>\n"
        f"🎛 Режим: <b>{html.escape(mode_ru)}</b>\n"
        f"🃏 Карт: <b>{items_count}</b>\n"
        f"💰 Цена: <b>{html.escape(price_line)}</b>\n"
        f"📎 Пруф: <b>{proof_line}</b>\n"
        f"👤 Статус пользователя: <b>{user_status}</b>\n"
        f"👤 Пользователь: {user_link}\n"
        f"💬 Комментарий: <b>{html.escape(comment)}</b>\n\n"
        f"🏷 Редкость: <b>{html.escape(rarity_line)}</b>\n"
        f"🎁 При получении в подарок даёт: <b>{html.escape(gift_line)}</b>\n"
        f"📜 История: {html.escape(story)}\n"
        f"💬 Цитата: {html.escape(quote)}\n\n"
        f"🧾 <b>Состав:</b>\n{html.escape(comp_block)}"
    )
    return text


def _kb_exchange_view_cards(deck_id: int, cards: list[dict], whole_deck_count: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # показываем “колода целиком” только если она реально есть
    if int(whole_deck_count or 0) > 0:
        kb.button(
            text=f"📚 Колода целиком ({int(whole_deck_count)})",
            callback_data=f"ex_view:deck_whole:{int(deck_id)}",
        )

    for c in cards or []:
        card_id = int(c.get("card_id") or 0)
        cn = (c.get("card_name") or "—").strip()
        hn = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)

        kb.button(
            text=f"🃏 {cn} — {hn} ({cnt})",
            callback_data=f"ex_view:card:{int(deck_id)}:{card_id}",
        )

    kb.button(text="⬅️ Назад", callback_data="ex_view:decks")
    kb.adjust(1)
    return kb.as_markup()


def _kb_exchange_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:deck:{deck_id}")
    kb.adjust(1)
    return kb.as_markup()


EX_WHOLE_DECK_MODE = "deck"  # строго колода целиком


EX_WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")


async def _q_exchange_has_whole_deck_lot(deck_id: int) -> bool:
    service = await ExchangeCatalogService.create()
    return await service.has_whole_deck_lot(deck_id)


async def _q_exchange_whole_deck_count(deck_id: int) -> int:
    service = await ExchangeCatalogService.create()
    return await service.whole_deck_count(deck_id)


@router.callback_query(F.data.startswith("ex_appr:deck:"))
@admin_only
async def ex_appr_deck(call: types.CallbackQuery):
    # ex_appr:deck:<deck_id>
    parts = (call.data or "").split(":")
    deck_id = int(parts[2])

    cards = await _q_exchange_approved_cards_by_deck(deck_id)
    whole_deck_count = await _q_exchange_whole_deck_count(deck_id)

    if not cards and int(whole_deck_count or 0) <= 0:
        await _safe_edit_text_or_caption(
            call.message,
            text="Принятых лотов по этой колоде нет.",
            reply_markup=_kb_exchange_approved_decks(await _q_exchange_approved_decks()),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text=(
            "🛒 <b>Биржа</b>\n"
            f"📚 Колода: <b>{deck_id}</b>\n\n"
            "Выберите:"
        ),
        reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
    )
    await call.answer()


async def _q_exchange_approved_whole_deck_batch_ids(deck_id: int) -> list[int]:
    service = await ExchangeCatalogService.create()
    return await service.approved_whole_deck_batch_ids(deck_id)


def _kb_exchange_approved_whole_batches(deck_id: int, batch_ids: list[int], page: int) -> InlineKeyboardMarkup:
    page = max(0, int(page or 0))
    per_page = 12
    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)

    if page > last:
        page = last

    start = page * per_page
    chunk = batch_ids[start:start + per_page]

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"ID {bid}", callback_data=f"ex_appr:lot:{deck_id}:0:{bid}")

    kb.adjust(3)

    # пагинация
    if total > per_page:
        kb.row(
            types.InlineKeyboardButton(
                text="⬅️" if page > 0 else " ",
                callback_data=f"ex_appr:deck_whole:{deck_id}:{page - 1}" if page > 0 else "noop",
            ),
            types.InlineKeyboardButton(text=f"{page + 1}/{last + 1}", callback_data="noop"),
            types.InlineKeyboardButton(
                text="➡️" if page < last else " ",
                callback_data=f"ex_appr:deck_whole:{deck_id}:{page + 1}" if page < last else "noop",
            ),
        )

    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}"))
    return kb.as_markup()


@router.callback_query(F.data.startswith("ex_appr:deck_whole:"))
@admin_only
async def ex_appr_deck_whole(call: types.CallbackQuery):
    # ex_appr:deck_whole:<deck_id>:<page>
    parts = (call.data or "").split(":")
    deck_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    batch_ids = await _q_exchange_approved_whole_deck_batch_ids(deck_id)
    if not batch_ids:
        await _safe_edit_text_or_caption(
            call.message,
            text="Нет принятых лотов «колода целиком».",
            reply_markup=_kb_exchange_approved_deck_menu(deck_id, await _q_exchange_approved_cards_by_deck(deck_id), 0),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text=(
            "📚 <b>Биржа → Колода целиком</b>\n\n"
            "Выбери лот по Batch-ID:"
        ),
        reply_markup=_kb_exchange_approved_whole_batches(deck_id, batch_ids, page),
    )
    await call.answer()


async def get_exchange_card_info(card_id: int) -> dict:
    service = await ExchangeCatalogService.create()
    return await service.card_info(card_id)


def _kb_exchange_view_batches(deck_id: int, card_id: int, batch_ids: list[int], page: int = 0):
    per_page = 12
    total = len(batch_ids)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    s = page * per_page
    e = s + per_page
    chunk = batch_ids[s:e]

    kb = InlineKeyboardBuilder()

    for bid in chunk:
        kb.button(text=f"ID {bid}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{bid}")
    kb.adjust(3)

    if pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_view:card:{deck_id}:{card_id}:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="ex_view:noop"))
        if page < pages - 1:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_view:card:{deck_id}:{card_id}:{page + 1}"))
        kb.row(*row)

    kb.row(InlineKeyboardButton(text="📄 Показать списком", callback_data=f"ex_view:card_list:{deck_id}:{card_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_view:deck:{deck_id}"))
    return kb.as_markup()


@router.callback_query(F.data.startswith("ex_view:deck:"))
async def ex_view_deck(call: types.CallbackQuery):
    if await is_admin(call.from_user.id):
        raise SkipHandler

    deck_id = int(call.data.split(":")[2])
    await _render_exchange_deck(call, deck_id)
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:deck:"))
@admin_only
async def ex_view_deck_admin(call: types.CallbackQuery):
    deck_id = int(call.data.split(":")[2])
    await _render_exchange_deck(call, deck_id)
    await call.answer()


@router.callback_query(F.data == "ex_view:noop")
async def ex_view_noop(call: types.CallbackQuery):
    await call.answer()


async def _q_exchange_approved_cards_by_deck(deck_id: int) -> list[dict]:
    service = await ExchangeCatalogService.create()
    return await service.approved_cards_by_deck(deck_id)


async def _q_exchange_card_batches(deck_id: int, card_id: int, limit: int = 80) -> list[dict]:
    service = await ExchangeCatalogService.create()
    return await service.card_batches(deck_id, card_id, limit=limit)


@router.callback_query(F.data.startswith("ex_appr:card:"))
@admin_only
async def ex_appr_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0

    # card_id=0 => колода целиком
    if card_id == 0:
        rows = await _q_exchange_whole_deck_batches(deck_id, limit=5000)
        batch_ids = [int(r["batch_id"]) for r in rows]

        if not batch_ids:
            cards = await _q_exchange_approved_cards_by_deck(deck_id)
            whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
            await _safe_edit_text_or_caption(
                call.message,
                text="Нет принятых лотов <b>колодой целиком</b>.",
                reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
            )
            await call.answer()
            return

        total = len(batch_ids)
        per_page = 12
        last = max(0, (total - 1) // per_page)
        page = max(0, min(page, last))
        chunk = batch_ids[page * per_page: page * per_page + per_page]

        # текст как у тебя на скрине
        lines = [
            "✅ <b>Биржа — Колода целиком</b>",
            f"📚 <b>Колода:</b> {deck_id}",
            f"Страница: {page + 1}/{last + 1} • Всего: {total}",
            "",
            "Выбери лот по Batch-ID:",
        ]

        # короткий список строк по текущей странице (опционально, но удобно)
        row_by_id = {int(r["batch_id"]): r for r in rows}
        for bid in chunk:
            r = row_by_id.get(int(bid), {})
            price = r.get("price")
            cur = r.get("currency") or ""
            uname = (r.get("username") or "").strip()
            cur_e = _cur_emoji(cur)
            price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
            who = f"@{uname}" if uname else "—"
            lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who}")

        await _safe_edit_text_or_caption(
            call.message,
            text="\n".join(lines),
            reply_markup=_kb_exchange_approved_batches(deck_id, card_id, batch_ids, page),
        )
        await call.answer()
        return

    # обычная карта
    batch_ids = await _q_exchange_approved_batches_by_card(deck_id, card_id)
    if not batch_ids:
        cards = await _q_exchange_approved_cards_by_deck(deck_id)
        whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
        await _safe_edit_text_or_caption(
            call.message,
            text="Нет принятых лотов по этой карте.",
            reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text="Выбери лот по Batch-ID:",
        reply_markup=_kb_exchange_approved_batches(deck_id, card_id, batch_ids, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:lot:"))
@admin_only
async def ex_appr_lot_show(call: types.CallbackQuery):
    # ex_appr:lot:<deck_id>:<card_id>:<batch_id>
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    batch_id = int(parts[4])

    caption = await _format_exchange_approved_lot_caption(batch_id)

    if card_id == 0:
        back_cb = f"ex_appr:deck_whole:{deck_id}:0"
    else:
        back_cb = f"ex_appr:card:{deck_id}:{card_id}:0"

    kb = _kb_exchange_approved_lot_actions(batch_id=batch_id, back_cb=back_cb)

    media_id = None
    kind = "photo"
    try:
        cover_id, cover_kind = await _get_exchange_cover_media(batch_id)
        if cover_id:
            media_id = cover_id
            kind = cover_kind
    except Exception:
        media_id = None

    if media_id:
        try:
            await safe_send_media(
                call.bot,
                chat_id=call.message.chat.id,
                file_id=str(media_id),
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
                protect_content=False,
            )
        except Exception:
            await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:list:all:"))
@admin_only
async def ex_appr_list_all(call: types.CallbackQuery):
    # ex_appr:list:all:<page>
    page = int(call.data.split(":")[3])
    page = max(0, page)
    per_page = 12

    service = await ExchangeCatalogService.create()
    rows = await service.approved_lots()

    total = len(rows)
    if total <= 0:
        await call.message.edit_text(
            "Нет принятых лотов биржи.",
            reply_markup=_kb_exchange_approved_root(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    last = max(0, (total - 1) // per_page)
    page = min(page, last)
    chunk = rows[page * per_page: page * per_page + per_page]

    lines = [
        f"📄 <b>Биржа • Принятые лоты (списком)</b>\nСтраница: <b>{page + 1}/{last + 1}</b> • Всего: <b>{total}</b>\n"]
    for r in chunk:
        bid = int(r["batch_id"])
        deck_id = int(r.get("deck_id") or 0)
        deck_name = (r.get("deck_name") or "").strip()
        deck_title = f"{deck_id}" + (f" — {html.escape(deck_name)}" if deck_name else "")
        mode = (r.get("mode") or "").strip().lower()
        mode_ru = {"card": "карта", "deck": "колода", "deck_split": "разбор"}.get(mode, mode or "—")
        cur = str(r.get("currency") or "алмазы").strip()
        cur_emoji = _cur_emoji(cur.lower())
        price = r.get("price")
        price_line = f"{int(price)}{cur_emoji}" if price is not None else f"—{cur_emoji}"
        cnt = int(r.get("items_count") or 0)
        lines.append(
            f"• <code>{bid}</code> • 📚 {deck_title} • 🎛 {html.escape(mode_ru)} • 🃏 {cnt} • 💰 {html.escape(price_line)}")

    kb = InlineKeyboardBuilder()
    # быстрый выбор batch_id
    for r in chunk:
        bid = int(r["batch_id"])
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lot:0:0:{bid}")

    # навигация
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:list:all:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:list:all:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(3, 3, 3, 3, 3, 1, 1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:list:card:"))
@admin_only
async def ex_appr_list_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[3])
    card_id = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 0

    per_page = 12

    # card_id=0 => колода целиком
    if card_id == 0:
        rows = await _q_exchange_whole_deck_batches(deck_id, limit=5000)
        batch_ids = [int(r["batch_id"]) for r in rows]

        if not batch_ids:
            await call.message.edit_text("Нет принятых лотов <b>колодой целиком</b>.", parse_mode="HTML")
            await call.answer()
            return

        total = len(batch_ids)
        last = max(0, (total - 1) // per_page)
        page = max(0, min(page, last))
        chunk = batch_ids[page * per_page: page * per_page + per_page]

        row_by_id = {int(r["batch_id"]): r for r in rows}

        lines = [
            "✅ <b>Биржа — Колода целиком</b>",
            f"📚 <b>Колода:</b> {deck_id}",
            f"Страница: {page + 1}/{last + 1} • Всего: {total}",
            "",
        ]
        for bid in chunk:
            r = row_by_id.get(int(bid), {})
            price = r.get("price")
            cur = r.get("currency") or ""
            uname = (r.get("username") or "").strip()
            cur_e = _cur_emoji(cur)
            price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
            who = f"@{uname}" if uname else "—"
            lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who}")

        kb = InlineKeyboardBuilder()
        for bid in chunk:
            kb.button(text=f"🆔 {int(bid)}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{int(bid)}")

        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="⬅️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page - 1}")
        nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
        if page < last:
            nav.button(text="➡️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page + 1}")

        kb.adjust(3)
        kb.row(*nav.buttons, width=3)
        kb.button(text="⬅️ Назад", callback_data=f"ex_appr:card:{deck_id}:{card_id}:0")
        kb.adjust(3, 3, 3, 3, 1)

        await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
        await call.answer()
        return

    # обычная карта (как было)
    card = await get_exchange_card_info(card_id)
    card_name = html.escape((card or {}).get("card_name") or f"ID {card_id}")
    hero_name = html.escape((card or {}).get("hero_name") or "")

    batch_ids = await _q_exchange_approved_batches_by_card(deck_id, card_id)
    if not batch_ids:
        await call.message.edit_text("Нет принятых лотов по этой карте.", parse_mode="HTML")
        await call.answer()
        return

    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)
    page = max(0, min(page, last))
    chunk = batch_ids[page * per_page: page * per_page + per_page]

    lines = [
        "✅ <b>Биржа • Принятые лоты</b>",
        f"🃏 <b>Карта:</b> {card_name}" + (f" — {hero_name}" if hero_name else ""),
        f"Страница: {page + 1}/{last + 1} • Всего: {total}",
        "",
    ]

    for bid in chunk:
        batch = await get_exchange_batch_by_id(int(bid))
        if not batch:
            continue
        price = batch.get("price")
        cur = batch.get("currency") or ""
        uname = (batch.get("username") or "").strip()
        mode = (batch.get("mode") or "").strip()

        cur_e = _cur_emoji(cur)
        price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
        who = f"@{uname}" if uname else "—"

        mode_ru = {
            "card": "Карта",
            "deck_split": "Карта",
            "deck": "Колода целиком",
            "whole_deck": "Колода целиком",
            "full_deck": "Колода целиком",
        }.get(mode, mode or "—")

        lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who} • {mode_ru}")

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"🆔 {int(bid)}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{int(bid)}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:card:{deck_id}:{card_id}:0")
    kb.adjust(3, 3, 3, 3, 1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_del:"))
@admin_only
async def ex_appr_delete(call: types.CallbackQuery):
    batch_id = int(call.data.split(":")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Лот не найден.", show_alert=True)
        return

    try:
        service = await ExchangeService.create()
        await service.delete(
            batch_id,
            moderator_id=call.from_user.id,
            moderator_username=call.from_user.username or call.from_user.full_name,
            comment="deleted_by_admin",
        )
    except Exception:
        await call.answer("Не удалось удалить.", show_alert=True)
        return

    # лог как у расписания
    try:
        when_msk = _fmt_dt_msk(datetime.now(timezone.utc))
        admin_html = _user_link(call.from_user.id, call.from_user.username)
        user_id = int(batch.get("user_id") or 0)
        user_html = _user_link(user_id, batch.get("username")) if user_id else "—"
        log_text = (
            "🗑 <b>Биржа: лот удалён</b>\n"
            f"🕒 {html.escape(when_msk)} (МСК)\n"
            f"🧑‍💼 Админ: {admin_html}\n"
            f"👤 Пользователь: {user_html}\n"
            f"🆔 Batch: <code>{batch_id}</code>\n\n"
            "Действие: <code>exchange_delete</code> через бота"
        )
        await send_admin_log(call.bot, log_text)
    except Exception:
        pass

    await call.answer("Удалено ✅", show_alert=False)


@router.callback_query(F.data.startswith("ex_edit:"))
@admin_only
async def ex_appr_edit_entry(call: types.CallbackQuery, state: FSMContext):
    # точка входа в редактор принятой биржи
    # (дальше ты уже делал FSM на pending: цену/валюту/коммент/пруф/режим и т.д.)
    batch_id = int(call.data.split(":")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Лот не найден.", show_alert=True)
        return

    await state.clear()
    await state.update_data(exchange_batch_id=batch_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="🎛 Тип (режим)", callback_data=f"ex_edit_mode:{batch_id}")
    kb.button(text="💰 Цена", callback_data=f"ex_edit_price:{batch_id}")
    kb.button(text="💱 Валюта", callback_data=f"ex_edit_currency:{batch_id}")
    kb.button(text="💬 Комментарий", callback_data=f"ex_edit_comment:{batch_id}")
    kb.button(text="📸 Пруф", callback_data=f"ex_edit_proof:{batch_id}")
    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(2, 2, 1, 1)

    await call.message.answer(
        f"✏️ <b>Редактор биржи</b>\nBatch: <code>{batch_id}</code>\n\n"
        "Выбери, что редактировать:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


async def _safe_edit_text_or_caption(
        msg: types.Message,
        *,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Правильно редактируем:
    - если это текстовое сообщение -> edit_text
    - если это фото/видео с подписью -> edit_caption
    - иначе -> шлём новым сообщением
    """
    try:
        if msg.text is not None:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
            return
        if msg.caption is not None:
            await msg.edit_caption(text, parse_mode="HTML", reply_markup=reply_markup)
            return
    except Exception:
        pass

    await msg.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def _q_exchange_whole_deck_batches(deck_id: int, limit: int = 50) -> list[dict]:
    service = await ExchangeCatalogService.create()
    return await service.whole_deck_batches(deck_id, limit=limit)


async def _q_exchange_deck_total_cards(deck_id: int) -> int:
    service = await ExchangeCatalogService.create()
    return await service.deck_total_cards(deck_id)


async def _q_exchange_batch_items_count(batch_id: int) -> int:
    service = await ExchangeCatalogService.create()
    return await service.batch_items_count(batch_id)


@router.callback_query(F.data.startswith("ex_view:card_list:"))
async def ex_view_card_list(call: types.CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) < 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    card_id = int(parts[3])

    # если есть “колода целиком” — карточные лоты скрыты
    if await _q_exchange_whole_deck_count(deck_id) > 0:
        await call.message.edit_text(
            "🛒 Биржа → Лоты по карте (списком)\n\nКарточные лоты скрыты из-за «колоды целиком».",
            reply_markup=_kb_back_to_deck(deck_id),
        )
        await call.answer()
        return

    rows = await _q_exchange_card_batches(deck_id, card_id, limit=80)
    if not rows:
        await call.message.edit_text(
            "🛒 Биржа → Лоты по карте (списком)\n\nЛотов нет.",
            reply_markup=_kb_back_to_card(deck_id, card_id),
        )
        await call.answer()
        return

    lines = ["🛒 Биржа → Лоты по карте (списком)\n"]
    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        price = r.get("price")
        cur = _currency_label(r.get("currency") or "алмазы")
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id:{int(r.get('user_id') or 0)}"
        lines.append(f"• #{batch_id} — {who} — {price} {cur}")

    await call.message.edit_text("\n".join(lines), reply_markup=_kb_back_to_card(deck_id, card_id))
    await call.answer()


async def _render_exchange_deck(call: types.CallbackQuery, deck_id: int) -> None:
    deck = await get_deck_by_id(deck_id)
    deck_title = html.escape((deck or {}).get("title") or (deck or {}).get("name") or f"Колода {deck_id}")

    whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
    cards = await _q_exchange_approved_cards_by_deck(deck_id)

    lines = [
        "🛒 <b>Биржа</b>",
        f"📚 <b>Колода:</b> {deck_title}",
        "",
        "<b>Выберите:</b>",
    ]
    if whole_deck_count:
        lines.append("📦 Есть лоты «колода целиком».")
    # карточные лоты НЕ скрываем, просто показываем всё вместе

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_exchange_view_cards(deck_id, cards, whole_deck_count),
    )


def _kb_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_back_to_card(deck_id: int, card_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:card:{int(deck_id)}:{int(card_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_back_to_decks() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="ex_view:decks")
    kb.adjust(1)
    return kb.as_markup()


async def _q_exchange_decks_with_approved() -> list[int]:
    deck_ids = await _get_exchange_deck_ids()
    service = await ExchangeCatalogService.create()
    return await service.decks_with_approved(deck_ids)


@router.callback_query(F.data == "ex_view:decks")
async def ex_view_decks(call: types.CallbackQuery):
    decks = await _q_exchange_decks_with_approved()
    kb = InlineKeyboardBuilder()
    for d in decks:
        kb.button(text=f"📚 Колода {d}", callback_data=f"ex_view:deck:{d}")
    kb.adjust(2 if len(decks) >= 2 else 1)
    kb.button(text="⬅️ Назад", callback_data="admin_panel")  # если нужно, подстрой под свой “назад”
    kb.adjust(2 if len(decks) >= 2 else 1, 1)

    await call.message.edit_text(
        "🛒 <b>Биржа</b>\n\nВыберите колоду:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:deck_whole:"))
async def ex_view_deck_whole(call: types.CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])

    rows = await _q_exchange_whole_deck_batches(deck_id, limit=50)
    if not rows:
        await call.message.edit_text(
            f"🛒 <b>Биржа → Колода целиком</b>\n\n📚 Колода: <b>{deck_id}</b>\n\nЛотов нет.",
            parse_mode="HTML",
            reply_markup=_kb_back_to_deck(deck_id),
        )
        await call.answer()
        return

    lines = ["🛒 <b>Биржа → Колода целиком</b>\n"]
    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        price = r.get("price")
        cur = _currency_label(r.get("currency") or "алмазы")
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id:{int(r.get('user_id') or 0)}"
        lines.append(f"• <b>#{batch_id}</b> — {price} {cur} — {who}")

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_back_to_deck(deck_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:card:"))
async def ex_view_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    page = int(parts[4]) if len(parts) >= 5 else 0

    rows = await _q_exchange_card_batches(deck_id, card_id)
    batch_ids = [int(r["batch_id"]) for r in rows]

    card = await get_exchange_card_info(card_id)
    card_name = html.escape((card or {}).get("card_name") or f"ID {card_id}")
    hero_name = html.escape((card or {}).get("hero_name") or "")

    lines = [
        "🛒 <b>Биржа → Карта → Лоты</b>",
        f"📚 <b>Колода:</b> {deck_id}",
        f"🃏 <b>Карта:</b> {hero_name + ' — ' if hero_name else ''}{card_name}",
        "",
        "Выбери лот по Batch-ID:",
    ]

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_exchange_view_batches(deck_id, card_id, batch_ids, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:card_dump:"))
async def ex_view_card_dump(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])

    rows = await _q_exchange_card_batches(deck_id, card_id)
    if not rows:
        await call.message.edit_text("Нет принятых лотов по этой карте.", parse_mode="HTML")
        await call.answer()
        return

    lines = [
        "🛒 <b>Биржа → Карта → Лоты (списком)</b>",
        f"📚 <b>Колода:</b> {deck_id}",
        "",
    ]

    for r in rows[:60]:
        bid = int(r["batch_id"])
        amt = r.get("amount")
        cur = (r.get("currency") or "").strip()
        uname = (r.get("user_username") or "").strip()
        cur_emo = _currency_emoji(cur)
        price = f"{amt} {cur_emo}" if amt is not None else "—"
        who = f"@{uname}" if uname else "—"
        lines.append(f"• <b>#{bid}</b> — {price} — {who}")

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:card:{deck_id}:{card_id}:0")
    kb.adjust(1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


def _kb_exchange_approved_deck_menu(deck_id: int, cards: list[dict], whole_deck_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # "Колода целиком" отдельной кнопкой, но НИЧЕГО не прячем
    if int(whole_deck_count or 0) > 0:
        kb.button(
            text=f"📚 Колода целиком ({int(whole_deck_count)})",
            callback_data=f"ex_appr:deck_whole:{deck_id}:0",
        )

    for c in cards:
        card_id = int(c.get("card_id") or 0)
        card_name = (c.get("card_name") or "—").strip()
        hero_name = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)

        kb.button(
            text=f"🃏 {card_name} — {hero_name} • {cnt}",
            callback_data=f"ex_appr:card:{deck_id}:{card_id}:0",
        )

    kb.button(text="⬅️ Назад", callback_data="ex_appr:decks")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def ex_appr_root(call: types.CallbackQuery):
    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nОткрываю принятые лоты:",
        reply_markup=_kb_exchange_approved_root(),
    )
    await call.answer()


@router.callback_query(F.data == "ex_appr:decks")
@admin_only
async def ex_appr_decks(call: types.CallbackQuery):
    decks = await _q_exchange_approved_decks()
    if not decks:
        await _safe_edit_text_or_caption(
            call.message,
            text="🛒 <b>Биржа</b>\n\nПринятых лотов пока нет.",
            reply_markup=_kb_exchange_approved_root(),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nВыберите колоду:",
        reply_markup=_kb_exchange_approved_decks(decks),
    )
    await call.answer()


def _kb_ex_appr_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()
