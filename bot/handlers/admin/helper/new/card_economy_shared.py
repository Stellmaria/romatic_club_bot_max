"""Shared context, logging and presentation helpers for card economy flows."""

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
from bot.services.card_economy import CardEconomyService
from bot.services.card_subscriptions import CardSubscriptionsService
from bot.telegram.callbacks import safe_callback_answer
from bot.core.settings import ADMIN_LOG_CHATS
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

# Currency labels are shared by leaderboard and winner-message presentation.
_NOM = {
    "diamonds": "алмазы",
    "diamond": "алмазы",
    "cups": "чашки",
    "tea": "чашки",
    "treasures": "сокровища",
    "tgstars": "старсы",
}


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
