"""Shared dependencies and pure helpers for :mod:`.moderation` features.

This module has no Telegram router and can be imported without registering handlers.
"""

import html as _html


import html as _html


import logging


from collections import defaultdict


from datetime import datetime, date, timedelta


from typing import Any


from aiogram import types, Router, F


from aiogram.exceptions import TelegramAPIError, TelegramBadRequest


from aiogram.filters import Command, CommandObject


from aiogram.fsm.context import FSMContext


from aiogram.fsm.state import StatesGroup, State


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from dateutil import tz


from bot.handlers.admin.admin_panel_shared import notify_owners_lot_changed


from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, REJECT_LOT_ADMIN_LOG, REJECT_LOT_USER_NOTIFY, \
    MSG_REASON_REJECT_ADD, MSG_REASON_REJECT_DELETE, MSG_PHOTO_CONFIRM, MSG_PHOTO_NOT_FOUND, MSG_CONFIRM_PUBLICATION, \
    REJECT_DELETE_USER_NOTIFY, \
    REJECT_DELETE_ADMIN_LOG, CANCEL_TEXTS, BUTTONS, CALLBACK_CONFIRM_LOT


from bot.handlers.admin.helper.admin_keyboards import days_keyboard


from bot.handlers.admin.helper.admin_keyboards import months_keyboard


from bot.handlers.admin.helper.admin_service import get_free_slots_and_schedule_for_lot


from bot.handlers.admin.action_support.exchange import safe_answer_photo, tg_clean
from bot.handlers.admin.action_support.forms import (
    add_deck_fsm_entry,
    owners_to_links_text,
    start_edit_schedule,
    start_preview_schedule,
)
from bot.handlers.admin.action_support.moderation import (
    process_reject_action,
    show_delete_requests_for_moderation,
    show_pendinglots,
)
from bot.handlers.admin.action_support.roles import admin_add_remove
from bot.handlers.admin.action_support.transport import (
    owner_or_secret_required,
    process_universal_cancel_text,
    safe_edit_message,
)
from bot.services.admin_logging import send_admin_log
from bot.services.admin_owners import get_lot_owners_text


from bot.handlers.admin.helper.new.formatting import format_pending_lot, format_admin_action_log, \
    get_lot_owners_with_levels


from bot.handlers.admin.helper.new.helper import split_message


from bot.handlers.admin.helper.new.keyboards import build_lot_keyboard, time_slots_keyboard, \
    build_back_keyboard, back_keyboard, menu_keyboard, build_back_button


from bot.handlers.admin.helper.new.wrapper import admin_only


from bot.handlers.admin.helper.user_helpers import get_owner_refs, build_schedule_lines, find_free_slots, \
    filter_slots_by_user_type, get_pretty_owners_for_log


from bot.handlers.admin.logs_admin import short_media_id


from bot.handlers.auction.exchange_moderation import show_pending_exchange_requests


from bot.telegram.media import bot_send_media_any as _bot_send_media_any


from bot.handlers.auction.publication import publish_auction_lot


from bot.services.admin_thanks import admin_tag, build_thanks_kb


from bot.core.time import auction_end_at_59, to_moscow, utc_now


from bot.handlers.helper.helpers_users import notify_lot_owner


from bot.domain.auctions import AuctionSlotConflict, InvalidAuctionTransition, InvalidExchangeTransition


from bot.services.auction_workflows import AuctionModerationService


from bot.services.exchanges import ExchangeService


from bot.utils import generate_free_slots_for_date


from bot.core.legacy_config import legacy_config


from db.auctions import (
    get_auctions_by_date_with_owners as get_auctions_by_date,
    get_auctions_by_date_with_owners,
)
from db.exchange import get_exchange_batch


from db.auctions import (
    get_lot_by_id,
    get_delete_request,
    update_delete_request_status,
    get_lot_owners,
    get_auctions_by_date,
)
from db.users import (
    is_luxury_user,
    get_user,
)
from db.admin import log_audit_action


from bot.telegram.states import (
    ApproveLotFSM,
    ModActionFSM,
    PreviewScheduleFSM,
    RejectDeleteFSM,
)


async def _update_auction_field(auction_id: int, field: str, value: Any) -> dict[str, Any]:
    service = await AuctionModerationService.create()
    return await service.update_field(auction_id, field=field, value=value)


import html


def _pretty(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"


async def notify_owners_pending_changed(
    bot,
    *,
    auction_id: int,
    admin_user: types.User,
    changes: list[tuple[str, object, object]],
) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners(int(auction_id))
    if not lot or not owners:
        return

    moderator_tag = admin_tag(admin_user)
    kb = await build_thanks_kb(int(auction_id), moderator_tag)

    def _v(x: object) -> str:
        if x is None:
            return "—"
        s = str(x).strip()
        return s if s else "—"

    ch = "\n".join([f"• <b>{t}:</b> <code>{_v(o)}</code> → <code>{_v(n)}</code>" for t, o, n in changes])

    caption = (
        "🧩 <b>Изменения в вашей заявке (модерация)</b>\n\n"
        f"Лот: <b>{lot.get('card_name') or '—'}</b> — <i>{lot.get('hero_name') or '—'}</i>\n"
        f"ID: <code>{auction_id}</code>\n\n"
        f"<b>Что изменили:</b>\n{ch}\n\n"
        f"👤 <b>Кто изменил:</b> {moderator_tag}\n"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n"
    )

    media_id = lot.get("image_id") or lot.get("photo_id")
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
            # pending тоже отправим с текущим медиа
            try:
                await bot.send_photo(uid, media_id, caption=caption, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await bot.send_message(uid, caption, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


async def _log_pending_change(
        bot,
        *,
        admin_user: types.User,
        auction_id: int,
        action_type: str,
        field_title: str,
        old_value: Any,
        new_value: Any,
) -> None:
    new_lot = await get_lot_by_id(int(auction_id))
    owners_text = await get_lot_owners_text(int(auction_id))

    log_text = format_admin_action_log(
        action="edit_pending",
        admin={
            "id": admin_user.id,
            "user_id": admin_user.id,  # на всякий случай под твою структуру
            "username": admin_user.username or "",
            "full_name": admin_user.full_name or "",
        },
        lot=new_lot,
        owners_text=owners_text,
    )
    log_text += _field_log_block(field_title, old_value, new_value)

    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=int(auction_id),
        details=f"{field_title}: {_pretty(old_value)} -> {_pretty(new_value)}",
    )


def _pretty_bool(v: Any) -> str:
    if v is None:
        return "—"
    return "✅ Да" if bool(v) else "❌ Нет"


def _pretty_value(field: str, v: Any) -> str:
    if v is None or v == "":
        return "—"
    if field in ("craft_uid_possible",):
        return _pretty_bool(v)
    return str(v)


def _field_log_block(field_title: str, old_value: Any, new_value: Any) -> str:
    return (
        "\n\n🧩 <b>Изменение поля</b>"
        f"\n📝 <b>Поле:</b> {html.escape(field_title)}"
        f"\n📎 <b>Было:</b> {html.escape(_pretty_value(field_title, old_value))}"
        f"\n✅ <b>Стало:</b> {html.escape(_pretty_value(field_title, new_value))}"
    )


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


async def _send_pending_lot_card(message: types.Message, bot, auction_id: int) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners = await get_lot_owners_with_levels(bot, int(auction_id))
    text = format_pending_lot(lot, owners)
    kb = build_lot_keyboard(lot, role="admin")

    media_id = (lot or {}).get("image_id") or (lot or {}).get("card_image_id")
    if media_id:
        # safe_answer_photo(msg, image_id, ...) — никаких photo_id=
        await safe_answer_photo(message, media_id, caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


def split_message_by_blocks(blocks, chunk_size=4096):
    chunks = []
    current = ""
    for block in blocks:
        if len(current) + len(block) > chunk_size:
            chunks.append(current)
            current = ""
        current += block
    if current:
        chunks.append(current)
    return chunks


def safe_html(text):
    return html.escape(str(text)) if text else ""


async def _log_pending_field_change(
        bot,
        *,
        admin_user: types.User,
        auction_id: int,
        field_title: str,
        old_value,
        new_value,
        action_type: str,
        lot_override: dict | None = None,
) -> None:
    lot = await get_lot_by_id(int(auction_id))
    owners_text = await get_lot_owners_text(int(auction_id))

    merged_lot = dict(lot or {})
    if lot_override:
        merged_lot.update(lot_override)

    log_text = format_admin_action_log(
        action="edit_lot",
        admin={"id": admin_user.id, "username": admin_user.username or admin_user.full_name},
        lot=merged_lot,
        owners_text=owners_text,
    )
    log_text += (
        "\n\n🧩 <b>Изменение в модерации (редактор заявки)</b>"
        f"\n✏️ <b>Поле:</b> {tg_clean(field_title)}"
        f"\n🔁 <b>Было:</b> {tg_clean(_pretty(old_value))}"
        f"\n✅ <b>Стало:</b> {tg_clean(_pretty(new_value))}"
    )
    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=int(auction_id),
        details=f"{field_title}: {_pretty(old_value)} -> {_pretty(new_value)}",
    )


def owners_to_compact_text(owners) -> str:
    import json
    if owners is None:
        return "—"
    if isinstance(owners, str):
        try:
            owners = json.loads(owners)
        except Exception:
            owners = []
    if not owners:
        return "—"
    parts = []
    for o in owners:
        uid = o.get("user_id")
        uname = (o.get("username") or "").strip()
        parts.append(f"@{uname}" if uname else (f"id:{uid}" if uid else "—"))
    return ", ".join([p for p in parts if p and p != "—"]) or "—"


log = logging.getLogger("auction_bot")


MSK = tz.gettz("Europe/Moscow")


def _to_msk(dt):
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


from aiogram.filters import Command


from aiogram import types


from db.admin import is_admin


from db.uid import is_user_banned  # если у тебя так называется


from db.users import get_user_by_username


CLIK_ROOT_TEXT = (
    "🐸 <b>Жабий помощник</b>\n\n"
    "Привет! Я бот — твой помощник здесь.\n"
    "Смотри прайс-лист, изучай памятки и смело оформляй заказ.\n"
    "Если что-то не ясно — задай вопрос, я помогу."
)


CLIK_PRICE_TEXT = (
    "💸 <b>Прайс-лист</b>\n"
    "#price_list\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)


CLIK_INSTRUCTION_TEXT = (
    "📌 <b>Памятки / Советы</b>\n"
    "#instruction\n\n"
    "Пока заглушка. Если надо — добавь сюда текст или отправку фото."
)


CLIK_ASK_TEXT = (
    "❓ <b>Задать вопрос</b>\n"
    "#ask_me\n\n"
    "Напиши вопрос одним сообщением.\n"
    "Я отправлю его админам."
)


CLIK_ORDER_PICK_PAY_TEXT = (
    "🛒 <b>Оформить заказ</b>\n"
    "#make_an_order\n\n"
    "Выбери вариант оплаты:"
)


CLIK_CB = "clik"


CLIK_STORY_COVER_PVT = "AgACAgQAAxkBAAELwn9ppGTmTAABJV5-fL_avtoXg1oHo-IAAioOaxtHzCBRAut-JCPq_mIBAAMCAANtAAM6BA"


CLIK_PVT_LI = [
    ("seb", "Себастьян", "AgACAgQAAxkBAAELwoFppGUelLt-CdGhoOkrqOTM490lGAACFQ5rG-beIFFkrLymWF1WEQEAAwIAA20AAzoE"),
    ("wil", "Вильям", "AgACAgQAAxkBAAELwo1ppGVI6pJ2q5wuNzNXhAka1GakSAACKw5rG0fMIFHW7G34DQqgUAEAAwIAA20AAzoE"),
    ("kri", "Кристина", "AgACAgQAAxkBAAELwo9ppGV02pUgY_2Sy9-ZTO0d4Eo2SAACLA5rG0fMIFFwCeb8kSdCQwEAAwIAA20AAzoE"),
    ("jac", "Джеки", "AgACAgQAAxkBAAELwpZppGWD_2ppkKYejBEyWzx9R-oUFAACLQ5rG0fMIFGP0i2Y6VUTcgEAAwIAA20AAzoE"),
    ("jor", "Хорхе", "AgACAgQAAxkBAAELwrRppGYH8dpcZunjNaNLtz3oH2CyuQACMA5rG0fMIFGcwl05-liqJQEAAwIAA20AAzoE"),
    ("cli", "Клайв", "AgACAgQAAxkBAAELwrhppGYVzWcSl7SYIN-xEDlGsvUeMwACMQ5rG0fMIFGo_o6Kjxw0vgEAAwIAA20AAzoE"),
    ("die", "Диего", "AgACAgQAAxkBAAELwr9ppGYt2gue1lPtrVO_FoQ1PJ-VuQACMg5rG0fMIFE0SHwnZRXkSwEAAwIAA20AAzoE"),
    ("kai", "Кай", "AgACAgQAAxkBAAELwqRppGXQJF3BXJ7b7GFjg9gJU1HwtAACLw5rG0fMIFFzVcOPF9D3BgEAAwIAA20AAzoE"),
    ("lor", "Лоренза", "AgACAgQAAxkBAAELwqJppGWrDdb6ipQYfmnIvChEkF0-1AACLg5rG0fMIFGoyJNIma67CQEAAwIAA20AAzoE"),
]


CLIK_PVT_LI_MAP = {k: {"name": n, "photo": p} for k, n, p in CLIK_PVT_LI}


CLIK_STORIES = [
    "Паруса в Тумане",
    "Рожденная Луной",
    "Моя Голивудская История",
    "Королева за 30 дней",
    "Тени Сентфора",
    "Высокий Прибой",
    "В Ритме Страсти",
    "Я Охочусь на Тебя",
    "Секрет Небес",
    "Легенда Ивы",
    "Дракула. История любви",
    "Путь валькирии",
    "Ярость Титанов",
    "Десять Желаний Софи",
    "Грешный Лондон",
    "По Тонкому Льду",
    "Арканум",
    "Хроники Гладиаторов",
    "Сердце Треспии",
    "Кали: Зов Тьмы",
    "Цветок из Огня Тиамат",
    "Теодора",
    "Сквозь Бурю и Пламя",
    "Идеал",
    "Пси",
    "Покоряя Версаль",
    "Роза Пустыни",
    "Секрет Небес 2",
    "W: Ловчая Времени",
    "Эдемов Сад",
    "Идеал. Том 2",
    "Разбитое Сердце Астреи",
    "Секрет Небес Реквием",
    "Семь Братьев",
    "И Поглотит Нас Морок",
    "Бюро Паралельных Миров. Том 1",
    "Te amo: Том 1. Залив Надежды",
]


CLIK_STORIES_PER_PAGE = 10


class ClikFSM(StatesGroup):
    waiting_question = State()

    order_story = State()
    order_tasks = State()
    order_ach_mode = State()
    order_love_mode = State()          # для НЕ-ПВТ (1/2/3/все)
    order_love_select = State()        # для ПВТ (выбор персонажей)
    waiting_other_text = State()
    order_cups_source = State()

    waiting_order = State()            # логин/пароль


def _clik_mark(v: bool) -> str:
    return "✅" if v else "⬜️"


def _clik_story_key(title: str) -> str:
    return "pvt" if title.strip() == "Паруса в Тумане" else "other"


def _clik_pages(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)


def _kb_clik_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Прайс-лист", callback_data=f"{CLIK_CB}:price"),
            InlineKeyboardButton(text="📌 Памятки", callback_data=f"{CLIK_CB}:instruction"),
        ],
        [
            InlineKeyboardButton(text="🛒 Оформить заказ", callback_data=f"{CLIK_CB}:order"),
            InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"{CLIK_CB}:ask"),
        ],
    ])


def _kb_clik_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")]
    ])


def _kb_clik_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍵 Чашки", callback_data=f"{CLIK_CB}:pay:cups"),
            InlineKeyboardButton(text="💎 Алмазы", callback_data=f"{CLIK_CB}:pay:diamonds"),
            InlineKeyboardButton(text="₽ Рубли", callback_data=f"{CLIK_CB}:pay:rub"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:root")],
    ])


def _kb_clik_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")]
    ])


def _kb_clik_stories(page: int) -> InlineKeyboardMarkup:
    total = len(CLIK_STORIES)
    pages = _clik_pages(total, CLIK_STORIES_PER_PAGE)
    page = max(0, min(int(page), pages - 1))

    start = page * CLIK_STORIES_PER_PAGE
    chunk = CLIK_STORIES[start:start + CLIK_STORIES_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    for i, title in enumerate(chunk, start=start):
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CLIK_CB}:s:pick:{i}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CLIK_CB}:s:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"{CLIK_CB}:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CLIK_CB}:s:page:{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="⬅️ Назад к оплате", callback_data=f"{CLIK_CB}:order")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _clik_task_flags(data: dict) -> dict:
    return {
        "play": bool(data.get("clik_t_play")),
        "ach": bool(data.get("clik_t_ach")),
        "love": bool(data.get("clik_t_love")),
        "wardrobe": bool(data.get("clik_t_wardrobe")),
        "other": bool(data.get("clik_t_other")),
    }


def _clik_order_intro(data: dict, step_text: str) -> str:
    pay = html.escape(str(data.get("clik_pay") or "—"))
    story = html.escape(str(data.get("clik_story") or "—"))
    return (
        "✅ <b>Сообщение для заказа</b>\n\n"
        f"💳 <b>Оплата:</b> {pay}\n"
        f"📚 <b>История:</b> {story}\n\n"
        f"{step_text}"
    )


def _clik_preview_summary(data: dict) -> str:
    flags = _clik_task_flags(data)

    # ачивки (заглушка)
    ach_mode = str(data.get("clik_ach_mode") or "").strip()  # all | story

    # любовные линии
    story_key = str(data.get("clik_story_key") or "other")
    love_mode = str(data.get("clik_love_mode") or "").strip()  # all | 1 | 2 | 3
    love_selected = data.get("clik_love_selected") or []

    other_text = (data.get("clik_other_text") or "").strip()

    lines = ["<b>Что нужно сделать:</b>"]
    any_task = False

    if flags["play"]:
        lines.append("• пройти историю;")
        any_task = True

    if flags["ach"]:
        any_task = True
        if ach_mode == "all":
            lines.append("• собрать ачивки (все) — <i>список позже</i>;")
        elif ach_mode == "story":
            lines.append("• собрать ачивки (по истории) — <i>список позже</i>;")
        else:
            lines.append("• собрать ачивки — <i>режим не выбран</i>;")

    if flags["wardrobe"]:
        lines.append("• собрать гардероб (зеркало);")
        any_task = True

    if flags["love"]:
        any_task = True
        if story_key == "pvt":
            if love_selected:
                names = [CLIK_PVT_LI_MAP.get(k, {}).get("name", k) for k in love_selected]
                lines.append("• любовные линии: " + ", ".join(html.escape(n) for n in names) + ";")
            else:
                lines.append("• любовные линии — <i>не выбраны</i>;")
        else:
            if love_mode == "all":
                lines.append("• достичь 100% по любовным линиям (все);")
            elif love_mode in {"1", "2", "3"}:
                lines.append(f"• достичь 100% по любовным линиям ({love_mode});")
            else:
                lines.append("• достичь 100% по любовным линиям — <i>кол-во не выбрано</i>;")

    if flags["other"]:
        any_task = True
        if other_text:
            lines.append(f"• другое: <code>{html.escape(other_text)}</code>;")
        else:
            lines.append("• другое: <i>описание не задано</i>;")

    if not any_task:
        lines.append("• —")

    # чашки (если выбран pay=cups)
    if str(data.get("clik_pay_key") or "") == "cups":
        src = str(data.get("clik_cups_source") or "").strip()
        src_txt = "—"
        if src == "account":
            src_txt = "есть чашки на аккаунте"
        elif src == "daily":
            src_txt = "проходим на ежедневных"
        lines.append("")
        lines.append(f"🍵 <b>Чашки:</b> {html.escape(src_txt)}")

    return "\n".join(lines)


def _kb_clik_tasks(data: dict) -> InlineKeyboardMarkup:
    flags = _clik_task_flags(data)

    ach_mode = str(data.get("clik_ach_mode") or "").strip()
    ach_suffix = ""
    if flags["ach"]:
        ach_suffix = " (все)" if ach_mode == "all" else (" (по истории)" if ach_mode == "story" else "")

    story_key = str(data.get("clik_story_key") or "other")
    love_suffix = ""
    if flags["love"]:
        if story_key == "pvt":
            selected = data.get("clik_love_selected") or []
            love_suffix = f" ({len(selected)})" if selected else ""
        else:
            lm = str(data.get("clik_love_mode") or "").strip()
            love_suffix = " (все)" if lm == "all" else (f" ({lm})" if lm in {"1", "2", "3"} else "")

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"📖 Пройти историю {_clik_mark(flags['play'])}", callback_data=f"{CLIK_CB}:t:toggle:play")],
        [InlineKeyboardButton(text=f"🏆 Ачивки{ach_suffix} {_clik_mark(flags['ach'])}", callback_data=f"{CLIK_CB}:t:ach")],
        [InlineKeyboardButton(text=f"💞 Любовные линии{love_suffix} {_clik_mark(flags['love'])}", callback_data=f"{CLIK_CB}:t:love")],
        [InlineKeyboardButton(text=f"🪞 Гардероб (Зеркало) {_clik_mark(flags['wardrobe'])}", callback_data=f"{CLIK_CB}:t:toggle:wardrobe")],
        [InlineKeyboardButton(text=f"✍️ Другое {_clik_mark(flags['other'])}", callback_data=f"{CLIK_CB}:t:other")],
        [InlineKeyboardButton(text="➡️ Далее", callback_data=f"{CLIK_CB}:t:next")],
        [InlineKeyboardButton(text="⬅️ Назад к историям", callback_data=f"{CLIK_CB}:t:back_stories")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_clik_ach_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все ачивки", callback_data=f"{CLIK_CB}:ach:set:all")],
        [InlineKeyboardButton(text="✅ Только по выбранной истории", callback_data=f"{CLIK_CB}:ach:set:story")],
        [InlineKeyboardButton(text="❌ Выключить ачивки", callback_data=f"{CLIK_CB}:ach:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:ach:back")],
    ])


def _kb_clik_love_mode_generic() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все", callback_data=f"{CLIK_CB}:love:set:all")],
        [
            InlineKeyboardButton(text="1", callback_data=f"{CLIK_CB}:love:set:1"),
            InlineKeyboardButton(text="2", callback_data=f"{CLIK_CB}:love:set:2"),
            InlineKeyboardButton(text="3", callback_data=f"{CLIK_CB}:love:set:3"),
        ],
        [InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:love:off")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:love:back")],
    ])


def _kb_clik_love_pvt(data: dict) -> InlineKeyboardMarkup:
    selected = set(data.get("clik_love_selected") or [])
    rows: list[list[InlineKeyboardButton]] = []

    # кнопки персонажей (в 2 колонки)
    btns: list[InlineKeyboardButton] = []
    for k, n, _p in CLIK_PVT_LI:
        mark = "✅" if k in selected else "⬜️"
        btns.append(InlineKeyboardButton(text=f"{mark} {n}", callback_data=f"{CLIK_CB}:lpvt:toggle:{k}"))

    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])

    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"{CLIK_CB}:lpvt:done")])
    rows.append([InlineKeyboardButton(text="❌ Выключить линии", callback_data=f"{CLIK_CB}:lpvt:off")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:lpvt:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_clik_cups_source() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍵 Есть чашки на аккаунте", callback_data=f"{CLIK_CB}:cups:account")],
        [InlineKeyboardButton(text="📅 Проходим на ежедневных", callback_data=f"{CLIK_CB}:cups:daily")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CLIK_CB}:cups:back")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])


def _kb_clik_final_back(data: dict) -> InlineKeyboardMarkup:
    if str(data.get("clik_pay_key") or "") == "cups":
        back_cb = f"{CLIK_CB}:final:back_cups"
    else:
        back_cb = f"{CLIK_CB}:final:back_tasks"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CLIK_CB}:root")],
    ])


async def _ui_edit(call: types.CallbackQuery, *, text: str, kb: InlineKeyboardMarkup, photo_id: str | None = None):
    """
    Универсально обновляет UI.
    - Если photo_id указан: стараемся показать фото + caption (с заменой текст->фото при необходимости).
    - Если photo_id не указан: показываем текст (с заменой фото->текст при необходимости).
    """
    msg = call.message
    if not msg:
        return

    chat_id = msg.chat.id
    try:
        if photo_id:
            if msg.photo:
                media = types.InputMediaPhoto(media=photo_id, caption=text, parse_mode="HTML")
                await msg.edit_media(media=media, reply_markup=kb)
            else:
                new_msg = await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
        else:
            if msg.photo:
                new_msg = await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                try:
                    await msg.delete()
                except Exception:
                    pass
                call.message = new_msg
            else:
                await msg.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest:
        # Фоллбек: просто отправляем новое
        if photo_id:
            await call.bot.send_photo(chat_id, photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await call.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def _user_tag(u: types.User) -> str:
    uname = (u.username or "").strip()
    if uname:
        esc = html.escape(uname.lstrip("@"))
        return f'<a href="https://t.me/{esc}">@{esc}</a>'
    return f'<a href="tg://user?id={u.id}">id{u.id}</a>'

# Star imports are deliberate in the generated feature modules: they recreate
# the original module namespace while keeping handler ownership explicit.
__all__ = [name for name in globals() if not name.startswith("__")]
