"""Winner-facing /print command and its presentation helpers."""

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

from bot.handlers.admin.helper.new.card_economy_shared import NOM

# ---------------------------------------------------------------------------
# Router / constants
# ---------------------------------------------------------------------------

router = Router(name="admin_card_economy_winner_print")


_CWORD = {
    "diamonds": "алмазов", "diamond": "алмазов",
    "tea": "чашек", "cups": "чашек", "cup": "чашек",
    "treasures": "сокровищ", "tgstars": "старсов",
}


def _cword(cur: str | None, amount: int | None) -> str:
    return _CWORD.get((cur or "").lower(), (cur or "").lower())


async def _get_core(aid: int) -> dict | None:
    service = await CardEconomyService.from_runtime()
    return await service.auction_core(aid)


async def _get_owners(aid: int) -> list[str]:
    service = await CardEconomyService.from_runtime()
    return await service.auction_owner_usernames(aid)


async def _fallback_winner(aid: int) -> tuple[str | None, int | None]:
    service = await CardEconomyService.from_runtime()
    return await service.fallback_winner(aid)


def _link(msg_id: int | str | None) -> str:
    """
    Строит ссылку на пост аукциона. Сначала по username, иначе t.me/c/<id>/<msg>.
    """
    if not msg_id:
        return "(ссылка не найдена)"

    # username из конфига (на случай если ты опять засунул туда '/@https://')
    try:
        from bot.core.legacy_config import legacy_config
    except Exception:
        _U = None

    uname = (str(legacy_config.AUCTION_CHANNEL_USERNAME or "")).lstrip("@/").strip()
    if uname:
        return f"https://t.me/{uname}/{msg_id}"

    # фоллбэк для приватных каналов
    chan_id = None
    try:
        from bot.core.legacy_config import legacy_config
        chan_id = legacy_config.AUCTION_CHANNEL_ID
    except Exception:
        pass
    if chan_id is None:
        try:
            from bot.core.legacy_config import legacy_config
            chan_id = legacy_config.DISCUSSION_CHAT_ID
        except Exception:
            pass
    if chan_id is None:
        return "(ссылка не найдена)"

    core = str(chan_id).lstrip("-")
    if core.startswith("100"):
        core = core[3:]
    return f"https://t.me/c/{core}/{msg_id}"


def _price_phrase(cur: str | None, amount: int | float | None, cash_code: str | None = None) -> str:
    if amount is None:
        return "—"
    cur_l = (cur or "").lower()
    if cur_l in ("cash", "money", "fiat"):
        code = (cash_code or "").strip()
        return f"{int(amount) if float(amount).is_integer() else amount} {code}".strip()
    unit = NOM.get(cur_l, cur or "")
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
