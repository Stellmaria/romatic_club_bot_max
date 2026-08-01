"""Luxury subscription leaderboard and pagination handlers."""

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
from bot.handlers.card_subscribe import decks_keyboard, presets_manage_keyboard
from bot.services.card_economy import CardEconomyService
from bot.services.card_subscriptions import CardSubscriptionsService
from bot.telegram.callbacks import safe_callback_answer
from bot.core.legacy_config import legacy_config
from db.cards import (
    get_card,
    get_deck,
    norm_obtain_type,
    set_card_obtain,
    set_deck_type,
    get_all_decks,
)
from db.users import (
    get_user_id_by_username,
    is_luxury_user,
)
from db.subscriptions import (
    list_broadcast_targets,
    list_user_card_subs,
    mark_subscription_confirmed,
    mark_unreachable_user,
    unsubscribe_subscription,
)
from db.auctions import get_auction_winner
from bot.telegram.states import CardSubscribeFSM, EconomyFSM

# ---------------------------------------------------------------------------
# Router / constants
# ---------------------------------------------------------------------------

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot.handlers.admin.helper.new.card_economy_shared import (
    DEFAULT_EMOJI,
    DEFAULT_VIEW,
    PAGE_SIZE,
    LUXURY_SEND_HTML_KW,
    NOM,
    safe_edit,
    subs_word,
)

router = Router(name="admin_card_economy_luxury")


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

    service = await CardEconomyService.from_runtime()
    return await service.luxury_top(
        limit=limit,
        offset=offset,
        rarity=rarity_param,
    )


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
    unit = NOM.get(t_l, t_l)  # _NOM у тебя уже есть в файле
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
        f"<b>{subs}</b> {subs_word(subs)} · "
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
    line2 = f"   <b>{subs}</b> {subs_word(subs)} · {cal}{sched} · {book}{deck_txt}"
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
        await safe_edit(message_or_call, text, kb)
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
