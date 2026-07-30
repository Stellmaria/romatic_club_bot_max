import asyncio
import logging
import html
from datetime import date, datetime, timedelta
from typing import cast, Optional
from zoneinfo import ZoneInfo

from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery, User, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.time import auction_end_at_59, to_moscow, to_moscow_wall
from bot.domain.auctions import AuctionSlotConflict, InvalidAuctionTransition, currency_choices_label
from bot.services.auction_workflows import AuctionModerationService
from bot.handlers.admin.schedule_card_view import (
    build_schedule_lot_caption,
    build_schedule_lot_keyboard,
    refresh_schedule_card_origin,
    remember_schedule_card_origin,
)
from bot.handlers.admin.helper.admin_constants import (
    ADMIN_MESSAGES, CANCEL_TEXTS, BUTTONS, CURRENCY_EMOJI,
    RARITY_EMOJI, RARITY_TREASURE, RARITY_RU, ADMIN_COMMANDS_INFO
)
from bot.handlers.admin.helper.admin_keyboards import days_keyboard, months_keyboard
from bot.handlers.admin.helper.admin_service import (
    parse_auction_and_date_from_callback, get_free_slots_and_schedule_for_lot
)
from bot.handlers.admin.helper.new.Types import Owner
from bot.handlers.admin.action_support.compat import (admin_add_remove,
                                                         owner_or_secret_required, show_delete_requests_for_moderation,
                                                         show_pendinglots,
                                                         start_preview_schedule, start_edit_schedule,
                                                         get_lot_owners_text, send_admin_log,
                                                         add_deck_fsm_entry, start_add_card_fsm,
                                                         process_universal_cancel_callback, _do_trusted_action,
                                                         safe_answer_photo,
                                                         )
from bot.handlers.admin.action_support.compat import send_lot_card_safe
from bot.handlers.admin.helper.new.formatting import format_admin_action_log, format_pending_lot
from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard  # если у тебя этот импорт уже есть
from bot.handlers.admin.helper.new.keyboards import (
    menu_keyboard, back_keyboard, time_slots_keyboard, decks_keyboard, inline_back_keyboard, decks_menu_keyboard
)
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_lot_edit_log, short_media_id
from bot.handlers.auctions import build_thanks_kb, _bot_send_media_any, \
    _get_exchange_cover_media, \
    currency_to_emoji, format_pending_exchange_batch_card, pending_exchange_kb, _media_kind_from_error, \
    _format_exchange_approved_lot_caption, _kb_exchange_approved_lot_actions, _safe_edit_text_or_caption, \
    _q_exchange_whole_deck_batches, _kb_exchange_approved_decks, _kb_exchange_approved_root, _q_exchange_approved_decks, \
    show_pending_exchange_requests_all
from bot.utils_admin import format_log_entry
from bot.telegram.callbacks import safe_callback_answer
from config import ADMINS_OWNERS, ADMIN_SECRET
from db.db import (
    get_audit_logs, add_deck, log_audit_action, get_lot_by_id, update_auction_time_status, schedule_auction_time_if_available,
    get_user, update_auction_currency, update_auction_price, get_cards_by_deck_id, get_all_decks, delete_lot,
    get_lot_owners, is_luxury_user, get_auctions_by_date_with_owners, get_pending_auctions,
    set_exchange_batch_status, get_exchange_batch_by_id, count_pending_delete_requests_by_kind, set_card_video_by_id,
    get_card_by_id, update_lot_field, count_pending_exchange_batches, get_exchange_owners_for_cards,
    set_exchange_manual_price, set_exchange_manual_link, set_exchange_manual_winner, get_user_by_username,
    mark_exchange_manual_sent, reset_exchange_manual, get_exchange_items_by_batch_id, is_admin, get_deck_by_id,
    fetchrow, add_exchange_items_for_deck, get_exchange_deck_overview, set_exchange_batch_moderation,
    set_exchange_batch_deleted
)
from fsm_states import ModActionFSM, BroadcastFSM, AddDeckFSM, EditScheduleFSM, PreviewScheduleFSM

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
    total_row = await fetchrow(
        "SELECT COUNT(*)::int AS cnt FROM public.exchange_batches WHERE COALESCE(status,'pending')='pending'"
    )
    total = int((total_row or {}).get("cnt") or 0)
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    row = await fetchrow(
        """
        SELECT eb.batch_id,
               eb.user_id,
               u.username,
               u.full_name,
               eb.deck_id,
               d.name                                                                          AS deck_name,
               eb.mode,
               eb.currency,
               eb.price,
               eb.comment,
               eb.proof_photo_id,
               eb.created_at,
               (SELECT COUNT(*) FROM public.exchange_items ei WHERE ei.batch_id = eb.batch_id) AS items_count
        FROM public.exchange_batches eb
                 LEFT JOIN public.users u ON u.user_id = eb.user_id
                 LEFT JOIN public.decks d ON d.id = eb.deck_id
        WHERE COALESCE(eb.status, 'pending') = 'pending'
        ORDER BY eb.created_at DESC
        LIMIT 1
        """
    )
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


# noinspection PyInterpreter
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

    await update_lot_field(auction_id, field, value)

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


router = Router()


@router.callback_query(F.data == "admreq_back")
@admin_only
async def admreq_back(call: CallbackQuery):
    # возвращаемся в меню модерации
    try:
        await call.message.delete()
    except Exception:
        pass

    await call.message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"]
        )
    )
    await call.answer()


@router.message(F.text.regexp(r"^/ex_owners\s+\d+$"))
@admin_only
async def cmd_ex_owners(message: Message):
    parts = (message.text or "").split()
    card_id = int(parts[1])

    owners_map = await get_exchange_owners_for_cards([card_id], status="approved")
    owners = owners_map.get(card_id) or []

    if not owners:
        await message.answer(f"🛒 По карте <code>{card_id}</code> в бирже владельцев не найдено.", parse_mode="HTML")
        return

    lines = [f"🛒 <b>Владельцы по карте</b> <code>{card_id}</code>:\n"]
    for o in owners:
        uname = o["username"]
        utext = f"@{uname}" if uname else "—"
        lines.append(f"• {utext} (id:{o['user_id']}) × {o['qty']} | batch_id: <code>{o['batch_id']}</code>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("admreq|"))
@admin_only
async def admreq_select(call: CallbackQuery):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Некорректная команда.", show_alert=True)
        return

    _, req_type, kind = parts
    kind = _norm_auk_kind(kind)

    # сразу отвечаем на callback, чтобы не висело "часики"
    await call.answer()

    # можно убрать меню выбора, чтобы не засорять чат
    try:
        await call.message.delete()
    except Exception:
        pass

    if req_type == "pending":
        if kind == "exchange":
            await call.message.answer(
                "🛒 <b>Заявки на биржу</b>\n\nКак показать?",
                parse_mode="HTML",
                reply_markup=_kb_exchange_pending_mode(),
            )
        else:
            await show_pendinglots(call.message, kind=kind)
    elif req_type == "delete":
        await show_delete_requests_for_moderation(call.message, kind=kind)
    else:
        await call.message.answer("Некорректный тип заявок.")


async def send_admin_main_menu(message: Message) -> None:
    await message.answer("↩️ Возврат в главное меню...", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        ADMIN_MESSAGES.get("admin_panel_greeting", "Добро пожаловать в админ-панель! Выберите раздел:"),
        reply_markup=menu_keyboard(
            ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
            ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
        )
    )


@router.message(F.text.in_(['/admin', '/admin_panel']), F.chat.type == "private")
@admin_only
async def show_admin_menu(message: Message, state: FSMContext):
    await state.clear()
    await send_admin_main_menu(message)


@router.message(F.text == "⚙️ Модерация", F.chat.type == "private")
@admin_only
async def moderation_menu(message: Message):
    await message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["🧾 Верификация", "⛔ UID-бан"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"]
        )
    )


@router.message(F.text == "🤝 Доверить пользователя", F.chat.type == "private")
@admin_only
async def start_give_trusted(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_trusted_user)
    await message.answer(
        "Введите username (@username) или user_id для выдачи статуса 'доверенный':",
        reply_markup=inline_back_keyboard()
    )


@router.message(ModActionFSM.waiting_for_trusted_user, F.chat.type == "private")
@admin_only
async def give_trusted_user(message: Message, state: FSMContext):
    await _do_trusted_action(
        message=message,
        state=state,
        who=message.text,
        bot=message.bot,
        grant=True,
    )


@router.message(F.text == "❌ Снять доверие", F.chat.type == "private")
@admin_only
async def start_remove_trusted(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_untrusted_user)
    await message.answer(
        "Введите username (@username) или user_id для снятия статуса 'доверенный':",
        reply_markup=inline_back_keyboard()
    )


@router.message(ModActionFSM.waiting_for_untrusted_user, F.chat.type == "private")
@admin_only
async def remove_trusted_user(message: Message, state: FSMContext):
    await _do_trusted_action(
        message=message,
        state=state,
        who=message.text,
        bot=message.bot,
        grant=False,
    )


@router.message(F.text == "➕ Добавить админа", F.chat.type == "private")
@admin_only
async def start_add_admin(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_admin_user)
    await message.answer(
        "Введите @username или user_id и пароль через пробел (пример: @user password):",
        reply_markup=back_keyboard(text="Назад", callback="addadmin_cancel")
    )


@router.message(ModActionFSM.waiting_for_admin_user, F.chat.type == "private")
@admin_only
async def add_admin_user(message: Message, state: FSMContext):
    await admin_add_remove(message, state, is_remove=False)


@router.message(F.text == "➖ Удалить админа", F.chat.type == "private")
@admin_only
async def start_remove_admin(message: Message, state: FSMContext):
    await state.set_state(ModActionFSM.waiting_for_admin_remove_user)
    await message.answer(
        "Введите @username или user_id и пароль через пробел (пример: @user password):",
        reply_markup=back_keyboard(text="Назад", callback="removeadmin_cancel")
    )


@router.message(ModActionFSM.waiting_for_admin_remove_user, F.chat.type == "private")
@admin_only
@owner_or_secret_required
async def remove_admin_user(message: Message, state: FSMContext):
    await admin_add_remove(message, state, is_remove=True)


@router.message(F.text.in_(['/pendinglots', '📝 Заявки на модерацию']), F.chat.type == "private")
@admin_only
async def pendinglots_cmd(message: Message):
    await show_requests_kind_menu(message, req_type="pending")


@router.message(F.text.in_(['/delete_requests', '🗂️ Заявки на удаление']), F.chat.type == "private")
@admin_only
async def show_delete_requests_cmd(message: Message):
    await show_requests_kind_menu(message, req_type="delete")


@router.message(F.text == "📅 Расписание", F.chat.type == "private")
@admin_only
async def schedule_button(message: Message, state: FSMContext):
    await start_preview_schedule(message, state)


@router.message(F.text == "🛒 Биржа", F.chat.type == "private")
@admin_only
async def exchange_menu_button(message: Message):
    kb = InlineKeyboardBuilder()
    # ведём в корень “принятых лотов”, там уже есть “по колодам/списком”
    kb.button(text="✅ Принятые лоты", callback_data="ex_appr:root")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)

    await message.answer(
        "🛒 <b>Биржа</b>\n\nОткрываю принятые лоты:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


# @router.callback_query(F.data == "ex_appr:root")
# @admin_only
# async def ex_appr_root(call: types.CallbackQuery):
#     await _safe_edit_text_or_caption(
#         call.message,
#         text="🛒 <b>Биржа</b>\n\n✅ Принятые лоты:",
#         reply_markup=_kb_exchange_approved_root(),
#     )
#     await call.answer()


@router.callback_query(F.data == "ex_appr:decks")
@admin_only
async def ex_appr_decks(call: types.CallbackQuery):
    decks = await _q_exchange_approved_decks()
    if not decks:
        await _safe_edit_text_or_caption(
            call.message,
            text="🛒 <b>Биржа</b>\n\nПока нет принятых лотов.",
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


@router.callback_query(F.data.startswith("ex_appr:whole:"))
@admin_only
async def ex_appr_whole(call: types.CallbackQuery):
    # ex_appr:whole:<deck_id>:<page>
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    page = max(0, page)

    per_page = 12
    rows = await _q_exchange_whole_deck_batches(deck_id, limit=500)
    batch_ids = [int(r.get("batch_id") or 0) for r in (rows or []) if int(r.get("batch_id") or 0) > 0]

    total = len(batch_ids)
    if total <= 0:
        await _safe_edit_text_or_caption(
            call.message,
            text=(
                "📚 <b>Биржа → Колода целиком</b>\n\n"
                f"Колода: <b>{deck_id}</b>\n\n"
                "Лотов нет."
            ),
            reply_markup=_kb_ex_appr_back_to_deck(deck_id),
        )
        await call.answer()
        return

    last = max(0, (total - 1) // per_page)
    page = min(page, last)
    chunk = batch_ids[page * per_page: page * per_page + per_page]

    lines = [
        "📚 <b>Биржа → Колода целиком</b>",
        f"Колода: <b>{deck_id}</b>",
        f"Страница: <b>{page + 1}/{last + 1}</b> • Всего: <b>{total}</b>",
        "",
        "Выбери лот:",
    ]

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lotdeck:{deck_id}:{page}:{bid}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:whole:{deck_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:whole:{deck_id}:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}")
    kb.adjust(3, 3, 3, 3, 1, 1)

    await _safe_edit_text_or_caption(call.message, text="\n".join(lines), reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:lotdeck:"))
@admin_only
async def ex_appr_lotdeck_show(call: types.CallbackQuery):
    # ex_appr:lotdeck:<deck_id>:<page>:<batch_id>
    parts = (call.data or "").split(":")
    if len(parts) < 5:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    page = int(parts[3])
    batch_id = int(parts[4])

    caption = await _format_exchange_approved_lot_caption(batch_id)
    back_cb = f"ex_appr:whole:{deck_id}:{page}"
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

    # показываем лот (как в ex_appr_lot_show), но “назад” ведёт в whole list
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
            # fallback: просто текст
            await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)

    await call.answer()


def _kb_ex_appr_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def cb_exchange_approved_root(call: CallbackQuery):
    decks = await get_exchange_deck_overview(status="approved")
    if not decks:
        await call.answer("🛒 На бирже нет активных лотов.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for d in decks:
        deck_id = int(d["deck_id"])
        name = d["deck_name"]
        cnt = int(d["items_count"])
        kb.button(text=f"{name} ({cnt})", callback_data=f"exinv|{deck_id}|0")

    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1)

    await call.message.edit_text(
        "🛒 <b>Биржа</b>\nВыберите колоду:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


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
    accepted_label = currency_choices_label(
        lot.get("accepted_currencies"),
        fallback=currency_raw,
        custom_terms=lot.get("custom_offer_terms"),
    )

    craft_val = lot.get("craft_uid_possible")
    if craft_val is True:
        craft_s = "✅ Да"
    elif craft_val is False:
        craft_s = "❌ Нет"
    else:
        craft_s = "—"

    comment = (lot.get("comment") or "—").strip() if isinstance(lot.get("comment"), str) else (
            lot.get("comment") or "—")

    start_msk = to_moscow_wall(lot["start_time"])
    end_msk = to_moscow_wall(lot["end_time"])
    if kind == "reverse":
        price_text = (
            f"💱 <b>Валюта ставок:</b> {accepted_label}\n"
            "📉 <b>Победитель:</b> минимальная ставка\n"
        )
    elif kind == "free":
        price_text = f"💱 <b>Принимаются предложения:</b> {accepted_label}\n"
    else:
        price_text = (
            f"💵 <b>Стартовая цена:</b> {lot.get('start_price')} {currency_fancy}\n"
        )

    text = (
        f"<b>Лот:</b> {lot.get('card_name')} [{lot.get('hero_name') or '-'}]\n"
        f"⚙️ <b>Тип аука:</b> {kind_label}\n"
        f"🆔 <b>Крафт на UID:</b> {craft_s}\n"
        f"💬 <b>Комментарий:</b> {comment}\n"
        f"{price_text}"
        f"🕒 <b>Время:</b> {start_msk.strftime('%d.%m %H:%M')}–{end_msk.strftime('%H:%M')} (МСК)\n\n"
        "Что хотите изменить?"
    )

    await state.update_data(auction_id=auction_id)
    await target_message.answer(text, reply_markup=_edit_lot_menu_kb(auction_id), parse_mode="HTML")
    await state.set_state(EditScheduleFSM.choosing_field)


@router.message(F.text == "📝 Редактировать расписание", F.chat.type == "private")
@admin_only
async def edit_schedule_button(message: Message, state: FSMContext):
    await start_edit_schedule(message, state)


@router.callback_query(F.data.startswith("edit_schedule_lot|"))
@admin_only
async def edit_lot_menu(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    await remember_schedule_card_origin(
        state,
        call.message,
        auction_id,
        delete_callback_prefix="delete_lot",
        delete_label="🗑️ Удалить",
    )
    await _send_edit_lot_menu(call.message, state, auction_id)
    await safe_callback_answer(call)


@router.callback_query(F.data.startswith("expend_mode|"))
@admin_only
async def exchange_pending_mode_pick(call: CallbackQuery):
    mode = (call.data or "").split("|", 1)[-1]
    await call.answer()

    try:
        await call.message.delete()
    except Exception:
        pass

    if mode == "one":
        await show_pending_exchange_one(call.message)
    else:
        await show_pending_exchange_requests_all(call.message, limit=200)
@router.callback_query(EditScheduleFSM.choosing_field, F.data.startswith("edit_field|"))
@admin_only
async def edit_field_handler(call: CallbackQuery, state: FSMContext):
    _, field, auction_id_raw = call.data.split("|")
    try:
        auction_id = int(auction_id_raw)
    except Exception:
        data = await state.get_data()
        auction_id = int(data.get("auction_id") or 0)

    if not auction_id:
        await call.answer("Не понял какой лот.", show_alert=True)
        return

    await state.update_data(auction_id=auction_id, edit_field=field)

    # Время: выбор месяца/дня/слота (как в модерации при принятии заявки)
    if field == "time":
        await state.set_state(EditScheduleFSM.choosing_month)

        kb = months_keyboard(prefix="edit_schedule", auction_id=auction_id)
        kb = _kb_add_back(kb, "edit_lot_back")

        await call.message.answer(
            "Ок. Выберите новую дату/время (как раньше).\n\n"
            "Сначала выберите месяц:",
            reply_markup=kb,
        )
        await call.answer()
        return

    # Цена
    if field == "price":
        await call.message.answer("Введите новую стартовую цену:", reply_markup=_back_to_lot_kb())
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Валюта: обратный и свободный используют чай, алмазы или оба варианта.
    if field == "currency":
        lot = await get_lot_by_id(auction_id) or {}
        kind = _norm_auk_kind(lot.get("auction_kind"))
        if kind in {"reverse", "free"}:
            rows = [
                [types.InlineKeyboardButton(text="🍵 Чай", callback_data="set_currency|чашки")],
                [types.InlineKeyboardButton(text="💎 Алмазы", callback_data="set_currency|алмазы")],
                [types.InlineKeyboardButton(
                    text="🍵 + 💎 Чай или/и алмазы",
                    callback_data="set_currency|чашки_алмазы",
                )],
            ]
            if kind == "free":
                rows.append([types.InlineKeyboardButton(
                    text="🧩 Комби (свои варианты)",
                    callback_data="set_currency|custom_combo",
                )])
            prompt = (
                "Выберите валюту обратного аукциона:"
                if kind == "reverse"
                else "Выберите, какие предложения принимает свободный аукцион:"
            )
        else:
            rows = [
                [types.InlineKeyboardButton(text="💎 Алмазы", callback_data="set_currency|алмазы")],
                [types.InlineKeyboardButton(text="🍵 Чашки", callback_data="set_currency|чашки")],
                [types.InlineKeyboardButton(text="🪙 Сокровища", callback_data="set_currency|сокровища")],
            ]
            prompt = "Выберите валюту:"
        kb = _kb_add_back(types.InlineKeyboardMarkup(inline_keyboard=rows), "edit_lot_back")
        await call.message.answer(prompt, reply_markup=kb)
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Коммент
    if field == "comment":
        await call.message.answer(
            "Введите новый комментарий.\n"
            "Чтобы очистить: отправьте <code>-</code>",
            reply_markup=_back_to_lot_kb(),
            parse_mode="HTML",
        )
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Фото
    if field == "photo":
        await call.message.answer("Пришлите новое фото для лота:", reply_markup=_back_to_lot_kb())
        await state.set_state(EditScheduleFSM.entering_value)
        await call.answer()
        return

    # Тип аука
    if field == "auction_kind":
        await call.message.answer("Выберите тип аука:", reply_markup=_auk_kind_kb(auction_id))
        await call.answer()
        return

    # Крафт на UID
    if field == "craft_uid":
        await call.message.answer("Крафт на UID возможен?", reply_markup=_craft_uid_kb(auction_id))
        await call.answer()
        return

    await call.answer("Неизвестное поле.", show_alert=True)


@router.callback_query(F.data.startswith("set_auk_kind|"))
@admin_only
async def set_auction_kind_handler(call: CallbackQuery, state: FSMContext):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Кривой callback.", show_alert=True)
        return

    _, kind, auction_id_raw = parts
    auction_id = int(auction_id_raw)

    lot_before = await get_lot_by_id(auction_id)
    old_kind = (lot_before or {}).get("auction_kind")

    await update_lot_field(auction_id, "auction_kind", kind)

    await notify_owners_lot_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Тип аука", old_kind, kind)],
    )

    lot_after = dict(lot_before or {})
    lot_after["auction_kind"] = kind

    await send_lot_edit_log(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Тип аука", old_kind, kind)],
        audit_action_type="edit_lot_kind",
        audit_details=f"Тип аука изменён: {old_kind} -> {kind}",
    )

    await call.message.answer("✅ Тип аука обновлён.", reply_markup=_back_to_lot_kb())
    await call.answer()


@router.callback_query(F.data.startswith("set_craft_uid|"))
@admin_only
async def set_craft_uid_handler(call: CallbackQuery, state: FSMContext):
    parts = (call.data or "").split("|")
    if len(parts) != 3:
        await call.answer("Кривой callback.", show_alert=True)
        return

    _, val, auction_id_raw = parts
    auction_id = int(auction_id_raw)

    if val == "1":
        new_val = True
        val_s = "Да"
    elif val == "0":
        new_val = False
        val_s = "Нет"
    else:
        new_val = None
        val_s = "Не указано"

    lot_before = await get_lot_by_id(auction_id)
    old_val = (lot_before or {}).get("craft_uid_possible")

    await update_lot_field(auction_id, "craft_uid_possible", new_val)

    await notify_owners_lot_changed(
        call.bot,
        auction_id=auction_id,
        admin_user=call.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Крафт на UID", old_val, new_val)],
    )

    lot_after = dict(lot_before or {})
    lot_after["craft_uid_possible"] = new_val

    await send_lot_edit_log(
        call.bot,
        admin_user=call.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Крафт на UID", old_val, new_val)],
        audit_action_type="edit_lot_craft_uid",
        audit_details=f"craft_uid_possible: {old_val} -> {new_val}",
    )

    await call.message.answer(
        f"✅ Крафт на UID: <b>{val_s}</b>.",
        parse_mode="HTML",
        reply_markup=_back_to_lot_kb(),
    )
    await call.answer()


@router.message(EditScheduleFSM.entering_value, F.photo | F.video | F.animation | F.document)
@admin_only
async def edit_schedule_value_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    auction_id = data.get("auction_id")

    if field != "photo":
        await message.answer("Сейчас ожидается не медиа. Нажмите ⬅️ Назад и выберите нужное поле.")
        return

    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.animation:
        file_id = message.animation.file_id
    elif message.document and (message.document.mime_type or "").startswith("video/"):
        file_id = message.document.file_id

    if not file_id:
        await message.answer("Пришлите фото или видео.")
        return

    lot_before = await get_lot_by_id(auction_id)
    old_media = (lot_before or {}).get("image_id") or (lot_before or {}).get("card_image_id")

    await update_lot_field(auction_id, "image_id", file_id)

    await notify_owners_lot_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[("Медиа", short_media_id(old_media), short_media_id(file_id))],
    )

    lot_after = dict(lot_before or {})
    lot_after["image_id"] = file_id

    await send_lot_edit_log(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[("Медиа", short_media_id(old_media), short_media_id(file_id))],
        audit_action_type="edit_lot_media",
        audit_details=f"Медиа обновлено: {short_media_id(old_media)} -> {short_media_id(file_id)}",
    )

    await message.answer("✅ Медиа обновлено.")
    await _send_edit_lot_menu(message, state, auction_id)


@router.message(EditScheduleFSM.entering_value, F.text)
@admin_only
async def edit_schedule_value_text(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    auction_id = data.get("auction_id")

    if not auction_id:
        await message.answer("Потерялся auction_id. Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)
    txt = (message.text or "").strip()

    # Цена
    if field == "price":
        try:
            price = int(txt)
            if price <= 0:
                raise ValueError
        except Exception:
            await message.answer("Введите корректную цену (целое число > 0).", reply_markup=_back_to_lot_kb())
            return

        lot_before = await get_lot_by_id(auction_id)
        old_price = (lot_before or {}).get("start_price")
        cur = (lot_before or {}).get("currency")

        await update_auction_price(auction_id, price)

        await notify_owners_lot_changed(
            message.bot,
            auction_id=auction_id,
            admin_user=message.from_user,
            title="Изменения по вашему лоту",
            stage_label="в расписании",
            changes=[("Стартовая цена", old_price, price)],
        )

        lot_after = dict(lot_before or {})
        lot_after["start_price"] = price

        old_label = f"{old_price} {cur}" if old_price is not None and cur else (
            str(old_price) if old_price is not None else "—"
        )
        new_label = f"{price} {cur}" if cur else str(price)

        await send_lot_edit_log(
            message.bot,
            admin_user=message.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Стартовая цена", old_label, new_label)],
            audit_action_type="edit_lot_price",
            audit_details=f"Стартовая цена: {old_label} -> {new_label}",
        )

        await message.answer("✅ Цена обновлена.")
        await _send_edit_lot_menu(message, state, auction_id)
        return

    # Свободный аукцион: собственные варианты оплаты/обмена
    if field == "custom_offer_terms":
        if len(txt) < 3:
            await message.answer("Опишите варианты подробнее, минимум 3 символа.", reply_markup=_back_to_lot_kb())
            return
        if len(txt) > 500:
            await message.answer("Описание слишком длинное. Максимум 500 символов.", reply_markup=_back_to_lot_kb())
            return

        lot_before = await get_lot_by_id(auction_id) or {}
        if _norm_auk_kind(lot_before.get("auction_kind")) != "free":
            await message.answer("Свои варианты доступны только для свободного аукциона.")
            await state.clear()
            return

        old_label = currency_choices_label(
            lot_before.get("accepted_currencies"),
            fallback=lot_before.get("currency"),
            custom_terms=lot_before.get("custom_offer_terms"),
        )
        await update_lot_field(auction_id, "currency", "чашки")
        await update_lot_field(auction_id, "accepted_currencies", ["чашки", "алмазы"])
        await update_lot_field(auction_id, "custom_offer_terms", txt)
        await update_lot_field(auction_id, "start_price", 0)
        new_label = currency_choices_label(
            ["чашки", "алмазы"],
            fallback="чашки",
            custom_terms=txt,
        )
        lot_after = dict(lot_before)
        lot_after.update(
            currency="чашки",
            accepted_currencies=["чашки", "алмазы"],
            custom_offer_terms=txt,
            start_price=0,
        )
        await send_lot_edit_log(
            message.bot,
            admin_user=message.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Варианты предложений", old_label, new_label)],
            audit_action_type="edit_lot_custom_offer_terms",
            audit_details=f"Свои варианты: {old_label} -> {new_label}",
        )
        await notify_owners_lot_changed(
            message.bot,
            auction_id=auction_id,
            admin_user=message.from_user,
            title="Изменения по вашему лоту",
            stage_label="в расписании",
            changes=[("Варианты предложений", old_label, new_label)],
        )
        await message.answer(f"✅ Варианты обновлены: <b>{html.escape(new_label)}</b>.", parse_mode="HTML")
        await state.clear()
        return

    # Комментарий
    if field == "comment":
        new_comment = "" if txt == "-" else txt

        lot_before = await get_lot_by_id(auction_id)
        old_comment = (lot_before or {}).get("comment")

        await update_lot_field_with_notify(
            message.bot,
            auction_id=auction_id,
            field="comment",
            value=new_comment,
            admin_user=message.from_user,
            field_label="Комментарий",
        )

        lot_after = dict(lot_before or {})
        lot_after["comment"] = new_comment

        await send_lot_edit_log(
            message.bot,
            admin_user=message.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Комментарий", old_comment, new_comment)],
            audit_action_type="edit_lot_comment",
            audit_details=f"Комментарий: {(old_comment or '(пусто)')} -> {(new_comment or '(пусто)')}",
        )

        await message.answer("✅ Комментарий обновлён.")
        await _send_edit_lot_menu(message, state, auction_id)
        return

    await message.answer(
        "Сейчас ожидается другое действие. Нажмите ⬅️ Назад и выберите поле.",
        reply_markup=_back_to_lot_kb(),
    )


@router.callback_query(F.data == "edit_time_months")
@admin_only
async def edit_time_months(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")

    # если нет auction_id, просто вернёмся в старт редактуры расписания
    if not auction_id:
        await start_edit_schedule(call.message, state)
        await call.answer()
        return

    await state.set_state(EditScheduleFSM.choosing_month)
    kb = months_keyboard(prefix="edit_schedule", auction_id=int(auction_id))
    kb = _kb_add_back(kb, "edit_lot_back")
    await call.message.answer("Выберите месяц для изменения времени лота:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("edit_schedule|"))
@admin_only
async def edit_schedule_router(call: CallbackQuery, state: FSMContext):
    auction_id, date_str = await parse_auction_and_date_from_callback(call.data, state)

    if not date_str:
        await call.answer("Ошибка формата даты.", show_alert=True)
        return

    parts = date_str.split("-")

    # 1) Выбран месяц: YYYY-MM
    if len(parts) == 2:
        try:
            year, month = map(int, parts)
        except Exception:
            await call.answer("Ошибка даты месяца.", show_alert=True)
            return

        await state.update_data(year=year, month=month, auction_id=auction_id)

        kb = days_keyboard("edit_schedule", auction_id, year, month)
        # Назад снизу: возвращаемся к месяцам
        # (для лота это вернёт в months list, а оттуда можно уйти в меню лота)
        kb = _kb_add_back(kb, "edit_time_months")

        await call.message.answer(
            "Выберите день для изменения времени лота:" if auction_id else "Выберите день:",
            reply_markup=kb,
        )
        await state.set_state(EditScheduleFSM.choosing_day)
        await call.answer()
        return

    # 2) Выбран день: YYYY-MM-DD
    if len(parts) == 3:
        try:
            year, month, day = map(int, parts)
            selected_date = date(year, month, day)
        except Exception:
            await call.answer("Ошибка даты дня.", show_alert=True)
            return

        await state.update_data(selected_date=selected_date, auction_id=auction_id)

        # Редактируем конкретный лот (перенос времени)
        if auction_id:
            free_slots, is_luxury, schedule_str, lot, auctions = await get_free_slots_and_schedule_for_lot(
                int(auction_id), selected_date
            )

            if not free_slots:
                await call.message.answer("Нет свободных слотов на выбранную дату.")
                await call.answer()
                return

            kb = time_slots_keyboard("edit_time_slot", int(auction_id), free_slots, is_luxury)

            # Назад снизу: вернуться к дням этого месяца
            kb = _kb_add_back(kb, f"edit_schedule|{int(auction_id)}|{year:04d}-{month:02d}")

            text = (
                f"Расписание на {selected_date.strftime('%d.%m.%Y')}:\n"
                f"{schedule_str}\n\n"
                f"❗️ — слот занят этой же картой этим же владельцем\n"
                f"🟡 — слот занят этой же картой, но у другого владельца (вы можете выбрать этот слот)"
            )
            await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
            await state.set_state(EditScheduleFSM.choosing_time)
            await call.answer()
            return

        # Просмотр/редактура списка лотов на день (без переносов)
        auctions = await get_auctions_by_date_with_owners(selected_date)
        if not auctions:
            await call.message.answer("На выбранный день нет лотов.")
            await call.answer()
            return

        for lot in auctions:
            a_id = int(lot["auction_id"])
            owners_text = await get_lot_owners_text(a_id)
            caption = build_schedule_lot_caption(lot, owners_text)
            kb = build_schedule_lot_keyboard(
                a_id,
                delete_callback_prefix="delete_lot",
                delete_label="🗑️ Удалить",
            )

            image_id = lot.get("image_id")
            if image_id:
                await safe_answer_photo(call.message, image_id, caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                await call.message.answer(caption, reply_markup=kb, parse_mode="HTML")

        await call.answer()
        return

    # <- вот тут у тебя и был битый show_alert
    await call.answer("Непонятный формат даты.", show_alert=True)


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


# @router.callback_query(EditScheduleFSM.choosing_month, F.data.startswith("edit_schedule|"))
# async def edit_schedule_choose_month(call: CallbackQuery, state: FSMContext):
#     auction_id, year_month = await parse_auction_and_date_from_callback(call.data, state)
#     state_data = await state.get_data()
#     if not auction_id:
#         auction_id = state_data.get("auction_id")
#     try:
#         year, month = map(int, year_month.split('-')[:2])
#     except Exception as e:
#         await call.answer("Ошибка даты! Данные: " + str(year_month), show_alert=True)
#         return
#     await state.update_data(year=year, month=month)
#     kb = days_keyboard("edit_schedule", auction_id, year, month)
#     await call.message.answer(
#         "Выберите день для изменения времени лота:" if auction_id else "Выберите день для просмотра расписания:",
#         reply_markup=kb
#     )
#     await state.set_state(EditScheduleFSM.choosing_day)
#     await call.answer()


@router.callback_query(F.data == "edit_lot_back")
@admin_only
async def edit_lot_back(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    if auction_id:
        await _send_edit_lot_menu(call.message, state, int(auction_id))
    else:
        await start_edit_schedule(call.message, state)
    await call.answer()


@router.callback_query(F.data == "edit_schedule_back")
@admin_only
async def edit_schedule_back_any(call: CallbackQuery, state: FSMContext):
    # безопасный “назад” без попыток угадать твою текущую вложенность
    await start_edit_schedule(call.message, state)
    await call.answer()


# @router.callback_query(EditScheduleFSM.choosing_day, F.data.startswith("edit_schedule|"))
# async def edit_schedule_choose_day(call: CallbackQuery, state: FSMContext):
#     auction_id, date_str = await parse_auction_and_date_from_callback(call.data, state)
#     if not date_str:
#         await call.message.answer("Ошибка формата callback!", parse_mode="HTML")
#         await call.answer()
#         return
#     year, month, day = map(int, date_str.split('-'))
#     selected_date = date(year, month, day)
#     await state.update_data(selected_date=selected_date)
#     if auction_id:
#         free_slots, is_luxury, schedule_str, lot, auctions = await get_free_slots_and_schedule_for_lot(auction_id,
#                                                                                                        selected_date)
#         if not free_slots:
#             await call.message.answer("Нет свободных слотов на выбранную дату.", parse_mode="HTML")
#             await call.answer()
#             await state.set_state(EditScheduleFSM.choosing_day)
#             return
#         kb = time_slots_keyboard("edit_time_slot", auction_id, free_slots, is_luxury)
#         text = (
#             f"Расписание на {selected_date.strftime('%d.%m.%Y')}:\n"
#             f"{schedule_str}\n\n"
#             f"❗️ — слот занят этой же картой этим же владельцем\n"
#             f"🟡 — слот занят этой же картой, но у другого владельца (вы можете выбрать этот слот)"
#         )
#         await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
#         await state.set_state(EditScheduleFSM.choosing_time)
#     else:
#         auctions = await get_auctions_by_date_with_owners(selected_date)
#         if not auctions:
#             await call.message.answer("На выбранный день нет лотов.", parse_mode="HTML")
#             await call.answer()
#             await state.set_state(EditScheduleFSM.choosing_month)
#             return
#         for lot in auctions:
#             auction_id = lot["auction_id"]
#             currency_raw = lot.get("currency", "")
#             currency_fancy = CURRENCY_EMOJI.get(currency_raw.lower(), currency_raw)
#             kb = types.InlineKeyboardMarkup(inline_keyboard=[
#                 [
#                     types.InlineKeyboardButton(text="✏️ Редактировать",
#                                                callback_data=f"edit_schedule_lot|{auction_id}"),
#                     types.InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_lot|{auction_id}")
#                 ]
#             ])
#             image_id = lot.get("image_id")
#             hero_name = lot.get('hero_name') or "-"
#             deck_id = lot.get('deck_id') if lot.get('deck_id') is not None else "—"
#             owners_text = await get_lot_owners_text(lot['auction_id'])
#             admin_username, approved_at = await get_lot_approval_info(lot['auction_id'])
#             created_at = lot.get("created_at")
#             created_str = created_at.strftime('%d.%m.%Y %H:%M') if created_at else '-'
#             caption = (
#                 f"🎴 <b>{lot['card_name']}</b>\n"
#                 f"🔎 Auction ID: <b>{lot['auction_id']}</b>\n"
#                 f"👤 Герой: <b>{hero_name}</b>\n"
#                 f"Колода: <b>{deck_id}</b>\n"
#                 f"⏰ <b>{lot['start_time'].strftime('%H:%M')}–{lot['end_time'].strftime('%H:%M')}</b>\n"
#                 f"💵 <b>{lot['start_price']} {currency_fancy}</b>\n"
#                 f"💬 {lot.get('comment', '-') or '-'}\n"
#                 f"👑 Владелец(ы): {owners_text or '-'}\n"
#                 f"🕑 Дата заявки: {created_str}\n"
#             )
#             if image_id:
#                 await call.message.answer_photo(photo=image_id, caption=caption, reply_markup=kb, parse_mode="HTML")
#             else:
#                 await call.message.answer(caption, reply_markup=kb, parse_mode="HTML")


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


@router.callback_query(F.data.startswith("delete_lot|"))
@admin_only
async def delete_lot_confirm(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    lot = await get_lot_by_id(auction_id)
    text = (
        f"❗️ <b>Удалить лот?</b>\n\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Герой: {lot['hero_name']}\n"
        f"Стартовая цена: {lot['start_price']} {lot['currency']}\n\n"
        "<b>Действие необратимо!</b>"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"edit_schedule_lot|{auction_id}")],
        [types.InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"delete_lot_final|{auction_id}")]
    ])
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("delete_lot_final|"))
@admin_only
async def delete_lot_final(call: CallbackQuery, state: FSMContext):
    auction_id = int(call.data.split("|")[1])
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await call.message.answer("Лот уже удалён или не найден.")
        await call.answer()
        return

    await delete_lot(auction_id)
    owners = await get_lot_owners(auction_id)
    for o in owners:
        try:
            await call.bot.send_message(
                o['user_id'],
                f"🗑️ Ваш лот <b>{lot['card_name']}</b> был удалён модератором.",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[NOTIFY OWNER ERROR] {e}")
    owners_text = await get_lot_owners_text(auction_id)
    start_msk = to_moscow_wall(lot["start_time"])
    end_msk = to_moscow_wall(lot["end_time"])
    log_text = (
        f"🗑️ <b>Лот удалён админом</b>\n"
        f"🎴 Лот №{lot['auction_id']}: {lot['card_name']}\n"
        f"🙍‍♂️ Владелец(ы): {owners_text or '-'}\n"
        f"💰 Старт: {lot['start_price']} {lot['currency']}\n"
        f"💬 Комментарий: {lot.get('comment', '-')}\n"
        f"📅 Дата выхода: {start_msk.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {start_msk.strftime('%H:%M')}–{end_msk.strftime('%H:%M')} (МСК)\n"
        f"🛠️ Действие: удаление через панель расписания"
    )
    await send_admin_log(call.bot, log_text)
    await log_audit_action(
        user_id=call.from_user.id,
        action_type="admin_delete_lot",
        auction_id=auction_id,
        details="Лот удалён через редактор расписания"
    )
    await call.message.answer("✅ Лот успешно удалён.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("edit_time_slot|"))
@admin_only
async def edit_time_slot_confirm(call: CallbackQuery, state: FSMContext):
    _, auction_id, iso_str = call.data.split("|")
    auction_id = int(auction_id)
    start_time = to_moscow(datetime.fromisoformat(iso_str))
    end_time = auction_end_at_59(start_time)

    await state.update_data(new_start_time=start_time, new_end_time=end_time)

    lot = await get_lot_by_id(auction_id)
    text = (
        f"Подтвердите изменение времени для лота <b>{lot['card_name']}</b>:\n\n"
        f"Новое время: {start_time.strftime('%d.%m %H:%M')}–{end_time.strftime('%H:%M')}"
    )

    back_cb = f"edit_schedule|{auction_id}|{start_time.date().isoformat()}"
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"edit_time_save|{auction_id}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)]
    ])

    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("edit_time_save|"))
@admin_only
async def save_edited_time(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "new_start_time" not in data or "new_end_time" not in data:
        await safe_callback_answer(
            call,
            "Не найдено выбранное время. Откройте перенос заново.",
            show_alert=True,
        )
        return

    auction_id = int(call.data.split("|")[1])
    start_time = to_moscow(data["new_start_time"])
    end_time = auction_end_at_59(start_time)

    # Telegram keeps the loading spinner until answerCallbackQuery is called.
    # A reschedule also sends logs, refreshes an old card and notifies owners,
    # so answering only at the very end made a successful move look frozen.
    await safe_callback_answer(call, "⏳ Переношу лот…")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    lot = await get_lot_by_id(auction_id)
    if not lot:
        await call.message.answer("❌ Лот не найден. Обновите расписание.")
        return

    old_start = to_moscow(lot["start_time"])
    old_end = to_moscow(lot["end_time"])

    try:
        moderation_service = await AuctionModerationService.create()
        persisted_lot = await asyncio.wait_for(
            moderation_service.reschedule(
                auction_id,
                start_time=start_time,
                end_time=end_time,
            ),
            timeout=12,
        )
    except asyncio.TimeoutError:
        logger.error("Timed out while rescheduling auction_id=%s", auction_id)
        await call.message.answer(
            "❌ База слишком долго отвечала. Перенос отменён транзакцией. "
            "Откройте расписание заново и повторите попытку."
        )
        return
    except AuctionSlotConflict:
        await call.message.answer("❌ Этот слот уже занят. Выберите другое время.")
        return
    except InvalidAuctionTransition as exc:
        await call.message.answer(
            f"❌ Лот нельзя перенести из статуса <code>{exc.current}</code>. "
            "Обновите расписание.",
            parse_mode="HTML",
        )
        return
    except Exception:
        logger.exception("Could not reschedule auction_id=%s", auction_id)
        await call.message.answer(
            "❌ Не удалось перенести лот из-за внутренней ошибки. "
            "Изменение не подтверждено; проверьте расписание."
        )
        return

    persisted_start = to_moscow(persisted_lot["start_time"])
    persisted_end = to_moscow(persisted_lot["end_time"])
    if (
        persisted_start.replace(second=0, microsecond=0)
        != start_time.replace(second=0, microsecond=0)
        or persisted_end.replace(microsecond=0) != end_time.replace(microsecond=0)
    ):
        logger.error(
            "Reschedule verification mismatch auction_id=%s expected=%s/%s actual=%s/%s",
            auction_id,
            start_time,
            end_time,
            persisted_start,
            persisted_end,
        )
        await call.message.answer(
            "⚠️ База вернула другое время после переноса. "
            "Откройте расписание заново; успешный лог не отправлен."
        )
        return

    # The database move is already committed. Show the result before optional
    # Telegram/logging side effects so a slow log chat cannot hide success.
    await call.message.answer(
        "✅ <b>Лот перенесён</b>\n"
        f"{old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} → "
        f"{persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)",
        parse_mode="HTML",
    )

    owners = await get_lot_owners(auction_id)
    owner_ids = [o["user_id"] for o in owners]
    user_flags = []
    for owner_id in owner_ids:
        try:
            is_lux, user = await asyncio.gather(
                is_luxury_user(owner_id),
                get_user(owner_id),
            )
        except Exception:
            logger.exception("Could not read owner flags user_id=%s", owner_id)
            continue
        is_trusted = user and user.get("is_trusted")
        if is_lux:
            user_flags.append("Лакшери")
        if is_trusted:
            user_flags.append("Доверенный")
    flags_str = ", ".join(sorted(set(user_flags))) if user_flags else "Обычный"

    admin = {
        "id": call.from_user.id,
        "username": call.from_user.username or call.from_user.full_name,
    }
    owners_text = await get_lot_owners_text(auction_id)

    try:
        card_refresh_status = await asyncio.wait_for(
            refresh_schedule_card_origin(
                call.bot,
                state,
                auction_id,
                lot=persisted_lot,
                owners_text=owners_text,
            ),
            timeout=6,
        )
    except asyncio.TimeoutError:
        card_refresh_status = False
        logger.warning("Schedule card refresh timed out auction_id=%s", auction_id)
    except Exception:
        card_refresh_status = False
        logger.exception("Schedule card refresh failed auction_id=%s", auction_id)

    log_text = format_admin_action_log(
        action="move_lot",
        admin=admin,
        lot={
            **persisted_lot,
            "start_time": persisted_start,
            "end_time": persisted_end,
        },
        owners_text=owners_text,
    )
    log_text += (
        f"\n🏷️ <b>Тип владельца:</b> {flags_str}"
        f"\n📅 <b>Старое время:</b> {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} (МСК)"
        f"\n➡️ <b>Новое время:</b> {persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)"
    )
    try:
        await asyncio.wait_for(send_admin_log(call.bot, log_text), timeout=6)
    except Exception:
        logger.exception("Could not send reschedule admin log auction_id=%s", auction_id)

    try:
        await log_audit_action(
            user_id=call.from_user.id,
            action_type="move_lot",
            auction_id=auction_id,
            details=(
                f"Перенос с {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')} "
                f"на {persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} | "
                f"Тип владельца: {flags_str} | "
                f"Карточка обновлена: {card_refresh_status}"
            ),
        )
    except Exception:
        logger.exception("Could not write reschedule audit auction_id=%s", auction_id)

    async def _notify_owner(owner: dict) -> None:
        await call.bot.send_message(
            owner["user_id"],
            f"⏳ <b>Ваша карта <u>{lot['card_name']}</u> была перенесена!</b>\n\n"
            f"<b>Новое время аукциона:</b> "
            f"{persisted_start.strftime('%d.%m %H:%M')}–{persisted_end.strftime('%H:%M')} (МСК)\n"
            f"Ранее стояло: {old_start.strftime('%d.%m %H:%M')}–{old_end.strftime('%H:%M')}\n"
            f"<b>Ваш статус:</b> {flags_str}",
            parse_mode="HTML",
        )

    for owner in owners:
        try:
            await asyncio.wait_for(_notify_owner(owner), timeout=5)
        except Exception:
            logger.exception(
                "Could not notify owner about rescheduled auction_id=%s user_id=%s",
                auction_id,
                owner.get("user_id"),
            )

    if card_refresh_status is False:
        await call.message.answer(
            "⚠️ Время в базе изменено, но старую карточку Telegram не обновил. "
            "После повторного открытия расписания будет показано новое время."
        )

    await state.clear()


@router.callback_query(EditScheduleFSM.entering_value, F.data.startswith("set_currency|"))
@admin_only
async def set_currency_handler(call: CallbackQuery, state: FSMContext):
    _, selected = call.data.split("|", 1)
    data = await state.get_data()
    auction_id = int(data.get("auction_id") or 0)
    if not auction_id:
        await call.answer("Потерялся ID лота.", show_alert=True)
        return

    lot_before = await get_lot_by_id(auction_id) or {}
    kind = _norm_auk_kind(lot_before.get("auction_kind"))

    if selected == "custom_combo":
        if kind != "free":
            await call.answer("Свои варианты доступны только для свободного аукциона.", show_alert=True)
            return
        await state.update_data(edit_field="custom_offer_terms")
        await call.message.answer(
            "Введите свои варианты оплаты или обмена одним сообщением (до 500 символов):",
            reply_markup=_back_to_lot_kb(),
        )
        await call.answer()
        return

    if selected == "чашки_алмазы":
        currency = "чашки"
        accepted = ["чашки", "алмазы"]
    else:
        currency = selected
        accepted = [currency]

    if kind in {"reverse", "free"}:
        if currency not in {"чашки", "алмазы"}:
            await call.answer("Доступны только чай, алмазы или оба варианта.", show_alert=True)
            return

        old_label = currency_choices_label(
            lot_before.get("accepted_currencies"),
            fallback=lot_before.get("currency"),
            custom_terms=lot_before.get("custom_offer_terms"),
        )
        new_label = currency_choices_label(accepted, fallback=currency)
        await update_lot_field(auction_id, "currency", currency)
        await update_lot_field(auction_id, "accepted_currencies", accepted)
        await update_lot_field(auction_id, "custom_offer_terms", None)
        await update_lot_field(auction_id, "start_price", 0)

        lot_after = dict(lot_before)
        lot_after.update(
            currency=currency,
            accepted_currencies=accepted,
            custom_offer_terms=None,
            start_price=0,
        )
        await send_lot_edit_log(
            call.bot,
            admin_user=call.from_user,
            auction_id=auction_id,
            lot_for_log=lot_after,
            changes=[("Валюта/предложения", old_label, new_label)],
            audit_action_type="edit_lot_currency",
            audit_details=f"Валюты: {old_label} -> {new_label}",
        )
        await notify_owners_lot_changed(
            call.bot,
            auction_id=auction_id,
            admin_user=call.from_user,
            title="Изменения по вашему лоту",
            stage_label="в расписании",
            changes=[("Валюта/предложения", old_label, new_label)],
        )
        await call.message.answer(
            f"✅ Валюта обновлена: <b>{html.escape(new_label)}</b>.",
            parse_mode="HTML",
        )
        await call.answer()
        await state.clear()
        return

    await state.update_data(new_currency=currency)
    await call.message.answer(f"Введите новую стартовую цену (<b>{currency}</b>):")
    await state.set_state(EditScheduleFSM.editing_currency_price)
    await call.answer()


@router.message(EditScheduleFSM.editing_currency_price)
@admin_only
async def set_currency_price_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    currency = data.get("new_currency")

    if not auction_id or not currency:
        await message.answer("Потерялись данные (auction_id/валюта). Начните заново.")
        await state.clear()
        return

    auction_id = int(auction_id)

    try:
        price = int(message.text)
        if price <= 0:
            await message.answer("Цена должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("Введите корректную цену (целое положительное число).")
        return

    lot_before = await get_lot_by_id(auction_id)
    old_currency = (lot_before or {}).get("currency")
    old_price = (lot_before or {}).get("start_price")

    await update_auction_currency(auction_id, currency)
    await update_lot_field(auction_id, "accepted_currencies", [currency])
    await update_auction_price(auction_id, price)

    lot_after = dict(lot_before or {})
    lot_after["currency"] = currency
    lot_after["start_price"] = price

    old_label = f"{old_price} {old_currency}" if old_price is not None and old_currency else (
        str(old_price) if old_price is not None else "—")
    new_label = f"{price} {currency}"

    await send_lot_edit_log(
        message.bot,
        admin_user=message.from_user,
        auction_id=auction_id,
        lot_for_log=lot_after,
        changes=[
            ("Валюта", old_currency, currency),
            ("Стартовая цена", old_label, new_label),
        ],
        audit_action_type="edit_lot_currency_price",
        audit_details=f"Валюта/цена: {old_currency} {old_price} -> {currency} {price}",
    )

    await notify_owners_lot_changed(
        message.bot,
        auction_id=auction_id,
        admin_user=message.from_user,
        title="Изменения по вашему лоту",
        stage_label="в расписании",
        changes=[
            ("Валюта", old_currency, currency),
            ("Стартовая цена", old_price, price),
        ],
    )

    await message.answer(
        f"Валюта лота успешно изменена на <b>{currency}</b>!\n"
        f"Стартовая цена теперь <b>{price} {currency}</b>.",
        parse_mode="HTML",
    )
    await state.clear()


@router.message(EditScheduleFSM.entering_value)
async def edit_price_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    auction_id = data.get("auction_id")
    try:
        price = int(message.text)
        if price <= 0:
            await message.answer("Цена должна быть положительным числом.")
            return
    except ValueError:
        await message.answer("Введите корректную цену (целое положительное число).")
        return
    await update_auction_price(auction_id, price)
    lot = await get_lot_by_id(auction_id)
    owners_text = await get_lot_owners_text(auction_id)
    log_text = format_admin_action_log(
        action="edit_lot",
        admin={"id": message.from_user.id, "username": message.from_user.username or message.from_user.full_name},
        lot={**lot, "start_price": price},
        owners_text=owners_text
    )
    await send_admin_log(message.bot, log_text)
    await log_audit_action(
        user_id=message.from_user.id,
        action_type="edit_lot_price",
        auction_id=auction_id,
        details=f"Изменена цена на {price} {lot['currency']}"
    )
    for o in await get_lot_owners(auction_id):
        try:
            await message.bot.send_message(
                o['user_id'],
                f"💰 <b>У вашей карты <u>{lot['card_name']}</u> изменилась стартовая цена!</b>\n\n"
                f"Теперь: <b>{price} {lot['currency']}</b>.",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[NOTIFY OWNER ERROR] {e}")
    await message.answer(
        f"Стартовая цена лота успешно изменена на <b>{price} {lot['currency']}</b>!",
        parse_mode="HTML"
    )
    await state.clear()


@router.message(F.text.in_(["🔎 Список карт", "/cards"]), F.chat.type == "private")
@admin_only
async def show_decks_for_cards(message: Message):
    decks = await get_all_decks()
    if not decks:
        await message.answer("В базе нет ни одной колоды!")
        return
    await message.answer(
        "Выберите колоду для просмотра списка карт:",
        reply_markup=decks_keyboard(decks, prefix="show_deck")
    )


@router.callback_query(F.data.startswith("show_deck_"))
@admin_only
async def show_cards_in_deck(call: CallbackQuery):
    deck_id = int(call.data.split("_")[-1])
    cards = await get_cards_by_deck_id(deck_id)
    if not cards:
        await call.message.answer("В этой колоде пока нет ни одной карты.")
        await call.answer()
        return
    decks = await get_all_decks()
    deck_name = next((d['deck_name'] for d in decks if d['deck_id'] == deck_id), "-")
    await call.message.answer(f"<b>Карты в колоде <u>{deck_name}</u>:</b>", parse_mode="HTML")
    for card in cards:
        rarity = (card.get('rarity') or '').strip().lower()
        emoji = RARITY_EMOJI.get(rarity, '')
        treasure = RARITY_TREASURE.get(rarity)
        rarity_ru = RARITY_RU.get(rarity, rarity)
        caption = (
            f"{emoji} Название: {card['card_name']}\n"
            f"Герой: {card.get('hero_name', '-')}\n"
            f"Номер: {card.get('num', '-')}\n"
            f"Редкость: {rarity_ru}"
        )
        if treasure:
            caption += f"  —  За разбив: {treasure} 🪙 сокровищ"
        caption += f"\nИстория: {card.get('story', '-')}\n"
        if card.get("quote"):
            caption += f"Цитата: {card['quote']}\n"
        image_id = card.get("image_id")
        try:
            if image_id and image_id != "DEFAULT_PHOTO_ID":
                await _answer_media_any(call.message, image_id, caption=caption)
            else:
                await call.message.answer(caption)
        except Exception as e:
            await call.message.answer(f"{emoji} {card['card_name']}\n[Ошибка отправки медиа: {e}]")
    await call.answer()


@router.message(F.text == "👥 Пользователи", F.chat.type == "private")
@admin_only
async def users_menu(message: Message):
    await message.answer(
        "Действия с пользователями:",
        reply_markup=menu_keyboard(
            ["👤 Список админов", "👥 Список пользователей", "🤝 Список доверенных"],
            ["⬅️ Назад"]
        )
    )


#
# @router.message(F.text.in_(["/users", "👥 Список пользователей"]), F.chat.type == "private")
# async def users_list_cmd(message: Message):
#     await show_users(message)
#
#
# @router.message(F.text.in_(["/admins", "👤 Список админов"]), F.chat.type == "private")
# async def admins_list_cmd(message: Message):
#     await show_admins(message)
#
#
# @router.message(F.text.in_(["/trusted", "🤝 Список доверенных"]), F.chat.type == "private")
# async def trusted_list_cmd(message: Message):
#     await show_trusted(message)


@router.message(F.text == "🚫 Логи", F.chat.type == "private")
@admin_only
async def logs_menu(message: Message):
    await message.answer(
        "Действия с логами и аудитом:",
        reply_markup=menu_keyboard(
            ["📋 Аудит-логи"],
            ["⬅️ Назад"]
        )
    )


@router.message(F.text == "📋 Аудит-логи", F.chat.type == "private")
@admin_only
async def audit_logs_cmd(message: Message):
    logs = await get_audit_logs()
    if not logs:
        await message.answer("Логи не найдены.")
        return
    msg = "<b>Последние действия админов:</b>\n"
    msg += "".join(format_log_entry(log) for log in logs)
    await message.answer(msg, parse_mode="HTML")


@router.message(F.text == "📣 Рассылка", F.chat.type == "private")
@admin_only
async def broadcast_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Массовая рассылка всем пользователям:",
        reply_markup=menu_keyboard(["✉️ Создать рассылку"], ["⬅️ Назад"])
    )


@router.message(F.text == "✉️ Создать рассылку", F.chat.type == "private")
@admin_only
async def start_broadcast_from_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        ADMIN_MESSAGES["broadcast_enter_text"],
        reply_markup=menu_keyboard([BUTTONS["cancel"]])
    )
    await state.set_state(BroadcastFSM.waiting_for_text)


@router.message(F.text == "📊 Статистика", F.chat.type == "private")
@admin_only
async def stats_menu(message: Message):
    await message.answer(
        "Раздел: Статистика",
        reply_markup=menu_keyboard(
            ["📈 Показать статистику"],
            ["📅 Полное расписание"],
            ["⬅️ Назад"],
        ),
    )


# --- Полное расписание для раздела статистики (любой месяц/год со стрелочками) ---

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


@router.message(F.text == "📅 Полное расписание", F.chat.type == "private")
@admin_only
async def stats_full_schedule(message: Message, state: FSMContext):
    await state.clear()

    today = date.today()
    year, month = today.year, today.month

    await message.answer(
        "Выберите месяц для просмотра расписания:",
        reply_markup=_kb_stats_schedule_navigator(year, month),
    )
    await state.set_state(PreviewScheduleFSM.choosing_month)


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data.startswith("stats_schedule_set|"))
@admin_only
async def stats_schedule_set_month(call: CallbackQuery):
    try:
        _, ym = (call.data or "").split("|", 1)
        year_s, month_s = ym.split("-", 1)
        year = int(year_s)
        month = int(month_s)
        if month < 1 or month > 12:
            raise ValueError("month out of range")
    except Exception:
        await call.answer("Кривая кнопка.", show_alert=True)
        return

    msg = getattr(call, "message", None)
    if isinstance(msg, Message):
        try:
            await msg.edit_reply_markup(reply_markup=_kb_stats_schedule_navigator(year, month))
        except Exception:
            pass

    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data == "stats_schedule_today")
@admin_only
async def stats_schedule_today(call: CallbackQuery):
    today = date.today()
    year, month = today.year, today.month

    msg = getattr(call, "message", None)
    if isinstance(msg, Message):
        try:
            await msg.edit_reply_markup(reply_markup=_kb_stats_schedule_navigator(year, month))
        except Exception:
            pass

    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data == "stats_schedule_noop")
@admin_only
async def stats_schedule_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(PreviewScheduleFSM.choosing_month, F.data == "stats_schedule_noop")
@admin_only
async def stats_schedule_noop(call: CallbackQuery):
    await call.answer()

@router.message(F.text == "🎴 Карты", F.chat.type == "private")
async def cards_menu(message: Message):
    await message.answer(
        "Раздел: Карты",
        reply_markup=decks_menu_keyboard()
    )


@router.message(F.text == "➕ Добавить колоду", F.chat.type == "private")
async def add_deck_button(message: Message, state: FSMContext):
    await add_deck_fsm_entry(message, state)


@router.message(AddDeckFSM.waiting_for_admin_password)
async def check_admin_password(message: Message, state: FSMContext):
    if message.from_user.id in ADMINS_OWNERS or message.text.strip() == ADMIN_SECRET:
        await message.answer("Пароль принят. Введите название новой колоды:", reply_markup=back_keyboard())
        await state.set_state(AddDeckFSM.waiting_for_deck_name)
    else:
        await message.answer("❌ Неверный пароль!", reply_markup=back_keyboard())


@router.message(AddDeckFSM.waiting_for_deck_name)
async def deck_name_received(message: Message, state: FSMContext):
    deck_name = message.text.strip()
    await state.update_data(deck_name=deck_name)
    await message.answer(
        f"Добавить новую колоду с названием:\n<b>{deck_name}</b>?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_add_deck")],
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_deck")],
            ]
        ),
        parse_mode="HTML"
    )
    await state.set_state(AddDeckFSM.waiting_for_confirmation)


@router.callback_query(AddDeckFSM.waiting_for_confirmation, F.data == "confirm_add_deck")
async def confirm_add_deck(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await add_deck(data["deck_name"])
    await call.message.answer(f"Колода <b>{data['deck_name']}</b> успешно добавлена!", parse_mode="HTML")
    await log_audit_action(
        user_id=call.from_user.id,
        action_type="add_deck",
        auction_id=None,
        details=f"Добавлена колода: {data['deck_name']}"
    )
    await send_admin_log(
        call.bot,
        format_admin_action_log(
            action="add_deck",
            admin={"id": call.from_user.id, "username": call.from_user.username or call.from_user.full_name},
            lot={"deck_name": data["deck_name"]}
        )
    )
    await state.clear()
    await call.answer()


@router.callback_query(AddDeckFSM.waiting_for_confirmation, F.data == "cancel_add_deck")
async def cancel_add_deck(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Добавление колоды отменено.")
    await state.clear()
    await call.answer()


@router.message(F.text == "➕ Добавить карту", F.chat.type == "private")
@admin_only
async def add_card_button(message: Message, state: FSMContext):
    await start_add_card_fsm(message, state)


@router.callback_query(F.data == "universal_cancel")
@admin_only
async def universal_cancel_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await send_admin_main_menu(call.message)
    await call.answer()


@router.callback_query(F.data.in_(CANCEL_TEXTS.keys()))
@admin_only
async def universal_cancel(call: CallbackQuery, state: FSMContext):
    await process_universal_cancel_callback(call, state)


@router.message(
    F.text.lower().in_(["назад", "⬅️ назад", "отмена"]),
    F.chat.type == "private"
)
@admin_only
async def universal_back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await send_admin_main_menu(message)


@router.callback_query(F.data.in_([
    "admin_back",
    "addadmin_cancel", "removeadmin_cancel",
    "givetrusted_cancel", "removetrusted_cancel",
    "universal_cancel"
]))
@admin_only
async def admin_inline_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.bot.send_message(
        call.message.chat.id,
        ADMIN_MESSAGES.get("admin_panel_greeting", "Добро пожаловать в админ-панель! Выберите раздел:"),
        reply_markup=menu_keyboard(
            ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
            ["📊 Статистика", "📣 Рассылка", "🚫 Логи"]
        )
    )
    await call.answer()


@router.callback_query(F.data == "pending_menu:auctions")
@admin_only
async def pending_menu_auctions(call: CallbackQuery):
    lots = await get_pending_auctions()
    if not lots:
        await call.message.answer("Нет pending-аукционов.")
        await call.answer()
        return

    for lot in lots:
        owners_list: list[Owner] = cast(list[Owner], await get_lot_owners(lot["auction_id"]))
        text = format_pending_lot(lot, owners_list)
        kb = build_lot_keyboard(lot, role="admin")
        await send_lot_card_safe(call.message, lot, text, kb)

    await call.answer()


@router.message(F.text.in_(['/adminhelp', '/admin_help']), F.chat.type == "private")
@admin_only
async def admin_help(message: Message):
    await message.answer(ADMIN_COMMANDS_INFO, parse_mode="HTML")


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


@router.message(
    (F.text.startswith("/card_video") | F.caption.startswith("/card_video")),
    F.chat.type == "private",
)
@admin_only
async def cmd_card_video(message: Message):
    raw = (message.text or message.caption or "").strip()
    parts = raw.split()

    if len(parts) < 2:
        await message.answer(
            "Формат: <code>/card_video CARD_ID</code>\n"
            "Команду пиши в подписи к видео или реплаем на видео.",
            parse_mode="HTML",
        )
        return

    try:
        card_id = int(parts[1])
    except Exception:
        await message.answer("CARD_ID должен быть числом.", parse_mode="HTML")
        return

    card = await get_card_by_id(card_id)
    if not card:
        await message.answer(f"Карта <code>{card_id}</code> не найдена.", parse_mode="HTML")
        return

    src = message
    media = _extract_video_from_message(src)

    if not media and message.reply_to_message:
        src = message.reply_to_message
        media = _extract_video_from_message(src)

    if not media:
        await message.answer(
            "Пришли видео с подписью <code>/card_video CARD_ID</code>\n"
            "или ответь командой на сообщение с видео.",
            parse_mode="HTML",
        )
        return

    file_id, unique_id, thumb_id = media

    res = await set_card_video_by_id(
        card_id=card_id,
        video_file_id=file_id,
        unique_id=unique_id,
        thumb_file_id=thumb_id,
    )

    if not res.get("ok"):
        await message.answer(f"Не вышло: <code>{res.get('reason')}</code>", parse_mode="HTML")
        return

    note = ""
    if not res.get("has_media_type"):
        note = "\n\n⚠️ В БД нет <code>cards.media_type</code>. Я записал <code>image_id</code>, но публикация видео не заработает, пока не добавишь колонку и логику отправки видео."

    await message.answer(
        "✅ Видео привязано.\n\n"
        f"🃏 Карта: <code>{res['card_id']}</code>\n"
        f"👤 Герой: <b>{res['hero_name'] or '-'}</b>\n"
        f"🪪 Название: <b>{res['card_name'] or '-'}</b>\n\n"
        f"Обновлено:\n"
        f"• cards: <b>{res['card_updated']}</b>\n"
        f"• auctions (строго): <b>{res['auctions_updated_strict']}</b>\n"
        f"• auctions (fallback): <b>{res['auctions_updated_fallback']}</b>"
        f"{note}",
        parse_mode="HTML",
    )
    # ✅ логи + аудит
    try:
        log_text = (
            "🎞️ <b>Видео на карте установлено</b>\n"
            f"Админ: <b>{admin_tag(message.from_user)}</b>\n"
            f"Карта: <code>{res['card_id']}</code> • "
            f"<b>{_h(res.get('hero_name') or '-')}</b> — <b>{_h(res.get('card_name') or '-')}</b>\n"
            f"file_id: <code>{_h(_short_media(file_id))}</code>\n"
            f"cards: <b>{res.get('card_updated')}</b>\n"
            f"auctions(strict): <b>{res.get('auctions_updated_strict')}</b>\n"
            f"auctions(fallback): <b>{res.get('auctions_updated_fallback')}</b>"
        )
        await send_admin_log(message.bot, log_text)

        await log_audit_action(
            user_id=message.from_user.id,
            action_type="set_card_video",
            auction_id=None,
            details=(
                f"card_id={res.get('card_id')} file_id={file_id} "
                f"strict={res.get('auctions_updated_strict')} fallback={res.get('auctions_updated_fallback')}"
            ),
        )
    except Exception:
        # логи не должны ломать команду
        pass


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


@router.message(Command("fileid"), F.chat.type == "private")
@admin_only
async def cmd_fileid(message: types.Message):
    src = message.reply_to_message or message
    fid = _extract_media_file_id(src)
    if not fid:
        await message.answer("Нет медиа в сообщении (пришли/перешли видео или ответь /fileid на видео).")
        return
    await message.answer(f"file_id:\n<code>{fid}</code>", parse_mode="HTML")


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


@router.message(F.text.regexp(r"^/print_ex\s+\d+$"))
@admin_only
async def cmd_print_ex(message: Message):
    batch_id = int(message.text.split()[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer("Не нашёл заявку биржи с таким batch_id.")
        return

    text_view, kb = await _build_print_ex_view(batch_id)
    await message.answer(text_view, parse_mode="HTML", reply_markup=kb)

    # ✅ логи + аудит
    try:
        await send_admin_log(
            message.bot,
            (
                "🧾 <b>Биржа: открыт /print_ex</b>\n"
                f"🕒 {datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
                f"Админ: <b>{admin_tag(message.from_user)}</b> (id: {message.from_user.id})\n"
                f"Batch: <code>{batch_id}</code>\n"
                "Действие: <code>exchange_print_ex_open</code>"
            ),
        )
    except Exception:
        pass

    try:
        await log_audit_action(
            user_id=message.from_user.id,
            action_type="exchange_print_ex_open",
            auction_id=None,
            details=f"batch_id={batch_id}",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{PEX_PREFIX}|"))
@admin_only
async def cb_print_ex(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    _, bid_s, action = (call.data or "").split("|", 2)
    batch_id = int(bid_s)
    admin_id = int(call.from_user.id)

    if action == "refresh":
        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        await call.answer()
        return

    if action == "reset":
        await reset_exchange_manual(batch_id, admin_id)
        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        await call.answer("Сброшено")
        return

    if action in {"set_winner", "set_price", "set_link", "wizard"}:
        await state.update_data(pex_batch_id=batch_id, pex_chat_id=call.message.chat.id,
                                pex_msg_id=call.message.message_id)

        if action in {"set_winner", "wizard"}:
            await state.set_state(PrintExFSM.winner)
            await call.message.answer(
                "Пришли <b>победителя</b>: <code>user_id</code> или <code>@username</code> (можно форвард).")
            await call.answer()
            return

        if action == "set_price":
            await state.set_state(PrintExFSM.price)
            await call.message.answer("Пришли <b>цену</b> числом (например <code>500</code>).")
            await call.answer()
            return

        if action == "set_link":
            await state.set_state(PrintExFSM.link)
            await call.message.answer("Пришли <b>ссылку</b> на биржу (или текст).")
            await call.answer()
            return

    if action == "send_both":
        batch = await get_exchange_batch_by_id(batch_id)
        if not batch:
            await call.answer("Не найдено", show_alert=True)
            return

        # 1) ЛОЧИМ сразу
        locked = await mark_exchange_manual_sent(batch_id)
        if not locked:
            await call.answer("Уже разослано (кто-то успел раньше).", show_alert=True)
            return

        owner_id = int(batch["user_id"])
        owner = await get_user(owner_id)
        owner_username = (owner.get("username") if owner else None) or None

        winner_id = int(batch.get("manual_winner_id") or 0) or None
        w_un = (batch.get("manual_winner_username") or "").strip() or None
        if not winner_id and not w_un:
            await call.answer("Сначала выставь победителя", show_alert=True)
            return

        price = batch.get("manual_price")
        if price is None:
            price = batch.get("price")
        price = int(price or 0)

        currency = (batch.get("manual_currency") or batch.get("currency") or "diamonds").strip()
        cur_emoji = currency_to_emoji(currency)
        price_line = f"<b>{price}</b> {cur_emoji}" if price else f"— {cur_emoji}"

        link = (batch.get("manual_link") or "").strip()
        link_line = html.escape(link) if link else "—"

        moderator_tag = admin_tag(call.from_user)
        thanks_kb = await build_thanks_kb(int(batch_id), moderator_tag)

        owner_ref = _safe_user_mention(owner_id, owner_username, title="владелец")
        winner_ref = _safe_user_mention(winner_id, w_un, title="покупатель")

        text_owner = (
            f"✅ <b>Биржа</b> • лот <code>{batch_id}</code> продан\n\n"
            f"Покупатель: {winner_ref}\n"
            f"Цена: {price_line}\n"
            f"Ссылка: {link_line}\n\n"
            f"Модератор: {moderator_tag}"
        )

        text_winner = (
            f"🎉 <b>Биржа</b> • ты выбран победителем по лоту <code>{batch_id}</code>\n\n"
            f"Владелец: {owner_ref}\n"
            f"Цена: {price_line}\n"
            f"Ссылка: {link_line}\n\n"
            f"Модератор: {moderator_tag}"
        )

        ok_owner = True
        try:
            await bot.send_message(
                owner_id,
                text_owner,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=thanks_kb,
            )
        except Exception:
            ok_owner = False

        ok_winner = True
        if winner_id:
            try:
                await bot.send_message(
                    int(winner_id),
                    text_winner,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=thanks_kb,
                )
            except Exception:
                ok_winner = False
        else:
            ok_winner = False

        if ok_owner and ok_winner:
            await call.answer("Отправлено владельцу и покупателю.")
        elif ok_owner and not ok_winner:
            await call.answer(
                "Владельцу ушло. Покупателю не дошло (скорее всего не писал боту /start). Лот закрыт.",
                show_alert=True,
            )
        else:
            await call.answer("Не удалось отправить владельцу. Лот закрыт, проверь вручную.", show_alert=True)

        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        return


HOWMAX_TEXT = (
    "Регистрируетесь в боте <b>@RomanticClubBot</b>, нажимаете кнопку <b>Старт</b> и ждёте результат.\n"
    "Вам придут данные владельца/покупателя.\n"
    "Если возникнет ошибка, с вами свяжется админ.\n"
    "Обычно срок ожидания <b>одни сутки</b>."
)


@router.message(Command("howmax"))
async def howmax_cmd(message: types.Message) -> None:
    # чтобы не спамили все подряд в чатах: в группах/каналах только админы из вашей таблицы admins
    if message.chat.type != "private":
        if not await is_admin(message.from_user.id):
            return

    await message.answer(HOWMAX_TEXT, parse_mode="HTML")


@router.message(PrintExFSM.winner)
@admin_only
async def pex_set_winner(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    winner_id: int | None = None
    winner_un: str | None = None

    if message.forward_from and message.forward_from.id:
        winner_id = int(message.forward_from.id)
        winner_un = (message.forward_from.username or "").strip() or None
    else:
        t = (message.text or "").strip()
        if t.startswith("@"):
            u = await get_user_by_username(t[1:])
            if u:
                winner_id = int(u["user_id"])
                winner_un = (u.get("username") or "").strip() or None
        elif t.isdigit():
            winner_id = int(t)

    if not winner_id:
        await message.answer("⚠️ Не понял победителя. Пришли <code>user_id</code>, <code>@username</code> или форвард.")
        return

    await set_exchange_manual_winner(batch_id, winner_id, winner_un, admin_id)

    # если это wizard — сразу попросим цену
    await state.set_state(PrintExFSM.price)
    await message.answer("Ок. Теперь пришли <b>цену</b> числом (например <code>500</code>).")


@router.message(PrintExFSM.price)
@admin_only
async def pex_set_price(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    t = (message.text or "").strip()
    if not t.isdigit():
        await message.answer("⚠️ Цена должна быть числом.")
        return

    await set_exchange_manual_price(batch_id, int(t), admin_id)

    await state.set_state(PrintExFSM.link)
    await message.answer("Ок. Теперь пришли <b>ссылку</b> (или напиши <code>пропустить</code>).")


@router.message(PrintExFSM.link)
@admin_only
async def pex_set_link(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    t = (message.text or "").strip()
    link = None if t.lower() in {"пропустить", "skip", "-"} else t

    await set_exchange_manual_link(batch_id, link, admin_id)

    # обновим меню
    text, kb = await _build_print_ex_view(batch_id)
    chat_id = int(data["pex_chat_id"])
    msg_id = int(data["pex_msg_id"])
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise

    await state.clear()
    await message.answer("✅ Ручной итог сохранён. Жми «Отправить обоим» в меню /print_ex.")


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


@router.message(F.text.in_({"/id", "/fid"}))
async def cmd_id(message: types.Message):
    # Команда должна быть ответом на сообщение с медиа
    target = message.reply_to_message
    if not target:
        await message.answer("Реплаем на сообщение с медиа и жми /id.")
        return

    kind, f = _pick_media_file(target)
    if not f:
        await message.answer("В реплае нет медиа (или оно слишком экзотическое даже для Telegram).")
        return

    # Иногда хочется знать mime_type и размер
    mime = getattr(f, "mime_type", None)
    size = getattr(f, "file_size", None)
    name = getattr(f, "file_name", None)

    lines = [
        f"🎞 Тип: <b>{kind}</b>",
        f"🧩 file_id:\n<code>{f.file_id}</code>",
        f"🧷 file_unique_id:\n<code>{f.file_unique_id}</code>",
    ]
    if mime:
        lines.append(f"🧬 mime: <code>{mime}</code>")
    if name:
        lines.append(f"📎 name: <code>{name}</code>")
    if size is not None:
        lines.append(f"📦 size: <code>{size}</code>")

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "exmod:back")
@admin_only
async def ex_back_to_moderation(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"],
        ),
    )
    await call.answer()

@router.callback_query(F.data.startswith(f"{EX1_APPROVE}|"))
@admin_only
async def ex1_approve(call: CallbackQuery):
    batch_id = int((call.data or "").split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    ok = await set_exchange_batch_moderation(
        batch_id=batch_id,
        status="approved",
        moderator_id=call.from_user.id,
        moderator_username=call.from_user.username or call.from_user.full_name,
        moderator_comment=None,
    )
    if not ok:
        await call.answer("Не удалось обновить статус.", show_alert=True)
        return

    # лог (если хочешь, можно оставить)
    try:
        await _log_exchange_batch_action(
            call.bot,
            action_type="exchange_approve",
            admin_user=call.from_user,
            batch_id=batch_id,
            status="approved",
        )
    except Exception:
        pass

    # уведомление юзеру (коротко)
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            thanks_kb = await build_thanks_kb(int(batch_id), admin_tag(call.from_user))
            await call.bot.send_message(
                user_id,
                f"✅ Ваша заявка на биржу <code>{batch_id}</code> одобрена.",
                parse_mode="HTML",
                reply_markup=thanks_kb,
            )
    except Exception:
        pass

    await call.answer("Одобрено ✅")

    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await show_pending_exchange_one(call.message)


@router.callback_query(F.data.startswith(f"{EX1_DELETE}|"))
@admin_only
async def ex1_delete_ask(call: CallbackQuery):
    batch_id = int((call.data or "").split("|", 1)[1])
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=_kb_ex1_delete_confirm(batch_id))
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{EX1_DEL_NO}|"))
@admin_only
async def ex1_delete_no(call: CallbackQuery):
    batch_id = int((call.data or "").split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

    await call.answer("Ок, не удаляем")
    try:
        await call.message.edit_reply_markup(reply_markup=_kb_exchange_one(batch_id, has_proof=has_proof))
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{EX1_DEL_YES}|"))
@admin_only
async def ex1_delete_yes(call: CallbackQuery):
    batch_id = int((call.data or "").split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    # если уже публиковали пост биржи, попробуем снести
    posted_chat_id = batch.get("posted_chat_id")
    posted_message_id = batch.get("posted_message_id")
    if posted_chat_id and posted_message_id:
        try:
            await call.bot.delete_message(int(posted_chat_id), int(posted_message_id))
        except Exception:
            pass

    await set_exchange_batch_moderation(
        batch_id=batch_id,
        status="deleted",
        moderator_id=call.from_user.id,
        moderator_username=call.from_user.username or call.from_user.full_name,
        moderator_comment="deleted",
    )
    try:
        await set_exchange_batch_deleted(batch_id)
    except Exception:
        pass

    # лог
    try:
        await _log_exchange_batch_action(
            call.bot,
            action_type="exchange_delete",
            admin_user=call.from_user,
            batch_id=batch_id,
            status="deleted",
        )
    except Exception:
        pass

    # уведомим юзера
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            await call.bot.send_message(
                user_id,
                f"🗑 Ваша заявка на биржу <code>{batch_id}</code> удалена модератором.",
                parse_mode="HTML",
            )
    except Exception:
        pass

    await call.answer("Удалено 🗑")

    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await show_pending_exchange_one(call.message)


@router.callback_query(F.data.startswith(f"{EX1_REJECT}|"))
@admin_only
async def ex1_reject_start(call: CallbackQuery, state: FSMContext):
    batch_id = int((call.data or "").split("|", 1)[1])
    await state.update_data(
        ex1_reject_batch_id=batch_id,
        ex1_origin_chat_id=call.message.chat.id,
        ex1_origin_msg_id=call.message.message_id,
    )
    await state.set_state(ExchangeOneRejectFSM.waiting_for_reason)
    await call.answer()
    await call.message.answer("Напиши причину отклонения заявки на биржу:")


@router.message(ExchangeOneRejectFSM.waiting_for_reason, F.chat.type == "private")
@admin_only
async def ex1_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    batch_id = int(data.get("ex1_reject_batch_id") or 0)
    reason = (message.text or "").strip()

    if not batch_id or not reason:
        await message.answer("Нужна причина текстом.")
        return

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer("Заявка не найдена или уже обработана.")
        await state.clear()
        return

    ok = await set_exchange_batch_moderation(
        batch_id=batch_id,
        status="rejected",
        moderator_id=message.from_user.id,
        moderator_username=message.from_user.username or message.from_user.full_name,
        moderator_comment=reason,
    )
    if not ok:
        await message.answer("Не удалось обновить статус.")
        return

    # лог
    try:
        await _log_exchange_batch_action(
            message.bot,
            action_type="exchange_reject",
            admin_user=message.from_user,
            batch_id=batch_id,
            status="rejected",
        )
    except Exception:
        pass

    # уведомление юзеру
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            thanks_kb = await build_thanks_kb(int(batch_id), admin_tag(message.from_user))
            await message.bot.send_message(
                user_id,
                f"❌ Ваша заявка на биржу <code>{batch_id}</code> отклонена.\n"
                f"Причина: <i>{html.escape(reason)}</i>",
                parse_mode="HTML",
                reply_markup=thanks_kb,
            )
    except Exception:
        pass

    # удаляем старое сообщение с заявкой (если сможем)
    try:
        chat_id = int(data.get("ex1_origin_chat_id"))
        msg_id = int(data.get("ex1_origin_msg_id"))
        await message.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

    await state.clear()
    await message.answer(f"Отклонено ❌ (Batch {batch_id})")

    # показываем следующую
    await show_pending_exchange_one(message)
