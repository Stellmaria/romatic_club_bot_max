"""Exchange moderation presentation and Telegram delivery helpers."""

from __future__ import annotations

import contextlib
import html
import re

from aiogram import types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message

from bot.services.admin_thanks import build_thanks_kb
from bot.services.exchange_moderation import ExchangeModerationQueries
from bot.telegram.media import safe_send_media

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

    queries = await ExchangeModerationQueries.create()

    # deck name (если нет — покажем #id)
    deck_id = batch.get("deck_id")
    deck_name = None
    try:
        deck_name = await queries.deck_name(int(deck_id or 0))
    except Exception:
        deck_name = None
    deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

    # count cards
    items_cnt = 0
    try:
        items_cnt = await queries.batch_items_count(batch_id)
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
        cover_id, _ = await queries.first_card_media(batch_id)
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
    queries = await ExchangeModerationQueries.create()
    return await queries.first_card_media(batch_id)


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



# Public compatibility aliases. Cross-feature imports must use these names.
safe_user_mention = _safe_user_mention
cur_emoji = _cur_emoji
send_exchange_batch_card_admin = _send_exchange_batch_card_admin

__all__ = ['MAX_TG_LEN', 'SAFE_SPLIT', 'BR_RE', 'DT_FMT', '_safe_user_mention', '_as_str', '_admin_link_html', 'format_exchange_moderation_log', 'notify_exchange_user_moderation', 'format_exchange_new_request_log', '_looks_like_file_id', 'safe_answer_photo', 'tg_clean', '_get_exchange_cover_media_admin', '_media_kind_from_error_admin', '_send_exchange_batch_card_admin', 'build_exchange_pending_keyboard', '_cur_emoji', 'safe_user_mention', 'cur_emoji', 'send_exchange_batch_card_admin']
