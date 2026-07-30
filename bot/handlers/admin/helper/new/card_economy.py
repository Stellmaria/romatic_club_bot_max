from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from html import escape
from math import ceil
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.auction_notify import _kb_equal
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log as _send_admin_log
from bot.handlers.card_subscribe import _decks_keyboard, _presets_manage_keyboard
from config import ADMIN_LOG_CHATS
from db.db import (
    get_card,
    get_deck,
    get_user_id_by_username,
    list_broadcast_targets,
    list_user_card_subs,
    mark_subscription_confirmed,
    mark_unreachable_user,
    norm_obtain_type,
    set_card_obtain,
    set_deck_type,
    unsubscribe_subscription, get_auction_winner, fetchrow, fetch, get_all_decks, is_luxury_user, )
from fsm_states import EconomyFSM, CardSubscribeFSM

# ---------------------------------------------------------------------------
# Router / constants
# ---------------------------------------------------------------------------

router = Router(name="admin_card_economy")

PAGE_SIZE = 20
DEFAULT_VIEW = "spaced"  # 'compact' | 'spaced'
DEFAULT_EMOJI = False

CANCEL_TEXT = "Отменено"  # системный текст для кнопки отмены

CONF_CB_PREFIX = "sc:ok:"
UNSUB_CB_PREFIX = "sc:rm:"
SUBS_CONFIRM_CB = "subsconf"

# ниже запас по длине, чтобы не упираться в 4096 символов Telegram
CHUNK_LIMIT = 3500

SEND_HTML_KW = dict(
    parse_mode="HTML",
    disable_web_page_preview=True,
    protect_content=False,
)

LUXURY_SEND_HTML_KW = dict(
    parse_mode="HTML",
    disable_web_page_preview=True,
    protect_content=True,
)


# ---------------------------------------------------------------------------
# Общие утилиты / лог
# ---------------------------------------------------------------------------


def _mod_ctx(message: types.Message) -> tuple[str, str, Bot | None]:
    """Вернёт (username, user_id, bot) из message."""
    mu = message.from_user
    username_opt = getattr(mu, "username", None)
    mu_name = username_opt if isinstance(username_opt, str) and username_opt else "-"
    mu_id_val = getattr(mu, "id", None)
    mu_id = str(mu_id_val) if isinstance(mu_id_val, int) else "-"
    bot_obj = getattr(message, "bot", None)
    bot = bot_obj if isinstance(bot_obj, Bot) else None
    return mu_name, mu_id, bot


async def _admin_log(bot: Bot, text: str) -> None:
    """Отправит лог через общий хелпер; если он упал, шлёт напрямую в ADMIN_LOG_CHATS."""
    sent = False
    if callable(_send_admin_log):
        try:
            await _send_admin_log(bot, text)
            sent = True
        except TelegramAPIError:
            sent = False
    if sent:
        return

    chats = ADMIN_LOG_CHATS
    if isinstance(chats, int):
        targets = [chats]
    elif isinstance(chats, Iterable) and not isinstance(chats, (str, bytes)):
        targets = [int(x) for x in chats]
    else:
        targets = [int(chats)]

    for chat_id in targets:
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(chat_id, text, parse_mode="HTML")


async def _log_with_ctx(message: types.Message, body: str) -> None:
    mu_name, mu_id, bot = _mod_ctx(message)
    if bot:
        await _admin_log(
            bot,
            f"{body}\nМодератор: @{mu_name} (<code>{mu_id}</code>)",
        )


def _deck_name(deck: dict | None, deck_id: int) -> str:
    return escape(str(deck.get("name", f"#{deck_id}"))) if deck else f"#{deck_id}"


def _card_name(card: dict | None, card_id: int) -> str:
    return (
        escape(str(card.get("card_name", f"#{card_id}")))
        if card
        else f"#{card_id}"
    )


def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        selective=True,
    )


def _pluralize_ru(n: int, forms: tuple[str, str, str]) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 19:
        return forms[2]
    if 2 <= n1 <= 4:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]


def _subs_word(n: int) -> str:
    return _pluralize_ru(n, ("подписка", "подписки", "подписок"))


# ---------------------------------------------------------------------------
# Экономика: корень, клавиатуры, FSM
# ---------------------------------------------------------------------------


def _kb_economy_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Тип колоды", callback_data="economy:decktype"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Получение карты", callback_data="economy:obtain"
                )
            ],
        ]
    )


def _kb_deck_types() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Рулеточная")],
            [KeyboardButton(text="Ресурсная")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _kb_obtain_types() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Алмазы")],
            [KeyboardButton(text="Чай")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        selective=True,
    )


@router.message(F.text == "💰 Экономика")
@admin_only
async def economy_root(message: types.Message) -> None:
    await message.answer("Выберите раздел:", reply_markup=_kb_economy_root())


@router.callback_query(F.data.startswith("economy:"))
@admin_only
async def economy_cb(call: types.CallbackQuery, state: FSMContext) -> None:
    data = call.data or ""
    parts = data.split(":", 1)
    if len(parts) < 2 or call.message is None:
        await call.answer()
        return

    action = parts[1]
    if action == "decktype":
        await state.set_state(EconomyFSM.deck_id)
        await call.message.answer(
            "Введите ID колоды:", reply_markup=_cancel_kb()
        )
    elif action == "obtain":
        await state.set_state(EconomyFSM.obtain_card_id)
        await call.message.answer(
            "Введите card_id карты для настройки «Получения»:",
            reply_markup=_cancel_kb(),
        )
    await call.answer()


# ------------------ /decktype ------------------


@router.message(Command("decktype"))
@admin_only
async def cmd_decktype(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await economy_root(message)
        return

    _, did, dtype = parts
    try:
        deck_id = int(did)
    except ValueError:
        await message.answer("deck_id должен быть целым числом.")
        return

    deck = await get_deck(deck_id)
    if not isinstance(deck, dict):
        await message.answer("Такой колоды нет.")
        return

    try:
        before, after = await set_deck_type(deck_id, dtype)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    deck_name = _deck_name(deck, deck_id)
    await message.answer(
        f"✅ Тип колоды обновлён: <b>{deck_name}</b>\n"
        f"{before or '-'} → <b>{after}</b>",
        **SEND_HTML_KW,
    )

    await _log_with_ctx(
        message,
        "<b>⚙️ Тип колоды</b>\n"
        f"ID: {deck_id} {deck_name}\n"
        f"{before or '-'} → <b>{after}</b>",
    )


@router.message(EconomyFSM.deck_id, F.text)
@admin_only
async def fsm_deck_id(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    try:
        deck_id = int(text)
    except ValueError:
        await message.answer("Нужен целый ID колоды.")
        return

    deck = await get_deck(deck_id)
    if not deck:
        await message.answer("Такой колоды нет.")
        return

    await state.update_data(deck_id=deck_id)
    await message.answer(
        "Выберите тип колоды:", reply_markup=_kb_deck_types()
    )
    await state.set_state(EconomyFSM.deck_type)


@router.message(EconomyFSM.deck_type, F.text)
@admin_only
async def fsm_deck_type(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    data = await state.get_data()
    deck_id = int(data["deck_id"])

    try:
        before, after = await set_deck_type(deck_id, text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    deck = await get_deck(deck_id)
    deck_name = _deck_name(deck, deck_id)

    await message.answer(
        f"✅ Тип колоды обновлён: <b>{deck_name}</b>\n"
        f"{before or '-'} → <b>{after}</b>",
        reply_markup=ReplyKeyboardRemove(),
        **SEND_HTML_KW,
    )

    await _log_with_ctx(
        message,
        "<b>⚙️ Тип колоды</b>\n"
        f"ID: {deck_id} {deck_name}\n"
        f"{before or '-'} → <b>{after}</b>",
    )
    await state.clear()


# ------------------ /obtain ------------------


@router.message(Command("obtain"))
@admin_only
async def cmd_obtain(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=3)
    if len(parts) < 4:
        await economy_root(message)
        return

    _, cid, t, amt = parts[:4]
    try:
        card_id = int(cid)
        amount = int(amt)
    except ValueError:
        await message.answer("card_id и amount должны быть целыми числами.")
        return

    await _apply_obtain(message, card_id, t, amount)


@router.message(EconomyFSM.obtain_card_id, F.text)
@admin_only
async def fsm_obtain_card_id(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    try:
        card_id = int(text)
    except ValueError:
        await message.answer("Нужен целый card_id.")
        return

    card = await get_card(card_id)
    if not card:
        await message.answer("Такой карты нет.")
        return

    await state.update_data(card_id=card_id)
    await message.answer(
        "Выберите тип получения:", reply_markup=_kb_obtain_types()
    )
    await state.set_state(EconomyFSM.obtain_type)


@router.message(EconomyFSM.obtain_type, F.text)
@admin_only
async def fsm_obtain_type(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.lower() == "отмена":
        await state.clear()
        await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())
        return

    obtain = norm_obtain_type(text)
    if obtain not in {"diamonds", "tea"}:
        await message.answer(
            "Тип должен быть: Алмазы или Чай (diamonds|tea)."
        )
        return

    await state.update_data(obtain_type=obtain)
    await message.answer(
        "Введите количество (целое):", reply_markup=_cancel_kb()
    )
    await state.set_state(EconomyFSM.obtain_amount)


@router.message(EconomyFSM.obtain_amount, F.text.regexp(r"^\d+$"))
@admin_only
async def fsm_obtain_amount(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if not text:
        await message.answer("Нужно целое число.")
        return
    amount = int(text)

    data = await state.get_data()
    raw_id = data.get("card_id") or data.get("obtain_card_id")
    if raw_id is None or not isinstance(raw_id, (str, int)):
        await message.answer("card_id не найден или имеет неверный формат.")
        return
    try:
        card_id = int(raw_id)
    except (TypeError, ValueError):
        await message.answer("card_id имеет неверный формат.")
        return

    t_val = data.get("obtain_type")
    obtain_type = t_val if isinstance(t_val, str) else ""
    if not obtain_type:
        await message.answer("Тип получения не найден.")
        return

    await _apply_obtain(message, card_id, obtain_type, amount)
    await state.clear()


async def _apply_obtain(
        message: types.Message,
        card_id: int,
        obtain_type: str,
        amount: int,
) -> None:
    card = await get_card(card_id)
    if not isinstance(card, dict):
        await message.answer("Такой карты нет.")
        return

    try:
        before, after = await set_card_obtain(card_id, obtain_type, amount)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return

    name = _card_name(card, card_id)
    await message.answer(
        "✅ Получение карты обновлено: "
        f"<b>{name}</b>\n"
        f"type {before[0]} → <b>{after[0]}</b>\n"
        f"amount {before[1]} → <b>{after[1]}</b>",
        **SEND_HTML_KW,
    )

    await _log_with_ctx(
        message,
        "<b>🛒 Получение карты</b>\n"
        f"Card #{card_id} {name}\n"
        f"type {before[0]} → <b>{after[0]}</b>; "
        f"amount {before[1]} → <b>{after[1]}</b>",
    )


# ---------------------------------------------------------------------------
# Топ подписок (просмотр, пагинация)
# ---------------------------------------------------------------------------


_RARITY_LABEL = {
    "bronze": "бронза",
    "silver": "серебро",
    "gold": "золото",
    "diamond": "алмаз",
}

_RARITY_EMOJI = {
    "bronze": "🟫",
    "silver": "🥈",
    "gold": "🥇",
    "diamond": "💎",
}

_GIFT_EMOJI = {
    "diamonds": "💎",
    "diamond": "💎",
    "cups": "🍵",
    "tea": "🍵",
    "treasures": "🪙",
    "tgstars": "⭐",
}

DEFAULT_RARITY_TOKEN = "a"  # all

_RARITY_TOKEN_TO_VALUE = {
    "a": "all",
    "b": "bronze",
    "s": "silver",
    "g": "gold",
    "d": "diamond",
}

_RARITY_VALUE_TO_TOKEN = {v: k for k, v in _RARITY_TOKEN_TO_VALUE.items()}

_RARITY_BUTTON_TEXT = {
    "b": "🟫 Бронза",
    "s": "🥈 Серебро",
    "g": "🥇 Золото",
    "d": "💎 Алмаз",
    "a": "🌈 Все",
}


def _rarity_token_norm(token: str | None) -> str:
    t = (token or "").strip().lower()
    return t if t in _RARITY_TOKEN_TO_VALUE else DEFAULT_RARITY_TOKEN


def _rarity_value_norm(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in {"all", "a"}:
        return "all"
    if v in {"bronze", "silver", "gold", "diamond"}:
        return v
    # иногда люди пишут “бронза/серебро”… ну ладно
    ru_map = {"бронза": "bronze", "серебро": "silver", "золото": "gold", "алмаз": "diamond"}
    return ru_map.get(v, "all")


def _rarity_token_to_value(token: str | None) -> str:
    return _RARITY_TOKEN_TO_VALUE[_rarity_token_norm(token)]


def _rarity_value_to_token(value: str | None) -> str:
    v = _rarity_value_norm(value)
    return _RARITY_VALUE_TO_TOKEN.get(v, DEFAULT_RARITY_TOKEN)


def _rarity_title(value: str) -> str:
    v = _rarity_value_norm(value)
    if v == "all":
        return "все редкости"
    return _RARITY_LABEL.get(v, v)


async def _get_lux_top_rows(
        *,
        limit: int,
        offset: int,
        rarity: str,  # "all" | bronze | silver | gold | diamond
) -> tuple[list[dict], int]:
    """
    Возвращает (rows, total) для /lux_top c учётом rarity.
    Считаем по ТЕКУЩИМ подпискам user_subscriptions.
    """
    rarity = _rarity_value_norm(rarity)
    rarity_param = None if rarity == "all" else rarity

    # total = сколько разных карт вообще попадает в топ с учётом редкости
    total_row = await fetchrow(
        """
        WITH subs AS (SELECT us.card_id
                      FROM public.user_subscriptions us
                      WHERE us.card_id IS NOT NULL
                      GROUP BY us.card_id)
        SELECT COUNT(*)::int AS total
        FROM subs s
                 JOIN public.cards c ON c.card_id = s.card_id
        WHERE ($1::text IS NULL OR c.rarity = $1::text)
        """,
        rarity_param,
    )
    total = int((dict(total_row).get("total") if total_row else 0) or 0)

    # rows = топ по количеству подписчиков
    rows = await fetch(
        """
        WITH subs AS (SELECT us.card_id, COUNT(DISTINCT us.user_id) AS subs_count
                      FROM public.user_subscriptions us
                      WHERE us.card_id IS NOT NULL
                      GROUP BY us.card_id),
             sched AS (SELECT LOWER(a.card_name) AS cn,
                              LOWER(a.hero_name) AS hn,
                              COUNT(*)           AS scheduled_count
                       FROM public.auctions a
                       WHERE a.status IN ('scheduled', 'active', 'approved')
                       GROUP BY LOWER(a.card_name), LOWER(a.hero_name))
        SELECT c.card_id,
               c.card_name,
               c.hero_name,
               c.deck_id,
               c.rarity,
               c.obtain_type,
               c.obtain_amount,
               s.subs_count,
               COALESCE(sc.scheduled_count, 0) AS scheduled_count
        FROM subs s
                 JOIN public.cards c ON c.card_id = s.card_id
                 LEFT JOIN sched sc
                           ON sc.cn = LOWER(c.card_name)
                               AND sc.hn = LOWER(c.hero_name)
        WHERE ($3::text IS NULL OR c.rarity = $3::text)
        ORDER BY s.subs_count DESC, c.card_name ASC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
        rarity_param,
    )

    return [dict(r) for r in (rows or [])], total


def _rarity_pretty(rarity: str | None, emoji: bool) -> str:
    r = (rarity or "").strip().lower()
    label = _RARITY_LABEL.get(r, rarity or "—")
    if emoji:
        return f"{_RARITY_EMOJI.get(r, '💠')} {label}"
    return f"{label}"


def _gift_pretty(obtain_type: str | None, obtain_amount: int | None, emoji: bool) -> str:
    t_raw = (obtain_type or "").strip()
    amt = int(obtain_amount or 0)

    if not t_raw or amt <= 0:
        return ("🎁 —" if emoji else "—")

    # нормализуем тип, если функция есть (она у тебя импортится)
    try:
        t = norm_obtain_type(t_raw) or t_raw
    except Exception:
        t = t_raw

    t_l = t.strip().lower()
    unit = _NOM.get(t_l, t_l)  # _NOM у тебя уже есть в файле
    if emoji:
        cur_emoji = _GIFT_EMOJI.get(t_l, "🎁")
        return f"🎁 +{amt} {cur_emoji} {unit}"
    return f"+{amt} {unit}"


def _format_line_compact(i: int, row: dict, emoji: bool) -> str:
    name = escape(str(row.get("card_name") or "-").strip())
    hero = escape(str(row.get("hero_name") or "-").strip())
    subs = int(row.get("subs_count") or 0)
    sched = int(row.get("scheduled_count") or 0)
    deck_id = row.get("deck_id")

    cal = "🗓 " if emoji else ""
    book = "📚 " if emoji else ""
    deck_txt = f"№{deck_id}" if deck_id is not None else "—"

    rarity_txt = _rarity_pretty(row.get("rarity"), emoji)
    gift_txt = _gift_pretty(row.get("obtain_type"), row.get("obtain_amount"), emoji)

    return (
        f"{i}. <b>{name} — {hero}</b> · "
        f"<b>{subs}</b> {_subs_word(subs)} · "
        f"{cal}запланировано: {sched} · {book}колода {deck_txt} · "
        f"редк.: {rarity_txt} · 🎁 {gift_txt}"
    )


def _format_line_spaced(i: int, row: dict, emoji: bool) -> str:
    name = escape(str(row.get("card_name") or "-").strip())
    hero = escape(str(row.get("hero_name") or "-").strip())
    subs = int(row.get("subs_count") or 0)
    sched = int(row.get("scheduled_count") or 0)
    deck_id = row.get("deck_id")

    cal = "🗓 " if emoji else ""
    book = "📚 " if emoji else ""
    deck_txt = f"№{deck_id}" if deck_id is not None else "—"

    rarity_txt = _rarity_pretty(row.get("rarity"), emoji)
    gift_txt = _gift_pretty(row.get("obtain_type"), row.get("obtain_amount"), emoji)

    line1 = f"{i}. <b>{name} — {hero}</b>"
    line2 = f"   <b>{subs}</b> {_subs_word(subs)} · {cal}{sched} · {book}{deck_txt}"
    line3 = f"   редк.: {rarity_txt} · 🎁 {gift_txt}"
    return f"{line1}\n{line2}\n{line3}"


def _kb_top_nav(
        page: int,
        total: int,
        view: str,
        emoji: bool,
        rtoken: str,
) -> InlineKeyboardMarkup:
    pages = max(1, ceil(total / PAGE_SIZE))
    page = max(1, min(page, pages))
    prev_page = max(1, page - 1)
    next_page = min(pages, page + 1)

    vtoken = "c" if view == "compact" else "s"
    etoken = "1" if emoji else "0"
    rtoken = _rarity_token_norm(rtoken)

    def cd(p: int | None = None, v: str | None = None, e: str | None = None, r: str | None = None) -> str:
        return f"lt:p{p or page}:v{v or vtoken}:e{e or etoken}:r{r or rtoken}"

    rows: list[list[InlineKeyboardButton]] = []

    # ---- навигация ----
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀", callback_data=cd(prev_page)))
    nav.append(InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=cd()))
    if page < pages:
        nav.append(InlineKeyboardButton(text="▶", callback_data=cd(next_page)))
    rows.append(nav)

    # ---- настройки вида/эмодзи ----
    view_txt = "Вид: раздельный" if vtoken == "s" else "Вид: компактный"
    toggle_view = "c" if vtoken == "s" else "s"
    rows.append(
        [
            InlineKeyboardButton(text=view_txt, callback_data=cd(v=toggle_view)),
            InlineKeyboardButton(
                text=f"Эмодзи: {'вкл' if etoken == '1' else 'выкл'}",
                callback_data=cd(e=("0" if etoken == "1" else "1")),
            ),
        ]
    )

    # ---- фильтр редкости ----
    def rbtn(tok: str) -> InlineKeyboardButton:
        tok = _rarity_token_norm(tok)
        base = _RARITY_BUTTON_TEXT.get(tok, tok)
        text = f"✅ {base}" if tok == rtoken else base
        # при смене фильтра логично сбросить страницу на 1
        return InlineKeyboardButton(text=text, callback_data=cd(p=1, r=tok))

    rows.append([rbtn("b"), rbtn("s"), rbtn("g")])
    rows.append([rbtn("d"), rbtn("a")])

    # ---- закрыть ----
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="lt:close")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_page(
        page: int,
        total: int,
        rows: list[dict],
        view: str,
        emoji: bool,
        rarity: str,  # "all" | bronze | ...
) -> str:
    rarity = _rarity_value_norm(rarity)
    header = f"👑 Топ ожидаемых карт · лакшери · {_rarity_title(rarity)}"

    start_pos = (page - 1) * PAGE_SIZE + 1
    fmt = _format_line_spaced if view == "spaced" else _format_line_compact
    body = "\n".join(fmt(start_pos + idx, r, emoji) for idx, r in enumerate(rows)) or "Пусто."
    return f"{header}\n{body}"


async def _safe_edit(
        call: types.CallbackQuery,
        text: str,
        kb: InlineKeyboardMarkup,
) -> None:
    msg = call.message
    if msg is None:
        await call.answer()
        return

    current_text = msg.html_text or msg.text or ""
    current_kb = msg.reply_markup

    def _same_kb(a, b) -> bool:
        try:
            if a is b:
                return True
            if a is None or b is None:
                return False
            return a.model_dump(exclude_none=True) == b.model_dump(
                exclude_none=True
            )
        except Exception:
            return str(a) == str(b)

    same_text = current_text == text
    same_markup = _same_kb(current_kb, kb)

    if same_text and same_markup:
        await call.answer()
        return

    try:
        if same_text and not same_markup:
            await msg.edit_reply_markup(reply_markup=kb)
        else:
            await msg.edit_text(text, reply_markup=kb, **SEND_HTML_KW)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            await call.answer()
        else:
            raise


async def _render_page(
        message_or_call: types.Message | types.CallbackQuery,
        page: int,
        view: str,
        emoji: bool,
        rtoken: str,
        edit: bool = False,
) -> None:
    page = max(1, page)
    view = view if view in {"compact", "spaced"} else DEFAULT_VIEW
    rtoken = _rarity_token_norm(rtoken)
    rarity = _rarity_token_to_value(rtoken)

    offset = (page - 1) * PAGE_SIZE
    rows, total = await _get_lux_top_rows(limit=PAGE_SIZE, offset=offset, rarity=rarity)

    text = _format_page(page, total, rows, view, emoji, rarity)
    kb = _kb_top_nav(page, total, view, emoji, rtoken)

    msg = (
        message_or_call.message
        if isinstance(message_or_call, types.CallbackQuery)
        else message_or_call
    )

    if edit and isinstance(message_or_call, types.CallbackQuery) and msg:
        await _safe_edit(message_or_call, text, kb)
        return

    await msg.answer(text, reply_markup=kb, **LUXURY_SEND_HTML_KW)


@router.message(Command("lux_top"), F.chat.type == "private")
async def cmd_lux_top(message: types.Message) -> None:
    if not await is_luxury_user(message.from_user.id):
        await message.answer("Эта функция доступна только для Лакшери-пользователей.")
        return
    # формат:
    # /lux_top
    # /lux_top 2
    # /lux_top 2 spaced on bronze
    parts = (message.text or "").split()

    page = 1
    view = DEFAULT_VIEW
    emoji = DEFAULT_EMOJI
    rarity = "all"

    if len(parts) >= 2:
        try:
            page = int(parts[1])
        except ValueError:
            page = 1

    if len(parts) >= 3:
        v = parts[2].lower().strip()
        if v in {"compact", "spaced"}:
            view = v

    if len(parts) >= 4:
        e = parts[3].lower().strip()
        emoji = e in {"on", "1", "true", "да", "yes"}

    if len(parts) >= 5:
        rarity = parts[4].lower().strip()

    rtoken = _rarity_value_to_token(rarity)
    await _render_page(message, page, view, emoji, rtoken, edit=False)


@router.callback_query(F.data.startswith("lt:"))
async def lux_top_pager(call: types.CallbackQuery) -> None:
    data = call.data or ""
    if data == "lt:close":
        if call.message:
            try:
                await call.message.delete()
            except Exception:
                await call.answer("Закрыто")
        return

    page, view, emoji = 1, DEFAULT_VIEW, DEFAULT_EMOJI
    rtoken = DEFAULT_RARITY_TOKEN

    try:
        for token in data.split(":"):
            if token.startswith("p"):
                page = int(token[1:] or "1")
            elif token.startswith("v"):
                view = "compact" if token[1:] == "c" else "spaced"
            elif token.startswith("e"):
                emoji = token[1:] == "1"
            elif token.startswith("r"):
                rtoken = _rarity_token_norm(token[1:] or DEFAULT_RARITY_TOKEN)
    except Exception:
        page, view, emoji, rtoken = 1, DEFAULT_VIEW, DEFAULT_EMOJI, DEFAULT_RARITY_TOKEN

    await _render_page(call, page, view, emoji, rtoken, edit=True)


# ---------------------------------------------------------------------------
# Подтверждение подписок: билдер текста, клавиатуры, рассылка, колбэки
# ---------------------------------------------------------------------------


def _build_text(subs: list[dict]) -> str:
    """
    Строит читаемый список подписок пользователя + краткие инструкции.

    Ожидается структура элемента:
    {
        "sub_id": int,
        "card_name": str,
        "hero_name": str | None,
        "deck_id": int | None,
        "last_confirmed_at": datetime | None
    }
    """
    total = len(subs)
    lines: list[str] = []
    lines.append("🔔 <b>Ваши активные подписки</b>")
    lines.append(f"Всего: <b>{total}</b> {_subs_word(total)}")
    lines.append("")
    lines.append(
        "Нажмите на название, чтобы отметить подписку подтверждённой, "
        "или на «Отписаться», если она больше не нужна."
    )
    lines.append("Если сообщений будет много, пришлю их частями.")
    lines.append("")

    for i, s in enumerate(subs, 1):
        name = escape(str(s.get("card_name") or "-").strip())
        hero = escape(str(s.get("hero_name") or "").strip())
        deck_id = s.get("deck_id")
        deck_txt = f"№{deck_id}" if deck_id is not None else "—"
        ok_mark = " ✅" if s.get("last_confirmed_at") else ""
        title = f"{name} — {hero}" if hero else name
        lines.append(f"{i}. <b>{title}</b> · 📚 колода {deck_txt}{ok_mark}")

    lines.append("")
    lines.append("Готово. Проверьте список и обновите то, что нужно.")
    return "\n".join(lines)


def _build_keyboard(subs: list[dict]) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения: один ряд = одна подписка.
    Любые элементы без sub_id молча пропускаем.
    """
    rows = []
    for s in subs or []:
        sid = s.get("sub_id")
        if sid is None:
            continue
        hero = s.get("hero_name") or "—"
        card = s.get("card_name") or "—"
        text = f"✅ {hero} — {card}"[:64]  # чтобы не раздувать кнопку
        rows.append([InlineKeyboardButton(text=text, callback_data=f"subs:confirm:{sid}")])

    # запасные кнопки, если надо
    rows.append([InlineKeyboardButton(text="Подтвердить всё", callback_data="subs:confirm_all")])
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="subs:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_text() -> str:
    return (
        "<b>Нужно обновить подписки</b>\n\n"
        "Мы чистим и актуализируем ваши подписки на карты.\n"
        "Если хотите подтвердить актуальность и обновить настройки, "
        "нажмите кнопку ниже. Тогда пришлю ваш список подписок "
        "и клавиатуру для быстрого редактирования.\n\n"
        "Если подписок много, сообщения придут частями."
    )


def _build_confirm_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, показать список",
                    callback_data=f"{SUBS_CONFIRM_CB}:yes:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🙅‍♀️ Нет, оставить как есть",
                    callback_data=f"{SUBS_CONFIRM_CB}:no:{user_id}",
                )
            ],
        ]
    )


def _split_with_parts(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Режем длинный текст на части и маркируем «Часть i/n»."""
    chunks = [text[i: i + limit] for i in range(0, len(text), limit)] or [text]
    n = len(chunks)
    if n == 1:
        return chunks
    labeled: list[str] = []
    for i, c in enumerate(chunks, 1):
        labeled.append(f"<b>Часть {i}/{n}</b>\n\n{c}")
    return labeled


def _build_no_subs_text() -> str:
    return (
        "🔔 <b>У вас пока нет подписок</b>\n\n"
        "Нажмите кнопку ниже, чтобы выбрать карты и включить уведомления.\n"
        "В любой момент эту кнопку можно отправить вручную командой /subscribe_card."
    )


def _start_subscribe_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Начать подписку", callback_data="sub:open")]
    ])


def _start_subscribe_kb() -> ReplyKeyboardMarkup:
    # компактная одноразовая клавиатура, чтобы пользователь просто ткнул и отправил команду
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/subscribe_card")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter, TelegramForbiddenError


@router.message(Command("subs_confirm_broadcast"))
@admin_only
async def subs_confirm_broadcast(message: types.Message) -> None:
    bot = message.bot
    parts = (message.text or "").split(maxsplit=1)
    target_uid: int | None = None

    await message.answer("Стартую рассылку-подтверждение/приглашение.")

    # Опциональный таргет @user или id
    if len(parts) > 1:
        token = parts[1].strip()
        try:
            target_uid = int(token)
        except ValueError:
            handle = token if token.startswith("@") else f"@{token}"
            try:
                chat = await bot.get_chat(handle)
                if getattr(chat, "type", "private") == "private":
                    target_uid = int(chat.id)
            except TelegramAPIError:
                target_uid = None
            if target_uid is None:
                target_uid = await get_user_id_by_username(token)
        if target_uid is None:
            await message.answer(f"Не нашёл пользователя по «{token}». Пропускаю.")
            return

    targets = [target_uid] if target_uid is not None else await list_broadcast_targets()

    sent_confirms = sent_prompts = 0
    skipped_forbidden = skipped_bad_request = skipped_other = 0

    for uid in targets:
        try:
            subs = await list_user_card_subs(uid)
            subs = _normalize_sub_rows(subs)

            if subs:
                await bot.send_message(
                    uid,
                    _build_confirm_text(),
                    reply_markup=_build_confirm_kb(uid),
                    **SEND_HTML_KW,
                )
                sent_confirms += 1
            else:
                await bot.send_message(
                    uid,
                    _build_no_subs_text(),
                    reply_markup=_start_subscribe_inline_kb(),
                    **SEND_HTML_KW,
                )
                sent_prompts += 1

            await asyncio.sleep(0.05)

        except TelegramRetryAfter as e:
            await asyncio.sleep(getattr(e, "retry_after", 1.0))
        except TelegramForbiddenError:
            skipped_forbidden += 1
            with contextlib.suppress(Exception):
                await mark_unreachable_user(uid, "forbidden")
        except TelegramBadRequest:
            skipped_bad_request += 1
        except TelegramAPIError as e:
            skipped_other += 1
            with contextlib.suppress(Exception):
                await mark_unreachable_user(uid, f"api:{type(e).__name__}")
        except Exception:
            skipped_other += 1

    total_skipped = skipped_forbidden + skipped_bad_request + skipped_other
    await message.answer(
        "Готово. "
        f"Запросов подтверждения: {sent_confirms}, "
        f"приглашений: {sent_prompts}, "
        f"пропущено: {total_skipped} "
        f"(403: {skipped_forbidden}, 400: {skipped_bad_request}, прочее: {skipped_other})."
    )


async def _safe_edit_msg(
        msg: types.Message,
        text: str,
        kb: Optional[InlineKeyboardMarkup] = None,
        parse_mode: Optional[str] = None,
) -> None:
    """
    Пытается отредактировать исходное сообщение.
    Если редактировать нельзя/нечего — отправляет новое.
    """
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except TelegramBadRequest:
        # случаи: "message is not modified", "message can't be edited", "not enough rights"
        await msg.answer(text, reply_markup=kb, parse_mode=parse_mode)


def _normalize_sub_rows(rows) -> list[dict]:
    """
    Приводит записи подписок к единому виду.
    Принимает любые словари / Record'ы, вытаскивает sub_id из известных названий.
    Отбрасывает мусор без айди.
    """
    normalized: list[dict] = []
    for r in rows or []:
        d = dict(r)
        sid = d.get("sub_id") or d.get("id") or d.get("subscription_id")
        if sid is None:
            continue
        d["sub_id"] = int(sid)
        # подстрахуем ключи для названия карточки
        d.setdefault("hero_name", d.get("hero") or d.get("hero_title"))
        d.setdefault("card_name", d.get("card") or d.get("card_title") or d.get("title"))
        normalized.append(d)
    return normalized


@router.callback_query(CardSubscribeFSM.waiting_for_deck, F.data.in_({"sub:presets_open", "sub:preset:any_card"}))
async def open_presets_manager_from_decks(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(presets_back="decks")
    kb = await _presets_manage_keyboard(call.from_user.id, back="decks")
    await _safe_edit_msg(call.message, "Пресеты уведомлений по расписанию:", kb)
    await call.answer()


async def open_subscribe_from_broadcast(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    decks = await get_all_decks()
    if not decks:
        await call.message.answer("Пока нет доступных колод.")
        await call.answer()
        return
    await call.message.answer("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
    await state.set_state(CardSubscribeFSM.waiting_for_deck)
    await call.answer()


router.callback_query.register(open_subscribe_from_broadcast, F.data == "sub:open")

# почини несовпадение ключа пресетов; оставим оба алиаса
router.callback_query.register(
    open_presets_manager_from_decks,
    CardSubscribeFSM.waiting_for_deck,
    F.data.in_({"sub:presets_open", "sub:preset:any_card"}),
)


async def open_subscribe_from_button(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    decks = await get_all_decks()
    if not decks:
        await call.message.answer("Пока нет доступных колод.")
        await call.answer()
        return
    await call.message.answer("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
    await state.set_state(CardSubscribeFSM.waiting_for_deck)
    await call.answer()


@router.callback_query(F.data.startswith(f"{SUBS_CONFIRM_CB}:"))
async def subs_confirm_callback(call: types.CallbackQuery) -> None:
    """Обрабатывает подтверждение от пользователя."""
    bot = call.message.bot
    try:
        _, action, sid = call.data.split(":")
        uid = int(sid)
    except Exception:
        await call.answer("Неверные данные.", show_alert=False)
        return

    # Немного безопасности: реагируем только если нажимает сам пользователь
    if call.from_user and call.from_user.id != uid:
        await call.answer("Это не для вас.", show_alert=False)
        return

    if action == "no":
        try:
            await call.message.edit_text(
                "Окей, ничего не меняем. Если передумаете — "
                "зайдите в профиль и обновите подписки."
            )
        except TelegramAPIError:
            await call.message.answer(
                "Окей, ничего не меняем. Если передумаете — "
                "зайдите в профиль и обновите подписки."
            )
        return

    if action != "yes":
        await call.answer("Неизвестное действие.", show_alert=False)
        return

    # 'yes' — достаём актуальные подписки и шлём списком
    try:
        subs = await list_user_card_subs(uid)
        subs = _normalize_sub_rows(subs)
    except Exception:
        subs = []

    if not subs:
        try:
            await call.message.edit_text(
                "Подписок не найдено. Добавьте сначала хотя бы одну."
            )
        except TelegramAPIError:
            await call.message.answer(
                "Подписок не найдено. Добавьте сначала хотя бы одну."
            )
        return

    try:
        await call.message.edit_text("Окей, присылаю ваш список подписок…")
    except TelegramAPIError:
        pass

    full_text = _build_text(subs)
    parts = _split_with_parts(full_text, limit=CHUNK_LIMIT)

    # отправляем частями; клавиатура кладётся в последний блок
    for i, chunk in enumerate(parts, 1):
        markup = _build_keyboard(subs) if i == len(parts) else None
        try:
            await bot.send_message(uid, chunk, reply_markup=markup, **SEND_HTML_KW)
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(getattr(e, "retry_after", 1.0))
            try:
                await bot.send_message(
                    uid, chunk, reply_markup=markup, **SEND_HTML_KW
                )
            except TelegramAPIError:
                break
        except TelegramAPIError:
            break


@router.message(Command("subs_confirm_test"))
@admin_only
async def subs_confirm_test(message: types.Message) -> None:
    """Тестовая отправка списка и клавиатуры одному пользователю."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи @username или user_id. Пример: "
            "/subs_confirm_test @aam_cheshire"
        )
        return

    token = parts[1].strip()
    bot = message.bot

    await message.answer(
        "Стартую рассылку подтверждений (только указанному пользователю)."
    )

    target_uid: int | None = None
    try:
        target_uid = int(token)
    except ValueError:
        handle = token if token.startswith("@") else f"@{token}"
        try:
            chat = await bot.get_chat(handle)
            if getattr(chat, "type", "private") == "private":
                target_uid = int(chat.id)
        except TelegramAPIError:
            target_uid = None
        if target_uid is None:
            target_uid = await get_user_id_by_username(token)

    if target_uid is None:
        await message.answer(f"Не нашёл пользователя по «{token}». Пропускаю.")
        return

    sent, skipped = 0, 0
    try:
        subs = await list_user_card_subs(target_uid)
        if not subs:
            skipped += 1
        else:
            await bot.send_message(
                target_uid,
                _build_text(subs),
                reply_markup=_build_keyboard(subs),
                **SEND_HTML_KW,
            )
            sent += 1
            await asyncio.sleep(0.06)
    except TelegramRetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1.0))
    except TelegramAPIError as e:
        await mark_unreachable_user(target_uid, str(e))
        skipped += 1

    await message.answer(f"Готово. Отправлено: {sent}, пропущено: {skipped}.")


# ---------------------------------------------------------------------------
# Колбэки подтверждения/отписки (клавиатура списка подписок)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "sc:close")
async def sc_close(call: types.CallbackQuery) -> None:
    try:
        if call.message:
            await call.message.delete()
    finally:
        await call.answer()


# --- ЗАМЕНИ хендлер подтверждения на этот ---

@router.callback_query(F.data.startswith(CONF_CB_PREFIX))
async def sc_confirm(call: types.CallbackQuery) -> None:
    data = call.data or ""
    try:
        sub_id = int(data.split(":", 2)[-1])
    except Exception:
        await call.answer("Ошибка данных", show_alert=True)
        return

    ok = await mark_subscription_confirmed(sub_id, call.from_user.id)
    if not ok:
        await call.answer("Подписка не найдена", show_alert=True)
        return

    # Текущая и новая клавиатуры
    old_kb = call.message.reply_markup
    new_rows: list[list[InlineKeyboardButton]] = []
    target_cd = f"{CONF_CB_PREFIX}{sub_id}"
    changed = False

    if old_kb and old_kb.inline_keyboard:
        for row in old_kb.inline_keyboard:
            new_row: list[InlineKeyboardButton] = []
            for btn in row:
                if getattr(btn, "callback_data", None) == target_cd:
                    text = btn.text or ""
                    if not text.startswith("✅"):
                        text = f"✅ {text}"
                        changed = True
                    new_row.append(
                        InlineKeyboardButton(text=text, callback_data=btn.callback_data)
                    )
                else:
                    new_row.append(btn)
            new_rows.append(new_row)
    else:
        # На всякий: если клавиатуры не было, нечего редактировать
        await call.answer("Отмечено")
        return

    new_kb = InlineKeyboardMarkup(inline_keyboard=new_rows)

    # Если итог совпадает с тем, что уже стоит — не редактируем
    if not changed or _kb_equal(old_kb, new_kb):
        await call.answer("Уже отмечено")
        return

    # Безопасное редактирование (не свалимся на 'message is not modified')
    current_text = call.message.html_text or call.message.text or ""
    await _safe_edit(call, current_text, new_kb)
    await call.answer("Отмечено")


@router.message(Command("id"))
async def cmd_id(message: types.Message):
    tgt = message.reply_to_message or message

    if tgt.photo:
        p = tgt.photo[-1]
        text = (
            f"file_id: <code>{escape(p.file_id)}</code>\n"
            f"file_unique_id: <code>{escape(p.file_unique_id)}</code>\n"
            f"size: {p.width}x{p.height}"
        )
        await message.answer(text, parse_mode="HTML")
        return

    if tgt.document and str(tgt.document.mime_type or "").startswith("image/"):
        d = tgt.document
        text = (
            f"file_id: <code>{escape(d.file_id)}</code>\n"
            f"file_unique_id: <code>{escape(d.file_unique_id)}</code>\n"
            f"name: <code>{escape(d.file_name or '')}</code>"
        )
        await message.answer(text, parse_mode="HTML")
        return

    if tgt.sticker:
        s = tgt.sticker
        text = (
            f"sticker file_id: <code>{escape(s.file_id)}</code>\n"
            f"unique: <code>{escape(s.file_unique_id)}</code>"
        )
        await message.answer(text, parse_mode="HTML")
        return

    await message.answer("Пришли фото или ответь командой на сообщение с фото. Документы image/* тоже ок.")


# --- ЗАМЕНИ хендлер отписки на этот ---

@router.callback_query(F.data.startswith(UNSUB_CB_PREFIX))
async def sc_unsubscribe(call: types.CallbackQuery) -> None:
    data = call.data or ""
    try:
        sub_id = int(data.split(":", 2)[-1])
    except Exception:
        await call.answer("Ошибка данных", show_alert=True)
        return

    ok = await unsubscribe_subscription(sub_id, call.from_user.id)
    if not ok:
        await call.answer("Уже отписан или подписка не найдена", show_alert=True)
        return

    old_kb = call.message.reply_markup
    if not (old_kb and old_kb.inline_keyboard):
        await call.answer("Отписано")
        return

    target_ok = f"{CONF_CB_PREFIX}{sub_id}"
    target_rm = f"{UNSUB_CB_PREFIX}{sub_id}"

    new_rows: list[list[InlineKeyboardButton]] = []
    for row in old_kb.inline_keyboard:
        cds = [getattr(btn, "callback_data", None) for btn in row]
        if target_ok in cds or target_rm in cds:
            # выкидываем строку с этой подпиской
            continue
        new_rows.append(row)

    # Если всё выпилили — покажем заглушку + Закрыть
    rows_wo_close = [
        r for r in new_rows
        if not (len(r) == 1 and getattr(r[0], "callback_data", "") == "sc:close")
    ]
    if not rows_wo_close:
        new_rows = [
            [InlineKeyboardButton(text="Нет активных подписок", callback_data="noop")],
            [InlineKeyboardButton(text="Закрыть", callback_data="sc:close")],
        ]
    else:
        if not any(
                len(r) == 1 and getattr(r[0], "callback_data", "") == "sc:close"
                for r in new_rows
        ):
            new_rows.append([InlineKeyboardButton(text="Закрыть", callback_data="sc:close")])

    new_kb = InlineKeyboardMarkup(inline_keyboard=new_rows)

    # Если по факту ничего не изменилось — не трогаем сообщение
    if _kb_equal(old_kb, new_kb):
        await call.answer("Уже отписан")
        return

    current_text = call.message.html_text or call.message.text or ""
    await _safe_edit(call, current_text, new_kb)
    await call.answer("Отписано")


_CWORD = {
    "diamonds": "алмазов", "diamond": "алмазов",
    "tea": "чашек", "cups": "чашек", "cup": "чашек",
    "treasures": "сокровищ", "tgstars": "старсов",
}


def _cword(cur: str | None, amount: int | None) -> str:
    return _CWORD.get((cur or "").lower(), (cur or "").lower())


async def _get_core(aid: int) -> dict | None:
    sql = """
          SELECT a.auction_id,
                 a.card_name,
                 a.hero_name,
                 a.currency,
                 a.message_id,
                 c.deck_id
          FROM auctions a
                   LEFT JOIN cards c
                             ON lower(c.card_name) = lower(a.card_name)
                                 AND lower(c.hero_name) = lower(a.hero_name)
          WHERE a.auction_id = $1 \
          """
    return await fetchrow(sql, aid)


async def _get_owners(aid: int) -> list[str]:
    rows = await fetch("""
                       SELECT COALESCE(u.username, '') AS username
                       FROM auction_owners ao
                                LEFT JOIN users u ON u.user_id = ao.user_id
                       WHERE ao.auction_id = $1
                       ORDER BY ao.id
                       """, aid)
    out = []
    for r in rows:
        u = (r.get("username") or "").lstrip("@")
        if u:
            out.append("@" + u)
    return out


async def _fallback_winner(aid: int) -> tuple[str | None, int | None]:
    row = await fetchrow("""
                         SELECT u.username, b.amount
                         FROM bids b
                                  JOIN users u ON u.user_id = b.bidder_id
                         WHERE b.auction_id = $1
                         ORDER BY b.amount DESC, b.placed_at
                         LIMIT 1
                         """, aid)
    if not row:
        return None, None
    name = ("@" + (row["username"] or "").lstrip("@")) if row.get("username") else None
    return name, int(row["amount"])


def _link(msg_id: int | str | None) -> str:
    """
    Строит ссылку на пост аукциона. Сначала по username, иначе t.me/c/<id>/<msg>.
    """
    if not msg_id:
        return "(ссылка не найдена)"

    # username из конфига (на случай если ты опять засунул туда '/@https://')
    try:
        from config import AUCTION_CHANNEL_USERNAME as _U
    except Exception:
        _U = None

    uname = (str(_U or "")).lstrip("@/").strip()
    if uname:
        return f"https://t.me/{uname}/{msg_id}"

    # фоллбэк для приватных каналов
    chan_id = None
    try:
        from config import AUCTION_CHANNEL_ID as _CID
        chan_id = _CID
    except Exception:
        pass
    if chan_id is None:
        try:
            from config import DISCUSSION_CHAT_ID as _DISC
            chan_id = _DISC
        except Exception:
            pass
    if chan_id is None:
        return "(ссылка не найдена)"

    core = str(chan_id).lstrip("-")
    if core.startswith("100"):
        core = core[3:]
    return f"https://t.me/c/{core}/{msg_id}"


# единицы без склонения, как ты хотел
_NOM = {
    "diamonds": "алмазы",
    "diamond": "алмазы",
    "cups": "чашки",
    "tea": "чашки",
    "treasures": "сокровища",
    "tgstars": "старсы",
}


def _price_phrase(cur: str | None, amount: int | float | None, cash_code: str | None = None) -> str:
    if amount is None:
        return "—"
    cur_l = (cur or "").lower()
    if cur_l in ("cash", "money", "fiat"):
        code = (cash_code or "").strip()
        return f"{int(amount) if float(amount).is_integer() else amount} {code}".strip()
    unit = _NOM.get(cur_l, cur or "")
    val = int(amount) if isinstance(amount, (int, float)) and float(amount).is_integer() else amount
    return f"{val} {unit}".strip()


def _build_unified_text(a: dict, owners: list[str], win_name: str | None, win_bid: int | float | None) -> str:
    """
    Ровно тот текст, что ты показал:
    Привет!

    Поздравляю!!!! 🥳

    Аукцион <ссылка> завершён!
    Лот: <герой> — <карта> Колода №<n>

    Стоимость карты: <число> <валюта>
    Победитель: @user
    Владелец карты: @owner1, @owner2
    """
    hero = a.get("hero_name") or "-"
    card = a.get("card_name") or "-"
    deck = a.get("deck_id")
    lot_line = f"Лот: {hero} — {card}" + (f" Колода №{deck}" if deck else "")

    link = _link(a.get("message_id"))
    price_line = _price_phrase(a.get("currency"), win_bid, a.get("cash_code"))

    owner_line = ", ".join(owners) if owners else "—"
    winner = win_name or "—"

    return "\n".join([
        "Привет!",
        "",
        "Поздравляю!!!! 🥳",
        "",
        f"Аукцион {link} завершён!",
        lot_line,
        "",
        f"Стоимость карты: {price_line}",
        f"Победитель: {winner}",
        f"Владелец карты: {owner_line}",
    ])


@router.message(Command("print"))
async def cmd_print(msg: types.Message, command: CommandObject):
    import re
    m = re.search(r"(\d+)", (command.args or ""))
    if not m:
        await msg.answer("Использование: /print 999  (или /print_idlot 999)")
        return

    aid = int(m.group(1))
    a = await _get_core(aid)
    if not a:
        await msg.answer(f"Лот {aid} не найден.")
        return

    owners = await _get_owners(aid)

    win_name, win_bid = None, None
    if get_auction_winner:
        try:
            w = await get_auction_winner(aid)
            if w:
                win_name = ("@" + (w.get("username") or "").lstrip("@")) if w.get("username") else None
                win_bid = w.get("bid")
                if win_bid is not None:
                    try:
                        win_bid = int(win_bid)
                    except Exception:
                        pass
        except Exception:
            pass
    if win_name is None:
        win_name, win_bid = await _fallback_winner(aid)

    text = _build_unified_text(a, owners, win_name, win_bid)
    await msg.answer(text)
