"""Shared dependencies and pure helpers for :mod:`.admin_panel` features.

This module has no Telegram router and can be imported without registering handlers.
"""

import html


import logging


from datetime import date, datetime, timedelta


from typing import Any, cast, Optional


from zoneinfo import ZoneInfo


from aiogram import Router, types, F, Bot


from aiogram.filters import Command


from aiogram.fsm.context import FSMContext


from aiogram.fsm.state import StatesGroup, State


from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery, User, InlineKeyboardButton, InlineKeyboardMarkup


from aiogram.utils.keyboard import InlineKeyboardBuilder


from bot.handlers.admin.helper.admin_constants import (
    ADMIN_MESSAGES, CANCEL_TEXTS, BUTTONS, CURRENCY_EMOJI,
    RARITY_EMOJI, RARITY_TREASURE, RARITY_RU, ADMIN_COMMANDS_INFO
)


from bot.handlers.admin.helper.admin_keyboards import days_keyboard, months_keyboard


from bot.handlers.admin.helper.admin_service import (
    parse_auction_and_date_from_callback, get_free_slots_and_schedule_for_lot
)


from bot.handlers.admin.helper.new.Types import Owner


from bot.handlers.admin.action_support.exchange import safe_answer_photo
from bot.handlers.admin.action_support.forms import (
    add_deck_fsm_entry,
    start_add_card_fsm,
    start_edit_schedule,
    start_preview_schedule,
)
from bot.handlers.admin.action_support.moderation import (
    show_delete_requests_for_moderation,
    show_pendinglots,
)
from bot.handlers.admin.action_support.roles import _do_trusted_action, admin_add_remove
from bot.handlers.admin.action_support.transport import (
    owner_or_secret_required,
    process_universal_cancel_callback,
    send_lot_card_safe,
)
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text


from bot.handlers.admin.helper.new.formatting import format_admin_action_log, format_pending_lot


from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard  # если у тебя этот импорт уже есть


from bot.handlers.admin.helper.new.keyboards import (
    menu_keyboard, back_keyboard, time_slots_keyboard, decks_keyboard, inline_back_keyboard, decks_menu_keyboard
)


from bot.handlers.admin.helper.new.wrapper import admin_only


from bot.handlers.admin.logs_admin import send_lot_edit_log, short_media_id


from bot.services.admin_thanks import build_thanks_kb


from bot.core.time import auction_end_at_59, to_moscow


from bot.services.exchange_media import get_exchange_cover_media as _get_exchange_cover_media
from bot.services.exchange_moderation import ExchangeModerationQueries


from bot.telegram.media import bot_send_media_any as _bot_send_media_any


from bot.domain.auctions import AuctionSlotConflict, InvalidAuctionTransition, InvalidExchangeTransition


from bot.services.auction_workflows import AuctionModerationService


from bot.services.exchanges import ExchangeService


from bot.handlers.auction.exchange import currency_to_emoji


from bot.handlers.auction.exchange_moderation import (
    _media_kind_from_error,
    format_pending_exchange_batch_card,
    pending_exchange_kb,
    show_pending_exchange_requests_all,
)


from bot.handlers.auction.exchange_catalog import (
    _format_exchange_approved_lot_caption,
    _kb_exchange_approved_decks,
    _kb_exchange_approved_lot_actions,
    _kb_exchange_approved_root,
    _q_exchange_approved_decks,
    _q_exchange_whole_deck_batches,
    _safe_edit_text_or_caption,
)


from bot.utils_admin import format_log_entry


from bot.security import is_owner_or_valid_secret


from db.admin import (
    get_audit_logs,
    log_audit_action,
    is_admin,
)
from db.cards import (
    add_deck,
    get_cards_by_deck_id,
    get_all_decks,
    set_card_video_by_id,
    get_card_by_id,
    get_deck_by_id,
)
from db.auctions import (
    get_lot_by_id,
    get_lot_owners,
    get_auctions_by_date_with_owners,
    get_pending_auctions,
    count_pending_delete_requests_by_kind,
)
from db.users import (
    get_user,
    is_luxury_user,
    get_user_by_username,
)
from db.exchange import (
    get_exchange_batch_by_id,
    count_pending_exchange_batches,
    get_exchange_owners_for_cards,
    set_exchange_manual_price,
    set_exchange_manual_link,
    set_exchange_manual_winner,
    mark_exchange_manual_sent,
    reset_exchange_manual,
    get_exchange_items_by_batch_id,
    add_exchange_items_for_deck,
    get_exchange_deck_overview,
)


from bot.telegram.states import (
    AddDeckFSM,
    BroadcastFSM,
    EditScheduleFSM,
    ModActionFSM,
    PreviewScheduleFSM,
)


logger = logging.getLogger(__name__)


ADMIN_AUK_KIND_LABELS: dict[str, str] = {
    "standard": "⭐ Стандартный",
    "reverse": "✨ Обратный",
    "fast": "⚡ Быстрый",
    "free": "🪶 Свободный",
    "black": "👑 Чёрный",
    "exchange": "🛍 Биржа",
}


ADMIN_AUK_KIND_ORDER = ["standard", "reverse", "fast", "free", "black", "exchange"]


from html import escape as _h  # если уже есть escape — не дублируй


EX_CARDLIKE_MODES = ("card", "deck_split")


EX_WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")


def _admin_dict(u: types.User) -> dict:
    return {"id": u.id, "username": u.username or u.full_name}


def _short_media(v: object) -> str:
    # чтобы file_id не раздувал логи
    return short_media_id(v) if "short_media_id" in globals() else (str(v)[:12] + "…" if v else "—")


EX1_APPROVE = "ex1:approve"


EX1_REJECT = "ex1:reject"


EX1_DELETE = "ex1:delete"


EX1_DEL_YES = "ex1:del_yes"


EX1_DEL_NO = "ex1:del_no"


class ExchangeOneRejectFSM(StatesGroup):
    waiting_for_reason = State()


def _kb_exchange_one(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    row1 = []
    if has_proof:
        row1.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    row1.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    b.row(*row1)

    b.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"{EX1_APPROVE}|{batch_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{EX1_REJECT}|{batch_id}"),
    )

    b.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{EX1_DELETE}|{batch_id}"))
    return b.as_markup()


def _kb_ex1_delete_confirm(batch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=f"{EX1_DEL_YES}|{batch_id}")
    kb.button(text="⬅️ Нет", callback_data=f"{EX1_DEL_NO}|{batch_id}")
    kb.adjust(2)
    return kb.as_markup()


async def show_pending_exchange_one(message: types.Message) -> None:
    queries = await ExchangeModerationQueries.create()
    total = await queries.pending_count()
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    pending = await queries.pending(limit=1)
    row = pending[0] if pending else None
    if not row:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    batch_id = int(row.get("batch_id") or 0)
    proof_id = (row.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    items_count = int(row.get("items_count") or 0)

    try:
        lux = await is_luxury_user(int(row.get("user_id") or 0))
    except Exception:
        lux = False

    status_line = "👑 <b>Статус пользователя:</b> " + ("Лакшери" if lux else "Обычный")

    text = (
        f"🛒 <b>Заявки на биржу</b>\n"
        f"Осталось: <b>{total}</b>\n"
        f"{status_line}\n\n"
        + format_pending_exchange_batch_card(dict(row), items_count=items_count)
    )

    kb = _kb_exchange_one(batch_id, has_proof=has_proof)

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


async def _log_exchange_batch_action(
        bot: Bot,
        *,
        action_type: str,
        admin_user: types.User,
        batch_id: int,
        status: str,
) -> None:
    batch = await get_exchange_batch_by_id(int(batch_id))

    # fallback, если заявка исчезла
    if not batch:
        title = {"approved": "одобрено", "rejected": "отклонено", "deleted": "удалено"}.get(status, status)
        log_text = (
            f"🛒 <b>Биржа: {title}</b>\n"
            f"🕒 {datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
            f"Админ: <b>{admin_tag(admin_user)}</b>\n"
            f"Batch: <code>{int(batch_id)}</code>\n"
            f"⚠️ Заявка не найдена в БД\n"
            f"Действие: <code>{_h(action_type)}</code>"
        )
        await send_admin_log(bot, log_text)
        await log_audit_action(
            user_id=admin_user.id,
            action_type=action_type,
            auction_id=None,
            details=f"batch_id={batch_id} status={status} batch_not_found",
        )
        return

    # владелец
    owner_id = int(batch.get("user_id") or 0)
    owner = await get_user(owner_id)
    owner_un = (owner.get("username") if owner else None) or None
    owner_txt = _safe_user_mention(owner_id, owner_un)

    # колода
    deck_id = int(batch.get("deck_id") or 0)
    deck_name = ""
    try:
        d = await get_deck_by_id(deck_id)
        deck_name = (d.get("name") or "").strip() if d else ""
    except Exception:
        deck_name = ""

    deck_line = f"{deck_id} колода"
    if deck_name:
        deck_line = f"{deck_id} колода — {deck_name}"

    # режим по-русски
    mode = (batch.get("mode") or "card").strip()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Колода по картам (сплит)",
    }.get(mode, mode)

    # цена/валюта
    cur = (batch.get("currency") or "алмазы").strip()
    cur_emoji = currency_to_emoji(cur) or "💎"
    price = int(batch.get("price") or 0)

    # пруф
    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    proof_line = "✅ Есть" if has_proof else "❌ Нет"

    # карты в заявке
    items = []
    try:
        items = await get_exchange_items_by_batch_id(int(batch_id))
    except Exception:
        items = []
    cards_lines = []
    if items:
        # коротко: первые 6, чтобы лог не превращался в роман
        for i, it in enumerate(items[:6], start=1):
            cn = (it.get("card_name") or "—").strip()
            hn = (it.get("hero_name") or "—").strip()
            cards_lines.append(f"{i}. {hn} — {cn}")
        if len(items) > 6:
            cards_lines.append(f"…и ещё {len(items) - 6}")

    cards_block = "\n".join(cards_lines) if cards_lines else "—"

    created_at = batch.get("created_at")
    try:
        if isinstance(created_at, datetime):
            created_msk = created_at.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
        else:
            created_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        created_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")

    comment = (batch.get("comment") or "").strip()
    comment_line = _h(comment) if comment else "—"

    log_text = (
        f"🛒 <b>Биржа: {'одобрено' if status == 'approved' else 'отклонено'}</b>\n"
        f"🕒 {created_msk} (МСК)\n"
        f"Админ: <b>{admin_tag(admin_user)}</b> (id: {admin_user.id})\n"
        f"Batch: <code>{int(batch_id)}</code>\n"
        f"Пользователь: {owner_txt}\n\n"
        f"Колода: <b>{_h(deck_line)}</b>\n"
        f"Режим: <b>{_h(mode_ru)}</b>\n"
        f"Карт: <b>{len(items) if items else 0}</b>\n"
        f"Цена: <b>{price}</b> {cur_emoji}\n"
        f"Пруф: <b>{proof_line}</b>\n"
        f"Комментарий: <b>{comment_line}</b>\n\n"
        f"Состав:\n{_h(cards_block)}\n\n"
        f"Действие: <code>{_h(action_type)}</code>"
    )

    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=None,
        details=(
            f"batch_id={batch_id} status={status} mode={mode} currency={cur} "
            f"price={price} owner={owner_id} deck_id={deck_id} has_proof={has_proof}"
        ),
    )


async def safe_edit_text(
        message: types.Message,
        text: str,
        reply_markup: types.InlineKeyboardMarkup | None = None,
        **kwargs,
) -> bool:
    """
    Возвращает True если реально отредактировали, False если 'message is not modified'.
    Остальные ошибки не глотаем.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup, **kwargs)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return False
        raise


def _norm_auk_kind(v: object) -> str:
    s = (str(v) if v is not None else "").strip().lower()
    return s or "standard"


def admin_tag(user: User) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else f"id{user.id}"


async def notify_owners_lot_changed(
        bot,
        *,
        auction_id: int,
        admin_user,
        title: str,
        # старый интерфейс
        changes: Optional[list[tuple[str, object, object]]] = None,
        stage_label: Optional[str] = None,
        # новый интерфейс
        body: str | None = None,
        text: str | None = None,
        **_ignored,
) -> None:
    """
    Уведомление владельцев о том, что лот изменили.

    Поддерживает 2 режима:
    1) Старый: changes + stage_label
       changes: [("Поле", old, new), ...]
       stage_label: "в расписании" / "на модерации"
    2) Новый: body или text (готовый текст/блок)
       body имеет приоритет над text.

    Любые лишние kwargs игнорируются (для совместимости).
    """
    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners(int(auction_id))
    if not lot or not owners:
        return

    moderator_tag = admin_tag(admin_user)
    thanks_kb = await build_thanks_kb(int(auction_id), moderator_tag)

    # ---------- helpers ----------
    def _v(x: object) -> str:
        if x is None:
            return "—"
        s = str(x).strip()
        return s if s else "—"

    # ---------- build change block ----------
    final_body = body if body is not None else (text or "")

    ch_block = ""
    if changes:
        ch_lines: list[str] = []
        for field_title, old_v, new_v in changes:
            ch_lines.append(
                f"• <b>{_v(field_title)}:</b> <code>{_v(old_v)}</code> → <code>{_v(new_v)}</code>"
            )
        ch_block = "<b>Что изменили:</b>\n" + "\n".join(ch_lines)
    elif final_body.strip():
        # Если пришёл готовый текст, используем его как "блок изменений"
        ch_block = final_body.strip()
    else:
        ch_block = "<b>Что изменили:</b>\n• —"

    # ---------- lot info ----------
    card_name = (lot.get("card_name") or "—")
    hero_name = (lot.get("hero_name") or "—")
    media_id = (lot.get("image_id") or lot.get("photo_id"))

    # stage label: если не передали, пытаемся понять мягко
    stage = (stage_label or "").strip()
    if not stage:
        # очень мягкая деградация: не гадаем по DB статусам, просто нейтрально
        stage = "—"

    caption = (
        f"🛠 <b>{title}</b>\n\n"
        f"Лот: <b>{card_name}</b> — <i>{hero_name}</i>\n"
        f"ID: <code>{auction_id}</code>\n"
        f"Статус: <b>{stage}</b>\n\n"
        f"{ch_block}\n\n"
        f"👤 <b>Кто изменил:</b> {moderator_tag}\n"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n"
    )

    # ---------- send to owners ----------
    sent: set[int] = set()
    for o in owners:
        try:
            uid = int(o["user_id"])
        except Exception:
            continue
        if uid in sent:
            continue
        sent.add(uid)
        try:
            await _bot_send_media_any(
                bot,
                chat_id=uid,
                file_id=media_id,
                caption=caption,
                reply_markup=thanks_kb,
            )
        except Exception:
            # владельцы иногда "не доступны" (бота заблокировали и т.д.)
            pass


async def update_lot_field_with_notify(
        bot,
        *,
        auction_id: int,
        field: str,
        value,
        admin_user: types.User,
        field_label: str,
) -> None:
    before = await get_lot_by_id(auction_id)
    old_val = before.get(field)

    await _update_auction_field(auction_id, field, value)

    after = await get_lot_by_id(auction_id)
    # уведомляем только если лот уже в расписании/активен
    if str(after.get("status")) in {"scheduled", "active", "approved"}:
        body = (
            f"Лот №<b>{auction_id}</b>\n"
            f"Поле: <b>{field_label}</b>\n"
            f"Было: <code>{old_val}</code>\n"
            f"Стало: <code>{after.get(field)}</code>"
        )
        await notify_owners_lot_changed(
            bot,
            auction_id=auction_id,
            admin_user=admin_user,
            title="Изменения по вашему лоту",
            body=body,
        )


async def _answer_media_any(
        message: types.Message,
        file_id: str,
        *,
        caption: str,
        reply_markup: types.InlineKeyboardMarkup | None = None,
        parse_mode: str | None = "HTML",
        protect_content: bool = False,
) -> types.Message | None:
    """
    Пытается отправить file_id как photo -> video -> animation.
    Возвращает отправленное сообщение или None.
    """
    fid = (file_id or "").strip()
    if not fid:
        return None

    # 1) photo
    try:
        return await message.answer_photo(
            photo=fid,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    # 2) video
    try:
        return await message.answer_video(
            video=fid,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    # 3) animation (gif)
    try:
        return await message.answer_animation(
            animation=fid,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
    except Exception:
        return None


def _admin_auk_kind_keyboard(req_type: str, counts: dict[str, int] | None = None) -> types.InlineKeyboardMarkup:
    counts = counts or {}
    kb = InlineKeyboardBuilder()
    for k in ADMIN_AUK_KIND_ORDER:
        label = ADMIN_AUK_KIND_LABELS.get(k, k)
        cnt = int(counts.get(k) or 0)
        text = f"{label} ({cnt})" if cnt else label
        kb.button(text=text, callback_data=f"admreq|{req_type}|{k}")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(2)
    return kb.as_markup()


def _kb_exchange_pending_mode() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Вывалить все лоты", callback_data="expend_mode|all")
    kb.button(text="🧾 По одному", callback_data="expend_mode|one")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)
    return kb.as_markup()


def _count_pending_by_kind(lots: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {k: 0 for k in ADMIN_AUK_KIND_ORDER}
    for lot in lots:
        k = _norm_auk_kind(lot.get("auction_kind"))
        out[k] = out.get(k, 0) + 1
    return out


def _count_delete_by_kind(counts_db: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {k: 0 for k in ADMIN_AUK_KIND_ORDER}
    for k, v in (counts_db or {}).items():
        kk = _norm_auk_kind(k)
        out[kk] = out.get(kk, 0) + int(v)
    return out


def _requests_title(req_type: str) -> str:
    return "📝 Заявки на модерацию" if req_type == "pending" else "🗂️ Заявки на удаление"


async def show_requests_kind_menu(message: Message, req_type: str) -> None:
    req_type = (req_type or "").strip().lower()
    if req_type not in {"pending", "delete"}:
        await message.answer("Некорректный тип заявок.")
        return

    if req_type == "pending":
        ex_cnt = await count_pending_exchange_batches()
        lots = await get_pending_auctions()

        if not lots and ex_cnt == 0:
            await message.answer("Нет заявок на модерацию.")
            return

        counts = _count_pending_by_kind(lots or [])
        counts["exchange"] = ex_cnt  # ✅ вот оно
    else:
        counts_db = await count_pending_delete_requests_by_kind()
        counts = _count_delete_by_kind(counts_db)
        if sum(counts.values()) == 0:
            await message.answer("Нет заявок на удаление.")
            return

    await message.answer(
        f"{_requests_title(req_type)}\nВыберите вид аукциона:",
        reply_markup=_admin_auk_kind_keyboard(req_type, counts=counts),
    )


async def _update_auction_field(auction_id: int, field: str, value: Any) -> dict[str, Any]:
    service = await AuctionModerationService.create()
    return await service.update_field(auction_id, field=field, value=value)


async def send_admin_main_menu(message: Message) -> None:
    await message.answer("↩️ Возврат в главное меню...", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        ADMIN_MESSAGES.get("admin_panel_greeting", "Добро пожаловать в админ-панель! Выберите раздел:"),
        reply_markup=menu_keyboard(
            ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
            ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
        )
    )


def _kb_ex_appr_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _back_to_lot_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_lot_back")]]
    )


def _edit_lot_menu_kb(auction_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="⚙️ Тип аука", callback_data=f"edit_field|auction_kind|{auction_id}")],
            [types.InlineKeyboardButton(text="🆔 Крафт на UID", callback_data=f"edit_field|craft_uid|{auction_id}")],
            [types.InlineKeyboardButton(text="🕒 Время", callback_data=f"edit_field|time|{auction_id}")],
            [types.InlineKeyboardButton(text="💵 Цена", callback_data=f"edit_field|price|{auction_id}")],
            [types.InlineKeyboardButton(text="💱 Валюта", callback_data=f"edit_field|currency|{auction_id}")],
            [types.InlineKeyboardButton(text="💬 Комментарий", callback_data=f"edit_field|comment|{auction_id}")],
            [types.InlineKeyboardButton(text="🖼 Фото", callback_data=f"edit_field|photo|{auction_id}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_schedule_back")],
        ]
    )


def _auk_kind_kb(auction_id: int) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for k in ADMIN_AUK_KIND_ORDER:
        row.append(
            types.InlineKeyboardButton(
                text=ADMIN_AUK_KIND_LABELS.get(k, k),
                callback_data=f"set_auk_kind|{k}|{auction_id}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_lot_back")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _craft_uid_kb(auction_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Да", callback_data=f"set_craft_uid|1|{auction_id}"),
                types.InlineKeyboardButton(text="❌ Нет", callback_data=f"set_craft_uid|0|{auction_id}"),
            ],
            [types.InlineKeyboardButton(text="♻️ Сбросить", callback_data=f"set_craft_uid|none|{auction_id}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_lot_back")],
        ]
    )


async def _send_edit_lot_menu(target_message: Message, state: FSMContext, auction_id: int) -> None:
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await target_message.answer("Лот не найден.")
        return

    currency_raw = lot.get("currency", "")
    currency_fancy = CURRENCY_EMOJI.get(str(currency_raw).lower(), currency_raw)

    kind = _norm_auk_kind(lot.get("auction_kind"))
    kind_label = ADMIN_AUK_KIND_LABELS.get(kind, kind)

    craft_val = lot.get("craft_uid_possible")
    if craft_val is True:
        craft_s = "✅ Да"
    elif craft_val is False:
        craft_s = "❌ Нет"
    else:
        craft_s = "—"

    comment = (lot.get("comment") or "—").strip() if isinstance(lot.get("comment"), str) else (
            lot.get("comment") or "—")

    text = (
        f"<b>Лот:</b> {lot.get('card_name')} [{lot.get('hero_name') or '-'}]\n"
        f"⚙️ <b>Тип аука:</b> {kind_label}\n"
        f"🆔 <b>Крафт на UID:</b> {craft_s}\n"
        f"💬 <b>Комментарий:</b> {comment}\n"
        f"💵 <b>Стартовая цена:</b> {lot.get('start_price')} {currency_fancy}\n"
        f"🕒 <b>Время:</b> {to_moscow(lot['start_time']).strftime('%d.%m %H:%M')}–{to_moscow(lot['end_time']).strftime('%H:%M')}\n\n"
        "Что хотите изменить?"
    )

    await state.update_data(auction_id=auction_id)
    await target_message.answer(text, reply_markup=_edit_lot_menu_kb(auction_id), parse_mode="HTML")
    await state.set_state(EditScheduleFSM.choosing_field)


from aiogram.exceptions import TelegramBadRequest, TelegramAPIError


async def safe_send_media(bot, chat_id: int, file_id: str, *, caption: str = "", parse_mode: str = "HTML",
                          reply_markup=None):
    file_id = (file_id or "").strip()
    if not file_id:
        return await bot.send_message(chat_id, caption, parse_mode=parse_mode, reply_markup=reply_markup)

    try:
        return await bot.send_photo(chat_id, photo=file_id, caption=caption, parse_mode=parse_mode,
                                    reply_markup=reply_markup)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "video as photo" in s or "type video" in s:
            return await bot.send_video(chat_id, video=file_id, caption=caption, parse_mode=parse_mode,
                                        reply_markup=reply_markup, supports_streaming=True)
        if "animation as photo" in s or "gif as photo" in s:
            return await bot.send_animation(chat_id, animation=file_id, caption=caption, parse_mode=parse_mode,
                                            reply_markup=reply_markup)
        # последний шанс
        return await bot.send_document(chat_id, document=file_id, caption=caption, parse_mode=parse_mode,
                                       reply_markup=reply_markup)
    except TelegramAPIError:
        return await bot.send_document(chat_id, document=file_id, caption=caption, parse_mode=parse_mode,
                                       reply_markup=reply_markup)


def _kb_add_back(kb: types.InlineKeyboardMarkup, cb: str, text: str = "⬅️ Назад") -> types.InlineKeyboardMarkup:
    if not kb:
        return kb
    if not getattr(kb, "inline_keyboard", None):
        return kb

    # не плодим дубликаты
    try:
        if kb.inline_keyboard and kb.inline_keyboard[-1] and kb.inline_keyboard[-1][0].callback_data == cb:
            return kb
    except Exception:
        pass

    kb.inline_keyboard.append([types.InlineKeyboardButton(text=text, callback_data=cb)])
    return kb


_MONTH_RU_SHORT = {
    1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр", 5: "Май", 6: "Июн",
    7: "Июл", 8: "Авг", 9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек",
}


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month <= 1:
        return year - 1, 12
    return year, month - 1


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month >= 12:
        return year + 1, 1
    return year, month + 1


def _kb_stats_schedule_navigator(year: int, month: int) -> InlineKeyboardMarkup:
    # страховка от мусора
    month = max(1, min(12, int(month)))
    year = int(year)

    py, pm = _prev_month(year, month)
    ny, nm = _next_month(year, month)

    kb = InlineKeyboardBuilder()

    # навигация по годам
    kb.row(
        InlineKeyboardButton(text="⏪", callback_data=f"stats_schedule_set|{year - 1}-{month:02d}"),
        InlineKeyboardButton(text=str(year), callback_data="stats_schedule_noop"),
        InlineKeyboardButton(text="⏩", callback_data=f"stats_schedule_set|{year + 1}-{month:02d}"),
    )

    # навигация по месяцам
    kb.row(
        InlineKeyboardButton(text="◀️", callback_data=f"stats_schedule_set|{py}-{pm:02d}"),
        InlineKeyboardButton(text=f"{_MONTH_RU_SHORT.get(month, str(month))} {year}",
                             callback_data="stats_schedule_noop"),
        InlineKeyboardButton(text="▶️", callback_data=f"stats_schedule_set|{ny}-{nm:02d}"),
    )

    # открытие выбранного месяца (используем существующую модерационную логику)
    kb.row(
        InlineKeyboardButton(text="📅 Открыть месяц", callback_data=f"preview_schedule|{year}-{month:02d}")
    )

    # быстрый прыжок на текущий месяц
    kb.row(
        InlineKeyboardButton(text="⏺ Сегодня", callback_data="stats_schedule_today")
    )

    return kb.as_markup()


def _extract_video_from_message(msg: Message) -> tuple[str, str | None, str | None] | None:
    """
    Возвращает (file_id, unique_id, thumb_file_id) для video/animation/video-document.
    """
    if msg.video:
        thumb = msg.video.thumbnail.file_id if msg.video.thumbnail else None
        return (msg.video.file_id, msg.video.file_unique_id, thumb)

    if msg.animation:
        thumb = msg.animation.thumbnail.file_id if msg.animation.thumbnail else None
        return (msg.animation.file_id, msg.animation.file_unique_id, thumb)

    if msg.document and (msg.document.mime_type or "").startswith("video/"):
        return (msg.document.file_id, msg.document.file_unique_id, None)

    return None


def _extract_media_file_id(msg: types.Message) -> str | None:
    if getattr(msg, "photo", None):
        return msg.photo[-1].file_id
    if getattr(msg, "video", None):
        return msg.video.file_id
    if getattr(msg, "animation", None):
        return msg.animation.file_id
    doc = getattr(msg, "document", None)
    if doc and (doc.mime_type or "").startswith("video/"):
        return doc.file_id
    return None


PEX_PREFIX = "pex"  # callback: pex|<batch_id>|<action>


class PrintExFSM(StatesGroup):
    winner = State()
    price = State()
    link = State()


def _pex_cb(batch_id: int, action: str) -> str:
    return f"{PEX_PREFIX}|{int(batch_id)}|{action}"


def _safe_user_mention(user_id: int | None, username: str | None, *, title: str | None = None) -> str:
    """
    Формирует упоминание для parse_mode=HTML:
    - если есть username -> возвращает @username (ровно один @)
    - иначе -> кликабельная ссылка по id
    """
    un = (username or "").strip()
    if un.startswith("@"):
        un = un[1:]

    if un:
        return f"@{html.escape(un)}"

    uid = int(user_id or 0)
    if uid > 0:
        label = html.escape(title) if title else f"id{uid}"
        return f'<a href="tg://user?id={uid}">{label}</a>'

    return "—"


async def _build_print_ex_view(batch_id: int) -> tuple[str, InlineKeyboardMarkup]:
    batch = await get_exchange_batch_by_id(int(batch_id))
    if not batch:
        return (f"⚠️ Заявка биржи не найдена: <code>{batch_id}</code>", InlineKeyboardMarkup(inline_keyboard=[]))

    items = await get_exchange_items_by_batch_id(int(batch_id))

    owner = await get_user(int(batch["user_id"]))
    owner_username = (owner.get("username") if owner else None) or None
    owner_txt = _safe_user_mention(int(batch["user_id"]), owner_username)

    manual_winner_id = batch.get("manual_winner_id")
    manual_winner_username = (batch.get("manual_winner_username") or "").strip() or None

    winner_txt = "—"
    if manual_winner_id:
        winner_txt = _safe_user_mention(int(manual_winner_id), manual_winner_username)

    price = batch.get("manual_price")
    if price is None:
        price = batch.get("price")

    link = (batch.get("manual_link") or "").strip() or "—"
    sent = "✅ да" if batch.get("manual_sent_at") else "❌ нет"

    lines = [
        f"🛒 <b>PRINT_EX</b> • заявка <code>{batch_id}</code>",
        f"Статус: <b>{batch.get('status')}</b>",
        f"Владелец: {owner_txt}",
        f"Режим: <b>{batch.get('mode')}</b>",
        f"Цена: <b>{int(price or 0)}</b> {batch.get('currency')}",
        f"Комментарий: {(batch.get('comment') or '').strip() or '—'}",
        "",
        "📦 <b>Состав:</b>",
    ]

    if items:
        for it in items:
            nm = f"{(it.get('hero_name') or '').strip()} — {(it.get('card_name') or '').strip()}".strip(" —")
            qty = int(it.get("qty") or 1)
            lines.append(f"• {nm} ×{qty}  (<code>card_id={it.get('card_id')}</code>)")
    else:
        lines.append("—")

    lines += [
        "",
        "🧾 <b>Ручной итог:</b>",
        f"Победитель: {winner_txt}",
        f"Ссылка: {link}",
        f"Отправлено: {sent}",
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Отправить обоим", callback_data=_pex_cb(batch_id, "send_both")),
        ],
        [
            InlineKeyboardButton(text="👤 Сменить победителя", callback_data=_pex_cb(batch_id, "set_winner")),
            InlineKeyboardButton(text="💰 Сменить цену", callback_data=_pex_cb(batch_id, "set_price")),
        ],
        [
            InlineKeyboardButton(text="🔗 Сменить ссылку", callback_data=_pex_cb(batch_id, "set_link")),
            InlineKeyboardButton(text="♻️ Сброс", callback_data=_pex_cb(batch_id, "reset")),
        ],
        [
            InlineKeyboardButton(text="🧙 Мастер", callback_data=_pex_cb(batch_id, "wizard")),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=_pex_cb(batch_id, "refresh")),
        ],
    ])
    return ("\n".join(lines), kb)


async def _safe_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


HOWMAX_TEXT = (
    "Регистрируетесь в боте <b>@RomanticClubBot</b>, нажимаете кнопку <b>Старт</b> и ждёте результат.\n"
    "Вам придут данные владельца/покупателя.\n"
    "Если возникнет ошибка, с вами свяжется админ.\n"
    "Обычно срок ожидания <b>одни сутки</b>."
)


def _pick_media_file(message: types.Message):
    """
    Возвращает (kind, file) где file имеет .file_id и .file_unique_id
    Поддержка: photo, video, animation, document, audio, voice, sticker
    """
    # Фото: берём самое большое
    if message.photo:
        return "photo", message.photo[-1]

    if message.video:
        return "video", message.video

    if message.animation:
        return "animation", message.animation

    # Часто mp4 присылают "как файл"
    if message.document:
        mt = (message.document.mime_type or "").lower()
        if mt.startswith("video/"):
            return "document(video)", message.document
        return "document", message.document

    if message.audio:
        return "audio", message.audio

    if message.voice:
        return "voice", message.voice

    if message.sticker:
        return "sticker", message.sticker

    return None, None

# Star imports are deliberate in the generated feature modules: they recreate
# the original module namespace while keeping handler ownership explicit.
__all__ = [name for name in globals() if not name.startswith("__")]
