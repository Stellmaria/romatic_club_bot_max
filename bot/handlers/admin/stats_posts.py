import html
import re
from datetime import datetime, date, time

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.core.legacy_config import legacy_config
from db.legacy import (
    get_post_months,
    get_post_days,
    get_posts_for_day,
    count_posts_for_day,
    get_post_details,
    set_post_checked,
    set_post_manual_note,
    set_post_excluded,
)

from bot.legacy_fsm import PostStatsFSM, PostStatsEditFSM
from bot.telegram.callback_parser import split_callback_data

# Опционально: если ты добавила универсальный сеттер
try:
    from db.legacy import set_post_stat_value  # type: ignore
except Exception:
    set_post_stat_value = None  # noqa

router = Router()
PAGE_SIZE = 12

_BIGINT_MIN = -9223372036854775808
_BIGINT_MAX = 9223372036854775807


# -------------------------
# Helpers: links / formatting
# -------------------------
def _internal_chat_id(chat_id: int) -> int:
    # -1002285966851 -> 2285966851 (для ссылок /c/)
    cid = abs(int(chat_id))
    s = str(cid)
    if s.startswith("100"):
        return int(s[3:])
    return cid


def _discussion_link(discussion_id: int | None, root_id: int | None) -> str | None:
    if not root_id:
        return None
    if discussion_id:
        return f"https://t.me/c/{int(discussion_id)}/{int(root_id)}"
    internal = _internal_chat_id(legacy_config.DISCUSSION_CHAT_ID)
    return f"https://t.me/c/{internal}/{int(root_id)}"


def _uid_links_html(uid: int) -> str:
    return f"<a href='tg://user?id={uid}'>id:{uid}</a>"


def _safe(v) -> str:
    return html.escape("" if v is None else str(v))


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y %H:%M:%S") if dt else "—"


def _fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def _fmt_time(t: time | None) -> str:
    return t.strftime("%H:%M:%S") if t else "—"


# -------------------------
# Parsing input
# -------------------------
def _parse_int_from_text(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_time(s: str) -> time | None:
    s = (s or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return None


async def _save_field(post_id: int, field: str, value, admin_id: int) -> None:
    """
    Сохраняет ручное поле.
    Требует, чтобы в db/db.py была функция set_post_stat_value(post_id, field, value, admin_id).
    """
    if set_post_stat_value is None:
        raise RuntimeError(
            "Функция set_post_stat_value не найдена в db/db.py. "
            "Добавь её (универсальный сеттер) или скажи, и я дам версию без неё."
        )
    await set_post_stat_value(int(post_id), field, value, int(admin_id))


# -------------------------
# UI: keyboards
# -------------------------
def _kb_months(months: list[dict]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in months:
        ym = m["ym"]
        cnt = m["cnt"]
        checked = m["checked_cnt"]
        kb.button(text=f"{ym} • {checked}/{cnt}", callback_data=f"psm|{ym}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_days(ym: str, days: list[dict]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        day: date = d["day"]
        cnt = d["cnt"]
        checked = d["checked_cnt"]
        kb.button(
            text=f"{day.strftime('%d.%m')} • {checked}/{cnt}",
            callback_data=f"psd|{ym}|{day.isoformat()}",
        )
    kb.button(text="⬅️ К месяцам", callback_data="ps_back|months")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def _kb_posts(day_iso: str, offset: int, total: int, posts: list[dict]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for p in posts:
        post_id = p["post_id"]
        dt = p.get("post_date_msk")
        t = dt.strftime("%H:%M") if dt else "??:??"
        checked = "✅" if p.get("checked") else "⬜️"
        valid = p.get("thread_valid") or 0
        mx = p.get("max_thread_valid")
        mx_txt = f"/{mx}" if mx is not None else ""
        kb.button(
            text=f"{checked} {t} • #{post_id} • {valid}{mx_txt}",
            callback_data=f"psp|{day_iso}|{offset}|{post_id}",
        )

    # pagination row
    nav = InlineKeyboardBuilder()
    if offset > 0:
        nav.button(text="⬅️", callback_data=f"psl|{day_iso}|{max(0, offset - PAGE_SIZE)}")
    nav.button(text=f"{offset + 1}-{min(total, offset + PAGE_SIZE)} / {total}", callback_data="noop")
    if offset + PAGE_SIZE < total:
        nav.button(text="➡️", callback_data=f"psl|{day_iso}|{offset + PAGE_SIZE}")

    kb.attach(nav)

    kb.button(text="⬅️ К дням", callback_data=f"ps_back|days|{day_iso[:7]}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_post_detail(day_iso: str, offset: int, post: dict) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    pid = int(post["post_id"])

    if post.get("post_link"):
        kb.button(text="🔗 Открыть пост", url=str(post["post_link"]))

    disc = _discussion_link(post.get("discussion_id"), post.get("root_id"))
    if disc:
        kb.button(text="💬 Открыть обсуждение", url=disc)

    checked = bool(post.get("checked"))
    kb.button(
        text=("☑️ Снять проверку" if checked else "✅ Отметить проверено"),
        callback_data=f"psc|{day_iso}|{offset}|{pid}|{0 if checked else 1}",
    )

    # редактируемые поля (все из твоего списка + валид/всего)
    kb.button(text="✏️ № (порядковый)", callback_data=f"pse|{day_iso}|{offset}|{pid}|ordinal")
    kb.button(text="✏️ Дата (ручн.)", callback_data=f"pse|{day_iso}|{offset}|{pid}|date")
    kb.button(text="✏️ Время выхода", callback_data=f"pse|{day_iso}|{offset}|{pid}|time")

    kb.button(text="✏️ Колода №", callback_data=f"pse|{day_iso}|{offset}|{pid}|deck")
    kb.button(text="✏️ Название карты", callback_data=f"pse|{day_iso}|{offset}|{pid}|card")

    kb.button(text="✏️ Участников ставок", callback_data=f"pse|{day_iso}|{offset}|{pid}|bidders")
    kb.button(text="✏️ Мин ставка", callback_data=f"pse|{day_iso}|{offset}|{pid}|min")
    kb.button(text="✏️ Макс ставка", callback_data=f"pse|{day_iso}|{offset}|{pid}|max")

    kb.button(text="✏️ Валидные (ручн.)", callback_data=f"pse|{day_iso}|{offset}|{pid}|valid")
    kb.button(text="✏️ Всего ставок (ручн.)", callback_data=f"pse|{day_iso}|{offset}|{pid}|total")

    kb.button(text="✏️ Хозяин (user_id)", callback_data=f"pse|{day_iso}|{offset}|{pid}|owner")
    kb.button(text="✏️ Победитель (user_id)", callback_data=f"pse|{day_iso}|{offset}|{pid}|winner")
    kb.button(text="✏️ Ссылка", callback_data=f"pse|{day_iso}|{offset}|{pid}|link")

    excluded = bool(post.get("excluded"))
    if excluded:
        kb.button(text="↩️ Вернуть в список", callback_data=f"psx|{day_iso}|{offset}|{pid}|0")
    else:
        kb.button(text="🗑 Не аукцион", callback_data=f"psx|{day_iso}|{offset}|{pid}|1")

    kb.button(text="📝 Заметка", callback_data=f"psn|{day_iso}|{offset}|{pid}")
    kb.button(text="⬅️ Назад к списку", callback_data=f"psl|{day_iso}|{offset}")

    kb.adjust(2, 2, 2, 2, 2, 2, 1, 1)
    return kb.as_markup()


# -------------------------
# UI: render card
# -------------------------
def _render_post_detail(post: dict) -> str:
    pid = int(post["post_id"])
    checked = "✅" if post.get("checked") else "⬜️"

    scan_dt: datetime | None = post.get("post_date_msk")
    scan_date = scan_dt.strftime("%d.%m.%Y") if scan_dt else "—"
    scan_time = scan_dt.strftime("%H:%M:%S") if scan_dt else "—"

    # scan stats
    msgs_scanned = post.get("msgs_scanned")
    numeric_msgs = post.get("numeric_msgs")
    thread_bids = post.get("thread_bids")
    thread_valid = post.get("thread_valid")
    max_thread_valid = post.get("max_thread_valid")
    scan_winner = post.get("winner_id")

    # manual stats
    ordinal = post.get("ordinal_no")
    md = post.get("manual_date")
    mt = post.get("manual_time")
    deck = post.get("deck_no")
    card = post.get("card_title") or "—"
    bidders = post.get("bidders_count")
    min_bid = post.get("min_bid")
    max_bid = post.get("manual_max_bid")
    owner = post.get("owner_id")
    manual_winner = post.get("manual_winner_id")
    link = post.get("manual_link") or post.get("post_link")

    owner_txt = _uid_links_html(int(owner)) if owner else "—"
    winner_final = manual_winner or scan_winner
    winner_txt = _uid_links_html(int(winner_final)) if winner_final else "—"

    return (
        f"📌 <b>Пост #{pid}</b> {checked}\n"
        f"🗓 <b>Дата (скан):</b> {scan_date}\n"
        f"🕒 <b>Время (скан):</b> {scan_time} (МСК)\n"
        f"⏳ <b>Окончание:</b> {_safe(_fmt_dt(post.get('end_time_msk')))}\n"
        f"⏰ <b>Дедлайн:</b> {_safe(_fmt_dt(post.get('deadline_msk')))}\n\n"
        f"🔍 <b>Скан</b>\n"
        f"• root_id: <code>{_safe(post.get('root_id'))}</code>\n"
        f"• сообщений просмотрено: <b>{_safe(msgs_scanned)}</b>\n"
        f"• числовых: <b>{_safe(numeric_msgs)}</b>\n"
        f"• ставок в треде: <b>{_safe(thread_bids)}</b>\n"
        f"• валидных в треде: <b>{_safe(thread_valid)}</b>\n"
        f"• макс валидная: <b>{_safe(max_thread_valid)}</b>\n\n"
        f"📊 <b>Статистика (ручная)</b>\n"
        f"1) Порядковый №: <b>{_safe(ordinal)}</b>\n"
        f"2) Дата (ручн.): <b>{_safe(_fmt_date(md))}</b>\n"
        f"3) Время выхода (ручн.): <b>{_safe(_fmt_time(mt))}</b>\n"
        f"4) Номер колоды: <b>{_safe(deck)}</b>\n"
        f"5) Название карты: <b>{_safe(card)}</b>\n"
        f"6) Кол-во участников ставок: <b>{_safe(bidders)}</b>\n"
        f"7) Минимальная ставка: <b>{_safe(min_bid)}</b>\n"
        f"8) Максимальная ставка: <b>{_safe(max_bid)}</b>\n"
        f"9) Хозяин карты: {owner_txt}\n"
        f"10) Победитель: {winner_txt}\n"
        f"🔗 Ссылка: {_safe(link)}\n\n"
        f"📝 заметка админа: {_safe(post.get('manual_note') or '—')}"
    )


# -------------------------
# UI: screen rendering
# -------------------------
async def _show_posts_list(message: types.Message, day_iso: str, offset: int) -> None:
    day = date.fromisoformat(day_iso)
    total = await count_posts_for_day(day)
    posts = await get_posts_for_day(day, offset=offset, limit=PAGE_SIZE)

    text = f"🗓 День: <b>{day.strftime('%d.%m.%Y')}</b>\nПостов: <b>{total}</b>"

    try:
        await message.edit_text(
            text,
            reply_markup=_kb_posts(day_iso, offset, total, posts),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass


async def _show_post_detail(message: types.Message, day_iso: str, offset: int, post_id: int) -> None:
    post = await get_post_details(post_id)
    if not post:
        try:
            await message.edit_text("Пост не найден в БД.")
        except TelegramBadRequest:
            pass
        return

    try:
        await message.edit_text(
            _render_post_detail(post),
            reply_markup=_kb_post_detail(day_iso, offset, post),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass


# -------------------------
# Entry point
# -------------------------
@router.message(
    F.text.in_(["📮 Посты аукциона", "📈 Показать статистику", "Показать статистику"]),
    F.chat.type == "private",
)
@admin_only
async def posts_stats_entry(message: types.Message, state: FSMContext):
    await state.clear()
    months = await get_post_months()
    if not months:
        await message.answer("Пока нет данных по постам. Сначала импортни CSV в БД.")
        return

    await message.answer(
        "📮 Посты аукциона\n\nВыбери месяц:",
        reply_markup=_kb_months(months),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "noop")
async def _noop(call: types.CallbackQuery):
    await call.answer()


# -------------------------
# Navigation
# -------------------------
@router.callback_query(F.data.startswith("psm|"))
@admin_only
async def pick_month(call: types.CallbackQuery):
    await call.answer()
    ym = split_callback_data(call.data, "|", 1)[1]
    days = await get_post_days(ym)
    text = f"📅 Месяц: <b>{html.escape(ym)}</b>\nВыбери день:"
    try:
        await call.message.edit_text(text, reply_markup=_kb_days(ym, days), parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("psd|"))
@admin_only
async def pick_day(call: types.CallbackQuery):
    await call.answer()
    _, ym, day_iso = split_callback_data(call.data, "|", 2)
    await _show_posts_list(call.message, day_iso, 0)


@router.callback_query(F.data.startswith("psl|"))
@admin_only
async def posts_page(call: types.CallbackQuery):
    await call.answer()
    _, day_iso, off_s = split_callback_data(call.data, "|", 2)
    await _show_posts_list(call.message, day_iso, int(off_s))


@router.callback_query(F.data.startswith("psp|"))
@admin_only
async def post_detail(call: types.CallbackQuery):
    await call.answer()
    _, day_iso, off_s, post_id_s = split_callback_data(call.data, "|", 3)
    await _show_post_detail(call.message, day_iso, int(off_s), int(post_id_s))


@router.callback_query(F.data == "ps_back|months")
@admin_only
async def back_to_months(call: types.CallbackQuery):
    await call.answer()
    months = await get_post_months()
    try:
        await call.message.edit_text(
            "📮 Посты аукциона\n\nВыбери месяц:",
            reply_markup=_kb_months(months),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("ps_back|days|"))
@admin_only
async def back_to_days(call: types.CallbackQuery):
    await call.answer()
    ym = split_callback_data(call.data, "|")[-1]
    days = await get_post_days(ym)
    try:
        await call.message.edit_text(
            f"📅 Месяц: <b>{html.escape(ym)}</b>\nВыбери день:",
            reply_markup=_kb_days(ym, days),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass


# -------------------------
# Toggle: checked / excluded
# -------------------------
@router.callback_query(F.data.startswith("psc|"))
@admin_only
async def toggle_checked(call: types.CallbackQuery):
    await call.answer()
    _, day_iso, off_s, post_id_s, checked_s = split_callback_data(call.data, "|", 4)

    offset = int(off_s)
    post_id = int(post_id_s)
    checked = bool(int(checked_s))

    await set_post_checked(post_id, checked, call.from_user.id)
    await _show_post_detail(call.message, day_iso, offset, post_id)


@router.callback_query(F.data.startswith("psx|"))
@admin_only
async def toggle_excluded(call: types.CallbackQuery):
    await call.answer()
    _, day_iso, off_s, post_id_s, flag_s = split_callback_data(call.data, "|", 4)

    offset = int(off_s)
    post_id = int(post_id_s)
    excluded = bool(int(flag_s))

    await set_post_excluded(post_id, excluded, call.from_user.id, "не аукцион")

    if excluded:
        await _show_posts_list(call.message, day_iso, offset)
    else:
        await _show_post_detail(call.message, day_iso, offset, post_id)


# -------------------------
# Note flow
# -------------------------
@router.callback_query(F.data.startswith("psn|"))
@admin_only
async def ask_note(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    _, day_iso, off_s, post_id_s = split_callback_data(call.data, "|", 3)

    await state.set_state(PostStatsFSM.waiting_for_note)
    await state.update_data(day_iso=day_iso, offset=int(off_s), post_id=int(post_id_s))

    await call.message.answer(
        "Напиши заметку для этого поста.\n\nЧтобы очистить: отправь одиночный минус <code>-</code>.",
        parse_mode="HTML",
    )


@router.message(PostStatsFSM.waiting_for_note, F.chat.type == "private")
@admin_only
async def save_note(message: types.Message, state: FSMContext):
    data = await state.get_data()
    post_id = int(data["post_id"])
    day_iso = data["day_iso"]
    offset = int(data["offset"])

    txt = (message.text or "").strip()
    note = None if txt == "-" else txt

    await set_post_manual_note(post_id, note, message.from_user.id)
    await state.clear()

    post = await get_post_details(post_id)
    if not post:
        await message.answer("Пост не найден в БД.")
        return

    await message.answer(
        "Сохранено ✅\n\n" + _render_post_detail(post),
        parse_mode="HTML",
        reply_markup=_kb_post_detail(day_iso, offset, post),
        disable_web_page_preview=True,
    )


# -------------------------
# Edit fields flow
# -------------------------
_FIELD_LABELS = {
    # твои поля
    "ordinal": "Порядковый номер (число)",
    "date": "Дата (ДД.ММ.ГГГГ)",
    "time": "Время выхода (ЧЧ:ММ или ЧЧ:ММ:СС)",
    "deck": "Номер колоды (число)",
    "card": "Название карты (текст)",
    "bidders": "Количество участников ставок (число людей)",
    "min": "Минимальная ставка (число)",
    "max": "Максимальная ставка (число)",
    "owner": "Хозяин карты (user_id)",
    "winner": "Победитель (user_id)",
    "link": "Ссылка (текст)",
    # дополнительные поля из скана/ручной проверки
    "valid": "Валидные ставки (ручные, число)",
    "total": "Всего ставок (ручные, число)",
}

_INT_FIELDS = {"ordinal", "deck", "bidders", "min", "max", "owner", "winner", "valid", "total"}
_TEXT_FIELDS = {"card", "link"}
_DATE_FIELDS = {"date"}
_TIME_FIELDS = {"time"}


@router.callback_query(F.data.startswith("pse|"))
@admin_only
async def edit_field_prompt(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    _, day_iso, off_s, post_id_s, field = split_callback_data(call.data, "|", 4)

    if field not in _FIELD_LABELS:
        await call.answer("Неизвестное поле", show_alert=True)
        return

    await state.set_state(PostStatsEditFSM.waiting_for_value)
    await state.update_data(
        day_iso=day_iso,
        offset=int(off_s),
        post_id=int(post_id_s),
        field=field,
    )

    hint = _FIELD_LABELS[field]
    extra = ""
    if field in _INT_FIELDS:
        extra = "Отправь число. Для очистки: <code>-</code>."
    elif field in _DATE_FIELDS:
        extra = "Формат: <code>13.01.2026</code>. Для очистки: <code>-</code>."
    elif field in _TIME_FIELDS:
        extra = "Формат: <code>21:30</code> или <code>21:30:12</code>. Для очистки: <code>-</code>."
    elif field in _TEXT_FIELDS:
        extra = "Отправь текст. Для очистки: <code>-</code>."

    await call.message.answer(
        f"✏️ Введи новое значение:\n<b>{html.escape(hint)}</b>\n\n{extra}",
        parse_mode="HTML",
    )


@router.message(PostStatsEditFSM.waiting_for_value, F.chat.type == "private")
@admin_only
async def edit_field_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    post_id = int(data["post_id"])
    day_iso = data["day_iso"]
    offset = int(data["offset"])
    field = data["field"]

    raw = (message.text or "").strip()
    if raw == "-":
        value = None
    else:
        if field in _TEXT_FIELDS:
            value = raw
        elif field in _DATE_FIELDS:
            value = _parse_date(raw)
            if value is None:
                await message.answer("Неверная дата. Формат: 13.01.2026")
                return
        elif field in _TIME_FIELDS:
            value = _parse_time(raw)
            if value is None:
                await message.answer("Неверное время. Формат: 21:30 или 21:30:12")
                return
        else:
            value = _parse_int_from_text(raw)
            if value is None:
                await message.answer("Не вижу число. Отправь число или '-' чтобы очистить.")
                return
            if value < _BIGINT_MIN or value > _BIGINT_MAX:
                await message.answer("Число слишком большое для BIGINT. Отправь нормальное значение или '-'")
                return

    await _save_field(post_id, field, value, message.from_user.id)
    await state.clear()

    post = await get_post_details(post_id)
    if not post:
        await message.answer("Пост не найден в БД.")
        return

    await message.answer(
        "Сохранено ✅\n\n" + _render_post_detail(post),
        parse_mode="HTML",
        reply_markup=_kb_post_detail(day_iso, offset, post),
        disable_web_page_preview=True,
    )
def _collapse_int_ranges(ids: list[int]) -> str:
    ids = sorted({int(x) for x in (ids or [])})
    if not ids:
        return "—"

    parts: list[str] = []
    start = prev = ids[0]

    for x in ids[1:]:
        if x == prev + 1:
            prev = x
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = x

    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)

def _kb_free_auction_ids(limit: int) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📌 Занять первый свободный", callback_data=f"stats_free_ids:take:{int(limit)}")
    kb.button(text="🔄 Обновить", callback_data=f"stats_free_ids:refresh:{int(limit)}")
    kb.adjust(1, 1)
    return kb.as_markup()

async def _render_free_auction_ids_text(limit: int) -> str:
    from db.legacy import get_missing_auction_ids, count_missing_auction_ids  # локально, чтобы не спорить с импортами

    limit = max(1, min(int(limit or 50), 200))

    missing_cnt = await count_missing_auction_ids()
    ids = await get_missing_auction_ids(limit)

    return (
        "🧩 <b>Свободные ID аукционов (дырки)</b>\n"
        f"Всего дырок: <b>{missing_cnt}</b>\n"
        f"Показано: <b>{len(ids)}</b> (лимит {limit})\n\n"
        f"<code>{html.escape(_collapse_int_ranges(ids))}</code>\n\n"
        "📌 Кнопка «Занять» создаёт в <code>auctions</code> запись-резерв со статусом <b>pending</b>, "
        "чтобы этот ID больше не был свободным."
    )

@router.message(F.text.regexp(r"^/free_auction_ids(\s+\d+)?$"), F.chat.type == "private")
@admin_only
async def cmd_free_auction_ids(message: types.Message):
    m = re.search(r"^/free_auction_ids(?:\s+(\d+))?$", (message.text or "").strip())
    limit = int(m.group(1)) if (m and m.group(1)) else 50

    text = await _render_free_auction_ids_text(limit)
    await message.answer(text, parse_mode="HTML", reply_markup=_kb_free_auction_ids(limit))

@router.callback_query(F.data.startswith("stats_free_ids:refresh:"))
@admin_only
async def cb_free_auction_ids_refresh(call: types.CallbackQuery):
    await call.answer()
    parts = split_callback_data(call.data or "", ":")
    limit = int(parts[-1]) if parts and parts[-1].isdigit() else 50

    text = await _render_free_auction_ids_text(limit)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_kb_free_auction_ids(limit))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("stats_free_ids:take:"))
@admin_only
async def cb_free_auction_ids_take(call: types.CallbackQuery):
    await call.answer()
    from db.legacy import reserve_first_missing_auction_id_for_stats  # локально

    parts = split_callback_data(call.data or "", ":")
    limit = int(parts[-1]) if parts and parts[-1].isdigit() else 200

    new_id = await reserve_first_missing_auction_id_for_stats(
        admin_user_id=int(call.from_user.id),
        admin_username=call.from_user.username,
        scan_limit=limit,
    )

    if not new_id:
        await call.answer("Свободных ID не найдено (или уже заняли).", show_alert=True)
        return

    await call.answer(f"Занят ID: {new_id}", show_alert=True)

    text = await _render_free_auction_ids_text(50)
    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_kb_free_auction_ids(50))
    except TelegramBadRequest:
        pass