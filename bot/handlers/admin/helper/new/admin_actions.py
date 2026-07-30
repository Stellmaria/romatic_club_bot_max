from __future__ import annotations

import contextlib
import html
import importlib
import inspect
import json
import re
from datetime import datetime, timezone, timedelta
from functools import wraps
from html import escape as _h
from typing import (
    Any,
    Callable,
    Awaitable,
    Dict,
    Tuple,
    Union,
    Mapping,
    Sequence
)
from typing import Iterable
from typing import Optional
from typing import cast
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError
)
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    InputFile,
    User
)
from telethon.tl.functions.messages import SendMessageRequest

from bot.handlers.admin.helper.admin_constants import (
    CANCEL_MSG,
    CANCEL_TEXTS,
    ADMIN_MESSAGES,
    SYSTEM_MESSAGES
)
from bot.handlers.admin.helper.admin_constants import CURRENCY_EMOJI, RARITY_EMOJI
from bot.handlers.admin.helper.new.Types import Owner
from bot.handlers.admin.helper.new.formatting import format_pending_lot, format_admin_action_log, \
    get_lot_owners_with_levels
from bot.handlers.admin.helper.new.helper import normalize_chat_id
from bot.handlers.admin.helper.new.keyboards import (
    period_keyboard,
    menu_keyboard,
    decks_keyboard,
    back_keyboard, build_lot_keyboard
)
from bot.handlers.admin.helper.user_helpers import format_user_ref
from bot.handlers.admin.logs_admin import send_admin_log
from config import ADMIN_LOG_CHATS, ADMIN_SECRET, ADMINS_OWNERS, LOG_CHAT_ID, ADMINS
from config import AUCTION_CHANNEL_USERNAME, AUCTION_CHANNEL_ID, DISCUSSION_CHAT_ID
from db.db import (
    add_admin,
    get_lot_owners,
    get_pending_auctions,
    get_pending_exchange_batches,
    is_admin,
    list_pending_delete_requests,
    remove_admin,
    set_trusted_status, fetchrow
)

MAX_DEBUG_LEN = 3500
MAX_TG_LEN = 4096
SAFE_SPLIT = 3500
BR_RE = re.compile(r"(?i)<br\s*/?>")
DT_FMT = "%d.%m.%Y %H:%M:%S"

def _safe_user_mention(user_id: int, username: str | None) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">{user_id}</a>'
def _as_str(v: object, default: str = "") -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return default
    return str(v)
def _admin_link_html(admin_user: types.User) -> str:
    uname = (admin_user.username or "").strip()
    if uname:
        u = html.escape(uname)
        return f'<a href="https://t.me/{u}">@{u}</a>'
    name = html.escape(admin_user.full_name or "Админ")
    return f'<a href="tg://user?id={admin_user.id}">{name}</a>'

def format_exchange_moderation_log(
    *,
    action_title: str,     # "Отклонена заявка на биржу" / "Удалена заявка на биржу"
    action_code: str,      # "exchange_reject" / "exchange_delete"
    when_msk: str,
    admin_user: types.User,
    batch_id: int,
    sender_username: str | None,
    sender_id: int | None,
    deck_name: str,
    deck_id: int | None,
    mode: str,
    items_count: int,
    price: int | None,
    currency: str,
    has_proof: bool,
    comment: str | None,
    moderator_comment: str | None,
) -> str:
    admin_html = _admin_link_html(admin_user)

    # отправитель
    if sender_id:
        if sender_username:
            sender = f'<a href="https://t.me/{html.escape(sender_username)}">@{html.escape(sender_username)}</a>'
        else:
            sender = f'<a href="tg://user?id={sender_id}">id:{sender_id}</a>'
    else:
        sender = f"@{html.escape(sender_username)}" if sender_username else "—"

    cur = (currency or "алмазы").lower()
    cur_emoji = "🍵" if "чай" in cur else ("🪙" if "сокров" in cur else "💎")
    price_line = f"{price} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    cmt = (comment or "").strip() or "-"
    mcmt = (moderator_comment or "").strip() or "—"

    deck_line = html.escape(deck_name or (f"#{deck_id}" if deck_id else "—"))
    mode_line = html.escape(mode or "—")

    return (
        f"❌ <b>{html.escape(action_title)}</b>\n"
        f"🕒 {html.escape(when_msk)} (МСК)\n"
        f"👮 Админ: {admin_html}\n"
        f"👤 Отправитель: {sender}\n\n"
        f"🆔 Batch: <code>{batch_id}</code>\n"
        f"📚 Колода: <b>{deck_line}</b>\n"
        f"🎛 Режим: <b>{mode_line}</b>\n"
        f"🃏 Карт: <b>{items_count}</b>\n"
        f"💰 Цена: <b>{html.escape(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{html.escape(cmt)}</i>\n"
        f"📝 Комментарий модератора: <i>{html.escape(mcmt)}</i>\n\n"
        f"Действие: <code>{html.escape(action_code)}</code>"
    )

async def notify_exchange_user_moderation(
    bot,
    *,
    batch: dict,
    admin_user: types.User,
    title: str,                # "отклонена" / "удалена"
    reason: str | None,        # для reject, для delete = None
) -> None:
    batch_id = int(batch.get("batch_id") or 0)
    user_id = int(batch.get("user_id") or 0)

    # deck name (если нет — покажем #id)
    deck_id = batch.get("deck_id")
    deck_name = None
    try:
        drow = await fetchrow("SELECT name FROM public.decks WHERE id=$1", int(deck_id or 0))
        if drow:
            deck_name = (drow.get("name") or "").strip() or None
    except Exception:
        deck_name = None
    deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

    # count cards
    items_cnt = 0
    try:
        r = await fetchrow("SELECT COUNT(*) AS cnt FROM public.exchange_items WHERE batch_id=$1", batch_id)
        items_cnt = int((r or {}).get("cnt") or 0)
    except Exception:
        items_cnt = 0

    currency = str(batch.get("currency") or "алмазы")
    cur_low = currency.lower()
    cur_emoji = "🍵" if "чай" in cur_low else ("🪙" if "сокров" in cur_low else "💎")
    price = batch.get("price")
    price_line = f"{int(price)} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"

    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    # admin clickable
    admin_html = _admin_link_html(admin_user)

    comment = (batch.get("comment") or "").strip() or "-"
    rsn = (reason or "").strip() if reason is not None else ""
    if reason is not None and not rsn:
        rsn = "—"

    # медиа: сначала обложка карты (если есть), иначе пруф
    cover_id = None
    try:
        cover_id, _ = await _get_exchange_cover_media(batch_id)  # твоя функция
    except Exception:
        cover_id = None
    media_id = cover_id or (proof_id if has_proof else None)

    text = (
        f"❌ <b>Ваша заявка на биржу {html.escape(title)}</b>\n"
        f"🆔 Batch: <code>{batch_id}</code>\n\n"
        f"📚 Колода: <b>{html.escape(deck_title)}</b>\n"
        f"🎛 Режим: <b>{html.escape(str(batch.get('mode') or '—'))}</b>\n"
        f"🃏 Карт: <b>{items_cnt}</b>\n"
        f"💰 Цена: <b>{price_line}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{html.escape(comment)}</i>\n"
    )
    if reason is not None:
        text += f"🔒 Причина: <i>{html.escape(rsn)}</i>\n"

    text += (
        "\nЕсли есть вопросы — обратитесь к администрации.\n"
        f"Модератор: {admin_html}"
    )

    # (опционально) кнопка спасибо как у аукциона
    moderator_tag = admin_user.username or str(admin_user.id)
    thanks_kb = await build_thanks_kb(batch_id, moderator_tag)  # если у тебя так устроено

    if media_id:
        await safe_send_media(
            bot,
            chat_id=user_id,
            file_id=media_id,
            caption=text,
            reply_markup=thanks_kb,
            parse_mode="HTML",
        )
    else:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=thanks_kb, disable_web_page_preview=True)


def format_exchange_new_request_log(*,
                                    batch_id: int,
                                    created_at_msk: str,
                                    sender_username: str | None,
                                    sender_id: int | None,
                                    deck_id: int | None,
                                    deck_name: str | None,
                                    mode: str,
                                    items_count: int,
                                    price: int | None,
                                    currency: str,
                                    has_proof: bool,
                                    comment: str | None) -> str:
    # отправитель
    if sender_id:
        sender = _safe_user_mention(sender_id, sender_username)
    else:
        sender = f"@{sender_username}" if sender_username else "—"

    deck_title = (deck_name or "").strip()
    deck_part = deck_title if deck_title else (f"{deck_id}" if deck_id else "—")

    cur = (currency or "алмазы").lower()
    cur_emoji = _cur_emoji(cur)

    mode_lbl = {
        "card": "card",
        "deck": "deck",
        "deck_split": "deck_split",
    }.get((mode or "").strip().lower(), (mode or "—"))

    proof_line = "✅ Да" if has_proof else "❌ Нет"
    price_line = f"{int(price)} {cur_emoji} ({cur})" if price is not None else f"— {cur_emoji} ({cur})"

    cmt = (comment or "").strip()
    if not cmt:
        cmt = "-"

    return (
        "🛒 <b>Новая заявка на биржу</b>\n"
        f"🕒 {created_at_msk} (МСК)\n"
        f"👤 Отправитель: {sender}\n"
        f"🆔 Batch: <code>{batch_id}</code>\n\n"
        f"📚 Колода: <b>{tg_clean(str(deck_part))}</b>\n"
        f"🎛 Режим: <b>{tg_clean(mode_lbl)}</b>\n"
        f"🃏 Карт: <b>{items_count}</b>\n"
        f"💰 Цена: <b>{tg_clean(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{tg_clean(cmt)}</i>\n\n"
        "Действие: <code>exchange_add_request</code>"
    )


def _looks_like_file_id(v: str) -> bool:
    return isinstance(v, str) and len(v) > 60 and v.isascii()


async def safe_answer_photo(msg, image_id: str, **kw):
    """Отправляет превью лота максимально живуче.
    Fallback: photo -> video -> animation -> document.
    """

    # InputFile тоже может прилететь, не ломаемся
    if isinstance(image_id, InputFile):
        return await msg.answer_photo(photo=image_id, **kw)

    image_id = (str(image_id) if image_id is not None else "").strip()
    if not image_id:
        return await msg.answer(kw.get("caption", ""), parse_mode=kw.get("parse_mode", "HTML"))

    # 1) пробуем как фото
    try:
        return await msg.answer_photo(photo=image_id, **kw)
    except TelegramBadRequest as e:
        s = str(e)
        if "Wrong string length" in s:
            await msg.answer(
                f"⚠️ Превью без фото: повреждён file_id.\n\n{kw.get('caption', '')}",
                parse_mode="HTML",
            )
            return
        # если это НЕ ошибка типа файла, пусть падает выше
        if "Video as Photo" not in s and "type Video" not in s:
            raise
    except TelegramAPIError:
        pass

    # 2) пробуем как видео
    try:
        return await msg.answer_video(video=image_id, supports_streaming=True, **kw)
    except Exception:
        pass

    # 3) пробуем как анимацию (gif)
    try:
        return await msg.answer_animation(animation=image_id, **kw)
    except Exception:
        pass

    # 4) последний шанс: документ
    with contextlib.suppress(Exception):
        return await msg.answer_document(document=image_id, **kw)

    # 5) вообще всё умерло
    with contextlib.suppress(Exception):
        await msg.answer(
            f"⚠️ Превью без медиа: не удалось отправить файл.\n\n{kw.get('caption', '')}",
            parse_mode="HTML",
        )


def tg_clean(text: str) -> str:
    return BR_RE.sub("\n", text or "")


async def _get_exchange_cover_media_admin(batch_id: int) -> tuple[str | None, str]:
    """
    Обложка заявки биржи для админки: (file_id, kind)
    kind: 'photo' | 'video' | 'animation'
    Берём первую карту из exchange_items и тянем cards.image_id (+ media_type если колонка есть).
    """
    # 1) пробуем с media_type
    try:
        row = await fetchrow(
            """
            SELECT c.image_id, COALESCE(c.media_type, 'photo') AS media_type
            FROM public.exchange_items ei
                     JOIN public.cards c ON c.card_id = ei.card_id
            WHERE ei.batch_id = $1
            LIMIT 1
            """,
            batch_id,
        )
        if not row or not (str(row.get("image_id") or "").strip()):
            return None, "photo"

        kind = str(row.get("media_type") or "photo").strip().lower()
        if kind not in {"photo", "video", "animation"}:
            kind = "photo"
        return str(row["image_id"]).strip(), kind
    except Exception:
        # 2) если media_type нет в БД
        row = await fetchrow(
            """
            SELECT c.image_id
            FROM public.exchange_items ei
                     JOIN public.cards c ON c.card_id = ei.card_id
            WHERE ei.batch_id = $1
            LIMIT 1
            """,
            batch_id,
        )
        if not row or not (str(row.get("image_id") or "").strip()):
            return None, "photo"
        return str(row["image_id"]).strip(), "photo"


def _media_kind_from_error_admin(e: Exception) -> str | None:
    """
    Очень грубая эвристика по тексту ошибки Telegram,
    чтобы если попытались отправить видео как фото — переключиться.
    """
    s = str(e)
    s_low = s.lower()
    if "video" in s_low and "photo" in s_low:
        return "video"
    if "type video" in s_low:
        return "video"
    if "animation" in s_low or "gif" in s_low:
        return "animation"
    return None


async def _send_exchange_batch_card_admin(
        message: Message,
        *,
        batch_id: int,
        text: str,
        kb: InlineKeyboardMarkup,
        proof_id: str,
        has_proof: bool,
) -> None:
    """
    Шлёт карточку заявки биржи с медиа:
    1) обложка карты (cards.image_id по первым exchange_items)
    2) fallback на proof_photo_id
    3) если всё умерло — текстом
    """
    cover_id, cover_kind = await _get_exchange_cover_media_admin(batch_id)

    fallback_media_id = proof_id if (has_proof and proof_id) else None
    media_id = cover_id or fallback_media_id

    # если это не обложка, а пруф — считаем, что это фото (чаще всего так)
    kind = cover_kind if cover_id else "photo"

    if not media_id:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    try:
        if kind == "video":
            await message.answer_video(
                video=media_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
                protect_content=False,
                supports_streaming=True,
            )
        elif kind == "animation":
            await message.answer_animation(
                animation=media_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
                protect_content=False,
            )
        else:
            await message.answer_photo(
                photo=media_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb,
                protect_content=False,
            )
    except Exception as e:
        # если Telegram “это видео, а ты фото” — попробуем угадать и переотправить
        kind2 = _media_kind_from_error_admin(e) or "photo"
        try:
            if kind2 == "video":
                await message.answer_video(
                    video=media_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    protect_content=False,
                    supports_streaming=True,
                )
            elif kind2 == "animation":
                await message.answer_animation(
                    animation=media_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    protect_content=False,
                )
            else:
                await message.answer_photo(
                    photo=media_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    protect_content=False,
                )
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)


#
def _safe_strip(s: Optional[str]) -> str:
    return s.strip() if isinstance(s, str) else ""


#
def parse_datetime_field(field: Any) -> Optional[datetime]:
    if isinstance(field, str):
        try:
            return datetime.fromisoformat(field)
        except (ValueError, TypeError):
            return None
    return field if isinstance(field, datetime) else None


MSK_TZ = ZoneInfo("Europe/Moscow")
UTC = timezone.utc


def _to_msk(dt: Any) -> datetime | None:
    d = parse_datetime_field(dt)
    if not d:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    try:
        return d.astimezone(MSK_TZ)
    except Exception:
        return d


def _human_wait(delta: timedelta) -> str:
    sec = int(delta.total_seconds())
    if sec < 0:
        sec = 0
    days, sec = divmod(sec, 86400)
    hours, sec = divmod(sec, 3600)
    mins, _ = divmod(sec, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


def build_exchange_pending_keyboard(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    top_row: list[InlineKeyboardButton] = []
    if has_proof:
        top_row.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    top_row.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    rows.append(top_row)

    rows.append([
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"exchange_approve|{batch_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"exchange_reject|{batch_id}"),
    ])

    rows.append([
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


#
def _resolve_bot_from_message(
        message: Message, given: Optional[Bot] = None
) -> Optional[Bot]:
    if isinstance(given, Bot):
        return given
    mb = getattr(message, "bot", None)
    return mb if isinstance(mb, Bot) else None


#
def _ensure_sender(message: Message) -> tuple[Optional[int], Optional[str]]:
    fu = getattr(message, "from_user", None)
    if isinstance(fu, User):
        return fu.id, fu.username
    return None, None


#
def as_message(obj: Union[Message, CallbackQuery]) -> Optional[Message]:
    if isinstance(obj, Message):
        return obj
    if isinstance(obj, CallbackQuery):
        m = obj.message
        return m if isinstance(m, Message) else None
    return None


#
def require_bot(obj: Union[Message, CallbackQuery]) -> Optional[Bot]:
    return getattr(obj, "bot", None)


#
async def _call_maybe_await(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    res = fn(*args, **kwargs)
    if inspect.isawaitable(res):
        return await res
    return res


#
def format_date_time_block(st: Any, et: Any) -> str:
    st_dt = parse_datetime_field(st)
    et_dt = parse_datetime_field(et)
    if isinstance(st_dt, datetime) and isinstance(et_dt, datetime):
        date = st_dt.strftime("%d.%m.%Y")
        return (
            f"<b>Назначен на:</b> {date} {st_dt:%H:%M}–{et_dt:%H:%M} (МСК)\n"
        )
    return ""


#
def format_owner_html(o: Mapping[str, Any]) -> str:
    uid = o.get("user_id")
    raw_uname = o.get("username")
    uname = raw_uname.strip() if isinstance(raw_uname, str) else ""
    if not uid:
        return "—"
    if uname:
        esc = html.escape(uname)
        return f'<a href="https://t.me/{esc}">@{esc}</a>'
    uid_s = html.escape(str(uid))
    return "\n".join([
        f'<code>https://t.me/{uid_s}</code>',
        f'<a href="tg://user?id={uid_s}">tg://user?id={uid_s}</a>',
        f'<a href="tg://openmessage?user_id={uid_s}">tg://openmessage?user_id={uid_s}</a>',
    ])


#
def format_owners_block(owners: Iterable[Owner]) -> str:
    items: list[str] = []
    for o in owners:
        uid = o.get("user_id")
        username = o.get("username")
        full_name = o.get("full_name")

        if username:
            label: str = f"@{username}"
        elif full_name:
            label = full_name
        elif uid is not None:
            label = str(uid)
        else:
            label = "—"

        safe_label = _h(label)
        items.append(f'<a href="tg://user?id={uid}">{safe_label}</a>' if uid else safe_label)

    return ", ".join(items) if items else "—"


#
def owner_or_secret_required(
        func: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        msg: Optional[Message] = next(
            (a for a in args if isinstance(a, Message)), None
        )
        if msg is None:
            msg = kwargs.get("message")
        if msg is None:
            cq: Optional[CallbackQuery] = next(
                (a for a in args if isinstance(a, CallbackQuery)), None
            )
            msg = as_message(cq) if cq is not None else None
        if msg is None:
            return None

        text = _safe_strip(getattr(msg, "text", None))
        parts = text.split() if text else []

        uid = msg.from_user.id if msg.from_user else None
        is_owner = uid in ADMINS_OWNERS if uid is not None else False

        secret: str = ADMIN_SECRET if isinstance(ADMIN_SECRET, str) else ""
        has_secret = len(parts) > 1 and parts[-1] == secret

        if is_owner or has_secret:
            return await func(*args, **kwargs)

        await msg.answer("Требуется пароль владельца.")
        return None

    return wrapper


#
async def safe_edit_message(call: CallbackQuery, new_text: str, reply_markup=None, silent: bool = False) -> None:
    m = as_message(call)
    if m is None:
        await call.answer(tg_clean(new_text)[:190], show_alert=True)
        return
    try:
        await m.edit_text(tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")
        return
    except TelegramBadRequest:
        pass
    try:
        await m.edit_caption(tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")
        return
    except TelegramBadRequest:
        pass
    try:
        await m.delete()
    except TelegramAPIError as e:
        if not silent:
            print(f"[SAFE EDIT] Не удалось удалить сообщение: {e}")

    bot: Optional[Bot] = require_bot(call)
    chat_id: Optional[Union[int, str]] = getattr(getattr(m, "chat", None), "id", None)
    if bot is not None and chat_id is not None:
        await bot.send_message(chat_id, tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")


#
async def notify_owners(bot: Bot, text: str, silent: bool = False) -> None:
    for owner_id in ADMINS_OWNERS:
        try:
            await bot.send_message(owner_id, tg_clean(text), parse_mode="HTML")
        except TelegramAPIError as e:
            if not silent:
                print(f"[OWNER NOTIFY ERROR] {owner_id}: {e}")


#
async def send_log_to_chats(client_api, text: str) -> None:
    for chat_id in ADMIN_LOG_CHATS:
        try:
            entity = await client_api.get_entity(chat_id)
            await client_api(SendMessageRequest(peer=entity, message=text))
            print(f"[LOG OK] Сообщение отправлено в лог-чат {chat_id}")
        except Exception as e:
            print(f"[LOG ERROR] {e}")


#
async def verify_log_chats(bot: Bot) -> None:
    for name, raw in {"LOG_CHAT_ID": LOG_CHAT_ID}.items():
        cid = normalize_chat_id(raw)
        if cid is None:
            print(f"[BOOT] {name} пуст/некорректен: {raw!r}")
            continue
        try:
            chat = await bot.get_chat(cid)
            print(f"[BOOT] {name} ок: {getattr(chat, 'id', cid)}")
        except TelegramAPIError as e:
            print(f"[BOOT] {name} недоступен ({raw!r}): {e}")


#
def get_cancel_text(state_name: Optional[str]) -> str:
    cancel_map: dict[str, str] = {
        "ModActionFSM:waiting_for_unluxury_user": (
            CANCEL_TEXTS["removeluxury_cancel"][0]
        ),
        "ModActionFSM:waiting_for_luxury_user": (
            CANCEL_TEXTS["giveluxury_cancel"][0]
        ),
        "ModActionFSM:waiting_for_admin_user": (
            CANCEL_TEXTS["addadmin_cancel"][0]
        ),
        "ModActionFSM:waiting_for_admin_remove_user": (
            CANCEL_TEXTS["removeadmin_cancel"][0]
        ),
        "ModActionFSM:waiting_for_trusted_user": "Выдача доверия отменена.",
        "ModActionFSM:waiting_for_untrusted_user": (
            "Снятие доверия отменена."
        ),
    }
    key = state_name if isinstance(state_name, str) else ""
    default_msg = str(CANCEL_MSG)
    return cancel_map.get(key, default_msg)


#
async def process_universal_cancel_text(
        message: Message, state: FSMContext
) -> None:
    cancel_text = get_cancel_text(await state.get_state())
    await state.clear()
    await message.answer(
        f"{cancel_text}\n\n"
        f"{ADMIN_MESSAGES.get('admin_panel_greeting', 'Добро пожаловать в админ-панель!')}"
    )


#
async def process_universal_cancel_callback(
        call: CallbackQuery, state: FSMContext
) -> None:
    cancel_text = get_cancel_text(await state.get_state())
    await state.clear()

    msg: Optional[Message] = getattr(call, "message", None)
    if msg is not None:
        with contextlib.suppress(TelegramAPIError):
            await msg.delete()

    bot = call.bot if isinstance(getattr(call, "bot", None), Bot) else None

    chat_id: Optional[int] = getattr(getattr(msg, "chat", None), "id", None)
    if chat_id is None:
        fu = getattr(call, "from_user", None)
        if isinstance(fu, User):
            chat_id = fu.id

    if bot is not None and chat_id is not None:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"{cancel_text}\n\n"
                f"{ADMIN_MESSAGES.get('admin_panel_greeting', 'Добро пожаловать в админ-панель!')}"
            ),
            reply_markup=menu_keyboard(
                ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
                ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
            ),
        )
    await call.answer()


async def send_lot_card_safe(message: Message, lot: Mapping[str, Any], text: str, kb: InlineKeyboardMarkup) -> None:
    image_id = lot.get("image_id") or lot.get("card_image_id")
    try:
        if image_id:
            await safe_answer_photo(
                message,
                image_id,
                caption=tg_clean(text),
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(tg_clean(text), reply_markup=kb, parse_mode="HTML")
    except TelegramAPIError as e:
        bot = _resolve_bot_from_message(message)
        if bot is not None:
            await send_admin_log(bot, f"[ERROR] Не удалось отправить фото лота: {e}")
        else:
            with contextlib.suppress(TelegramAPIError):
                await message.answer(
                    tg_clean(text) + "\n[⚠️ Ошибка при отправке медиа]",
                    reply_markup=kb,
                    parse_mode="HTML",
                )


#
async def get_lot_owners_text(auction_id: int) -> str:
    owners = cast(list[Owner], await get_lot_owners(auction_id))
    return format_owners_block(owners)


async def show_pendinglots(message: Message, kind: str | None = None) -> None:
    """Показывает заявки на модерацию (аукционы + биржа).

    kind:
      - None -> всё
      - "exchange" -> только биржа
      - иначе -> фильтр по auction_kind (standard/reverse/...)
    """

    # В callback message.from_user = BOT, поэтому проверяем chat.id
    actor_id = message.chat.id if message.chat else None
    if actor_id not in ADMINS:
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


#
def _delete_request_created_str(row: Mapping[str, Any]) -> str:
    created_at = parse_datetime_field(row.get("created_at"))
    return (
        created_at.strftime("%d.%m.%Y %H:%M")
        if isinstance(created_at, datetime)
        else str(created_at)
    )


#
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
    if AUCTION_CHANNEL_USERNAME:
        return f"https://t.me/{AUCTION_CHANNEL_USERNAME.lstrip('@')}/{message_id}"
    if AUCTION_CHANNEL_ID and str(AUCTION_CHANNEL_ID).startswith("-100"):
        return f"https://t.me/c/{str(AUCTION_CHANNEL_ID)[4:]}/{message_id}"
    return None


def _build_discussion_link(message_id: int | None) -> str | None:
    if not message_id or not DISCUSSION_CHAT_ID:
        return None
    cid = str(DISCUSSION_CHAT_ID)
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


#
def _extract_reason_text(message: Message) -> str:
    return _safe_strip(getattr(message, "text", None))


#
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


#
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


#
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


#
async def add_admin_role(
        user_id: int,
        username: Optional[str],
        by_admin_id: int,
        *,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    if username is not None and not isinstance(username, str):
        username = None
    await add_admin(user_id, username, by_admin_id)
    if bot:
        text = format_admin_action_log(
            action="add_admin",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)


#
async def remove_admin_role(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await remove_admin(user_id)
    if bot:
        text = format_admin_action_log(
            action="remove_admin",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)


#
def _parse_admin_command_args(
        message: Message, is_owner: bool
) -> Tuple[Optional[str], Optional[str]]:
    text = _safe_strip(getattr(message, "text", None))
    parts = text.split()
    if parts and parts[0].startswith("/"):
        parts = parts[1:]
    if not parts:
        return None, None
    if is_owner:
        who = parts[0]
        password = parts[1] if len(parts) > 1 else None
        return who, password
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


#
async def _ensure_bot_or_fail(
        message: Message, state: Optional[FSMContext]
) -> Optional[Bot]:
    bot: Optional[Bot] = require_bot(message)
    if bot is None:
        await message.answer(
            "Техническая пауза: бот недоступен. Повторите позже."
        )
        if state:
            await state.clear()
        return None
    return bot


#
def _admin_link_text(
        by_admin_id: int, by_admin_username: Optional[str]
) -> str:
    return (
        f"<a href='tg://user?id={by_admin_id}'>"
        f"{by_admin_username or by_admin_id}</a>"
    )


#
async def _remove_admin_flow(
        *,
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        message: Message,
        bot: Bot,
) -> None:
    await remove_admin_role(
        user_id=who_id,
        by_admin_id=by_admin_id,
        username=who_username,
        bot=bot,
        admin_username=by_admin_username,
    )
    await message.answer(
        ADMIN_MESSAGES["user_removed_admin"].format(user_id=who_id)
    )
    await log_admin_action(
        user_id=by_admin_id,
        action_type="remove_admin",
        auction_id=None,
        details=f"Удалён админ {who_id} (@{who_username or 'no_username'})",
    )


#
async def _add_admin_flow(
        *,
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        message: Message,
        bot: Bot,
        state: Optional[FSMContext],
) -> None:
    if await is_admin(who_id):
        await message.answer("Пользователь уже является администратором.")
        if state:
            await state.clear()
        return
    await add_admin_role(
        user_id=who_id,
        username=who_username,
        by_admin_id=by_admin_id,
        bot=bot,
        admin_username=by_admin_username,
    )
    await message.answer(
        ADMIN_MESSAGES["user_now_admin"].format(user_id=who_id),
        parse_mode="HTML",
    )
    await log_admin_action(
        user_id=by_admin_id,
        action_type="add_admin",
        auction_id=None,
        details=f"Добавлен админ {who_id} (@{who_username or 'no_username'})",
    )


#
async def do_admin_add_remove(
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        is_remove: bool,
        message: Message,
        state: Optional[FSMContext] = None,
) -> None:
    bot = await _ensure_bot_or_fail(message, state)
    if bot is None:
        return

    if is_remove and who_id in ADMINS_OWNERS:
        admin_link = _admin_link_text(by_admin_id, by_admin_username)
        await log_admin_action(
            user_id=by_admin_id,
            action_type="remove_owner_attempt",
            auction_id=None,
            details=f"Попытка удалить владельца {who_id}",
        )
        await notify_owners(
            bot, f"🚫 Попытка удалить владельца! Попытался: {admin_link}"
        )
        await message.answer("Нельзя удалить владельца.")
        if state:
            await state.clear()
        return

    if is_remove and who_id == by_admin_id:
        await message.answer(SYSTEM_MESSAGES["cannot_delete_self"])
        if state:
            await state.clear()
        return

    if is_remove:
        await _remove_admin_flow(
            who_id=who_id,
            who_username=who_username,
            by_admin_id=by_admin_id,
            by_admin_username=by_admin_username,
            message=message,
            bot=bot,
        )
    else:
        await _add_admin_flow(
            who_id=who_id,
            who_username=who_username,
            by_admin_id=by_admin_id,
            by_admin_username=by_admin_username,
            message=message,
            bot=bot,
            state=state,
        )

    if state:
        await state.clear()


#
async def admin_add_remove(
        message: Message, state: FSMContext, is_remove: bool = False
) -> None:
    fu = getattr(message, "from_user", None)
    if not isinstance(fu, User):
        await message.answer("Не могу определить отправителя команды.")
        return

    is_owner = fu.id in ADMINS_OWNERS
    who, password = _parse_admin_command_args(message, is_owner)
    if not who:
        await message.answer(
            SYSTEM_MESSAGES["syntax_error"].format(
                example="Пример: /add_admin @username пароль"
            )
        )
        return
    if not is_owner and password != ADMIN_SECRET:
        await message.answer(SYSTEM_MESSAGES["invalid_password"])
        return

    from bot.handlers.helper.helpers_users import (
        resolve_user_identifier,
    )

    user = await resolve_user_identifier(who)
    if not user:
        await message.answer(SYSTEM_MESSAGES["user_not_found"])
        return

    await do_admin_add_remove(
        who_id=user["user_id"],
        who_username=user.get("username"),
        by_admin_id=fu.id,
        by_admin_username=fu.username,
        is_remove=is_remove,
        message=message,
        state=state,
    )


#
async def give_trusted_status(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await set_trusted_status(user_id, True)
    if bot:
        text = format_admin_action_log(
            action="give_trusted",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)
    await log_admin_action(
        user_id=by_admin_id,
        action_type="give_trusted",
        auction_id=None,
        details=f"Выдан trusted @{username or user_id} (id {user_id})",
    )


#
async def remove_trusted_status(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await set_trusted_status(user_id, False)
    if bot:
        text = format_admin_action_log(
            action="remove_trusted",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)
    await log_admin_action(
        user_id=by_admin_id,
        action_type="remove_trusted",
        auction_id=None,
        details=f"Снят trusted @{username or user_id} (id {user_id})",
    )


#
async def _resolve_user_or_error(
        who: str, answer: Callable[[str], Awaitable[Any]]
) -> Optional[dict]:
    from bot.handlers.admin.helper.user_helpers import (
        ensure_user_by_username_or_id,
    )

    user = await ensure_user_by_username_or_id(who)
    if not user:
        await answer(SYSTEM_MESSAGES["user_not_found"])
        return None
    return user


#
def _extract_who_text(who: Optional[str], message: Message) -> str:
    if isinstance(who, str) and who.strip():
        return who.strip()
    raw_text = getattr(message, "text", None)
    return raw_text.strip() if isinstance(raw_text, str) else ""


#
def _trusted_result_text(grant: bool, user: Mapping[str, Any]) -> str:
    action = "выдан" if grant else "снят"
    return (
        f"Статус 'Доверенный' {action} у пользователя "
        f"{format_user_ref(dict(user))}"
    )


#
async def _actor_and_bot_or_fail(
        message: Message, state: Optional[FSMContext], bot: Optional[Bot]
) -> Optional[Tuple[int, Optional[str], Bot]]:
    by_admin_id, admin_username = _ensure_sender(message)
    if by_admin_id is None:
        await message.answer("Не могу определить отправителя команды.")
        if state:
            await state.clear()
        return None
    bot_resolved = _resolve_bot_from_message(message, bot)
    if bot_resolved is None:
        await message.answer(
            "Техническая пауза: бот недоступен. Повторите позже."
        )
        if state:
            await state.clear()
        return None
    return by_admin_id, admin_username, bot_resolved


#
async def _do_trusted_action(
        *,
        message: Message,
        state: Optional[FSMContext],
        who: Optional[str],
        bot: Optional[Bot],
        grant: bool,
) -> None:
    who_text = _extract_who_text(who, message)
    user: Optional[Mapping[str, Any]] = await _resolve_user_or_error(
        who_text, message.answer
    )
    if not user:
        return

    actor = await _actor_and_bot_or_fail(message, state, bot)
    if actor is None:
        return
    by_admin_id, admin_username, bot_resolved = actor

    if grant:
        await give_trusted_status(
            user_id=user["user_id"],
            by_admin_id=by_admin_id,
            username=user.get("username"),
            bot=bot_resolved,
            admin_username=admin_username,
        )
    else:
        await remove_trusted_status(
            user_id=user["user_id"],
            by_admin_id=by_admin_id,
            username=user.get("username"),
            bot=bot_resolved,
            admin_username=admin_username,
        )

    await message.answer(_trusted_result_text(grant, user))

    if state:
        await state.clear()


#
async def start_preview_schedule(
        message_or_call: Union[Message, CallbackQuery], state: FSMContext
) -> None:
    await state.clear()
    await message_or_call.answer(
        "Выберите месяц для просмотра расписания:",
        reply_markup=period_keyboard(period="month", prefix="preview_schedule"),
    )
    from fsm_states import PreviewScheduleFSM

    await state.set_state(PreviewScheduleFSM.choosing_month)


#
async def start_edit_schedule(
        message_or_call: Union[Message, CallbackQuery],
        state: FSMContext,
        auction_id: Optional[int] = None,
) -> None:
    from fsm_states import EditScheduleFSM

    await state.clear()
    await state.set_state(EditScheduleFSM.choosing_month)
    reply_markup = period_keyboard(
        period="month", prefix="edit_schedule", auction_id=auction_id
    )

    if isinstance(message_or_call, Message):
        await message_or_call.answer(
            "Выберите месяц для просмотра и редактирования расписания:",
            reply_markup=reply_markup,
        )
    else:
        msg = getattr(message_or_call, "message", None)
        if isinstance(msg, Message):
            await msg.answer(
                "Выберите месяц для просмотра и редактирования расписания:",
                reply_markup=reply_markup,
            )
        await message_or_call.answer()


#
async def add_deck_fsm_entry(message: Message, state: FSMContext) -> None:
    from db.db import add_deck
    from fsm_states import AddDeckFSM

    fu = getattr(message, "from_user", None)
    is_owner = isinstance(fu, User) and (fu.id in ADMINS_OWNERS)

    text = _safe_strip(getattr(message, "text", None))
    parts = text.split(maxsplit=1)

    if is_owner and text.startswith("/add_deck") and len(parts) == 2:
        deck_name = parts[1]
        await add_deck(deck_name)
        await message.answer(
            f"Колода <b>{deck_name}</b> успешно добавлена!", parse_mode="HTML"
        )
    elif is_owner:
        await message.answer(
            "Введите название новой колоды:", reply_markup=back_keyboard()
        )
        await state.set_state(AddDeckFSM.waiting_for_deck_name)
    else:
        await message.answer(
            "Введите пароль администратора для добавления колоды:",
            reply_markup=back_keyboard(),
        )
        await state.set_state(AddDeckFSM.waiting_for_admin_password)


#
async def start_add_card_fsm(message: Message, state: FSMContext) -> None:
    from db.db import get_all_decks
    from fsm_states import AddCardFSM

    await state.clear()

    fu = getattr(message, "from_user", None)
    if not isinstance(fu, User):
        await message.answer("Не могу определить отправителя команды.")
        return

    if fu.id in ADMINS_OWNERS:
        decks = await get_all_decks()
        await message.answer(
            "Владелец, доступ разрешён без пароля.\nВыбери колоду:",
            reply_markup=decks_keyboard(decks, prefix="admin_deck"),
        )
        await state.set_state(AddCardFSM.waiting_for_deck)
    else:
        await message.answer(
            "Введите пароль администратора для добавления карты:",
            reply_markup=back_keyboard(text="Отмена", callback="addcard_cancel"),
        )
        await state.set_state(AddCardFSM.waiting_admin_password)


#
def owners_to_links_text(owners: Any) -> str:
    if owners is None:
        return "—"
    data: Any = owners
    if isinstance(owners, str):
        try:
            data = json.loads(owners)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = []
    if not isinstance(data, list) or not data:
        return "—"
    return "\n".join(format_owner_html(o) for o in data if isinstance(o, Mapping))


#
async def get_lot_by_channel_message_id(msg_id: int) -> Optional[dict]:
    try:
        db_mod = importlib.import_module("db.db")
    except ModuleNotFoundError:
        print("[WARN] Module 'db.db' not found")
        return None

    for name in (
            "get_lot_by_channel_message_id",
            "get_lot_by_message_id",
            "get_lot_by_discussion_message_id",
    ):
        fn = getattr(db_mod, name, None)
        if not callable(fn):
            if fn is not None:
                print(
                    f"[WARN] db.db.{name} is not callable "
                    f"(type={type(fn).__name__})"
                )
            continue
        try:
            result = await _call_maybe_await(fn, msg_id)
        except (TypeError, ValueError) as e:
            print(f"[WARN] db.db.{name} failed: {e}")
            continue
        except Exception as e:
            print(f"[WARN] db.db.{name} raised {type(e).__name__}: {e}")
            continue
        if result:
            if not isinstance(result, dict):
                print(
                    f"[WARN] db.db.{name} returned {type(result).__name__},"
                    " expected dict"
                )
            return result

    print(f"[WARN] No suitable DB function for message_id={msg_id}")
    return None


from datetime import datetime
from typing import Any

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from bot.handlers.auctions import admin_tag, build_thanks_kb, safe_send_media, _get_exchange_cover_media

from db.db import (  # добавь эти импорты к своим (или замени существующие)
    get_lot_by_id,
    log_admin_action,
    update_auction_time_status,
    update_lot_field,
)

def _short_media_id(v: object) -> str:
    """Чтобы логи/сообщения не превращались в простыню file_id."""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    if len(s) <= 22:
        return s
    return f"{s[:12]}…{s[-8:]}"


def _fmt_dt_msk(dt: object) -> str:
    """Форматируем как 28.02 22:30 (без споров про TZ в тексте)."""
    if not dt:
        return "—"
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m %H:%M")
    return str(dt)


def _fmt_window_msk(start: object, end: object) -> str:
    s = _fmt_dt_msk(start)
    e = _fmt_dt_msk(end)
    # если обе даты ок и одинаковый день, красиво: 28.02 22:30–23:00
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            if start.date() == end.date():
                return f"{start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')}"
    except Exception:
        pass
    return f"{s}–{e}"


def _cur_emoji(currency: str | None) -> str:
    c = (currency or "").strip().lower()
    return {
        "diamonds": "💎",
        "diamond": "💎",
        "cups": "🍵",
        "cup": "🍵",
        "treasures": "🪙",
        "treasure": "🪙",
        "money": "💵",
        "cash": "💵",
        "usd": "💵",
        "rub": "💵",
    }.get(c, "💰")


def _obtain_emoji(obtain_type: str | None) -> str:
    t = (obtain_type or "").strip().lower()
    return {
        "diamonds": "💎",
        "diamond": "💎",
        "cups": "🍵",
        "cup": "🍵",
        "treasures": "🪙",
        "treasure": "🪙",
    }.get(t, "🎁")


def _yn_uid(v: object) -> str:
    if v is True:
        return "🆔 ✅ Да"
    if v is False:
        return "🆔 ❌ Нет"
    return "🆔 —"


def _rarity_line(rarity: str | None) -> str:
    r = (rarity or "").strip().lower()
    emo = RARITY_EMOJI.get(r, "🏷️")
    return f"🏷️ {emo} {rarity or '—'}"


def _pick_sold_count(lot: dict) -> int | None:
    # под разные названия колонок/алиасов, потому что жизнь боль
    for k in ("sold_count", "sold_before", "sold_prev", "sold_total"):
        v = lot.get(k)
        if isinstance(v, int):
            return v
        try:
            if v is not None and str(v).isdigit():
                return int(v)
        except Exception:
            pass
    return None


def _user_status_label(owner_row: dict) -> str:
    # ожидаемые варианты: luxury_level / luxury_tier / is_luxury
    lvl = owner_row.get("luxury_level") or owner_row.get("luxury_tier") or owner_row.get("luxury_lvl")
    if lvl is not None:
        try:
            lvl_i = int(lvl)
            return f"👑 Лакшери {lvl_i}"
        except Exception:
            return "👑 Лакшери"
    is_lux = owner_row.get("is_luxury")
    if is_lux is True:
        return "👑 Лакшери"
    return "🙂 Обычный"


def _format_change_lines(lot_before: dict, changes: list[tuple[str, Any, Any]]) -> list[str]:
    """
    changes: [(field, old, new), ...]
    field: auction_kind | craft_uid_possible | time_window | start_price | currency | comment | image_id
    """
    lines: list[str] = []
    for field, old, new in changes:
        f = (field or "").strip().lower()

        if f in {"time", "time_window", "schedule_time"}:
            # old/new ожидаем как (start, end) tuple
            try:
                old_s, old_e = old
            except Exception:
                old_s, old_e = None, None
            try:
                new_s, new_e = new
            except Exception:
                new_s, new_e = None, None
            lines.append(f"🕒 <b>Время:</b> {_fmt_window_msk(old_s, old_e)} → {_fmt_window_msk(new_s, new_e)} (МСК)")
            continue

        if f in {"start_price", "price"}:
            cur = lot_before.get("currency")
            ce = _cur_emoji(cur)
            old_s = "—" if old is None else f"{old} {ce}"
            new_s = "—" if new is None else f"{new} {ce}"
            lines.append(f"💰 <b>Цена:</b> {old_s} → {new_s}")
            continue

        if f == "currency":
            lines.append(f"💱 <b>Валюта:</b> {old or '—'} → {new or '—'}")
            continue

        if f in {"comment", "note"}:
            o = (old or "—").strip() if isinstance(old, str) else (old or "—")
            n = (new or "—").strip() if isinstance(new, str) else (new or "—")
            lines.append(f"💬 <b>Комментарий:</b> {o} → {n}")
            continue

        if f in {"image_id", "photo", "media"}:
            lines.append(f"🖼 <b>Фото:</b> {_short_media_id(old)} → {_short_media_id(new)}")
            continue

        if f in {"craft_uid_possible", "craft_uid", "uid"}:
            lines.append(f"🆔 <b>Крафт на UID:</b> {_yn_uid(old)} → {_yn_uid(new)}")
            continue

        if f in {"auction_kind", "kind", "type"}:
            lines.append(f"⚙️ <b>Тип аука:</b> {old or '—'} → {new or '—'}")
            continue

        # fallback
        lines.append(f"✏️ <b>{field}:</b> {old if old is not None else '—'} → {new if new is not None else '—'}")

    return lines


def _build_owner_notice_text(
        *,
        title: str,
        lot_after: dict,
        lot_before: dict,
        owners_for_status: list[dict],
        changes: list[tuple[str, Any, Any]],
        moderator: types.User,
) -> str:
    card_name = lot_after.get("card_name") or "—"
    hero_name = lot_after.get("hero_name") or "—"
    auction_id = lot_after.get("auction_id") or lot_before.get("auction_id") or "—"

    # время текущего слота (после изменения)
    cur_window = _fmt_window_msk(lot_after.get("start_time"), lot_after.get("end_time"))

    # статус пользователя: если несколько владельцев, показываем статус первого (обычно один владелец)
    status_label = _user_status_label(owners_for_status[0]) if owners_for_status else "—"

    deck_id = lot_after.get("deck_id") or lot_after.get("deck_num") or "—"
    deck_name = lot_after.get("deck_name") or lot_after.get("deck") or "—"

    rarity = lot_after.get("rarity") or "—"
    obtain_type = lot_after.get("obtain_type")
    obtain_amount = lot_after.get("obtain_amount")
    obtain_line = "🎁 —"
    if obtain_amount is not None:
        obtain_line = f"🎁 +{obtain_amount} {_obtain_emoji(obtain_type)}"

    sold_cnt = _pick_sold_count(lot_after)
    sold_line = f"📊 {sold_cnt}" if sold_cnt is not None else "📊 —"

    story = lot_after.get("story") or "—"
    quote = lot_after.get("quote") or "—"

    change_lines = _format_change_lines(lot_before, changes)
    changes_block = "\n".join(change_lines) if change_lines else "—"

    # тот самый “как при подаче заявки” блок
    meta_block = (
        f"👤 <b>Статус пользователя:</b> {status_label}\n"
        f"Колода: 🃏 {deck_id} колода — {deck_name}\n"
        f"Редкость: {_rarity_line(rarity)}\n"
        f"Крафт на UID возможен: {_yn_uid(lot_after.get('craft_uid_possible'))}\n"
        f"Продано ранее: {sold_line}\n"
        f"При получении в подарок даёт: {obtain_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
        f"Оплата ставки в течение месяца."
    )

    mod_line = f"Лот изменён модератором: {admin_tag(moderator)}"
    thanks_line = "Если хочешь, можешь сказать спасибо ниже ❤️\n"

    return (
        f"<b>{title}</b>\n\n"
        f"Лот: <b>{card_name}</b> — <i>{hero_name}</i>\n"
        f"ID: <code>{auction_id}</code>\n"
        f"Текущее время аукциона: {cur_window} (МСК)\n\n"
        f"<b>Изменения:</b>\n{changes_block}\n\n"
        f"{meta_block}\n\n"
        f"{mod_line}\n"
        f"{thanks_line}"
    )


async def _bot_send_media_any(
        bot,
        *,
        chat_id: int,
        file_id: str | None,
        caption: str,
        reply_markup,
) -> None:
    """Пробуем фото -> видео -> анимация -> текст."""
    if not file_id:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=reply_markup)
        return

    try:
        await bot.send_photo(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    try:
        await bot.send_video(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    try:
        await bot.send_animation(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        return
    except Exception:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=reply_markup)


async def _notify_owners_and_log(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        title: str,
        changes: list[tuple[str, Any, Any]],
        action_type: str,
) -> None:
    lot_before = await get_lot_by_id(auction_id)
    lot_after = await get_lot_by_id(auction_id)  # после апдейта вызовут ещё раз, но пусть будет безопасно

    # владельцы (и статусы/уровни если есть)
    owners_rows = await get_lot_owners_with_levels(auction_id)
    owners_text = await get_lot_owners_text(auction_id)

    # owner notice text
    owner_caption = _build_owner_notice_text(
        title=title,
        lot_after=lot_after,
        lot_before=lot_before,
        owners_for_status=owners_rows or [],
        changes=changes,
        moderator=admin_user,
    )

    # кнопка спасибо (по аналогии с удалением)
    thanks_kb = await build_thanks_kb(auction_id, admin_tag(admin_user))

    # рассылаем владельцам
    sent_to: set[int] = set()
    for row in owners_rows or []:
        try:
            uid = int(row.get("user_id"))
        except Exception:
            continue
        if uid in sent_to:
            continue
        sent_to.add(uid)
        try:
            await _bot_send_media_any(
                bot,
                chat_id=uid,
                file_id=(lot_after.get("image_id") or lot_after.get("photo_id")),
                caption=owner_caption,
                reply_markup=thanks_kb,
            )
        except Exception:
            # владелец мог закрыть ЛС, заблокировать бота, etc.
            pass

    # лог в админ-чаты
    log_lines = _format_change_lines(lot_before, changes)
    log_text = (
            f"✏️ <b>Изменение лота в расписании</b>\n"
            f"Лот <code>{auction_id}</code>: <b>{lot_after.get('card_name') or '—'}</b> — <i>{lot_after.get('hero_name') or '—'}</i>\n"
            f"Модератор: {admin_tag(admin_user)}\n\n"
            f"<b>Изменения:</b>\n" + ("\n".join(log_lines) if log_lines else "—") + "\n\n"
                                                                                    f"<b>Владельцы:</b> {owners_text}"
    )
    try:
        from bot.handlers.admin.logs_admin import send_admin_log  # локально, чтобы меньше шансов на цикличный импорт
        await send_admin_log(bot, log_text)
    except Exception:
        pass

    # audit в БД
    try:
        await log_admin_action(
            admin_user.id,
            action_type,
            auction_id,
            f"title={title}; changes={[(a, str(b), str(c)) for a, b, c in changes]}",
        )
    except Exception:
        pass


# -----------------------
# PUBLIC API (вызывай из confirm-хендлеров)
# -----------------------

async def apply_scheduled_time_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_start: datetime,
        new_end: datetime,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_start = lot.get("start_time")
    old_end = lot.get("end_time")

    # обновляем расписание
    await update_auction_time_status(auction_id, new_start, new_end, lot.get("status"))

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="⏳ Лот перенесён",
        changes=[("time_window", (old_start, old_end), (new_start, new_end))],
        action_type="schedule_edit_time",
    )


async def apply_scheduled_price_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_price: int,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_price = lot.get("start_price")
    await update_lot_field(auction_id, "start_price", int(new_price))

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💰 Цена лота изменена",
        changes=[("start_price", old_price, int(new_price))],
        action_type="schedule_edit_price",
    )


async def apply_scheduled_currency_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_currency: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_currency = lot.get("currency")
    await update_lot_field(auction_id, "currency", (new_currency or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💱 Валюта лота изменена",
        changes=[("currency", old_currency, (new_currency or "").strip())],
        action_type="schedule_edit_currency",
    )


async def apply_scheduled_comment_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_comment: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_comment = lot.get("comment")
    await update_lot_field(auction_id, "comment", (new_comment or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="💬 Комментарий к лоту изменён",
        changes=[("comment", old_comment, (new_comment or "").strip())],
        action_type="schedule_edit_comment",
    )


async def apply_scheduled_photo_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_image_id: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_image_id = lot.get("image_id")
    await update_lot_field(auction_id, "image_id", (new_image_id or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="🖼 Фото лота изменено",
        changes=[("image_id", old_image_id, (new_image_id or "").strip())],
        action_type="schedule_edit_photo",
    )


async def apply_scheduled_auction_kind_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_kind: str,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_kind = lot.get("auction_kind")
    await update_lot_field(auction_id, "auction_kind", (new_kind or "").strip())

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="⚙️ Тип аука изменён",
        changes=[("auction_kind", old_kind, (new_kind or "").strip())],
        action_type="schedule_edit_kind",
    )

EX_WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")
async def apply_scheduled_craft_uid_change(
        bot: Bot,
        *,
        admin_user: types.User,
        auction_id: int,
        new_value: bool,
) -> None:
    lot = await get_lot_by_id(auction_id)
    old_val = lot.get("craft_uid_possible")
    await update_lot_field(auction_id, "craft_uid_possible", bool(new_value))

    await _notify_owners_and_log(
        bot,
        admin_user=admin_user,
        auction_id=auction_id,
        title="🆔 Крафт на UID изменён",
        changes=[("craft_uid_possible", old_val, bool(new_value))],
        action_type="schedule_edit_craft_uid",
    )