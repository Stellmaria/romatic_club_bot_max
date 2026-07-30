import asyncio
import html
import logging
import re
from calendar import monthrange
from datetime import datetime, timedelta, date, time
from datetime import time as dtime
from datetime import timezone
from html import escape
from html import escape as _h
from typing import Dict, List, Iterable
from typing import Optional, Tuple, Any
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram import types, Bot
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, User, Message
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.admin_constants import render_auction_caption, load_full_auction_ctx
from bot.handlers.admin.helper.new.admin_actions import send_admin_log, format_exchange_moderation_log, \
    notify_exchange_user_moderation, _safe_user_mention
from bot.handlers.admin.helper.new.utils import is_luxury_member
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.helper.user_helpers import get_owner_refs
from bot.handlers.admin.logs_admin import short_media_id
from bot.handlers.admin.services.market_utils import safe_edit_text
from bot.handlers.admin.services.schedule import _chunks
from bot.handlers.auction_comments import _post_rules_under_lot, CB_WIN_SEND, CB_WIN_EDIT_AMT, CB_WIN_EDIT_USER, \
    CB_WIN_SKIP, bot
from bot.handlers.constants import USER_MESSAGES
from bot.handlers.helper.helpers_users import _emoji_by_currency, _deck_tag
from bot.keyboards.keyboards import craft_uid_kb
from bot.domain.auctions import AuctionKind, currency_choices_label, normalize_currency_choices
from bot.core.time import ensure_utc, utc_now
from bot.services.auction_media import resolve_media_file_id
from config import AUCTION_CHANNEL_ID, DISCUSSION_CHAT_ID, AUCTION_CHANNEL_USERNAME, LOG_CHAT_ID, ADMIN_LOG_CHATS, \
    LUXURY_CHAT_ID_LVL2, LUXURY_CHAT_ID, ADMINS, AUTOBID_SET_PASSWORD
from db.db import (
    get_all_decks, get_cards_by_deck, get_card_by_id, add_pending_auction, update_lot_field, log_admin_action,
    is_luxury_user, get_user, get_lots_by_owner, add_pending_auction_by_card_id, has_pending_lot,
    list_auctions, update_auction_status, get_auctions_by_card_ref, get_auctions_in_range, get_auctions_for_local_day,
    get_exchange_cards_for_deck, create_exchange_batch,
    execute,
    fetch, get_exchange_batch_by_id, is_admin,
    set_exchange_batch_status, count_sold_by_card_id, set_exchange_batch_moderation,
    get_exchange_batch, count_sold_same_card, show_pending_auction_lots, get_deck_by_id, get_exchange_items_by_batch_id,
    get_exchange_cards_for_batch, set_exchange_batch_posted, set_exchange_batch_deleted, get_cards_by_ids,
    add_exchange_item_for_card, disable_autobid,
    get_user_by_username, list_autobids, upsert_autobid, get_lot_by_id, get_cards_ids_by_deck, fetchall, fetchrow,
    mark_exchange_manual_sent, set_exchange_manual_winner, logger, set_luxury_status, log_audit_action,
    release_stale_unpublished_lots, cancel_owner_unpublished_lots,
)
from db.db import get_exchange_approved_cards_by_deck
from fsm_states import UserAddLotFSM, ExchangeFSM, ModActionFSM

router = Router()

EX_MODE_DECK = "deck"
EX_MODE_CARD = "card"
SPINS_VIDEO_10 = "BAACAgQAAxkBAAEIVgppaPPTd4pws8Sjz7v6nVWI_5dRIAAC0CkAAubLSFN7Vkpq49f5UDgE"
SPINS_VIDEO_50 = "BAACAgQAAxkBAAEIVg9paPQBmbEWAxclpqDIKFM8AAGGFYcAAtEpAALmy0hThL18CciuaOA4BA"
SPINS_VIDEO_100 = "BAACAgQAAxkBAAEIVhdpaPQdPmArSXPZT-ertSRz7WFzawAC0ikAAubLSFMyA8sd2Z3j0DgE"
EX_STATUS_APPROVED = "approved"

EX_MODE_DECK_SPLIT = "deck_split"  # “часть колоды” / набор карт

# card-like = одиночные карты И карты из разбива колоды
EX_CARDLIKE_MODES = (EX_MODE_CARD, EX_MODE_DECK_SPLIT)
EX_MODE_CARDLIKE = EX_CARDLIKE_MODES

SPINS_VIDEO_BY_QTY: dict[int, str] = {
    10: SPINS_VIDEO_10,
    50: SPINS_VIDEO_50,
    100: SPINS_VIDEO_100,
}

ANY_DECK_PHOTO_ID = "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"
ANY_CARD_PHOTO_ID = "AgACAgQAAxkBAAEIUTBpaNQXPz1Hs-BRv8cVslhg336rfgACnQtrG4d4QVO6Nvb-lxW0sgEAAwIAA3kAAzgE"

ANY_DECK_VIDEO_ID = "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"
ANY_CARD_VIDEO_ID = "BAACAgQAAxkBAAEIWo1paQ7XlDRb6ZM0Oxq7ehCNJr4pYgAC8ykAAubLSFPfP-LTDfGf1jgE"

ANY_RARITY_PHOTO_ID = {
    "bronze": "AgACAgQAAxkBAAEIUmVpaOBrItyji7PD36DvC88aYB9k5AACSAtrG-bLSFOCHDKEZTodUwEAAwIAA3kAAzgE",
    "silver": "BAACAgQAAxkBAAEIWndpaQvYOulo05meSFJaH8wPI5y-cQAC5ykAAubLSFNdI3_N1yahGjgE",
    "gold": "AgACAgQAAyEFAASe0o_mAAKdiWlo4sBQh367mIsCZnCHSU1NhOSTAAKOC2sbNg5JU-F5VqCd9_IIAQADAgADeQADOAQ",
    "diamond": "AgACAgQAAyEFAASe0o_mAAKddmlo4XYHwzfWPaF7l9xX6ST0I9-PAAKMC2sbNg5JU3ZIl7V9cCT9AQADAgADeQADOAQ",
}

ANY_RARITY_VIDEO_ID = {
    "bronze": "BAACAgQAAxkBAAEIWnppaQwi-zYkzpEvKFxJiEQhqC9mUAAC6SkAAubLSFOzj3n1DvMNwTgE",
    "silver": "BAACAgQAAxkBAAEIWndpaQvYOulo05meSFJaH8wPI5y-cQAC5ykAAubLSFNdI3_N1yahGjgE",
    "gold": "BAACAgQAAxkBAAEIWn1paQx38fDOFQEYrctHnBFe1m2WPgAC7SkAAubLSFPXdSQ0fDYg3jgE",
    "diamond": "BAACAgQAAxkBAAEIWoppaQ19NstpCMDaEH1bdIdposqdbgAC8SkAAubLSFMo6jaqnPnThDgE",
}
SERVICE_MEDIA_FILE_IDS = {
    "subscription_gold": "BAACAgIAAyEFAASe0o_mAAEBMVFpwFjv7TCrHf133nFAMnaoXmNqmQAC5JoAArogAAFKcdmYLZI9RAk6BA",
    "subscription_premium": "BAACAgIAAyEFAASe0o_mAAEBMVJpwFjv7aAXhWp9YxfOwhFfy34pvAAC5ZoAArogAAFKKE0-v4MeGiE6BA",
    "progress_slots": "BAACAgIAAyEFAASe0o_mAAEBMVNpwFjvawh3Uo2GEpSIsfDF2FuHCwAC5poAArogAAFKDgABrF261VkTOgQ",
    "friends_plus": "BAACAgIAAyEFAASe0o_mAAEBMVRpwFjv0whTY7CmDp3Gl0JsddQC2wAC55oAArogAAFKwx1NGEAWecE6BA",
}


def _subscription_title(plan: str, months: int) -> str:
    base = "Золотой пропуск" if plan == "gold" else "Премиум пропуск"
    month_text = {
        1: "1 месяц",
        3: "3 месяца",
        6: "6 месяцев",
        12: "12 месяцев",
    }.get(months, f"{months} мес.")
    return f"{base} ({month_text})"


def _service_media_file_id(
    service: str | None,
    *,
    spins_qty: int | None = None,
    deck_id: int | None = None,
) -> str | None:
    service = (service or "").strip()

    if service == "spins":
        return (SPINS_VIDEO_BY_QTY.get(int(spins_qty or 0)) or "").strip() or None

    if service in SERVICE_MEDIA_FILE_IDS:
        return SERVICE_MEDIA_FILE_IDS[service]

    if service == "deck_all" and deck_id:
        return (EX_DECK_COVER_MEDIA.get(int(deck_id)) or ANY_DECK_VIDEO_ID or "").strip() or None

    return None
log = logging.getLogger("auction")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
MIN_START = {
    "алмазы": 30,
    "чашки": 2,
    "сокровища": 10,
}


def _cb_parts(data: str) -> list[str]:
    return (data or "").split(":")


def _mention_owner(owner_id: int | None, owner_username: str | None) -> str:
    u = (owner_username or "").strip()
    if u:
        if not u.startswith("@"):
            u = "@" + u
        return u
    if owner_id:
        return f'<a href="tg://user?id={owner_id}">пользователь</a>'
    return "—"
MAX_ANY_CARD = {
    "алмазы": {"any": 2500, "diamond": 2500, "gold": 2000, "silver": 1500, "bronze": 1000},
    "чашки": {"any": 12, "diamond": 12, "gold": 10, "silver": 8, "bronze": 6},
    "сокровища": {"any": 250, "diamond": 250, "gold": 200, "silver": 150, "bronze": 100},
}

MAX_CARD_BY_DECKTYPE = {
    "roulette": {
        "алмазы": {"bronze": 500, "silver": 600, "gold": 800, "diamond": 900},
        "чашки": {"bronze": 4, "silver": 6, "gold": 8, "diamond": 10},
        "сокровища": {"bronze": 100, "silver": 150, "gold": 200, "diamond": 300},
    },
    "resource": {
        "алмазы": {"bronze": 300, "silver": 400, "gold": 500, "diamond": 600},
        "чашки": {"bronze": 2, "silver": 2, "gold": 2, "diamond": 4},
        "сокровища": {"bronze": 10, "silver": 10, "gold": 20, "diamond": 40},
    },
}

MAX_DECK_BY_DECKTYPE = {
    "roulette": {"алмазы": 3000, "чашки": 12, "сокровища": 300},
    "resource": {"алмазы": 1000, "чашки": 10, "сокровища": 100},
}

FIXED_START_ANY_RARITY = {
    "bronze": {"алмазы": 500, "чашки": 4, "сокровища": 100},
    "silver": {"алмазы": 600, "чашки": 6, "сокровища": 150},
    "gold": {"алмазы": 800, "чашки": 8, "сокровища": 200},
    "diamond": {"алмазы": 900, "чашки": 10, "сокровища": 300},
}

FIXED_START_BY_SERVICE = {
    "friends_plus": {
        "алмазы": 850,
        "чашки": 10,
        "сокровища": 250,
    },
    "progress_slots": {
        "алмазы": 850,
        "чашки": 10,
        "сокровища": 250,
    },
    "spins": {
        10: {"алмазы": 600, "чашки": 6, "сокровища": 150},
        50: {"алмазы": 1030, "чашки": 14, "сокровища": 430},
        100: {"алмазы": 1400, "чашки": 20, "сокровища": 800},
    },
    "subscription_gold": {
        1: {"алмазы": 850, "чашки": 10, "сокровища": 250},
        3: {"алмазы": 1280, "чашки": 18, "сокровища": 680},
        6: {"алмазы": 1900, "чашки": 30, "сокровища": 1300},
        12: {"алмазы": 3130, "чашки": 56, "сокровища": 2530},
    },
    "subscription_premium": {
        1: {"алмазы": 750, "чашки": 8, "сокровища": 190},
        3: {"алмазы": 1050, "чашки": 14, "сокровища": 450},
        6: {"алмазы": 1480, "чашки": 22, "сокровища": 880},
        12: {"алмазы": 2300, "чашки": 38, "сокровища": 1700},
    },
}

SERVICE_TITLES = {
    "friends_plus": "Друзья+",
    "progress_slots": "Слоты прогресса",
    "subscription_gold": "Золотой пропуск",
    "subscription_premium": "Премиум пропуск",
    "spins": "Кручения",
}
START_RANGE_ANY_RARITY = {
    "bronze": {
        "алмазы": (30, 500),
        "чашки": (2, 6),
        "сокровища": (10, 100),
    },
    "silver": {
        "алмазы": (30, 600),
        "чашки": (2, 6),
        "сокровища": (10, 150),
    },
    "gold": {
        "алмазы": (30, 800),
        "чашки": (2, 8),
        "сокровища": (20, 200),
    },
    "diamond": {
        "алмазы": (30, 900),
        "чашки": (4, 10),
        "сокровища": (40, 300),
    },
}

START_RANGE_BY_SERVICE = {
    "friends_plus": {
        "алмазы": (30, 1300),
        "чашки": (2, 16),
        "сокровища": (250, 400),
    },
    "progress_slots": {
        "алмазы": (30, 1300),
        "чашки": (2, 16),
        "сокровища": (250, 400),
    },
    "spins": {
        10: {
            "алмазы": (30, 900),
            "чашки": (2, 10),
            "сокровища": (150, 250),
        },
        50: {
            "алмазы": (30, 1500),
            "чашки": (2, 20),
            "сокровища": (430, 600),
        },
        100: {
            "алмазы": (30, 2100),
            "чашки": (2, 30),
            "сокровища": (800, 1200),
        },
    },
    "subscription_gold": {
        1: {
            "алмазы": (30, 1300),
            "чашки": (2, 16),
            "сокровища": (250, 400),
        },
        3: {
            "алмазы": (30, 1900),
            "чашки": (2, 26),
            "сокровища": (680, 1000),
        },
        6: {
            "алмазы": (30, 2800),
            "чашки": (2, 40),
            "сокровища": (1300, 1900),
        },
        12: {
            "алмазы": (30, 4500),
            "чашки": (2, 80),
            "сокровища": (2530, 3600),
        },
    },
    "subscription_premium": {
        1: {
            "алмазы": (30, 1100),
            "чашки": (2, 12),
            "сокровища": (190, 300),
        },
        3: {
            "алмазы": (30, 1600),
            "чашки": (2, 22),
            "сокровища": (450, 700),
        },
        6: {
            "алмазы": (30, 2200),
            "чашки": (2, 32),
            "сокровища": (880, 1300),
        },
        12: {
            "алмазы": (30, 3400),
            "чашки": (2, 56),
            "сокровища": (1700, 2500),
        },
    },
}

# «Любая карта за чашки»: зависит от типа деки — 8 / 6
ANY_CARD_CUPS_BY_DECKTYPE = {"roulette": 8, "resource": 6}

# Нормализация редкости
RARITY_MAP = {
    "эпик": "diamond", "epic": "diamond", "алмаз": "diamond", "алмазная": "diamond", "diamond": "diamond",
    "золото": "gold", "gold": "gold",
    "серебро": "silver", "silver": "silver",
    "бронза": "bronze", "bronze": "bronze",
    "любая": "any", "any": "any",
}
RARITY_PRICE_KEYS = {"bronze", "silver", "gold", "diamond"}

DECK_SCOPE_KEYS = {"deck", "whole_deck", "full_deck", "колода", "вся_колода"}

EX_DECK_COVER_MEDIA: dict[int, str] = {
    18: "BAACAgQAAxkBAAEJWWdpfjDFq_YchjQPKpaJhd8O4TntKwAC9RsAAna_-FO_2KeAQzb4DzgE",
    20: "BAACAgQAAxkBAAEJWWFpfjCEe81DEJBAHn9BKBYWgGvrAwAC8hsAAna_-FN0tX00LgFtCDgE",
    22: "BAACAgIAAyEFAASe0o_mAAEBMZ5pwF8nenGGWKgz-6vB1kc0pbPF2QACEJsAArogAAFKmdXz6_xXigI6BA",
}

async def _exchange_deck_cover_id(deck_id: int) -> str:
    did = int(deck_id or 0)
    # Реестр БД является источником истины. Словарь оставлен только как
    # совместимый fallback для колод, которые ещё не перенесены в реестр.
    return (
        await resolve_media_file_id(
            "deck",
            did,
            fallback=EX_DECK_COVER_MEDIA.get(did) or ANY_DECK_VIDEO_ID,
        )
        or ""
    ).strip()


def h(v: object, default: str = "—") -> str:
    if v is None:
        return default
    s = str(v).strip()
    if not s:
        return default
    return html.escape(s, quote=False)


def admin_link_html(admin_user: types.User) -> str:
    uname = (admin_user.username or "").strip()
    if uname:
        u = html.escape(uname)
        return f'<a href="https://t.me/{u}">@{u}</a>'
    # без юзернейма: кликаем по tg://user?id=
    name = html.escape(admin_user.full_name or "Админ")
    return f'<a href="tg://user?id={admin_user.id}">{name}</a>'

def _is_deck_lot(data: dict) -> bool:
    if data.get("is_whole_deck") is True:
        return True
    scope = str(data.get("lot_scope") or "").strip().lower()
    if scope in {"deck", "whole_deck", "full_deck", "колода", "вся_колода"}:
        return True
    if data.get("deck_mode") is True:
        return True
    # эвристика: выбран deck_id без card_id и это не «любая карта»
    any_card = str(data.get("any_card") or "").strip().lower()
    return bool(data.get("deck_id") and not data.get("card_id")
                and any_card not in {"1", "true", "yes", "да", "any", "любая"})


def _norm_currency(val: str | None) -> str | None:
    s = (val or "").strip().lower()

    # 💎
    if ("💎" in s) or ("алмаз" in s) or ("кристалл" in s) or ("diamond" in s):
        return "алмазы"

    # 🍵
    if ("🍵" in s) or ("чаш" in s) or ("чай" in s) or ("cup" in s) or ("tea" in s):
        return "чашки"

    # 🪙
    if ("🪙" in s) or ("сокров" in s) or ("treasure" in s):
        return "сокровища"

    return None


def currency_to_emoji(currency: str | None) -> str:
    # твой проект уже импортит _emoji_by_currency, просто делаем "публичную" обёртку
    cur = (currency or "").strip()
    e = ""
    try:
        e = _emoji_by_currency(cur)  # noqa: SLF001 (да, protected, зато работает)
    except Exception:
        e = ""
    return e or CURRENCY_EMOJI.get(_norm_currency(cur) or cur.lower(), "💎")


def _rarity_norm(v: str | None) -> str:
    return _norm_rarity(v)


def _rarity_badge(v: str | None) -> str:
    r = _rarity_norm(v)
    return {
        "bronze": "🥉",
        "silver": "🥈",
        "gold": "🥇",
        "diamond": "💎",
    }.get(r, "🔷")


def _digits_int(v: object, default: int = 0) -> int:
    if v is None:
        return default
    s = str(v)
    ds = "".join(ch for ch in s if ch.isdigit())
    return int(ds) if ds else default


def _normalize_card_ids(v: object) -> list[int]:
    if v is None:
        return []
    if isinstance(v, int):
        return [v]
    if isinstance(v, (list, tuple, set)):
        seq = v
    else:
        seq = [v]

    out: list[int] = []
    seen: set[int] = set()
    for x in seq:
        try:
            i = int(x)
        except Exception:
            continue
        if i not in seen:
            out.append(i)
            seen.add(i)
    return out


def _exchange_gift_for_card(card: dict) -> tuple[str, int]:
    # у тебя уже есть _exchange_gain_for_card(...) – просто даём имя,
    # которое ты используешь в новом оформлении
    return _exchange_gain_for_card(card)


async def _deck_price_for_deck(deck_id: int) -> int:
    if int(deck_id) in EX_WHOLE_DECK_PRICE:
        return int(EX_WHOLE_DECK_PRICE[int(deck_id)])

    full_cards = await get_exchange_cards_for_deck(int(deck_id))
    total = 0
    for c in full_cards or []:
        try:
            total += int(_exchange_price_for_card(c) or 0)
        except Exception:
            pass
    return int(total)



def _norm_rarity(val: str | None) -> str:
    if not val:
        return "any"
    return RARITY_MAP.get(val.strip().lower(), "any")


def _norm_obtain_type(v: str | None) -> str | None:
    s = (v or "").strip().lower()
    if s in {"tea", "cups", "чай", "чашки"}:
        return "чашки"
    if s in {"diamond", "diamonds", "алмазы", "алмаз"}:
        return "алмазы"
    if s in {"treasure", "treasures", "сокровища"}:
        return "сокровища"
    return None


async def _get_obtain_type_from_state_or_db(data: dict) -> str | None:
    # 1) если пресетом явно зафиксировали (см. ниже)
    forced = _norm_obtain_type(data.get("forced_obtain_type"))
    if forced:
        return forced

    # 2) если в state уже лежит
    ot = _norm_obtain_type(data.get("obtain_type"))
    if ot:
        return ot

    # 3) добиваем из БД по card_id
    cid = data.get("card_id")
    if cid:
        row = await fetchrow("SELECT obtain_type FROM cards WHERE card_id = $1", int(cid))
        if row:
            return _norm_obtain_type(row.get("obtain_type"))

    return None


async def _get_deck_type_from_state_or_db(data: dict) -> str | None:
    dt = (data.get("deck_type") or "").strip().lower()
    if dt in {"roulette", "resource"}:
        return dt

    if data.get("card_id"):
        row = await fetchrow("""
                             SELECT d.deck_type
                             FROM cards c
                                      JOIN decks d ON d.id = c.deck_id
                             WHERE c.card_id = $1
                             """, int(data["card_id"]))
        if row and row["deck_type"] in ("roulette", "resource"):
            return row["deck_type"]

    if data.get("deck_id"):
        row = await fetchrow("SELECT deck_type FROM decks WHERE id = $1", int(data["deck_id"]))
        if row and row["deck_type"] in ("roulette", "resource"):
            return row["deck_type"]

    if data.get("card_name") and data.get("hero_name"):
        row = await fetchrow("""
                             SELECT d.deck_type
                             FROM cards c
                                      JOIN decks d ON d.id = c.deck_id
                             WHERE c.card_name = $1
                               AND c.hero_name = $2 LIMIT 1
                             """, data["card_name"], data["hero_name"])
        if row and row["deck_type"] in ("roulette", "resource"):
            return row["deck_type"]

    return None


TREASURES_LOCKED = True
TREASURES_LOCK_REASON = "🪙 Сокровища временно отключены из-за мошенников. Используйте 💎 или 🍵."

def currency_kb() -> ReplyKeyboardMarkup:
    row = [KeyboardButton(text="💎"), KeyboardButton(text="🍵")]
    # “под замок” в UI
    if TREASURES_LOCKED:
        row.append(KeyboardButton(text="🪙 🔒"))
    else:
        row.append(KeyboardButton(text="🪙"))
    return ReplyKeyboardMarkup(
        keyboard=[row],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def auction_currency_kb(auction_kind: str | None) -> ReplyKeyboardMarkup:
    kind = str(auction_kind or "standard").strip().lower()
    if kind in {"reverse", "free"}:
        rows = [
            [KeyboardButton(text="🍵 Чай"), KeyboardButton(text="💎 Алмазы")],
            [KeyboardButton(text="🍵 + 💎 Чай или/и алмазы")],
        ]
        if kind == "free":
            rows.append([KeyboardButton(text="🧩 Комби (свои варианты)")])
        return ReplyKeyboardMarkup(
            keyboard=rows,
            resize_keyboard=True,
            one_time_keyboard=True,
        )
    return currency_kb()


async def _ask_for_currency(message: types.Message, state: FSMContext) -> None:
    """Move the add-lot FSM to currency selection and render the right keyboard.

    This helper is intentionally kept in the legacy compatibility module too:
    the production entrypoint still registers ``bot.handlers.auctions.router``.
    Without it every preset callback raised ``NameError`` after updating FSM
    data, which looked in Telegram like a permanently spinning button.
    """
    data = await state.get_data()
    kind = str(data.get("auction_kind") or "standard").strip().lower()

    if kind == AuctionKind.FREE.value:
        prompt = "Выберите, в какой валюте принимать предложения:"
    elif kind == AuctionKind.REVERSE.value:
        prompt = "Выберите валюту обратного аукциона:"
    else:
        prompt = "Выберите валюту:"

    # Set the state before sending the message, so a fast tap cannot arrive
    # while the previous state is still active.
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await message.answer(prompt, reply_markup=auction_currency_kb(kind))


def _currency_label(currency: str) -> str:
    return {"алмазы": "алмазы", "чашки": "чай", "сокровища": "сокровища"}.get(currency, currency)


CURRENCY_EMOJI = {"алмазы": "💎", "чашки": "🍵", "сокровища": "🪙"}
CURRENCY_STEP = {"алмазы": 10, "чашки": 2, "сокровища": 10}


def _cur_emoji(currency: str) -> str:
    return CURRENCY_EMOJI.get(currency, "💎")


def _cur_step(currency: str) -> int:
    return CURRENCY_STEP.get(currency, 1)


async def _get_rarity_from_state_or_db(data: dict) -> Optional[str]:
    for k in ("rarity", "card_rarity", "selected_rarity"):
        r = _norm_rarity(data.get(k))
        if r:
            return r

    sel = data.get("selected_card") or {}
    r = _norm_rarity(sel.get("rarity"))
    if r:
        return r

    # эвристика по названию
    cname = (data.get("card_name") or "").lower()
    for token, val in (("бронз", "bronze"), ("сереб", "silver"), ("золот", "gold"),
                       ("эпик", "diamond"), ("алмаз", "diamond")):
        if token in cname:
            return val

    # добиваем из БД
    cid = data.get("card_id")
    if cid:
        try:
            card = await get_card_by_id(cid)
            return _norm_rarity(card.get("rarity"))
        except Exception:
            pass
    return None


async def compute_start_price_limits(state: FSMContext, currency_ui: str) -> tuple[int, int, str]:
    """
    Возвращает (min_allowed, max_allowed, reason) для стартовой цены.

    Логика:
    - для пресетов по редкости -> START_RANGE_ANY_RARITY
    - для сервисных лотов -> START_RANGE_BY_SERVICE
    - для обычных карт/колод -> старая логика MAX_*
    """

    data = await state.get_data()

    currency = _norm_currency(currency_ui)
    global_min = int(MIN_START[currency])

    def _ret(lo: int, hi: int, reason: str) -> tuple[int, int, str]:
        lo = max(global_min, int(lo))
        hi = max(lo, int(hi))
        return lo, hi, reason

    any_cups_by_decktype = globals().get("ANY_CARD_CUPS_BY_DECKTYPE", {"roulette": 8, "resource": 6})
    rarity_keys = set(globals().get("RARITY_PRICE_KEYS", {"bronze", "silver", "gold", "diamond"}))

    service = (data.get("service") or "").strip()
    months = int(data.get("subscription_months") or 0)
    spins_qty = int(data.get("spins_qty") or 0)

    rarity = _norm_rarity(await _get_rarity_from_state_or_db(data))
    deck_type = await _get_deck_type_from_state_or_db(data)

    # =========================================================
    # 0) ПРЕСЕТЫ ПО РЕДКОСТИ: Любая бронза / серебро / золото / алмазная
    # =========================================================
    is_any_rarity_preset = (
        data.get("card_id") is None
        and (data.get("hero_name") or "") == "Лот от игрока"
        and rarity in START_RANGE_ANY_RARITY
    )
    if is_any_rarity_preset:
        rng = START_RANGE_ANY_RARITY.get(rarity, {}).get(currency)
        if rng:
            title = {
                "bronze": "Любая бронза",
                "silver": "Любое серебро",
                "gold": "Любая золотая",
                "diamond": "Любая алмазная",
            }[rarity]
            return _ret(rng[0], rng[1], title)

    # =========================================================
    # 1) СЕРВИСНЫЕ ЛОТЫ
    # =========================================================
    if service in {"friends_plus", "progress_slots"}:
        rng = START_RANGE_BY_SERVICE.get(service, {}).get(currency)
        if rng:
            return _ret(rng[0], rng[1], SERVICE_TITLES.get(service, service))

    if service in {"subscription_gold", "subscription_premium"}:
        rng = START_RANGE_BY_SERVICE.get(service, {}).get(months, {}).get(currency)
        if rng:
            return _ret(
                rng[0],
                rng[1],
                f"{SERVICE_TITLES.get(service, service)} ({months} мес.)"
            )

    if service == "spins":
        rng = START_RANGE_BY_SERVICE.get("spins", {}).get(spins_qty, {}).get(currency)
        if rng:
            return _ret(rng[0], rng[1], f"Кручения ({spins_qty} шт.)")

    # =========================================================
    # 2) КОЛОДА
    # =========================================================
    if _is_deck_lot(data):
        if deck_type in MAX_DECK_BY_DECKTYPE and currency in MAX_DECK_BY_DECKTYPE[deck_type]:
            return _ret(
                global_min,
                MAX_DECK_BY_DECKTYPE[deck_type][currency],
                ("Рулеточная" if deck_type == "roulette" else "Сокровищная") + " колода",
            )

        caps: list[int] = []
        for dt in ("roulette", "resource"):
            cur_caps = MAX_DECK_BY_DECKTYPE.get(dt, {})
            if currency in cur_caps:
                caps.append(cur_caps[currency])

        if caps:
            return _ret(global_min, min(caps), "Колода (тип неизвестен) — нижний потолок")

        return _ret(global_min, 10 ** 9, "Колода (тип неизвестен) — без потолка")

    # =========================================================
    # 3) КАРТА
    # =========================================================

    # "Любая карта" за чашки — особое правило по типу деки
    if rarity == "any" and currency == "чашки":
        if deck_type:
            cap = int(any_cups_by_decktype.get(deck_type, MAX_ANY_CARD["чашки"]["any"]))
            return _ret(
                global_min,
                cap,
                f"Любая карта ({'рулеточная' if deck_type == 'roulette' else 'сокровищная'})",
            )
        return _ret(global_min, MAX_ANY_CARD["чашки"]["any"], "Любая карта (тип деки неизвестен)")

    # ресурсные карты через отдельную obtain-логику, если она есть
    if deck_type == "resource":
        cap_by_obtain = globals().get("RESOURCE_CAP_BY_OBTAIN")
        get_obtain = globals().get("_get_obtain_type_from_state_or_db")
        if cap_by_obtain and callable(get_obtain):
            obtain_type = await get_obtain(data)
            if obtain_type in {"чашки", "алмазы"}:
                table = cap_by_obtain[obtain_type]
                if rarity in table:
                    return _ret(global_min, int(table[rarity]), f"Ресурсная ({obtain_type}, {rarity})")
                return _ret(global_min, int(max(table.values())), f"Ресурсная ({obtain_type}, редкость не указана)")

    # известны тип деки + редкость
    if deck_type and (rarity in rarity_keys):
        try:
            deck_cap = int(MAX_CARD_BY_DECKTYPE[deck_type][currency][rarity])
        except KeyError:
            deck_cap = 10 ** 9

        global_cap = int(MAX_ANY_CARD.get(currency, {}).get(rarity, deck_cap))
        cap = min(deck_cap, global_cap)

        return _ret(
            global_min,
            cap,
            f"Карта ({'рулеточная' if deck_type == 'roulette' else 'сокровищная'}, {rarity})",
        )

    # известна дека, но редкость любая/неизвестна
    if deck_type and rarity == "any":
        try:
            dt_caps = list(MAX_CARD_BY_DECKTYPE[deck_type][currency].values())
            deck_any_cap = int(max(dt_caps)) if dt_caps else 10 ** 9
        except Exception:
            deck_any_cap = 10 ** 9

        global_any = int(MAX_ANY_CARD.get(currency, {}).get("any", deck_any_cap))
        cap = min(deck_any_cap, global_any)

        return _ret(
            global_min,
            cap,
            f"Карта ({'рулеточная' if deck_type == 'roulette' else 'сокровищная'}, любая)",
        )

    # редкость известна, дека нет
    if rarity in rarity_keys:
        cap = int(MAX_ANY_CARD.get(currency, {}).get(rarity, MAX_ANY_CARD[currency]["any"]))
        return _ret(global_min, cap, f"Карта ({rarity}, тип деки неизвестен) — глобальный потолок")

    # вообще общий fallback
    return _ret(global_min, int(MAX_ANY_CARD[currency]["any"]), "Любая карта")
def _first_key(rec: Any, names: Iterable[str]) -> Optional[Any]:
    """Вернёт значение по первому существующему ключу из списка."""
    # asyncpg.Record поддерживает словарный доступ и .keys()
    for k in names:
        try:
            if isinstance(rec, dict):
                if k in rec and rec[k] is not None:
                    return rec[k]
            else:
                # asyncpg.Record или объект с атрибутами
                if hasattr(rec, k):
                    v = getattr(rec, k)
                    if v is not None:
                        return v
                # доступ как по ключу
                v = rec[k]  # может кинуть KeyError — ловим ниже
                if v is not None:
                    return v
        except Exception:
            continue
    return None


def _parse_time(value: Any) -> Optional[dtime]:
    """Пытается получить time из разных типов: time, datetime, 'HH:MM', 'HH:MM:SS', int минут и т.д."""
    if value is None:
        return None
    if isinstance(value, dtime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None)
    if isinstance(value, (int, float)):
        # трактуем как часы в минутах с полуночи
        minutes = int(value)
        h, m = divmod(minutes, 60)
        try:
            return dtime(h, m)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                pass
        # иногда приходит "2025-09-10 15:30:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt.timetz().replace(tzinfo=None)
        except Exception:
            return None
    return None


def _extract_time(rec: Any) -> Optional[dtime]:
    v = _first_key(rec, ("time", "starts_at", "start_at", "start_time", "datetime", "dt", "ts"))
    t = _parse_time(v)
    if t:
        return t
    # запасной вариант: час/минута по отдельности
    h = _first_key(rec, ("hour", "h"))
    m = _first_key(rec, ("minute", "min", "m"))
    try:
        if isinstance(h, (int, float)) and isinstance(m, (int, float)):
            return dtime(int(h), int(m))
    except Exception:
        return None
    return None


def _extract_title(rec: Any) -> Optional[str]:
    v = _first_key(rec, ("title", "name", "lot_title", "caption", "text"))
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _format_line(t: dtime, title: str) -> str:
    return f"{t.strftime('%H:%M')} 🃏{title}"


# ---------- /day handler ----------

ANNOUNCE_TZ = ZoneInfo("Europe/Moscow")  # если показываешь время в МСК
UTC = timezone.utc


def _day_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    # Полночь и конец дня в «анонсной TZ», затем перевод в UTC
    start_local = datetime.combine(target_date, dtime.min).replace(tzinfo=ANNOUNCE_TZ)
    end_local = (start_local + timedelta(days=1))  # полузакрытый интервал [start, end)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
from aiogram.exceptions import TelegramNetworkError


async def safe_answer(message, text: str, **kwargs):
    try:
        return await message.answer(text, **kwargs)
    except TelegramNetworkError as e:
        logger.warning(f"safe_answer network error: {e}")
        return None

async def _ensure_membership(bot, user_id: int, channel_id: int, discussion_chat_id: int) -> bool | None:
    try:
        m1 = await bot.get_chat_member(channel_id, user_id)
        m2 = await bot.get_chat_member(discussion_chat_id, user_id)

        ok1 = getattr(m1, "status", None) not in {"left", "kicked"}
        ok2 = getattr(m2, "status", None) not in {"left", "kicked"}
        return bool(ok1 and ok2)

    except TelegramNetworkError as e:
        logger.warning(f"_ensure_membership network error for user {user_id}: {e}")
        return None

    except TelegramBadRequest as e:
        logger.warning(f"_ensure_membership bad request for user {user_id}: {e}")
        return False

    except Exception as e:
        logger.exception(f"_ensure_membership unexpected error for user {user_id}: {e}")
        return None


@router.message(F.text.regexp(r"^/addlot(?:@\w+)?(?:\s|$)"))
async def addlot_regex_entry(message: types.Message, state: FSMContext, bot: Bot):
    await addlot_start(message, state, bot)


def _cancel_pending_lot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отозвать неопубликованную заявку",
                    callback_data="user_cancel_pending_lots",
                )
            ]
        ]
    )


async def _cancel_pending_for_user(user_id: int) -> list[int]:
    # Сначала переводим пропущенные слоты без Telegram-поста в
    # publication_failed, после чего их можно безопасно отозвать.
    await release_stale_unpublished_lots(int(user_id))
    return await cancel_owner_unpublished_lots(int(user_id))


@router.message(Command("cancel_pending"), F.chat.type == "private")
async def cancel_pending_lot_command(message: types.Message, state: FSMContext) -> None:
    cancelled = await _cancel_pending_for_user(message.from_user.id)
    await state.clear()
    if cancelled:
        ids = ", ".join(str(item) for item in cancelled)
        await message.answer(
            "✅ Неопубликованная заявка отозвана.\n"
            f"Лоты: <code>{html.escape(ids)}</code>\n\n"
            "Теперь можно отправить новую через /addlot.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await message.answer(
        "Не нашла заявки, которую можно отозвать автоматически. "
        "Уже опубликованный или будущий запланированный лот удаляется через администратора.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(F.data == "user_cancel_pending_lots")
async def cancel_pending_lot_callback(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        await call.answer()
    except Exception:
        pass
    cancelled = await _cancel_pending_for_user(call.from_user.id)
    await state.clear()
    if cancelled:
        ids = ", ".join(str(item) for item in cancelled)
        await call.message.answer(
            "✅ Неопубликованная заявка отозвана.\n"
            f"Лоты: <code>{html.escape(ids)}</code>\n\n"
            "Теперь можно отправить новую через /addlot.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await call.message.answer(
        "Заявка уже опубликована, запланирована на будущее или была обработана. "
        "Такой лот автоматически не удаляется.",
        reply_markup=ReplyKeyboardRemove(),
    )

async def addlot_start(message: types.Message, state: FSMContext, bot: Bot) -> None:
    if message.chat.type != "private":
        me = await bot.me()
        url = f"https://t.me/{me.username}?start=addlot"
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="Открыть диалог с ботом", url=url)]]
        )
        await message.reply("Эту команду нужно запускать в личке с ботом.", reply_markup=kb)
        return

    await state.clear()

    try:
        u = await get_user(message.from_user.id)
        is_lux = bool(u and (u.get("is_luxury") or u.get("is_lux")))
        is_trusted = bool(u and u.get("is_trusted"))
    except Exception:
        is_lux = False
        is_trusted = False

    try:
        await _ensure_membership(bot, message.from_user.id, AUCTION_CHANNEL_ID, DISCUSSION_CHAT_ID)
    except PermissionError:
        await message.answer(
            USER_MESSAGES.get(
                "auction_access_denied",
                "❗ Для того чтобы выставить карту на аукцион, нужно выполнить следующие пункты:\n"
                "1️⃣ Быть подписанным на наш канал https://t.me/karty_kr, а также состоять в чате https://t.me/karta_kr\n"
                "2️⃣ Иметь именно подарочную копию выставляемой карты\n"
                "3️⃣ Знать номер колоды и имя персонажа вашей карты\n"
                "4️⃣ Указать стартовую цену (Самая лучшая от 90-300💎)\n"
                "5️⃣ Оставить эту карту у себя до выхода аукциона"
            )
        )
        return
    except Exception:
        await message.answer("Не удалось проверить подписку. Попробуй позже или напиши админам.")
        return

    luxury_level = await get_user_luxury_level(bot, message.from_user.id)

    released = await release_stale_unpublished_lots(message.from_user.id)
    if released:
        logger.warning(
            "Released stale unpublished lots for user_id=%s: %s",
            message.from_user.id,
            released,
        )

    if not (is_lux or luxury_level > 0) and await has_pending_lot(message.from_user.id):
        await state.clear()
        await message.answer(
            "❗ У вас уже есть незавершённая заявка или лот.\n"
            "Новая заявка не создаётся, пока предыдущая находится на модерации, "
            "в будущем расписании или уже идёт.\n\n"
            "Неопубликованную зависшую заявку можно отозвать кнопкой ниже или командой "
            "/cancel_pending.",
            reply_markup=_cancel_pending_lot_keyboard(),
        )
        return

    await state.update_data(
        is_lux=is_lux,
        is_trusted=is_trusted,
        luxury_level=luxury_level,
        auction_kind=None,
    )

    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)

    await message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_kind_locked:"))
async def auk_kind_locked(call: types.CallbackQuery) -> None:
    await call.answer("Этот тип доступен только по уровню Лакшери.", show_alert=True)


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_kind:"))
async def auk_kind_selected(call: types.CallbackQuery, state: FSMContext) -> None:
    _, kind = call.data.split(":", 1)

    try:
        selected_kind = AuctionKind.from_raw(kind)
    except ValueError:
        await call.answer("Неизвестный тип аукциона.", show_alert=True)
        return

    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)
    if luxury_level < selected_kind.minimum_luxury_level:
        await call.answer(
            f"Этот тип доступен с уровня Лакшери {selected_kind.minimum_luxury_level}.",
            show_alert=True,
        )
        return

    # ✅ БИРЖА
    if selected_kind is AuctionKind.EXCHANGE:
        await state.update_data(auction_kind=selected_kind.value)
        await state.set_state(ExchangeFSM.waiting_for_deck)

        decks = await _get_exchange_decks_for_menu()
        deck_ids_label = " / ".join(str(_deck_id_from_row(d)) for d in decks)

        await call.message.answer(
            f"🛒 Биржа: выбери колоду ({deck_ids_label}):",
            reply_markup=exchange_deck_keyboard(decks),
        )
        await call.answer()
        return

    is_lux = bool(data.get("is_lux", False))
    is_trusted = bool(data.get("is_trusted", False))
    await state.update_data(auction_kind=selected_kind.value)
    await call.answer()
    await _start_deck_choice(call.message, state, is_lux, is_trusted)


async def _start_deck_choice(message: types.Message, state: FSMContext, is_lux: bool, is_trusted: bool):
    await state.update_data(is_lux=is_lux, is_trusted=is_trusted)

    decks = await get_all_decks()
    if not decks:
        await message.answer("Пока нет доступных колод.")
        return

    keyboard = [
        [types.InlineKeyboardButton(
            text=f"{deck['deck_id']}. {deck.get('deck_name') or deck.get('title') or 'Колода'}",
            callback_data=f"user_deck_{deck['deck_id']}"
        )] for deck in decks
    ]

    keyboard.append([types.InlineKeyboardButton(text="Другие лоты", callback_data="user_own_custom")])
    # ✅ кнопка “Типы аукционов” (как просила)
    keyboard.append([types.InlineKeyboardButton(text="📁 Типы аукционов", callback_data="user_auk_types")])

    # ✅ назад к выбору аукциона
    keyboard.append([types.InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="user_back_to_auction_kind")])

    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer("Выберите колоду:", reply_markup=kb)

    # ❗ВАЖНО: строка должна заканчиваться тут. НИКАКИХ декораторов на этой же строке.
    await state.set_state(UserAddLotFSM.waiting_for_deck)


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_back_to_auction_kind")
async def cb_user_back_to_auction_kind(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)

    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)
    await call.message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )
    await call.answer()

def kb_decks(decks: list[dict]) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []

    for d in decks:
        deck_id = d.get("deck_id") or d.get("id")
        if not deck_id:
            continue

        num = d.get("num")
        if num is None:
            num = deck_id

        name = d.get("name") or d.get("deck_name") or d.get("title") or "—"

        rows.append([
            types.InlineKeyboardButton(
                text=f"{num}. {name}",
                callback_data=f"user_deck_{int(deck_id)}",
            )
        ])

    rows.append([types.InlineKeyboardButton(text="Друзья+", callback_data="user_friends_plus")])
    rows.append([types.InlineKeyboardButton(text="Слоты прогресса", callback_data="user_progress_slots")])
    rows.append([types.InlineKeyboardButton(text="Пропуски", callback_data="user_subscription")])
    rows.append([types.InlineKeyboardButton(text="Кручения", callback_data="user_spins")])
    rows.append([types.InlineKeyboardButton(text="Колода-конструктор", callback_data="user_deck_constructor")])
    rows.append([types.InlineKeyboardButton(text="Другие лоты", callback_data="user_own_variant")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def kb_presets_menu() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Друзья+", callback_data="user_friends_plus")],
        [types.InlineKeyboardButton(text="Слоты прогресса", callback_data="user_progress_slots")],
        [types.InlineKeyboardButton(text="Пропуски", callback_data="user_subscription")],
        [types.InlineKeyboardButton(text="Кручения", callback_data="user_spins")],
        [types.InlineKeyboardButton(text="Колода-конструктор", callback_data="user_deck_constructor")],
        [types.InlineKeyboardButton(text="Любая бронза", callback_data="user_any_bronze")],
        [types.InlineKeyboardButton(text="Любое серебро", callback_data="user_any_silver")],
        [types.InlineKeyboardButton(text="Любая золотая", callback_data="user_any_gold")],
        [types.InlineKeyboardButton(text="Любая алмазная", callback_data="user_any_diamond")],
        [types.InlineKeyboardButton(text="Любая карта", callback_data="user_any_card")],
        [types.InlineKeyboardButton(text="Любая колода", callback_data="user_any_deck")],
        [types.InlineKeyboardButton(text="Алмазы за чай", callback_data="user_res_diamonds_for_tea")],
        [types.InlineKeyboardButton(text="Чай за алмазы", callback_data="user_res_tea_for_diamonds")],
        [types.InlineKeyboardButton(text="Назад", callback_data="user_deck_back")],
    ])


def kb_subscription_types(back_cb: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🥇 Золотой пропуск", callback_data="user_subscription_gold")],
            [types.InlineKeyboardButton(text="💎 Премиум пропуск", callback_data="user_subscription_premium")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )

def kb_subscription_periods(plan: str, back_cb: str) -> types.InlineKeyboardMarkup:
    title = "Золотой пропуск" if plan == "gold" else "Премиум пропуск"
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"{title} • 1 месяц", callback_data=f"user_subscription_period:{plan}:1")],
            [types.InlineKeyboardButton(text=f"{title} • 3 месяца", callback_data=f"user_subscription_period:{plan}:3")],
            [types.InlineKeyboardButton(text=f"{title} • 6 месяцев", callback_data=f"user_subscription_period:{plan}:6")],
            [types.InlineKeyboardButton(text=f"{title} • 12 месяцев", callback_data=f"user_subscription_period:{plan}:12")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )
async def _start_subscription_period_step(
    call: types.CallbackQuery,
    state: FSMContext,
    *,
    plan: str,
    back_cb: str,
) -> None:
    title = "Золотой пропуск" if plan == "gold" else "Премиум пропуск"

    await state.update_data(
        deck_id=None,
        card_id=None,
        hero_name=None,
        rarity="any",
        deck_type=None,
        subscription_plan=plan,
        subscription_months=None,
        card_name=title,          # пока базовое название
        service=None,             # сервис зафиксируем после выбора срока
        image_id=None,
        image_file_id=None,
    )

    await call.message.answer(
        "Выберите срок подписки:",
        reply_markup=kb_subscription_periods(plan, back_cb),
    )
    await state.set_state(UserAddLotFSM.waiting_for_subscription)

    try:
        await call.answer()
    except Exception:
        pass
async def _check_service_addlot_access(call: types.CallbackQuery, state: FSMContext) -> bool:
    user_id = call.from_user.id
    is_lux = await is_luxury_user(user_id)

    if is_lux:
        return True

    if await has_pending_lot(user_id):
        await call.message.answer(
            "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
            "Дождитесь её обработки, чтобы отправить новую.\n\n"
            "Хотите выставлять несколько карт? Получите лакшери-статус!"
        )
        await state.clear()
        try:
            await call.answer()
        except Exception:
            pass
        return False

    user_lots = await get_lots_by_owner(user_id)
    scheduled_user_lots = [
        a for a in (user_lots or [])
        if (a.get("status") or "") in {"scheduled", "active"}
    ]

    if scheduled_user_lots:
        last_lot = max(scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time")))
        date_str = last_lot["end_time"].strftime("%d.%m.%Y")
        start = last_lot["start_time"].strftime("%H:%M")
        end = last_lot["end_time"].strftime("%H:%M")
        next_possible_time = last_lot["end_time"] + timedelta(minutes=1)

        msg = (
            "❗ У вас уже есть запланированный лот в аукционе.\n\n"
            f"Лот: <b>{last_lot['card_name']}</b>\n"
            f"Дата: <b>{date_str}</b>\n"
            f"Время: <b>{start}–{end}</b>\n\n"
            f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
            f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
            "Хотите выставлять несколько карт? Получите лакшери-статус!"
        )
        await call.message.answer(msg, parse_mode="HTML")
        await state.clear()
        try:
            await call.answer()
        except Exception:
            pass
        return False

    return True
def deck_constructor_bronze_kb() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="4 бронзы")],
            [types.KeyboardButton(text="5 бронз")],
            [types.KeyboardButton(text="6 бронз")],
            [types.KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def spins_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="10 кручений")],
            [KeyboardButton(text="50 кручений")],
            [KeyboardButton(text="100 кручений")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_spins")
async def cb_spins_from_decks(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(deck_id=None, card_id=None, rarity="any", deck_type=None,
                            card_name="Кручения", service="spins", spins_qty=None)
    await call.message.answer("Выберите количество кручений:", reply_markup=spins_kb())
    # будем ловить число в том же состоянии, где обычно ловим валюту
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_spins")
async def cb_spins_from_presets(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(card_id=None, card_name="Кручения", service="spins", spins_qty=None)
    await call.message.answer("Выберите количество кручений:", reply_markup=spins_kb())
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await call.answer()


@router.message(StateFilter(UserAddLotFSM.waiting_for_currency))
async def addlot_currency_or_spins(message: types.Message, state: FSMContext):
    data = await state.get_data()
    service = (data.get("service") or "").strip()

    # 1) Ветка КРУЧЕНИЙ: сначала выбираем количество
    if service == "spins" and not data.get("spins_qty"):
        m = re.search(r"\d+", message.text or "")
        qty = int(m.group()) if m else 0
        if qty not in (10, 50, 100):
            await message.answer("Выберите кнопкой: 10, 50 или 100 кручений.")
            return

        video_id = (SPINS_VIDEO_BY_QTY.get(qty) or "").strip()
        if not video_id or video_id.startswith("PASTE_"):
            await message.answer(
                "Видео для кручений ещё не настроено.\n"
                "Нужно вставить Telegram file_id в SPINS_VIDEO_10_ID / 50 / 100."
            )
            return

        await state.update_data(
            spins_qty=qty,
            card_name=f"Кручения ({qty} шт.)",
            image_id=video_id,
            image_file_id=video_id,
        )
        await _ask_for_currency(message, state)
        return

    # 2) Выбор валюты с учётом типа аукциона.
    auction_kind = str(data.get("auction_kind") or "standard").strip().lower()
    raw_currency = (message.text or "").strip().lower()
    is_reverse = auction_kind == AuctionKind.REVERSE.value
    is_free = auction_kind == AuctionKind.FREE.value

    if is_free and ("комби" in raw_currency or "свои вариант" in raw_currency):
        await state.update_data(
            currency="чашки",
            accepted_currencies=["чашки", "алмазы"],
            custom_offer_terms=None,
            start_price=0,
            min_start=None,
            max_start=None,
        )
        await message.answer(
            "Опишите свои варианты оплаты или обмена одним сообщением.\n"
            "Например: <code>2 чая + карта из КР</code> или "
            "<code>алмазы + обмен на другую карту</code>.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_custom_offer_terms)
        return

    has_tea = "🍵" in raw_currency or "чай" in raw_currency or "чаш" in raw_currency
    has_diamonds = "💎" in raw_currency or "алмаз" in raw_currency
    accepted_currencies: list[str]
    if (is_reverse or is_free) and has_tea and has_diamonds:
        currency = "чашки"  # compatibility scalar for old code paths
        accepted_currencies = ["чашки", "алмазы"]
    else:
        currency = _norm_currency(message.text)
        accepted_currencies = [currency] if currency else []

    if not currency:
        await message.answer(
            "Выберите валюту кнопкой.",
            reply_markup=auction_currency_kb(auction_kind),
        )
        return

    if is_reverse or is_free:
        if currency not in {"чашки", "алмазы"}:
            await message.answer(
                "Для этого типа доступны только 🍵 чай, 💎 алмазы или оба варианта.",
                reply_markup=auction_currency_kb(auction_kind),
            )
            return
    elif TREASURES_LOCKED and currency == "сокровища":
        await message.answer(TREASURES_LOCK_REASON, reply_markup=currency_kb())
        return

    await state.update_data(
        currency=currency,
        accepted_currencies=accepted_currencies,
        custom_offer_terms=None,
    )

    if is_reverse or is_free:
        await state.update_data(start_price=0, min_start=None, max_start=None)
        await message.answer(
            USER_MESSAGES.get(
                "add_comment",
                "Введите комментарий к лоту или '-' если не нужен:",
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_comment)
        return

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, currency)
    max_allowed = max(min_allowed, max_allowed)

    emoji = _cur_emoji(currency)
    step = _cur_step(currency)

    await message.answer(
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} {emoji}</b>\n"
        f"({hint})\n"
        f"Шаг цены: {step} ({_currency_label(currency)})\n\n"
        f"Введите стартовую цену (целое число):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await state.set_state(UserAddLotFSM.waiting_for_start_price)


@router.message(StateFilter(UserAddLotFSM.waiting_for_custom_offer_terms), F.text)
async def addlot_custom_offer_terms(message: types.Message, state: FSMContext):
    terms = (message.text or "").strip()
    if terms.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        await message.answer("Создание лота отменено.", reply_markup=ReplyKeyboardRemove())
        return
    if len(terms) < 3:
        await message.answer("Опишите варианты подробнее, минимум 3 символа.")
        return
    if len(terms) > 500:
        await message.answer("Описание слишком длинное. Максимум 500 символов.")
        return

    await state.update_data(
        currency="чашки",
        accepted_currencies=["чашки", "алмазы"],
        custom_offer_terms=terms,
        start_price=0,
        min_start=None,
        max_start=None,
    )
    await message.answer(
        USER_MESSAGES.get(
            "add_comment",
            "Введите комментарий к лоту или '-' если не нужен:",
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_comment)


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_subscription")
async def cb_subscription_menu_from_decks(call: types.CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_decks"),
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_subscription_back_decks")
async def cb_subscription_back_decks(call: types.CallbackQuery, state: FSMContext):
    decks = await get_all_decks()
    await call.message.answer("Выберите колоду:", reply_markup=kb_decks(decks))
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_deck),
    F.data.in_(["user_subscription_gold", "user_subscription_premium"]),
)
async def cb_subscription_choose_type_from_decks(call: types.CallbackQuery, state: FSMContext):
    allowed = await _check_service_addlot_access(call, state)
    if not allowed:
        return

    plan = "gold" if call.data == "user_subscription_gold" else "premium"
    await _start_subscription_period_step(
        call,
        state,
        plan=plan,
        back_cb="user_subscription_back_decks",
    )
@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_friends_plus")
async def cb_friends_plus_from_decks(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return
        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}]
        if scheduled_user_lots:
            last_lot = max(scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time")))
            date_str = last_lot["end_time"].strftime("%d.%m.%Y")
            start = last_lot["start_time"].strftime("%H:%M")
            end = last_lot["end_time"].strftime("%H:%M")
            next_possible_time = last_lot["end_time"] + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    media_id = _service_media_file_id("friends_plus")
    await state.update_data(
        deck_id=None,
        card_id=None,
        rarity="any",
        deck_type=None,
        card_name="Друзья+",
        service="friends_plus",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()

@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_progress_slots")
async def cb_progress_slots_from_decks(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return
        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}]
        if scheduled_user_lots:
            last_lot = max(scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time")))
            date_str = last_lot["end_time"].strftime("%d.%m.%Y")
            start = last_lot["start_time"].strftime("%H:%M")
            end = last_lot["end_time"].strftime("%H:%M")
            next_possible_time = last_lot["end_time"] + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    media_id = _service_media_file_id("progress_slots")
    await state.update_data(
        deck_id=None,
        card_id=None,
        rarity="any",
        deck_type=None,
        card_name="Слоты прогресса",
        service="progress_slots",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()

@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_own_custom")
async def cb_show_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Выберите один из вариантов:", reply_markup=kb_presets_menu())
    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_deck_back")
async def cb_presets_back(call: types.CallbackQuery, state: FSMContext):
    # подставь свою функцию получения колод
    decks = await get_all_decks()  # или твой источник
    await call.message.answer("Выберите колоду:", reply_markup=kb_decks(decks))
    await state.set_state(UserAddLotFSM.waiting_for_deck)
    await call.answer()


# 3) ВЫБОР КОНКРЕТНОЙ КОЛОДЫ (строго фильтруем начало)
@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data.startswith("user_deck_"))
async def user_choose_deck(call: types.CallbackQuery, state: FSMContext):
    # тут твоя старая логика, НО без ветки own_custom
    deck_id = int(call.data.split("_")[2])
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return

        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}]
        if scheduled_user_lots:
            last_lot = max(scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time")))
            date_str = last_lot["end_time"].strftime("%d.%m.%Y")
            start = last_lot["start_time"].strftime("%H:%M")
            end = last_lot["end_time"].strftime("%H:%M")
            next_possible_time = last_lot["end_time"] + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    await state.update_data(deck_id=deck_id)
    cards = await get_cards_by_deck(deck_id)
    keyboard = [[
        types.InlineKeyboardButton(
            text=f"{c['num']}. {c['hero_name']} ({c['rarity']})",
            callback_data=f"user_card_{c['card_id']}"
        )
    ] for c in (cards or [])]
    keyboard.append([types.InlineKeyboardButton(
        text=f"Вся колода №{deck_id}", callback_data=f"user_all_deck_{deck_id}"
    )])
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await call.message.answer("Выберите карту или «Вся колода»:", reply_markup=kb)
    await state.set_state(UserAddLotFSM.waiting_for_card)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_card), F.data.startswith("user_card_"))
async def user_choose_concrete_card(call: types.CallbackQuery, state: FSMContext):
    card_id = int(call.data.split("_")[-1])
    card = await get_card_by_id(card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return

    try:
        await call.answer()
    except Exception:
        pass

    card_name = card.get("card_name") or card.get("hero_name") or f"Card #{card_id}"
    deck_id = int(card.get("deck_id")) if card.get("deck_id") else None
    rarity = _norm_rarity(card.get("rarity"))
    deck_type = (card.get("deck_type") or "").strip().lower()

    if deck_type not in {"roulette", "resource"} and deck_id:
        decks = await get_all_decks()
        for d in decks or []:
            if int(d.get("deck_id")) == deck_id:
                dt = (d.get("deck_type") or "").strip().lower()
                if dt in {"roulette", "resource"}:
                    deck_type = dt
                break

    await state.update_data(
        card_id=card_id,
        card_name=card_name,
        deck_id=deck_id,
        deck_type=deck_type,
        rarity=rarity
    )

    await _ask_for_currency(call.message, state)


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_card), F.data.startswith("user_all_deck_"))
async def user_choose_all_deck(call: types.CallbackQuery, state: FSMContext):
    deck_id = int(call.data.split("_")[-1])
    deck_type = None
    decks = await get_all_decks()
    for d in decks or []:
        if int(d.get("deck_id")) == deck_id:
            dt = (d.get("deck_type") or "").strip().lower()
            if dt in {"roulette", "resource"}:
                deck_type = dt
            break
    exchange_deck_ids = await _get_exchange_deck_ids(decks)
    cover_id = await _exchange_deck_cover_id(deck_id) if deck_id in exchange_deck_ids else None

    await state.update_data(
        card_id=None,
        card_name=f"Вся колода №{deck_id}",
        hero_name=f"Вся колода №{deck_id}",
        deck_id=deck_id,
        deck_type=deck_type,
        image_id=cover_id,
        image_file_id=cover_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_bronze")
async def user_choose_any_bronze(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая бронзовая",
        hero_name="Лот от игрока",
        rarity="bronze",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["bronze"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_silver")
async def user_choose_any_silver(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая серебряная",
        hero_name="Лот от игрока",
        rarity="silver",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["silver"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_gold")
async def user_choose_any_gold(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая золотая",
        hero_name="Лот от игрока",
        rarity="gold",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["gold"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_diamond")
async def user_choose_any_diamond(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая алмазная",  # если хочешь — переименуешь в "Любой эпик", но это уже не про фото
        hero_name="Лот от игрока",
        rarity="diamond",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["diamond"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_card")
async def user_choose_any_card(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая карта",
        hero_name="Лот от игрока",
        rarity="any",
        card_id=None,
        image_id=ANY_CARD_VIDEO_ID,  # ← ВОТ ЭТОГО У ТЕБЯ НЕ ХВАТАЛО
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_deck")
async def user_choose_any_deck(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая колода",
        hero_name="Лот от игрока",
        service="deck",
        rarity="any",
        card_id=None,
        image_id=ANY_DECK_PHOTO_ID,  # ← И ЭТОГО ТОЖЕ
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_custom")
async def user_choose_custom(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Напишите вручную, какой лот вы хотите выставить (текстом):")
    await state.set_state(UserAddLotFSM.waiting_for_custom_card)
    await call.answer()


@router.message(StateFilter(UserAddLotFSM.waiting_for_custom_card))
async def user_process_custom_card(message: types.Message, state: FSMContext):
    name = message.text.strip()
    rarity = _norm_rarity(name)
    await state.update_data(card_id=None, card_name=name, rarity=rarity)
    await _ask_for_currency(message, state)


@router.message(StateFilter(UserAddLotFSM.waiting_for_start_price), F.text.regexp(r"^\d+$"))
async def addlot_start_price(message: types.Message, state: FSMContext):
    price = int(message.text)
    data = await state.get_data()

    min_start = int(data.get("min_start", 2))
    max_start = max(min_start, int(data.get("max_start", 30 ** 9)))
    currency = data.get("currency", "алмазы")
    emoji = _cur_emoji(currency)
    step = _cur_step(currency)

    if not (min_start <= price <= max_start):
        if min_start == max_start:
            await message.answer(
                f"Недопустимая цена. Разрешённое значение: <b>{min_start} {emoji}</b>.",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"Недопустимая цена. Разрешённый диапазон: <b>{min_start}–{max_start} {emoji}</b>.",
                parse_mode="HTML"
            )
        return

    if price % step != 0:
        await message.answer(f"Цена должна быть кратна {step}.")
        return

    await state.update_data(start_price=price)
    await message.answer(
        USER_MESSAGES.get("add_comment", "Введите комментарий к лоту или '-' если не нужен:")
    )
    await state.set_state(UserAddLotFSM.waiting_for_comment)


@router.message(StateFilter(UserAddLotFSM.waiting_for_start_price))
async def addlot_price_invalid(message: types.Message):
    await message.answer("Введите целое число без пробелов и символов.")

@router.message(StateFilter(UserAddLotFSM.waiting_for_comment), F.text)
async def addlot_comment(message: types.Message, state: FSMContext):
    comment = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await state.update_data(comment=comment)
    d = await state.get_data()

    # сервисные лоты (кручения/услуги) — вопрос про крафт не нужен
    if d.get("service"):
        await state.update_data(craft_uid_possible=None)

        currency = d.get("currency", "алмазы")
        emoji = _cur_emoji(currency)
        kind_key = str(d.get("auction_kind") or "standard").strip().lower()
        accepted_label = html.escape(
            currency_choices_label(d.get("accepted_currencies"), fallback=currency, custom_terms=d.get("custom_offer_terms"))
        )

        if d.get("service") == "spins":
            lot_title = f"Кручения ({d.get('spins_qty')} шт.)"
        else:
            lot_title = str(d.get("card_name") or "Лот")

        if kind_key == AuctionKind.REVERSE.value:
            price_line = (
                f"Валюта ставок: {accepted_label}\n"
                "Побеждает минимальная ставка.\n"
            )
        elif kind_key == AuctionKind.FREE.value:
            price_line = f"Принимаются предложения: {accepted_label}\n"
        else:
            price_line = f"Минимальная ставка: {d.get('start_price')} {emoji}\n"

        preview = (
            f"<b>Лот:</b> {html.escape(lot_title)}\n"
            f"{price_line}"
            f"Комментарий: {html.escape(comment or '-')}\n"
            "Всё верно? Отправить заявку на модерацию?"
        )

        kb = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="✅ Подтвердить"),
                       types.KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await message.answer(preview, reply_markup=kb, parse_mode="HTML")
        await state.set_state(UserAddLotFSM.waiting_for_confirmation)
        return

    # обычный лот (карта) — спрашиваем про крафт на UID
    await message.answer(
        "Возможен ли <b>крафт на UID</b> для этого лота?\n"
        "Выберите кнопку ниже:",
        reply_markup=craft_uid_kb(),
        parse_mode="HTML",
    )
    await state.set_state(UserAddLotFSM.waiting_for_craft_uid)


@router.message(StateFilter(UserAddLotFSM.waiting_for_confirmation), F.text.in_(["✅ Подтвердить", "да"]))
async def user_addlot_confirm(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    is_lux = await is_luxury_user(user_id)
    user = await get_user(user_id)
    is_trusted = bool(user and user.get("is_trusted"))

    data = await state.get_data()  # <— добавили
    if not (is_lux or is_trusted) and not data.get("service"):
        await message.answer(
            "Почти готово! Пришлите ОДНО фото вашей подарочной карты или целой КОЛОДЫ одним фото для подтверждения:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_proof_photo_final)
    else:
        comment = remove_usernames((data.get("comment") or "").strip())
        await _final_addlot_create(
            message,
            user_id=user_id,
            card_id=data.get("card_id"),
            hero_name=data.get("hero_name"),
            card_name=data.get("card_name"),
            start_price=int(data.get("start_price") or 0),
            currency=data.get("currency") or "алмазы",
            accepted_currencies=data.get("accepted_currencies"),
            custom_offer_terms=data.get("custom_offer_terms"),
            comment=comment,
            image_file_id=data.get("image_id") or data.get("image_file_id"),
            auction_kind=data.get("auction_kind") or "standard",
            craft_uid_possible=data.get("craft_uid_possible"),
            proof_photo_id=None,
        )
        await state.clear()
        await message.answer(
            USER_MESSAGES.get("commands_info", "Главное меню:"),
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )


@router.message(StateFilter(UserAddLotFSM.waiting_for_confirmation), F.text.in_(["❌ Отмена", "нет"]))
async def user_addlot_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(USER_MESSAGES.get("lot_creation_canceled", "Создание лота отменено."),
                         reply_markup=ReplyKeyboardRemove())
    await message.answer(USER_MESSAGES.get("commands_info", "Главное меню:"), parse_mode="HTML")


@router.message(StateFilter(UserAddLotFSM.waiting_for_confirmation))
async def addlot_confirm_invalid(message: types.Message):
    await message.answer("Пожалуйста, выберите действие кнопкой.")


@router.message(StateFilter(UserAddLotFSM.waiting_for_proof_photo_final), F.photo)
async def user_addlot_proof_final(message: types.Message, state: FSMContext, bot: Bot):
    proof_photo_id = message.photo[-1].file_id
    data = await state.get_data()
    comment = remove_usernames((data.get("comment") or "").strip())

    await _final_addlot_create(
        message,
        user_id=message.from_user.id,
        card_id=data.get("card_id"),
        hero_name=data.get("hero_name"),
        card_name=data.get("card_name"),
        start_price=int(data.get("start_price") or 0),
        currency=data.get("currency") or "алмазы",
        accepted_currencies=data.get("accepted_currencies"),
        custom_offer_terms=data.get("custom_offer_terms"),
        comment=comment,
        image_file_id=data.get("image_id") or data.get("image_file_id"),
        auction_kind=data.get("auction_kind") or "standard",
        craft_uid_possible=data.get("craft_uid_possible"),
        proof_photo_id=proof_photo_id,
    )

    await state.clear()
    await message.answer(
        USER_MESSAGES.get("commands_info", "Главное меню:"),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(StateFilter(UserAddLotFSM.waiting_for_proof_photo_final))
async def user_addlot_proof_required(message: types.Message):
    await message.answer("Пожалуйста, пришлите фото подарочной карты (без него заявку не примут).")


async def _send_user_pending_lot_preview(
        message: types.Message,
        *,
        auction_id: int,
        auction_kind: str,
        start_price: int,
        currency: str,
        accepted_currencies: list[str] | tuple[str, ...] | None,
        custom_offer_terms: str | None = None,
        image_file_id: str | None = None,
        comment: str | None = None,
) -> None:
    """Красивое подтверждение пользователю: фото + инфо по заявке."""

    # лейблы типов (как у админки)
    kind_map = {
        "standard": "⭐️ Стандартный",
        "reverse": "✨ Обратный",
        "fast": "⚡️ Быстрый",
        "free": "🪶 Свободный",
        "black": "👑 Чёрный",
        "exchange": "🛒 Биржа",
    }
    kind_key = (auction_kind or "").strip().lower()
    kind_label = kind_map.get(kind_key, auction_kind)
    currencies_preview = currency_choices_label(
        accepted_currencies,
        fallback=currency,
        custom_terms=custom_offer_terms,
    )
    if kind_key == AuctionKind.REVERSE.value:
        price_preview = (
            f"Валюта ставок: <b>{html.escape(currencies_preview)}</b>\n"
            "Ставки идут на понижение"
        )
    elif kind_key == AuctionKind.FREE.value:
        price_preview = (
            "Принимаются предложения: "
            f"<b>{html.escape(currencies_preview)}</b>"
        )
    else:
        price_preview = (
            f"Цена старта: <b>{int(start_price)}</b> (мин. ставка) "
            f"{_emoji_by_currency(currency)}"
        )

    # подтянем расширенный контекст по лоту (карта/колода/редкость/цитата и т.д.)
    ctx = await load_full_auction_ctx(int(auction_id))
    luxury_level = 0
    try:
        luxury_level = await get_user_luxury_level(message.bot, message.from_user.id)
    except Exception:
        luxury_level = 0

    user_status = "🙂 Обычный"
    if luxury_level >= 2:
        user_status = "👑 Лакшери 2"
    elif luxury_level == 1:
        user_status = "👑 Лакшери 1"
    auction = (ctx or {}).get("auction") or {}
    card = (ctx or {}).get("card") or {}
    deck = (ctx or {}).get("deck") or {}

    hero = (auction.get("hero_name") or card.get("hero_name") or "").strip()
    ctitle = (auction.get("card_name") or card.get("card_name") or "").strip()

    title_line = "—"
    if hero and ctitle:
        title_line = f"{html.escape(hero)} — {html.escape(ctitle)}"
    elif ctitle:
        title_line = html.escape(ctitle)
    elif hero:
        title_line = html.escape(hero)

    cur = (currency or auction.get("currency") or "").strip().lower()
    cur_emoji = _emoji_by_currency(cur)  # уже есть в imports
    start_i = int(start_price or auction.get("start_price") or 0)

    # “Продано ранее”
    sold_cnt = 0
    try:
        cid = card.get("card_id")
        if cid:
            sold_cnt = int(await count_sold_by_card_id(int(cid)))
        elif hero and ctitle:
            sold_cnt = int(await count_sold_same_card(hero_name=hero, card_name=ctitle))
    except Exception:
        sold_cnt = 0

    # Колода
    deck_id = deck.get("deck_id")
    deck_name = (deck.get("name") or "").strip()
    if deck_id and deck_name:
        deck_line = f"Колода: 🃏 {int(deck_id)} колода — {html.escape(deck_name)}"
    elif deck_id:
        deck_line = f"Колода: 🃏 {int(deck_id)} колода"
    else:
        deck_line = "Колода: —"

    rarity = (card.get("rarity") or "").strip()
    # Редкость
    if not rarity:
        try:
            inferred = await _get_rarity_from_state_or_db({
                "card_name": ctitle or auction.get("card_name") or "",
                "hero_name": hero or auction.get("hero_name") or "",
                "card_id": card.get("card_id"),
            })
            if inferred:
                rarity = inferred
        except Exception:
            pass

    if rarity:
        rkey = str(rarity).strip().lower()
        rarity_ru = {
            "bronze": "бронза",
            "silver": "серебро",
            "gold": "золото",
            "diamond": "эпик",
            "epic": "эпик",
            "алмазная": "эпик",
            "серебряная": "серебро",
            "бронзовая": "бронза",
            "золотая": "золото",
        }.get(rkey, rarity)

        rarity_emoji = {
            "bronze": "🟫",
            "silver": "🟦",
            "gold": "🟨",
            "diamond": "🔷",
            "epic": "🔷",
        }.get(rkey, "")

        rarity_line = f"Редкость: 🏷️ {html.escape((rarity_emoji + ' ' + rarity_ru).strip())}"
    else:
        rarity_line = "Редкость: 🏷️ —"

    craft_val = auction.get("craft_uid_possible")
    if craft_val is True:
        craft_line = "Крафт на UID возможен: 🆔 ✅ Да"
    elif craft_val is False:
        craft_line = "Крафт на UID возможен: 🆔 ❌ Нет"
    else:
        craft_line = "Крафт на UID возможен: 🆔 —"

    # Подарок (obtain_type/obtain_amount)
    gift_line = "При получении в подарок даёт: 🎁 —"
    try:
        ot = str(card.get("obtain_type") or "").strip().lower()
        amt = int(card.get("obtain_amount") or 0)
        if ot and amt > 0:
            em = {"diamonds": "💎", "cups": "🍵", "treasures": "🪙"}.get(ot, "💰")
            gift_line = f"При получении в подарок даёт: 🎁 +{amt} {em}"
    except Exception:
        pass

    story = (card.get("story") or "").strip()
    quote = (card.get("quote") or "").strip()

    story_line = f"История: 📜 {html.escape(story)}" if story else "История: 📜 —"
    quote_line = f"Цитата: 💬 {html.escape(quote)}" if quote else ""

    # Коммент (если есть)
    clean_comment = _tg_clean(comment or "").strip() if comment else ""
    comment_line = f"Комментарий: 💬 {html.escape(clean_comment)}" if clean_comment else ""

    # Короткий caption под фото (чтобы не упереться в лимит)
    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"<b>Лот №{int(auction_id)}</b>\n"
        f"⚙️ Тип: {html.escape(kind_label)}\n\n"
        f"<b>{title_line}</b>\n"
        f"{(price_preview + chr(10) + chr(10)) if price_preview else ''}"
        "⏳ Дата и время: будет назначено после модерации"
    )

    # Полная инфа отдельным сообщением (чтобы и красиво, и без лимитов)
    details_lines = [
        f"👤 Статус пользователя: <b>{user_status}</b>",
        deck_line,
        rarity_line,
        craft_line,
        f"Продано ранее: 📊 <b>{int(sold_cnt)}</b>",
        gift_line,
        story_line,
    ]
    if quote_line:
        details_lines.append(quote_line)
    if comment_line:
        details_lines.append(comment_line)

    details_lines.append("Оплата ставки в течение месяца.")
    details_text = "\n".join(details_lines)

    photo_id = image_file_id or auction.get("image_id") or card.get("image_id")

    if photo_id:
        try:
            sent_ok = await _answer_media_any(
                message,
                file_id=photo_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove(),
            )
            if sent_ok:
                await message.answer(details_text, parse_mode="HTML")
                return
        except Exception:
            pass

    await message.answer(
        caption + "\n\n" + details_text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _user_status_label(user_id: int) -> str:
    # админа показываем отдельной “короной власти”, если хочешь
    try:
        if await is_admin(user_id):
            return "🛡️ Админ"
    except Exception:
        pass

    lvl = 0
    try:
        lvl = int(await get_user_luxury_level(user_id))
    except Exception:
        lvl = 0

    if lvl >= 2:
        return "👑 Лакшери 2"
    if lvl == 1:
        return "👑 Лакшери 1"
    badge = await _uid_verification_badge(int(user_id))
    return f"👤 Обычный • {badge}"


async def _lux_badge(user_id: int) -> str:
    # если где-то в файле используешь _lux_badge – пусть живёт
    return await _user_status_label(user_id)


async def _final_addlot_create(
        message: types.Message,
        *,
        user_id: int,
        card_id: int | None,
        hero_name: str | None,
        card_name: str | None,
        start_price: int,
        currency: str,
        accepted_currencies: list[str] | tuple[str, ...] | None,
        custom_offer_terms: str | None,
        comment: str,
        image_file_id: str | None = None,
        auction_kind: str,
        craft_uid_possible: bool | None = None,
        proof_photo_id: str | None = None,
) -> None:
    kind = AuctionKind.from_raw(auction_kind)
    normalized_choices = normalize_currency_choices(accepted_currencies, fallback=currency)
    if not normalized_choices:
        normalized_choices = normalize_currency_choices([currency])
    accepted_currencies = [choice.value for choice in normalized_choices]
    if kind in {AuctionKind.FREE, AuctionKind.REVERSE}:
        if not accepted_currencies or any(
            value not in {"чашки", "алмазы"} for value in accepted_currencies
        ):
            await message.answer("❌ Для этого типа доступны только чай, алмазы или оба варианта.")
            return
        if len(accepted_currencies) > 2:
            await message.answer("❌ Можно выбрать не больше двух валют.")
            return
        if kind is AuctionKind.REVERSE and custom_offer_terms:
            await message.answer("❌ Свои варианты доступны только для свободного аукциона.")
            return
    elif len(accepted_currencies) != 1:
        await message.answer("❌ Для этого типа аукциона можно выбрать только одну валюту.")
        return
    if kind not in {AuctionKind.REVERSE, AuctionKind.FREE} and int(start_price) <= 0:
        await message.answer("❌ Стартовая цена должна быть больше нуля.")
        return

    if card_id:
        auction_id = await add_pending_auction_by_card_id(
            card_id=card_id,
            start_price=start_price,
            currency=currency,
            accepted_currencies=list(accepted_currencies or [currency]),
            custom_offer_terms=custom_offer_terms,
            comment=comment,
            image_id=image_file_id,
            owner_id=user_id,
            auction_kind=auction_kind,
            proof_photo_id=proof_photo_id,
            craft_uid_possible=craft_uid_possible
        )
    else:
        auction_id = await add_pending_auction(
            owner_id=user_id,
            hero_name=hero_name or "",
            card_name=card_name or "",
            start_price=start_price,
            currency=currency,
            accepted_currencies=list(accepted_currencies or [currency]),
            custom_offer_terms=custom_offer_terms,
            comment=comment,
            image_id=image_file_id,
            auction_kind=auction_kind,
            proof_photo_id=proof_photo_id,
            craft_uid_possible=craft_uid_possible
        )

    if not auction_id:
        await message.answer("❌ Не удалось создать заявку. Попробуйте позже.")
        return

    # 1) Лог в БД (audit_logs)
    try:
        await log_admin_action(
            user_id=user_id,
            action_type="add_lot",
            auction_id=int(auction_id),
            details=(
                f"kind={auction_kind} currency={currency} "
                f"accepted={accepted_currencies or [currency]} custom={custom_offer_terms or '-'} start={start_price} "
                f"card='{card_name or '-'}' hero='{hero_name or '-'}' comment='{comment}'"
            ),
        )
    except Exception:
        pass

    # 2) Лог в админ-лог чаты (как раньше)
    try:
        from bot.handlers.admin.helper.new.utils import auction_kind_label

        bot = message.bot
        uname = (message.from_user.username or "").strip() if message.from_user else ""
        user_ref = f"@{html.escape(uname)}" if uname else f"<code>{user_id}</code>"

        kind_label = html.escape(auction_kind_label(auction_kind))
        cur_emoji = _emoji_by_currency(currency)
        accepted_label = html.escape(
            currency_choices_label(accepted_currencies, fallback=currency, custom_terms=custom_offer_terms)
        )

        lot_title = html.escape(str(card_name or "-"))
        hero_title = html.escape(str(hero_name or ""))

        lot_line = f"🎴 Лот №{int(auction_id)}: {lot_title}"
        if hero_title:
            lot_line += f" — {hero_title}"

        craft_val = craft_uid_possible

        if craft_val is True:
            craft_txt = "✅ Да"
        elif craft_val is False:
            craft_txt = "❌ Нет"
        else:
            craft_txt = "—"

        kind_key = str(auction_kind or "").strip().lower()
        if kind_key == AuctionKind.REVERSE.value:
            price_log_line = (
                f"💱 Валюта ставок: <b>{accepted_label}</b>\n"
                "📉 Побеждает минимальная ставка.\n"
            )
        elif kind_key == AuctionKind.FREE.value:
            price_log_line = f"💱 Принимаются предложения: <b>{accepted_label}</b>\n"
        else:
            price_log_line = f"💰 Старт: <b>{int(start_price)} {cur_emoji}</b>\n"

        log_text = (
            "🆕 <b>Новая заявка на лот</b>\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
            f"🙍‍♂️ Отправитель: {user_ref}\n"
            f"{lot_line}\n"
            f"⚙️ Тип: {kind_label}\n"
            f"{price_log_line}"
            f"🆔 Крафт на UID: {craft_txt}\n"
            f"📝 Комментарий: {_tg_clean(comment or '-')}\n"
            "Действие: add_lot через бота."
        )

        await send_admin_log(bot, log_text)
    except Exception:
        log.exception("add_lot admin-log failed")

    await _send_user_pending_lot_preview(
        message,
        auction_id=int(auction_id),
        auction_kind=auction_kind,
        start_price=start_price,
        currency=currency,
        accepted_currencies=accepted_currencies,
        custom_offer_terms=custom_offer_terms,
        image_file_id=image_file_id,
        comment=comment,
    )


@router.message(F.text.lower().in_(["отмена", "cancel", "/cancel"]))
async def cancel_any(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(USER_MESSAGES.get("action_cancelled", "Действие отменено."),
                         reply_markup=ReplyKeyboardRemove())


def remove_usernames(comment: str) -> str:
    if not comment:
        return ""
    return re.sub(r'@\w+', '', comment).strip()


def _resolve_channel(chat_id: Optional[int]) -> Any:
    """
    Возвращает chat-id для отправки:
      1) int из параметра, если валиден;
      2) username канала из конфигурации;
      3) иначе кидает ValueError.
    """
    if isinstance(chat_id, int) and chat_id != 0:
        return chat_id
    if AUCTION_CHANNEL_USERNAME:
        return AUCTION_CHANNEL_USERNAME  # строка типа '@your_channel'
    raise ValueError("Не задан channel_id и нет AUCTION_CHANNEL_USERNAME")


def _normalize_photo_arg(auction: dict) -> Optional[InputFile | str]:
    """
    Возвращает корректный аргумент для photo:
      - Telegram file_id (str) или URL (str)
      - InputFile для загрузки файла с диска
      - None, если фото прислать нельзя
    Фильтруем мусорные значения: None, '', 'DEFAULT_PHOTO_ID', 'null', 'None', 0.
    """
    raw = auction.get("image_id") or auction.get("image") or auction.get("photo_id")
    # иногда в базу пишут строковые "None"/"null"/"0" — вычистим
    bad = {None, "", "DEFAULT_PHOTO_ID", "None", "null", "0", 0}
    if raw in bad:
        return None

    # если словарь с путём до файла
    if isinstance(raw, dict) and "path" in raw:
        try:
            return InputFile(raw["path"])
        except Exception:
            return None

    # если уже InputFile
    if isinstance(raw, InputFile):
        return raw

    # если строка: оставляем как есть (file_id или URL)
    if isinstance(raw, str):
        raw = raw.strip()
        if raw and raw not in bad:
            return raw

    # ничего пригодного
    return None


def _caption_for_telegram(caption: str, for_photo: bool) -> Tuple[str, bool]:
    """
    Возвращает (caption, ok_for_photo).
    Для фото Telegram ограничивает подпись ~1024 символами,
    для обычного сообщения — около 4096.
    Если подпись длинная, принудительно уйдём в send_message.
    """
    if not isinstance(caption, str):
        return "", True
    if for_photo and len(caption) > 1000:
        # чуть запас, чтобы не получить Bad Request
        return caption, False
    return caption, True


def _iter_admin_log_chats() -> list[int]:
    out = []
    try:
        for x in ADMIN_LOG_CHATS:
            if isinstance(x, int):
                out.append(x)
    except Exception:
        pass
    try:
        if isinstance(LOG_CHAT_ID, int):
            out.append(LOG_CHAT_ID)
    except Exception:
        pass
    # уникализируем
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


async def _log_admin(bot: Bot, text: str) -> None:
    for chat_id in _iter_admin_log_chats():
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


def _kb_winner_actions(aid: int, wid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{aid}:{wid}")],
        [
            InlineKeyboardButton(text="✎ Исправить стоимость", callback_data=f"{CB_WIN_EDIT_AMT}:{aid}:{wid}"),
            InlineKeyboardButton(text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{aid}:{wid}"),
        ],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{aid}:{wid}")],
    ])


def _resolve_target_channel() -> str | int:
    """
    Возвращает chat_id для публикации:
      -100... (int) если задан AUCTION_CHANNEL_ID,
      '@username' если задан AUCTION_CHANNEL_USERNAME.
    """
    if AUCTION_CHANNEL_ID:
        return int(AUCTION_CHANNEL_ID)
    if AUCTION_CHANNEL_USERNAME:
        return "@" + AUCTION_CHANNEL_USERNAME.lstrip("@")
    raise RuntimeError("Не задан ни AUCTION_CHANNEL_ID, ни AUCTION_CHANNEL_USERNAME")


def _build_msg_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    """
    Возвращает t.me ссылку на пост канала.
    Для публичного канала используем @username, для приватного — форму /c/<id>/<msg>.
    """
    if username:
        return f"https://t.me/{username}/{message_id}"
    # приватный канал: -100xxxxxxxxxx → c/xxxxxxxxxx/<msg>
    cid = str(chat_id)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"
    return None


async def publish_auction_lot(
        bot: Bot,
        auction: dict,
        channel_id: int | str = AUCTION_CHANNEL_ID,
        lot_number: Optional[int] = None,
):
    """
    Публикация лота в канал:
      - рендерит подпись из БД (auctions+cards+decks) без упоминаний юзернеймов
      - отправляет фото (если можно) или текст
      - обновляет message_id и статус
      - запускает фоновую вставку «Правила ставок» в обсуждение

    Требуются помощники:
      get_owner_refs, remove_usernames, load_full_auction_ctx, render_auction_caption,
      _resolve_channel, _normalize_photo_arg, _caption_for_telegram,
      update_lot_field, update_auction_status, _post_rules_under_lot
    """
    logger = logging.getLogger("auction")
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    auction_id = auction.get("auction_id")
    logger.info("publish_auction_lot: старт для auction_id=%s", auction_id)

    # уже опубликовано
    if auction.get("message_id"):
        logger.info("publish_auction_lot: уже опубликовано, message_id=%s", auction["message_id"])
        return auction["message_id"]

    # вычислим количество владельцев (без раскрытия имён)
    try:
        owners = await get_owner_refs(auction_id)
        owners_list = [o.strip() for o in str(owners).split(",")] if owners else []
        owners_count = len({o for o in owners_list if o}) or 1
    except Exception as e:
        logger.warning("publish_auction_lot: ошибка при получении владельцев: %s", e)
        owners_count = 1

    # комментарий без юзернеймов
    comment_raw = auction.get("comment") or "-"
    comment_clean = remove_usernames(comment_raw)

    # расширенный контекст из БД
    ctx = await load_full_auction_ctx(auction_id)
    full_auction = ctx.get("auction") or {}
    full_card = ctx.get("card") or {}
    full_deck = ctx.get("deck") or {}

    try:
        card_id = full_card.get("card_id") or full_auction.get("card_id")
        if card_id:
            full_auction["sold_count"] = await count_sold_by_card_id(card_id=int(card_id))
        else:
            hn = (full_auction.get("hero_name") or full_card.get("hero_name") or "").strip()
            cn = (full_auction.get("card_name") or full_card.get("card_name") or "").strip()
            if hn and cn:
                full_auction["sold_count"] = await count_sold_same_card(hero_name=hn, card_name=cn)
    except Exception as e:
        logger.warning("publish_auction_lot: не удалось посчитать sold_count: %s", e)

    # подстраховка критичных полей
    full_auction.setdefault("end_time", auction.get("end_time"))
    full_auction.setdefault("hero_name", auction.get("hero_name"))
    full_auction.setdefault("card_name", auction.get("card_name"))
    full_auction.setdefault("currency", auction.get("currency"))
    full_auction.setdefault("start_price", auction.get("start_price"))
    if not full_auction.get("comment"):
        full_auction["comment"] = comment_clean

    # рендер подписи
    caption = render_auction_caption(
        full_auction,
        card=full_card,
        deck=full_deck,
        owners_count=owners_count,
        show_min_bid=True,
    )

    # получатель
    try:
        target_chat = _resolve_channel(channel_id)
    except ValueError as e:
        logger.error("publish_auction_lot: %s", e)
        return None

    # фото/текст
    photo_arg = (
            _normalize_photo_arg(full_auction)
            or _normalize_photo_arg(full_card)
            or _normalize_photo_arg(auction)
    )
    caption_for_photo, ok_for_photo = _caption_for_telegram(caption, for_photo=True)

    msg = None
    try:
        if photo_arg and ok_for_photo:
            try:
                msg = await _bot_send_media_any(
                    bot,
                    chat_id=target_chat,
                    file_id=photo_arg,
                    caption=caption_for_photo,
                    parse_mode="HTML",
                    disable_notification=False,
                )
                if msg:
                    logger.info(
                        "publish_auction_lot: send_media ok, message_id=%s",
                        getattr(msg, "message_id", None),
                    )
            except Exception as e:
                logger.warning("publish_auction_lot: send_media не удалось (%s) -> fallback send_message", e)

        if msg is None:
            msg = await bot.send_message(
                chat_id=target_chat,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
                disable_notification=False,
            )
            logger.info("publish_auction_lot: send_message ok, message_id=%s", getattr(msg, "message_id", None))

        # фиксация публикации
        if msg and getattr(msg, "message_id", None):
            try:
                await update_lot_field(auction_id, "message_id", msg.message_id)
            except Exception as e:
                logger.warning("publish_auction_lot: не удалось сохранить message_id: %s", e)

            try:
                await update_auction_status(auction_id, "active")
            except Exception as e:
                logger.warning("publish_auction_lot: не удалось обновить статус: %s", e)

            # лог-ссылка, куда опубликовано
            try:
                chat_obj = await bot.get_chat(msg.chat.id)
                uname = getattr(chat_obj, "username", None)
                link = (
                    f"https://t.me/{uname}/{msg.message_id}" if uname else
                    (f"https://t.me/c/{str(msg.chat.id)[4:]}/{msg.message_id}" if str(msg.chat.id).startswith(
                        "-100") else None)
                )
                logger.info(
                    "publish_auction_lot: опубликовано в chat_id=%s, msg_id=%s, link=%s",
                    msg.chat.id, msg.message_id, link or "n/a",
                )
            except Exception:
                pass

            # правила под постом — в фоне, после привязки обсуждения
            try:
                asyncio.create_task(_post_rules_under_lot(bot, auction_id))
            except Exception as e:
                logger.warning("publish_auction_lot: не удалось разместить правила под лотом %s: %r", auction_id, e)

            return msg.message_id

        logger.error("publish_auction_lot: нет message_id после отправки")
        return None

    except Exception as e:
        logger.error("publish_auction_lot: критическая ошибка публикации: %s", e, exc_info=True)
        return None


async def get_lot_number_for_day(auction: dict) -> int:
    all_day_lots = await list_auctions(["active", "scheduled", "pending"])
    my_day = auction['start_time'].date()
    day_lots = [a for a in all_day_lots if a['start_time'].date() == my_day]
    day_lots = sorted(day_lots, key=lambda a: a['start_time'])
    for idx, lot in enumerate(day_lots, 1):
        if lot['auction_id'] == auction['auction_id']:
            return idx
    return 1


async def find_discussion_message_id(bot: Bot, discussion_chat_id: int, channel_id: int, channel_msg_id: int,
                                     search_limit: int = 200):
    try:
        async for msg in bot.get_chat_history(discussion_chat_id, limit=search_limit):
            if (
                    getattr(msg, 'forward_from_chat', None)
                    and msg.forward_from_chat.id == channel_id
                    and msg.forward_from_message_id == channel_msg_id
            ):
                print(f"[find_discussion_message_id] Найден discussion_message_id: {msg.message_id}")
                return msg.message_id
    except Exception as e:
        print(f"[find_discussion_message_id] Ошибка поиска пересланного сообщения: {e}")
    print("[find_discussion_message_id] Не найден пересланный пост из канала!")
    return None

async def _release_auction_publish_claim(auction_id: int) -> None:
    await execute(
        """
        UPDATE public.auctions
        SET status = 'scheduled'
        WHERE auction_id = $1
          AND status = 'publishing'
          AND message_id IS NULL
        """,
        int(auction_id),
    )
async def _claim_auction_for_publish(auction_id: int) -> bool:
    row = await fetchrow(
        """
        UPDATE public.auctions
        SET status = 'publishing'
        WHERE auction_id = $1
          AND status = 'scheduled'
          AND message_id IS NULL
        RETURNING auction_id
        """,
        int(auction_id),
    )
    return bool(row)


async def _release_publish_claim(auction_id: int) -> None:
    await execute(
        """
        UPDATE public.auctions
        SET status = 'scheduled'
        WHERE auction_id = $1
          AND status = 'publishing'
          AND message_id IS NULL
        """,
        int(auction_id),
    )

async def auction_publisher_loop(bot: Bot):
    while True:
        now = utc_now()
        try:
            released = await release_stale_unpublished_lots()
            if released:
                logger.error(
                    "auction_publisher_loop: marked stale unpublished lots as publication_failed: %s",
                    released,
                )
        except Exception:
            logger.exception("auction_publisher_loop: stale publication cleanup failed")

        auctions = await list_auctions(["scheduled"])

        for auction in auctions:
            auction_id = int(auction["auction_id"])
            start_time = auction.get("start_time")

            if (
                auction.get("message_id")
                or not start_time
                or ensure_utc(start_time) > now
            ):
                continue

            try:
                claimed = await _claim_auction_for_publish(auction_id)
                if not claimed:
                    continue

                lot_number = await get_lot_number_for_day(auction)
                msg_id = await publish_auction_lot(
                    bot,
                    auction,
                    channel_id=AUCTION_CHANNEL_ID,
                    lot_number=lot_number,
                )

                if not msg_id:
                    await _release_auction_publish_claim(auction_id)

            except Exception:
                logger.exception(
                    "auction_publisher_loop: ошибка публикации auction_id=%s",
                    auction_id,
                )
                try:
                    await _release_auction_publish_claim(auction_id)
                except Exception:
                    logger.exception(
                        "auction_publisher_loop: не удалось снять publishing claim auction_id=%s",
                        auction_id,
                    )

        await asyncio.sleep(30)


@router.message(Command("when"), F.chat.type == "private")
async def cmd_when(message: types.Message) -> None:
    # Разрешаем: админы и Лакшери
    from db.db import is_admin  # локальный импорт
    from collections import defaultdict
    from html import escape

    uid = message.from_user.id
    allowed = await is_admin(uid) or await is_luxury_user(uid)
    if not allowed:
        await message.answer(
            "Команда доступна только администраторам и Лакшери-пользователям.",
            protect_content=True,
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/when &lt;имя героя или card_id&gt;</code>",
            parse_mode="HTML",
            protect_content=True,
        )
        return

    # Поддержка суффикса "all" для показа архивных дат:
    raw = parts[1].strip()
    show_all = False
    if raw.lower().endswith(" all"):
        show_all = True
        raw = raw[:-4].strip()

    statuses = (
        ["pending", "scheduled", "active"]
        if not show_all
        else ["pending", "scheduled", "active", "finished"]
    )
    lots = await get_auctions_by_card_ref(raw, statuses=statuses)

    if not lots:
        await message.answer(
            "Ничего не найдено среди лотов с выбранной картой "
            "(card_id обязателен при оформлении).",
            protect_content=True,
        )
        return

    # группируем по дню
    by_day = defaultdict(list)
    for lot in lots:
        by_day[lot["start_time"].date()].append(lot)

    title = escape(raw)
    header = "🗓 Даты для карты" if raw.isdigit() else "🗓 Даты для героя"
    out = [f"{header} «{title}»:\n"]

    for d in sorted(by_day.keys()):
        out.append(f"<b>{d.strftime('%d.%m.%Y')}</b>")
        seen = set()
        for lot in sorted(by_day[d], key=lambda x: x["start_time"]):
            t = lot["start_time"].strftime("%H:%M")
            key = (t, lot.get("auction_id"))
            if key in seen:
                continue
            seen.add(key)

            deck_part = _deck_tag(lot.get("deck_id"))
            hero = escape(lot.get("hero_name") or "-")
            name = escape(lot.get("card_name") or "-")

            price = lot.get("start_price")
            cur = lot.get("currency")
            price_part = (
                f"  {price} {_emoji_by_currency(cur)}"
                if isinstance(price, int)
                else ""
            )

            out.append(f"{t} 🃏({hero}){deck_part} {name}{price_part}")
        out.append("")

    await message.answer(
        "\n".join(out).strip(),
        parse_mode="HTML",
        protect_content=True,
    )


def _slot_iter(day: date) -> List[datetime]:
    """
    Все стартовые моменты слотов в указанный день в пределах рабочего окна.
    """
    start_dt = datetime.combine(day, WORK_START)
    end_dt = datetime.combine(day, WORK_END)
    out = []
    cur = start_dt
    while cur <= end_dt:
        out.append(cur)
        cur += SLOT
    return out


# --- GAPS helpers (month/day parsing) -----------------------------------------

_MONTHS_RU = {
    "янв": 1, "январь": 1,
    "фев": 2, "февраль": 2,
    "мар": 3, "март": 3,
    "апр": 4, "апрель": 4,
    "май": 5,
    "июн": 6, "июнь": 6,
    "июл": 7, "июль": 7,
    "авг": 8, "август": 8,
    "сен": 9, "сентябрь": 9,
    "окт": 10, "октябрь": 10,
    "ноя": 11, "ноябрь": 11,
    "дек": 12, "декабрь": 12,
}


def _parse_gaps_day(s: Optional[str]) -> Optional[date]:
    """
    Парсит дату для /gaps (один день).
    Поддержка: DD.MM[.YYYY], DD/MM[(/YYYY)], DD-MM[-YYYY], YYYY-MM-DD, сегодня/завтра.
    Если год не указан — берём текущий, а если дата уже прошла — следующий год (удобнее для планирования).
    """
    if not s:
        return None

    raw = (s or "").strip().lower()
    if not raw:
        return None

    if raw in {"сегодня", "today"}:
        return date.today()
    if raw in {"завтра", "tomorrow"}:
        return date.today() + timedelta(days=1)

    # YYYY-MM-DD
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        pass

    import re

    m = re.match(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$", raw)
    if not m:
        return None

    dd = int(m.group(1))
    mm = int(m.group(2))
    yy_raw = m.group(3)

    # год не указан
    if yy_raw is None:
        base_year = date.today().year
        try:
            cand = date(base_year, mm, dd)
        except Exception:
            return None
        if cand < date.today():
            cand = date(base_year + 1, mm, dd)
        return cand

    # год указан
    yy = int(yy_raw)
    if yy < 100:
        yy += 2000

    try:
        return date(yy, mm, dd)
    except Exception:
        return None


def _month_bounds(s: Optional[str]) -> Tuple[datetime, datetime, str]:
    """
    Парсит месяц для /gaps.
    Поддержка: YYYY-MM, MM.YYYY, MM-YYYY, YYYY/MM, MM, 'январь'/'янв' и т.п.
    Если год не указан — берём текущий; если месяц уже прошёл — следующий год (для планирования).
    """
    t = date.today()
    y = t.year
    m = t.month

    if s:
        raw = s.strip().lower()
        raw = raw.replace("/", "-").replace(".", "-")

        import re

        if raw in _MONTHS_RU:
            m = _MONTHS_RU[raw]
            if m < t.month:
                y += 1
        elif re.fullmatch(r"\d{4}-\d{1,2}", raw):
            y, m = map(int, raw.split("-", 1))
        elif re.fullmatch(r"\d{1,2}-\d{4}", raw):
            m, y = map(int, raw.split("-", 1))
        elif re.fullmatch(r"\d{1,2}", raw):
            m = int(raw)
            y = t.year
            if m < t.month:
                y += 1
        else:
            # пробуем "YYYY M" или "M YYYY"
            parts = [p for p in re.split(r"\s+", (s or "").strip()) if p]
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                if len(parts[0]) == 4:
                    y = int(parts[0])
                    m = int(parts[1])
                elif len(parts[1]) == 4:
                    m = int(parts[0])
                    y = int(parts[1])

    try:
        start = datetime(y, m, 1, 0, 0)
    except Exception:
        start = datetime(t.year, t.month, 1, 0, 0)

    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)

    return start, end, start.strftime("%m.%Y")


def _slot_iter_range(day: date, start_t: time, end_t: time) -> List[datetime]:
    start_dt = datetime.combine(day, start_t)
    end_dt = datetime.combine(day, end_t)
    cur = start_dt
    out: List[datetime] = []
    while cur <= end_dt:
        out.append(cur)
        cur += SLOT
    return out


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _fmt_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _dur_to_str(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d}"


def _blocks_to_durations_str(blocks: List[Tuple[datetime, datetime]]) -> str:
    """
    Превращает список блоков в строку вида '2:30, 4:00'.
    """
    if not blocks:
        return "—"
    parts = []
    for a, b in blocks:
        minutes = int((b - a).total_seconds() // 60)
        parts.append(_dur_to_str(minutes))
    return ", ".join(parts)


WORK_START = time(11, 0)
WORK_END = time(22, 31)
SLOT = timedelta(minutes=30)
LOT_DURATION = timedelta(minutes=31)  # реальная длительность аукциона

# Окна подсчёта слотов
LUX_START = time(11, 0)
LUX_END = time(22, 31)
REG_START = time(12, 0)
REG_END = time(20, 31)


def _contiguous_blocks_from_slots(free_slots: List[datetime]) -> List[Tuple[datetime, datetime]]:
    if not free_slots:
        return []
    free_slots = sorted(free_slots)
    blocks: List[Tuple[datetime, datetime]] = []
    block_start = prev = free_slots[0]
    for cur in free_slots[1:]:
        if cur - prev == SLOT:
            prev = cur
            continue
        blocks.append((block_start, prev + SLOT))
        block_start = prev = cur
    blocks.append((block_start, prev + SLOT))
    return blocks


def _plural_slots_ru(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "слот"
    if n10 in (2, 3, 4) and not (12 <= n100 <= 14):
        return "слота"
    return "слотов"


def _list_free_slots(slots: List[datetime], busy: List[Tuple[datetime, datetime]]) -> List[datetime]:
    res = []
    for s in slots:
        if not any(_overlaps(s, s + SLOT, b0, b1) for (b0, b1) in busy):
            res.append(s)
    return res


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_subscription")
async def cb_subscription_menu_from_presets(call: types.CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_presets"),
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_subscription_back_presets")
async def cb_subscription_back_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Выберите вариант:", reply_markup=kb_presets_menu())
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data.in_(["user_subscription_gold", "user_subscription_premium"]),
)
async def user_choose_subscription(call: types.CallbackQuery, state: FSMContext):
    allowed = await _check_service_addlot_access(call, state)
    if not allowed:
        return

    plan = "gold" if call.data == "user_subscription_gold" else "premium"
    await _start_subscription_period_step(
        call,
        state,
        plan=plan,
        back_cb="user_subscription_back_presets",
    )

@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data.startswith("user_subscription_period:"),
)
async def cb_subscription_period_selected(call: types.CallbackQuery, state: FSMContext):
    try:
        _, plan, months_raw = call.data.split(":")
        months = int(months_raw)
    except Exception:
        await call.answer("Некорректный срок подписки.", show_alert=True)
        return

    if months not in (1, 3, 6, 12):
        await call.answer("Доступно только 1, 3, 6 или 12 месяцев.", show_alert=True)
        return

    service = f"subscription_{plan}"
    media_id = _service_media_file_id(service)

    await state.update_data(
        subscription_plan=plan,
        subscription_months=months,
        card_name=_subscription_title(plan, months),
        service=service,
        image_id=media_id,
        image_file_id=media_id,
    )

    await _ask_for_currency(call.message, state)

    try:
        await call.answer()
    except Exception:
        pass
@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data == "user_subscription_back_decks",
)
async def cb_subscription_period_back_decks(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_decks"),
    )
    await state.set_state(UserAddLotFSM.waiting_for_deck)
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data == "user_subscription_back_presets",
)
async def cb_subscription_period_back_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_presets"),
    )
    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    try:
        await call.answer()
    except Exception:
        pass
@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_friends_plus")
async def user_choose_friends_plus(call: types.CallbackQuery, state: FSMContext):
    media_id = _service_media_file_id("friends_plus")
    await state.update_data(
        card_id=None,
        card_name="Друзья+",
        service="friends_plus",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_progress_slots")
async def user_choose_progress_slots(call: types.CallbackQuery, state: FSMContext):
    media_id = _service_media_file_id("progress_slots")
    await state.update_data(
        card_id=None,
        card_name="Слоты прогресса",
        service="progress_slots",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.message(Command("gaps"), F.chat.type == "private")
async def cmd_gaps(message: types.Message) -> None:
    # Разрешаем команду администраторам и Лакшери
    from db.db import is_admin  # локальный импорт, чтобы не трогать верхние импорты

    uid = message.from_user.id
    allowed = await is_admin(uid) or await is_luxury_user(uid)
    if not allowed:
        await message.answer(
            "Команда доступна только администраторам и Лакшери-пользователям.",
            protect_content=True,
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else None
    if arg and arg.strip().lower() in {"help", "?", "помощь", "хелп", "-h", "--help"}:
        await message.answer(
            _tg_clean(
                "🕳 <b>/gaps — свободные места (дыры) в расписании</b>\n\n"
                "✅ <b>Месяц</b>:\n"
                "• <code>/gaps 2026-02</code>\n"
                "• <code>/gaps 02.2026</code>\n"
                "• <code>/gaps февраль</code> / <code>/gaps фев</code>\n"
                "• <code>/gaps 2</code>\n\n"
                "✅ <b>Один день</b>:\n"
                "• <code>/gaps 2026-01-15</code>\n"
                "• <code>/gaps 15.01</code>\n"
                "• <code>/gaps 15.01.2026</code>\n"
                "• <code>/gaps сегодня</code> / <code>/gaps завтра</code>\n\n"
                "ℹ️ Если год не указан, бот подставит текущий. Если дата/месяц уже прошли, возьмёт следующий год."
            ),
            parse_mode="HTML",
            protect_content=True,
        )
        return
    one_day = _parse_gaps_day(arg)
    if one_day:
        range_start = datetime.combine(one_day, time(0, 0))
        range_end = range_start + timedelta(days=1)
        label = one_day.strftime("%d.%m.%Y")
        mode = "day"
    else:
        range_start, range_end, label = _month_bounds(arg)
        mode = "month"

    today_d = date.today()
    now = datetime.now()

    lots = await get_auctions_in_range(
        range_start,
        range_end,
        statuses=["scheduled", "active"],  # pending не берём
    )

    # ====== TAKEN STARTS ONLY: блокируем только сами старты на сетке 30 минут ======
    from collections import defaultdict

    busy_starts: Dict[date, set[time]] = defaultdict(set)
    for a in lots:
        st: datetime = a["start_time"]
        if st.minute in (0, 30):
            busy_starts[st.date()].add(time(st.hour, st.minute))
        else:
            mm = 0 if st.minute < 30 else 30
            busy_starts[st.date()].add(time(st.hour, mm))

    # ==============================================================================

    def _slots_str(slots: List[datetime]) -> str:
        if not slots:
            return "0 слотов — —"
        times = ", ".join(_fmt_hhmm(s) for s in slots)
        n = len(slots)
        n10, n100 = n % 10, n % 100
        if n10 == 1 and n100 != 11:
            word = "слот"
        elif n10 in (2, 3, 4) and not (12 <= n100 <= 14):
            word = "слота"
        else:
            word = "слотов"
        return f"{n} {word} — {times}"

    # ========== РЕЖИМ: ОДИН ДЕНЬ (разделённый вывод) ==========
    if mode == "day" and one_day:
        d = one_day
        taken = busy_starts.get(d, set())

        def _free_by_start(slots: List[datetime]) -> List[datetime]:
            return [s for s in slots if time(s.hour, s.minute) not in taken]

        show_slots = _slot_iter_range(d, WORK_START, WORK_END)
        lux_slots = _slot_iter_range(d, LUX_START, LUX_END)
        reg_slots = _slot_iter_range(d, REG_START, REG_END)

        if d == today_d:
            show_slots = [s for s in show_slots if s >= now]
            lux_slots = [s for s in lux_slots if s >= now]
            reg_slots = [s for s in reg_slots if s >= now]

        free_slots_for_show = _free_by_start(show_slots)

        show_blocks = _contiguous_blocks_from_slots(free_slots_for_show)
        pretty_segments: list[str] = []
        for a0, b0 in show_blocks:
            left = _fmt_hhmm(a0)
            right = _fmt_hhmm(b0 - SLOT)
            pretty_segments.append(left if left == right else f"{left}–{right}")

        lux_free = _free_by_start(lux_slots)
        reg_free = _free_by_start(reg_slots)

        if not (pretty_segments or lux_free or reg_free):
            await message.answer(
                f"🎯 На <b>{label}</b> свободных слотов нет.",
                parse_mode="HTML",
                protect_content=True,
            )
            return

        lines: list[str] = [
            "🕳 Свободные слоты на "
            f"<b>{label}</b> "
            f"(вывод: {WORK_START.strftime('%H:%M')}–{WORK_END.strftime('%H:%M')}, шаг 30 мин)\n",
            f"<b>Показ</b>: " + (", ".join(pretty_segments) if pretty_segments else "—"),
            f"<b>Лакшери</b>: {_slots_str(lux_free)}",
            f"<b>Обычные</b>: {_slots_str(reg_free)}",
            "",
            f"Итого свободных стартов (показ): <b>{len(free_slots_for_show)}</b>",
        ]

        await message.answer(
            _tg_clean("\n".join(lines)),
            parse_mode="HTML",
            protect_content=True,
        )
        return

    # ========== РЕЖИМ: МЕСЯЦ (как было, но даты жирные) ==========
    month_start = range_start
    y, m = month_start.year, month_start.month
    from_day = today_d.day if (y == today_d.year and m == today_d.month) else 1
    days_in_month = monthrange(y, m)[1]

    lines: List[str] = [
        "🕳 Свободные слоты на "
        f"<b>{label}</b> "
        f"(вывод: {WORK_START.strftime('%H:%M')}–{WORK_END.strftime('%H:%M')}, "
        "шаг 30 мин)\n"
    ]

    total_free_slots = 0

    for day_num in range(from_day, days_in_month + 1):
        d = date(y, m, day_num)

        show_slots = _slot_iter_range(d, WORK_START, WORK_END)
        if d == today_d:
            show_slots = [s for s in show_slots if s >= now]

        taken = busy_starts.get(d, set())

        def _free_by_start(slots: List[datetime]) -> List[datetime]:
            return [s for s in slots if time(s.hour, s.minute) not in taken]

        free_slots_for_show = _free_by_start(show_slots)
        total_free_slots += len(free_slots_for_show)

        show_blocks = _contiguous_blocks_from_slots(free_slots_for_show)
        pretty_segments = []
        for a0, b0 in show_blocks:
            left = _fmt_hhmm(a0)
            right = _fmt_hhmm(b0 - SLOT)
            pretty_segments.append(left if left == right else f"{left}–{right}")

        lux_slots = _slot_iter_range(d, LUX_START, LUX_END)
        reg_slots = _slot_iter_range(d, REG_START, REG_END)
        if d == today_d:
            lux_slots = [s for s in lux_slots if s >= now]
            reg_slots = [s for s in reg_slots if s >= now]

        lux_free = _free_by_start(lux_slots)
        reg_free = _free_by_start(reg_slots)

        if pretty_segments or lux_free or reg_free:
            lines.append(
                f"<b>{d.strftime('%d.%m')}</b>: "
                + (", ".join(pretty_segments) if pretty_segments else "—")
                + f"  (Л: {_slots_str(lux_free)}; О: {_slots_str(reg_free)})"
            )

    if len(lines) == 1:
        await message.answer(
            f"🎯 На оставшиеся дни {label} свободных слотов нет.",
            parse_mode="HTML",
            protect_content=True,
        )
        return

    lines.append(
        "\nИтого свободных слотов "
        f"(по окну {WORK_START.strftime('%H:%M')}–{WORK_END.strftime('%H:%M')}): "
        f"<b>{total_free_slots}</b>"
    )

    text = _tg_clean("\n".join(lines))
    for part in _chunks(text):
        await message.answer(
            part,
            parse_mode="HTML",
            protect_content=True,
        )


async def _send_long_message(message: types.Message, lines: list[str], *, chunk_limit: int = 3500) -> None:
    """
    Безопасно отправляет большой текст частями, чтобы не ловить
    TelegramBadRequest: message is too long.
    Разбиваем по строкам, не рвём HTML-теги.
    """
    buf: list[str] = []
    cur_len = 0

    async def _flush():
        nonlocal buf, cur_len
        if buf:
            await message.answer("\n".join(buf), parse_mode="HTML")
            buf = []
            cur_len = 0

    for line in lines:
        # +1 за перевод строки
        add_len = len(line) + (1 if buf else 0)
        if cur_len + add_len > chunk_limit:
            await _flush()
        buf.append(line)
        cur_len += add_len

    await _flush()


from aiogram.types import InlineKeyboardMarkup

AUCTION_KIND_RULES = [
    ("standard", "⭐️ Стандартный", 0),
    ("reverse", "✨ Обратный", 1),
    ("fast", "⚡️ Быстрый", 2),
    ("free", "🪶 Свободный", 1),
    ("black", "👑 Чёрный", 2),
    ("exchange", "🛒 Биржа", 0),
]


def auction_kind_keyboard(luxury_level: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for kind, title, need_lvl in AUCTION_KIND_RULES:
        if luxury_level >= need_lvl:
            text = title
            cb = f"auk_kind:{kind}"
        else:
            text = f"🔒 {title} (Л{need_lvl})"
            cb = f"auk_kind_locked:{kind}:{need_lvl}"
        kb.button(text=text, callback_data=cb)

    kb.adjust(2)

    # 📚 Гайды отдельной строкой
    kb.row(
        InlineKeyboardButton(text="📚 Гайды от Давида", callback_data="auk_guide_menu:root"),
        InlineKeyboardButton(text="💬 Ответы от Давида", callback_data="auk_guide_menu:david"),
    )
    kb.row(
        InlineKeyboardButton(text="🏆 Рейтинг спасибо", callback_data="auk_guide_menu:thanks_top"),
    )

    return kb.as_markup()


def _tg_username_key(username: str | None) -> str:
    """Ключ для склейки: без @, lower."""
    if not username:
        return ""
    return username.strip().lstrip("@").lower()


def _tg_username_clean(username: str | None) -> str:
    """Для отображения/ссылки: без @, как есть."""
    if not username:
        return ""
    return username.strip().lstrip("@")


def admin_thanks_text(page: int, items: list, total_pages: int) -> str:
    # Совместимость: если кто-то вызвал как admin_thanks_text(items, page, total_pages)
    if isinstance(page, (list, tuple)) and isinstance(items, int):
        page, items = items, page

    try:
        page = int(page)
    except Exception:
        page = 0

    if not isinstance(items, (list, tuple)):
        items = []

    lines = [
        "🏆 <b>Рейтинг админских “Спасибо”</b>",
        f"📖 Страница: <b>{page + 1}/{int(total_pages) if total_pages else 1}</b>",
        "",
    ]

    if not items:
        lines.append("Пока тут пусто. Люди ещё не научились благодарить.")
        return "\n".join(lines)

    def _clean_username(u: str | None) -> str:
        return (u or "").strip().lstrip("@")

    def _key(u: str | None) -> str:
        return _clean_username(u).lower()

    # Склеиваем дубли (Nick vs @Nick)
    merged: dict[str, dict] = {}
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            author, total, users = row[0], row[1], row[2]
        else:
            # asyncpg.Record / dict
            try:
                author = row["author"]
                total = row["thanks_total"] if "thanks_total" in row else row.get("total")
                users = row["users_total"] if "users_total" in row else row.get("users")
            except Exception:
                continue

        author_clean = _clean_username(str(author or ""))
        if not author_clean:
            continue

        k = author_clean.lower()
        rec = merged.get(k)
        if not rec:
            rec = {"author": author_clean, "total": 0, "users": 0}
            merged[k] = rec

        rec["total"] += int(total or 0)
        # users корректно склеить можно только по user_id-сетам; тут безопасно берём max
        rec["users"] = max(rec["users"], int(users or 0))

    rows = sorted(merged.values(), key=lambda x: (-x["total"], -x["users"], x["author"].lower()))

    base = page * ADMIN_THANKS_PAGE_SIZE
    for i, r in enumerate(rows, start=1):
        place = base + i
        author = html.escape(r["author"])
        link = f'<a href="https://t.me/{author}">@{author}</a>'
        lines.append(f"{place}. {link} — <b>{r['total']}</b> 🙏 | 👥 <b>{r['users']}</b>")

    return "\n".join(lines)


def admin_thanks_kb(page: int, total_pages: int, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"auk_admin_thanks:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="auk_admin_thanks:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"auk_admin_thanks:page:{page + 1}"))
    kb.row(*nav)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))

    # та же глобальная кнопка спасибо (чтобы была “везде”)
    kb.row(InlineKeyboardButton(
        text=f"🙏 Спасибо: {total} | 👥 {users}",
        callback_data="auk_guides_thanks:menu_root",
    ))
    return kb.as_markup()


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guide_menu:thanks_top")
async def auk_admin_thanks_open(call: types.CallbackQuery) -> None:
    await call.answer()
    total, users = await _get_guides_thanks_totals()
    items, total_pages = await _get_admin_thanks_page(0)

    await call.message.edit_text(
        admin_thanks_text(0, items, total_pages),
        parse_mode="HTML",
        reply_markup=admin_thanks_kb(0, total_pages, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_admin_thanks:page:"))
async def auk_admin_thanks_page(call: types.CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":")[-1])
    total, users = await _get_guides_thanks_totals()
    items, total_pages = await _get_admin_thanks_page(page)

    await call.message.edit_text(
        admin_thanks_text(page, items, total_pages),
        parse_mode="HTML",
        reply_markup=admin_thanks_kb(page, total_pages, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_admin_thanks:noop")
async def auk_admin_thanks_noop(call: types.CallbackQuery) -> None:
    await call.answer()


# =======================
# 📚 GUIDES (content)
# =======================
GUIDE_AUTHOR_USERNAME = "Dear_Davidik"
GUIDE_AUTHOR_LINK = f'<a href="https://t.me/{GUIDE_AUTHOR_USERNAME}">@{GUIDE_AUTHOR_USERNAME}</a>'

GUIDE_CREDIT = f"\n\n✍️ <b>Написал и оформил:</b> {GUIDE_AUTHOR_LINK}"
DAVID_SIGN = f"\n\n✍️ <b>Ответ от:</b> {GUIDE_AUTHOR_LINK}"

# 🆔 UID craft guide authors
GUIDE_UID_AUTHOR_USERNAME = "skamto"
GUIDE_UID_AUTHOR_LINK = f'<a href="https://t.me/{GUIDE_UID_AUTHOR_USERNAME}">@{GUIDE_UID_AUTHOR_USERNAME}</a>'

GUIDE_UID_CREDIT = (
    f"\n\n✍️ <b>Автор:</b> Анонимный автор"
    f"\n✍️ <b>Написал и оформил:</b> {GUIDE_AUTHOR_LINK}"
)

GUIDE_TREASURES_PHOTO_ID = "AgACAgQAAxkBAAEH03RpY9cqYlBZOvrwI4gLmb-YGcw7JAACDQtrGw6yIVNMfJZvRLF9cQEAAwIAA3gAAzgE"

GUIDE_TREASURES_TEXT = (
                           "🪙 <b>Как оплачивать сокровищами?</b>\n\n"
                           "🧩 Сокровища — ресурс игры: даётся при разбиве карт, а также падает из колеса 🎡.\n"
                           "Мы используем 🪙 для покупки колод.\n\n"
                           "✅ <b>Для оплаты сокровищами нужно:</b>\n"
                           "🎁 Подарочные карты в нужном количестве, которые при получении дадут столько 🪙, сколько нужно заплатить.\n\n"
                           "🃏 <b>Карты делятся на номинал:</b>\n"
                           "🥉 Бронза — <b>10</b> 🪙\n"
                           "🥈 Серебро — <b>20</b> 🪙\n"
                           "🥇 Золото — <b>40</b> 🪙\n"
                           "💎 Эпик — <b>60</b> 🪙\n\n"
                           "💖☀️💧 <i>(При разбиве сокровища всегда дают рандомное количество по виду: сердца, солнца, капли)</i>\n\n"
                           "❗️❗️❗️ <b>ПОЖАЛУЙСТА, СЧИТАЙТЕ ВНИМАТЕЛЬНЕЕ, КАКОЕ КОЛИЧЕСТВО СОКРОВИЩ ПОЛУЧИТ ЧЕЛОВЕК "
                           "ПРИ ВАШЕЙ ОТПРАВКЕ КАРТ НА РАЗБИВ</b> ❗️❗️❗️"
                       ) + GUIDE_CREDIT

GUIDE_CUPS_PHOTOS = [
    "AgACAgQAAxkBAAEH0_lpY9pRWQU0QDAy8rwxsG3LV0546wACDgtrGw6yIVM6nEZHpvpR3QEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH0_tpY9pjdTtu4SnivV3D2Oe1VP0M4gACDwtrGw6yIVMayvfycNczuAEAAwIAA3gAAzgE",
    "AgACAgQAAxkBAAEH0_1pY9pxvKD_m4858Rj9DKI-J756vQACEAtrGw6yIVPxj6wzv_XKZQEAAwIAA3gAAzgE",
    "AgACAgQAAxkBAAEH0_9pY9qDysXqkdw9HUEfCdZdLQ2duwACEQtrGw6yIVPSRSmOwBZgJAEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH1AFpY9qYF3rdjlAOnltdqeQbD6m-2QACEgtrGw6yIVOyMTjvifnyRgEAAwIAA3kAAzgE",
]
GUIDE_TYPE_ANY_CARD_TEXT = (
    "🃏 <b>«Любая карта» — что это значит и как работает?</b>\n\n"
    "Лот «Любая карта» означает: не важно, бронза/серебро/золото/эпик.\n"
    "Но он <b>обязывает продавца</b> иметь у себя в наличии <b>все карты</b> выбранной категории на момент выставления лота.\n\n"

    "👑 <b>Кто выбирает карту?</b>\n"
    "✅ <b>Победитель</b>. Именно он говорит, какую карту из категории он хочет.\n\n"

    "⚠️ <b>Важное предупреждение</b>\n"
    "Если победитель выбрал карту, а у вас её нет, это считается махинациями.\n"
    "За такое можно получить <b>варн/бан</b>.\n\n"

    "📦 <b>Что нужно иметь в наличии</b>\n"
    "• 🥉 «Бронза» — все бронзы\n"
    "• 🥈 «Серебро» — все серебра\n"
    "• 🥇 «Золото» — все золота\n"
    "• 💎 «Эпик» — все эпики\n"
    "• 🃏 «Любая карта» — <b>все карты</b> на момент выставления аукциона\n\n"

    "🙏 Пожалуйста, рассчитывайте свои возможности и желания заранее.\n"
) + GUIDE_CREDIT
GUIDE_CUPS_TEXT = (
                      "🍵 <b>Как оплачивать чашками?</b>\n\n"
                      "📌 Чашки — один из основных ресурсов игры для прохождения историй.\n"
                      "🎁 Их можно получить, когда вам дарят карту, что даёт чашки: <b>2/4/6/8/12</b>.\n\n"
                      "✅ <b>Для оплаты чашками нужно:</b>\n"
                      "• 🎁 Подарочные карты, которые при получении дают нужное количество 🍵.\n"
                      "🛒 <i>Их можно приобрести, покупая колоды.</i>\n\n"
                      "🔎 <b>Как понять, что карта чашечная?</b>\n"
                      "1️⃣ Выберите карту и нажмите «создать подарочную» — покажет, что даст карта при отправке.\n"
                      "2️⃣ Нажмите ➕ в анкете рядом с коллекционными картами. Там всегда последняя колода, которую можно купить. "
                      "В правом верхнем углу будет указан номинал.\n"
                      "3️⃣ Посмотреть трекер-лист по картам, что вышли за всё время.\n\n"
                      "❗️ <b>БУДЬТЕ ВНИМАТЕЛЬНЫ: СЧИТАЙТЕ, СКОЛЬКО ТОЧНО КАРТ С ЧАШКАМИ У ВАС ЕСТЬ ДЛЯ ОПЛАТЫ</b> ❗️"
                  ) + GUIDE_CREDIT

GUIDE_DIAMONDS_PHOTOS = [
    "AgACAgQAAxkBAAEH1SppY93FVqn3FpG6Rn-c3cmdoQAB6NUAAhYLaxsOsiFTFl026WXL68oBAAMCAAN5AAM4BA",
    "AgACAgQAAxkBAAEH1SxpY93YYm0pJfP0TlVyxfxSodaeBwACFwtrGw6yIVOXMMQvv6B0DwEAAwIAA20AAzgE",
    "AgACAgQAAxkBAAEH1S5pY93oO_CT7o3Rs1shMyJ_OoQmhwACGAtrGw6yIVMRnEDNTnX3AAEBAAMCAANtAAM4BA",
]

GUIDE_DIAMONDS_TEXT = (
                          "💎 <b>Как оплачивать алмазами?</b>\n\n"
                          "💠 Алмазы — основная валюта игры: на них совершают покупки в сериях и берут удвоение на колесо 🎡.\n\n"
                          "✅ <b>У вас должно быть для оплаты алмазами:</b>\n\n"
                          "1️⃣ <b>Нужное количество алмазов</b> и точный расчёт выплат за месяц (если нет твинов).\n"
                          "📝 <i>Примечание:</i>\n"
                          "• Не рекомендуется превышать лимит, если стоимость вышла в <b>900</b> 💎 за месяц (оплата по 30 💎 в сутки)\n"
                          "• и <b>3000</b> 💎 в месяц (по 100 💎 в сутки с функцией <b>+Друзья</b>)\n\n"
                          "⚠️ <i>P.S.</i> Мы не рекомендуем превышать лимиты и не несём ответственность за ваши подсчёты.\n\n"
                          "2️⃣ <b>Ферма</b> в игре, благодаря которой вы будете кидать большое количество 💎.\n"
                          "• <i>Фермы</i> — это специальные дополнительные аккаунты в приложениях или программах (пример в фото).\n\n"
                          "3️⃣ 🎁 Вы также можете оплачивать картами, что дают 💎 при получении.\n"
                          "• Но в этом варианте их обычно нужно слишком много.\n\n"
                          "4️⃣ 🔁 Возможен другой источник оплаты, если нет ферм.\n"
                          "Например: вы выставили лот и заработали 15к 💎, но вы также купили карту за 10к 💎. "
                          "Вы можете попросить человека оплатить ваш долг.\n"
                          "📣 Пожалуйста, поставьте в известность обоих участников сделки.\n\n"
                          "❗️ <b>БУДЬТЕ БДИТЕЛЬНЫ И РАССЧИТЫВАЙТЕ СВОИ АЛМАЗЫ ПРИ ПОКУПКЕ КАРТ</b> ❗️"
                      ) + GUIDE_CREDIT

GUIDE_UID_CRAFT_PHOTO_ID = (
    "AgACAgQAAxkBAAEIU7FpaOoQaDSe9h1-4ziJzuFSSJAUWwACSwtrG-bLSFPUmi3RIn1HpQEAAwIAA3kAAzgE"
)

GUIDE_UID_CRAFT_TEXT = (
                           "🆕 <b>«Крафт по UID»</b>\n\n"
                           "✨ <b>Крафт по UID</b> — это когда продавец покупает на официальном сайте за реальные деньги право крафта, "
                           "но оформляет его на UID покупателя.\n"
                           "💎 Покупатель платит только алмазами/чашками/сокровищами, реальные деньги тратит продавец.\n\n"
                           "🎁 <b>На официальном сайте по UID можно закрафтить:</b>\n"
                           "• 🃏 подарочный дубль карты: бронза / серебро / золото / эпик\n"
                           "• 🤝 друзей\n"
                           "• 🧩 дополнительные слоты\n"
                           "• 🎰 крутки: 10 / 50 / 100\n\n"
                           "🔧 <b>Как это работает в аукционе</b>\n"
                           "1️⃣ Продавец создаёт лот через бота и нажимает кнопку «ДА» на вопрос о возможности <b>Крафта по UID</b>.\n"
                           "2️⃣ Проходит аукцион, бот определяет победителя.\n"
                           "3️⃣ Продавцу передаётся UID победителя.\n"
                           "4️⃣ Продавец:\n"
                           "   • заходит на официальный сайт Клуба Романтики,\n"
                           "   • покупает нужный крафт за реальные деньги,\n"
                           "   • вводит UID победителя.\n"
                           "✅ Победитель в игре получает <b>право крафта</b> (карта/друзья/слоты/крутки — в зависимости от лота).\n\n"
                           "⚠️ <b>Важно помнить</b>\n"
                           "• «Крафт по UID» — это передача права на крафт, а не просто «скинуть карту».\n"
                           "• 💰 Деньги за крафт платит продавец лота на официальном сайте.\n"
                           "• 🔎 Очень внимательно проверяйте UID победителя — крафт уйдёт именно на тот аккаунт, который вы введёте.\n"
                           "• 🃏 Если вы хотите выставить карту, а не крафт — выбирайте «НЕТ» на вопрос о возможности «Крафта по UID».\n"
                           "📌 Все остальные правила аукциона и работы бота Макса остаются прежними"
                       ) + GUIDE_UID_CREDIT

GUIDE_AUTOBID_PHOTO_ID = "AgACAgQAAxkBAAEJIBxpenbWkrqL-xVl_scLsl-vrpKHFQAC5gxrGxTH2FNrxa7zQVGDMgEAAwIAA3kAAzgE"

GUIDE_VENOM_RULES_TEXT = (
    "🕷️ <b>Гайд: как Веном реагирует на ставки</b>\n\n"
    "За соблюдение правил во время аукционов следит бот «Веном». "
    "Собрали примеры его ответов, чтобы вы понимали, что будет происходить 👇\n\n"

    "1️⃣ <b>Нормальная ставка</b>\n"
    "Пользователь: <code>300</code>\n"
    "✅ Ставка записана.\n\n"

    "2️⃣ <b>Ставка ниже минималки</b>\n"
    "Пользователь: <code>280</code>\n"
    "Веном: ⚠️ Ставка не принята. Минимум сейчас: <b>290</b> чай.\n"
    "📝 Сообщение остаётся.\n\n"

    "3️⃣ <b>Не ставка (текст вместо числа)</b>\n"
    "Пользователь: <code>две сотни</code>\n"
    "Веном: ❌ Сообщение удаляется.\n"
    "❗ Пиши числом или с <b>K/К</b> (например <code>10к</code>).\n\n"

    "4️⃣ <b>Неправильный шаг валюты</b>\n"
    "Пользователь: <code>291</code> (чай, должно быть чётное)\n"
    "Веном: ❌ Ставка удалена, мут <b>1 мин</b>.\n\n"

    "5️⃣ <b>Флуд (не ответ на пост лота)</b>\n"
    "Пользователь: <code>поздравляю</code>\n"
    "Веном: ❌ Сообщение удаляется, мут <b>1 мин</b>.\n\n"

    "6️⃣ <b>Исправление ставки (/oops)</b>\n"
    "Пользователь: <code>2000</code> → <code>oops 200</code>\n"
    "Веном: ✅ Ставка исправлена.\n\n"

    "7️⃣ <b>Поздний /oops (больше 60 сек)</b>\n"
    "Веном: ❌ Мут на <b>1 мин</b>, ставка не исправлена.\n\n"

    "8️⃣ <b>Удаление ставки вручную</b>\n"
    "Веном: ⚠️ Предупреждение за удаление ставки.\n\n"

    "━━━━━━━━━━━━━━━━━━\n"
    "👑 <b>Для админов</b>\n"
    "• Админский флуд/не-ставка игнорируется.\n"
    "• Ставки ниже минимума и неправильный шаг валюты: бот пишет, но <b>не удаляет</b>.\n"
    "• Админские ставки можно редактировать без ограничений.\n"
) + GUIDE_CREDIT
GUIDE_TYPE_EXCHANGE_TEXT = (
    "🛒 <b>Биржа</b>\n\n"
    "Биржа — масштабный аукцион, в котором участвует много людей и выставляется огромное количество карт.\n"
    "Здесь карты уходят <b>по фиксированной цене</b>.\n"
    "Вы боретесь не за цену, а за <b>количество</b>.\n\n"

    "⏱️ <b>Суть биржи</b>\n"
    "Биржа — это гонка на время. Самое важное — успеть урвать нужные карты!\n\n"

    "📝 <b>Кто может подать заявки</b>\n"
    "Подать заявку/ки на биржу может каждый желающий, лимит не ограничен.\n\n"

    "🧍‍♀️ <b>Кто может покупать</b>\n"
    "Участвовать может любой, но лимиты на покупку разные:\n"
    "• 🍵 Чайные карты — до <b>1 шт</b> одному человеку\n"
    "• 💎 Алмазные карты — до <b>3 шт</b>\n"
    "• 🃏 Колода — <b>одна в одни руки</b>\n\n"

    "🕒 <b>Время проведения</b>\n"
    "В течение суток, пока карты не будут распроданы.\n\n"

    "💳 <b>Оплата</b>\n"
    "Алмазы 💎\n\n"

    "✅ <b>Как забрать карту</b>\n"
    "Нужно написать в комментариях: <code>Беру</code>\n"
    "Если больше одной: <code>Беру 3</code>\n"
) + GUIDE_CREDIT
GUIDE_TYPE_STANDARD_TEXT = (
    "⭐️ <b>Стандартный аукцион</b>\n\n"
    "Это основной формат аукциона: лот публикуется в канале, а ставки делаются <b>только в комментариях</b> под постом.\n\n"

    "📌 <b>Где проходит</b>\n"
    "• Пост лота в канале\n"
    "• Комментарии под постом (там же бот принимает ставки)\n\n"

    "🧾 <b>Как выставить лот</b>\n"
    "1️⃣ /addlot → выбери <b>⭐️ Стандартный</b>\n"
    "2️⃣ Выбери <b>колоду</b> из списка (1–20) или «Свой вариант / пресеты»\n"
    "3️⃣ Заполни данные карты и стартовую цену\n"
    "4️⃣ Дождись модерации, затем лот выйдет по расписанию\n\n"

    "🕷️ <b>Как принимаются ставки</b>\n"
    "• Нормальная ставка числом: ✅ записывается\n"
    "• Текст вместо числа: ❌ удаляется\n"
    "• Шаг валюты/ошибки: могут быть ❌ удаление/мут (зависит от ситуации)\n"
    "• Есть исправление ставки через <code>/oops</code> (ограничено по времени)\n\n"

    "⚠️ <b>Важно</b>\n"
    "• Учитываются только сообщения-ставки в комментариях под лотом.\n"
    "• Следить за правилами помогает бот «Веном».\n"
) + GUIDE_CREDIT
GUIDE_AUTOBID_TEXT = (
    "🚀 <b>«Автоставки от Макса»</b> 🤖 (обновлённая механика)\n\n"
    "🎯 <b>Автоставка теперь работает как снайпер</b>: бот не торгуется шагами бесконечно, "
    "а делает <b>одну финальную ставку</b> под конец аукциона.\n\n"

    "⚙️ <b>Как это работает</b>\n"
    "1️⃣ Ты выбираешь лот и задаёшь <b>максимальную сумму</b> (лимит).\n"
    "2️⃣ Бот ждёт почти до самого конца.\n"
    "3️⃣ В финальные секунды бот делает <b>одну ставку</b> в комментариях под лотом.\n"
    "✅ Если твой лимит выше текущей ставки, бот постарается перехватить лидерство.\n\n"

    "⏰ <b>Когда бот ставит</b>\n"
    "• Обычно примерно за <b>2 секунды</b> до конца.\n"
    "• Часто это выглядит как ставка в районе <code>:58</code> перед завершением.\n"
    "• Перед ставкой может появиться <i>typing…</i>, чтобы это не выглядело как вмешательство инопланетян.\n\n"

    "💱 <b>Особенности по валютам</b>\n\n"

    "💎 <b>Алмазы</b>\n"
    "• Ставки учитываются кратно <b>30</b>.\n"
    "• Если текущая ставка ниже твоего лимита: бот повышает по правилам, стараясь выйти на <b>лимит</b>.\n"
    "• Если тебя уже догнали до лимита: бот может сделать <b>одну попытку оверкапа</b> <b>+90💎</b> (если это имеет смысл).\n\n"

    "☕️ <b>Чай / чашки</b>\n"
    "• Если текущая ставка ниже твоего лимита: бот ставит сразу <b>лимит</b>.\n"
    "• Если тебя догнали до лимита: может сделать <b>одну попытку оверкапа</b> <b>+2☕️</b>.\n\n"

    "🪙 <b>Другое (монеты и т.п.)</b>\n"
    "• Если текущая ставка ниже лимита: бот ставит сразу <b>лимит</b>.\n"
    "• Если тебя догнали: может сделать <b>одну попытку оверкапа</b> <b>+10</b>.\n\n"

    "⚠️ <b>Важно</b>\n"
    "• Это не «автоторги каждую минуту». Это <b>один финальный выстрел</b>.\n"
    "• Если ты и так лидер, бот <b>не перебивает сам себя</b>.\n"
    "• Работает только в <b>комментариях</b> под постом лота (как и обычные ставки).\n"
    "• Функция <b>платная</b> и включается вручную админами.\n"
    "• Привязка идёт к <b>конкретному лоту</b> и <b>конкретному пользователю</b>.\n\n"

    "📩 <b>Как подключить</b>\n"
    "Напиши админам:\n"
    "• 🆔 ID лота\n"
    "• 👤 твой @username\n"
    "• 🔢 лимит (максимальная сумма)\n"
) + GUIDE_CREDIT

# =======================
# 📝 GUIDE: application (how to submit)
# =======================
GUIDE_REPORT_SCAM_PHOTOS = [
    # 1
    "AgACAgQAAxkBAAELXiFpmg8yRzgwu-gRxXaBVQh3KN2kqQACzw1rG9-W0FAPDX9nx1qwQAEAAwIAA3kAAzoE",
    # 2
    "AgACAgQAAxkBAAELXiNpmg9Ec8PSoI6evB8l9DkZ4tEQ2AAC0A1rG9-W0FCkfb_oB8FRKwEAAwIAA3kAAzoE",
    # 3
    "AgACAgQAAxkBAAELXiVpmg9VQoVSUKt-mAKJrEbsm5NsGAAC0Q1rG9-W0FBKbiAy9_9HlwEAAwIAA3kAAzoE",
    # 4
    "AgACAgQAAxkBAAELXidpmg99i9fPTqxwr-a32L7zIjbb7wAC0g1rG9-W0FCXdaOICCT6ywEAAwIAA3kAAzoE",
    # 5
    "AgACAgQAAxkBAAELXixpmg-QpPZlyZfTeKEw25_luiUthQAC0w1rG9-W0FDLfAABVmUt6csBAAMCAAN5AAM6BA",
]
GUIDE_APPLY_PHOTOS = [
    "AgACAgQAAxkBAAEH2E5pY_DClQV03UhjYeZCXEl2BUpfiQACRAtrGw6yIVPgkaiIOnb8QQEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FNpY_GSq8Vr0_99IXQTCv04eXbuHgACRQtrGw6yIVNCfLlJztdRpQEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FVpY_KNg3NTdlVhvD3bz9d1ZWA7mQACRgtrGw6yIVNTA3zvEp4JYwEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FdpY_Kjh7nHdjkrL__D0HtOP8f2ugACRwtrGw6yIVNO2h2xVzuhQwEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2FppY_K-PgiH6JRj_XovfUsKrVatFAACSAtrGw6yIVNaTRoNljdP2AEAAwIAA3kAAzgE",
    "AgACAgQAAxkBAAEH2F5pY_LPibtWmDEJVVXdEke00KTMFQACSQtrGw6yIVMlcJ6Upw7olAEAAwIAA3kAAzgE",
]

GUIDE_APPLY_TEXT = (
                       "📝 <b>Как подать заявку на аукцион?</b>\n\n"
                       "1️⃣ 🤖 Зайдите в бот Макс (<code>@RomanticClubBot</code>) и нажмите <b>Старт</b>.\n\n"
                       "2️⃣ 📱 В левом нижнем углу откройте меню и выберите <b>«Подать заявку на аукцион»</b>.\n\n"
                       "3️⃣ 🏷️ Выберите вид аукциона.\n\n"
                       "4️⃣ 🗂️ Найдите ту колоду и карту, что у вас есть.\n\n"
                       "5️⃣ 💰 Выберите номинал (🍵 чай / 💎 алмазы / 🪙 сокровища), а также стартовую ставку из предложенных.\n"
                       "При желании добавьте комментарий.\n\n"
                       "6️⃣ 📸 Если вы <b>не ЛАКШЕРИ</b> (человек с подпиской), то вы обязаны отправить фото подтверждения подарочной карты в наличии.\n"
                       "❗️<b>Отправлять только 1 скрин</b>❗️\n\n"
                       "7️⃣ ⏳ Через некоторое время вам придёт подтверждение на добавление вашего Лота на аукцион "
                       "(в течение <b>2 суток</b>, всё зависит от загруженности бота).\n\n"
                       "📸 Примеры скринов можно открыть кнопкой «Примеры (скрины)»."
                   ) + GUIDE_CREDIT
GUIDE_REPORT_SCAM_TEXT = (
    "🛡️ <b>Гайд: как подать жалобу на мошенника?</b>\n\n"
    "Сейчас есть 2 рабочих способа написать в официальную поддержку КР 📩\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌐 <b>Вариант 1: через официальный сайт (где вы делаете покупки)</b>\n"
    "1️⃣ Перейдите на официальный сайт КР (тот, где оформляете покупки).\n"
    "2️⃣ Справа внизу нажмите фиолетовый значок со знаком вопросика ❔\n"
    "3️⃣ Он перенаправит вас в поддержку.\n\n"
    "🖼️ <i>(Изображение 1)</i>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎮 <b>Вариант 2: через приложение игры</b>\n"
    "1️⃣ Зайдите в игру.\n"
    "2️⃣ Нажмите ⚙️ настройки в правом верхнем углу.\n\n"
    "🖼️ <i>(Изображение 2)</i>\n\n"
    "3️⃣ Вас перекинет в основное меню, выберите «Поддержка» 🆘\n\n"
    "🖼️ <i>(Изображение 3)</i>\n\n"
    "4️⃣ Далее нажмите на нужную почту (контакт поддержки) 📧\n"
    "5️⃣ Откроется почта, и там уже пишете жалобу + прикладываете доказательства.\n\n"
    "🖼️ <i>(Изображение 4–5)</i>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📎 <b>Что лучше указать в жалобе (чтобы не «потеряли»)</b>\n"
    "✅ ID игрока (UID)\n"
    "✅ Суть обмана (что обещал/что получил/что не сделал)\n"
    "✅ Дата/примерное время\n"
    "✅ Скриншоты переписки/договорённости/пруфы передачи карт\n\n"
    "➡️ Готовые тексты жалоб откройте кнопкой ниже."
) + GUIDE_CREDIT

GUIDE_LUXURY_PERKS_TEXT = (
    "👑 <b>Лакшери-плюшки в боте Максе и как ими пользоваться</b>\n\n"
    "📌 Чтобы всё заработало после покупки Лакшери у админа, сначала обновите статус в боте.\n"
    "💳 Стоимость: <b>199/299₽</b> в месяц (в зависимости от уровня, возможна оплата другой валютой).\n\n"

    "🔄 <b>1) Обновить статус Лакшери</b>\n"
    "• Откройте меню и нажмите «Проверить Лакшери статус»\n"
    "• Команда: <code>/luxury_check</code>\n\n"

    "━━━━━━━━━━━━━━━━━━\n"
    "📒 <b>2) Журнал самых ожидаемых карт</b>\n"
    "Показывает рейтинг карт, на которые подписаны люди.\n"
    "Это помогает понять:\n"
    "• какие карты выгоднее выставлять\n"
    "• с каких можно получить больше прибыли\n"
    "Команда: <code>/lux_top</code>\n\n"

    "━━━━━━━━━━━━━━━━━━\n"
    "🕳 <b>3) Свободные места на аукционе (дыры в расписании)</b>\n"
    "Показывает свободное время, куда можно попросить админа поставить ваш лот.\n"
    "Команда: <code>/gaps</code>\n\n"

    "🗓 <b>Форматы для /gaps</b>\n"
    "✅ <b>Месяц</b> (покажет свободные слоты по дням):\n"
    "• <code>/gaps 2026-02</code>\n"
    "• <code>/gaps 02.2026</code>\n"
    "• <code>/gaps февраль</code> / <code>/gaps фев</code>\n"
    "• <code>/gaps 2</code>\n\n"
    "✅ <b>Один день</b> (раздельный вывод по «Показ/Лакшери/Обычные»):\n"
    "• <code>/gaps 2026-01-15</code>\n"
    "• <code>/gaps 15.01</code>\n"
    "• <code>/gaps 15.01.2026</code>\n"
    "• <code>/gaps сегодня</code> / <code>/gaps завтра</code>\n\n"
    "ℹ️ Если год не указан, бот подставит текущий, а если дата/месяц уже прошли, возьмёт следующий год (для планирования).\n\n"

    "━━━━━━━━━━━━━━━━━━\n"
    "📅 <b>4) Расписание аукционов на любую дату</b>\n"
    "Доступ к расписанию на ближайшие 3 месяца: можно смотреть, когда и какие лоты будут.\n"
    "Команда: <code>/vip_schedule</code>\n"
) + GUIDE_CREDIT
GUIDE_TYPE_STANDARD_TEXT = (
    "⭐️ <b>Стандартный аукцион</b>\n\n"
    "Это основной формат: лот публикуется в канале, ставки делаются <b>только в комментариях</b> под постом.\n\n"
    "📌 <b>Как участвовать</b>\n"
    "• Открываете пост лота\n"
    "• Пишите ставку числом в комментариях\n\n"
    "⚠️ <b>Важно</b>\n"
    "• Считаются только ставки в комментариях под постом\n"
    "• За правилами следит «Веном» (удаление/мут/предупреждения по ситуации)\n"
) + GUIDE_CREDIT

GUIDE_REPORT_SCAM_TEMPLATES_TEXT = (
    "📨 <b>Шаблоны жалоб</b> (копируй-вставляй)\n\n"
    "⚠️ Совет: добавьте 1–2 строки от себя (дата/обстоятельства) и прикрепите пруфы 📎\n"
    "ID мошенника: <code>5ce16c00e4b0aed72208dee5</code>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "1) Здравствуйте, хочу проинформировать о мошеннических действиях со стороны пользователя с ID: 5ce16c00e4b0aed72208dee5. "
    "Согласно договорённости, этот игрок обязался отправить свои карты взамен отправленных мною карт, но так и не выполнил своё обязательство.\n"
    "Прошу принять меры и учесть данное нарушение.\n\n"
    "2) Здравствуйте,\n"
    "Хочу уведомить о мошенничестве со стороны пользователя 5ce16c00e4b0aed72208dee5. Она предлагает купить карты за карты, после выплаты не отправляет свои.\n\n"
    "3) Добрый день,\n"
    "Прошу обратить внимание на мошеннические действия пользователя с ID 5ce16c00e4b0aed72208dee5. Она обманула очень многих.\n\n"
    "4) Приветствую,\n"
    "Сообщаю о нарушении условий сделки со стороны игрока 5ce16c00e4b0aed72208dee5. Он получил карты по договору, но не исполнил свою часть обязательств обмена картами.\n\n"
    "5) Здравствуйте,\n"
    "Информирую вас о ситуации с мошенничеством от пользователя 5ce16c00e4b0aed72208dee5. В тематических группах этот пользователь разводит других пользователей на карты!\n\n"
    "6) Добрый день,\n"
    "Заявляю о том, что пользователь 5ce16c00e4b0aed72208dee5 нарушил условия сделки: по договору он должен был прислать карты за переданные карты, но так этого не сделал.\n\n"
    "7) Здравствуйте,\n"
    "Прошу обратить внимание на действия игрока 5ce16c00e4b0aed72208dee5, который, получив карты, не исполнил обязательство по выплате, нарушив соглашение.\n\n"
    "8) Добрый день,\n"
    "Обращаюсь с жалобой на мошенничество со стороны пользователя 5ce16c00e4b0aed72208dee5. Девушка так и не отправила мне карты в ответ, несмотря на договорённость.\n\n"
    "9) Здравствуйте,\n"
    "Информирую о том, что игрок с ID 5ce16c00e4b0aed72208dee5 нарушил условия сделки. После получения карт он не произвёл оплату картами в ответ, как было обещано в соглашении.\n\n"
    "10) Добрый день,\n"
    "Сообщаю о мошеннических действиях от пользователя с ID 5ce16c00e4b0aed72208dee5. Он обязался выслать карты за полученные карты, но не выполнил своё обязательство.\n\n"
    "11) Здравствуйте,\n"
    "Уведомляю вас о том, что игрок 5ce16c00e4b0aed72208dee5 не выполняет условия соглашения: после получения карт он не отправляет желаемые карты и перестает выходить на связь."
) + GUIDE_CREDIT
# =======================
# 🙏 THANKS (global)
# =======================

_GUIDES_THANKS_READY = False


async def _ensure_guides_thanks_table() -> None:
    """
    Глобальный счётчик "Спасибо" для всех гайдов.
    Можно нажимать сколько угодно раз, считаем суммарное число нажатий
    и число уникальных пользователей.
    """
    global _GUIDES_THANKS_READY
    if _GUIDES_THANKS_READY:
        return

    await execute("""
                  CREATE TABLE IF NOT EXISTS public.guides_thanks
                  (
                      user_id
                      BIGINT
                      PRIMARY
                      KEY,
                      thanks_count
                      INTEGER
                      NOT
                      NULL
                      DEFAULT
                      0,
                      last_at
                      TIMESTAMP
                      WITHOUT
                      TIME
                      ZONE
                      DEFAULT
                      CURRENT_TIMESTAMP
                  );
                  """)

    _GUIDES_THANKS_READY = True


async def _get_guides_thanks_totals() -> tuple[int, int]:
    await _ensure_guides_thanks_table()
    row = await fetchrow("""
                         SELECT COALESCE(SUM(thanks_count), 0) AS total,
                                COUNT(*)                       AS users
                         FROM public.guides_thanks
                         """)
    return int(row["total"] or 0), int(row["users"] or 0)


async def _inc_guides_thanks(user_id: int, author: str | None = None) -> tuple[int, int]:
    await _ensure_guides_thanks_table()

    await execute("""
                  INSERT INTO public.guides_thanks (user_id, thanks_count)
                  VALUES ($1, 1) ON CONFLICT (user_id)
        DO
                  UPDATE SET
                      thanks_count = public.guides_thanks.thanks_count + 1,
                      last_at = CURRENT_TIMESTAMP
                  """, user_id)

    if author:
        await _inc_admin_thanks(author=author, user_id=user_id)

    return await _get_guides_thanks_totals()


async def _reset_guides_thanks() -> None:
    """Полное обнуление глобального счётчика 'Спасибо' для гайдов."""
    await _ensure_guides_thanks_table()
    await execute("TRUNCATE TABLE public.guides_thanks")


# =======================
# 📚 GUIDES (menus + kb)
# =======================

GUIDES_MENU_TEXT: dict[str, str] = {
    "menu_root": (
        "📚 <b>Гайды</b>\n"
        "Выберите раздел:"
    ),
    "menu_payment": (
        "💳 <b>Оплата</b>\n"
        "Выберите способ оплаты:"
    ),
    "menu_types": (
        "🗂️ <b>Типы аукционов</b>\n"
        "Выберите тип:"
    ),
}


def guides_kb(page: str, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # 1) Кнопки страницы
    if page == "menu_root":
        kb.button(text="💳 Оплата", callback_data="auk_guide_menu:payment")
        kb.button(text="📝 Оформление заявки", callback_data="auk_guide:apply")
        kb.button(text="🆔 Крафт по UID", callback_data="auk_guide:uid_craft")
        kb.button(text="🤖 Автоставки", callback_data="auk_guide:autobid")
        kb.button(text="🕷️ Веном: правила ставок", callback_data="auk_guide:venom_rules")
        kb.button(text="👑 Лакшери: плюшки", callback_data="auk_guide:luxury_perks")

        # ✅ НОВОЕ
        kb.button(text="🛡️ Жалоба на мошенника", callback_data="auk_guide:report_scam")

        kb.button(text="🗂️ Типы аукционов", callback_data="auk_guide_menu:types")
        kb.adjust(1)


    elif page == "menu_payment":
        kb.button(text="🪙 Оплата сокровищами", callback_data="auk_guide:treasures")
        kb.button(text="🍵 Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="💎 Оплата алмазами", callback_data="auk_guide:diamonds")
        kb.button(text="⬅️ Назад", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "menu_types":
        kb.button(text="⭐️ Стандартный", callback_data="auk_guide:type_standard")
        kb.button(text="🛒 Биржа", callback_data="auk_guide:type_exchange")
        kb.button(text="🃏 Любая карта", callback_data="auk_guide:type_any_card")
        kb.button(text="⬅️ Назад", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "type_standard":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)

    elif page == "type_exchange":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)

    elif page == "type_any_card":
        kb.button(text="⬅️ Назад к типам", callback_data="auk_guide_menu:types")
        kb.adjust(1)
    elif page == "treasures":
        kb.button(text="➡️ Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)
    elif page == "luxury_perks":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)
    elif page == "cups":
        kb.button(text="➡️ Оплата алмазами", callback_data="auk_guide:diamonds")
        kb.button(text="⬅️ Оплата сокровищами", callback_data="auk_guide:treasures")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)

    elif page == "diamonds":
        kb.button(text="⬅️ Оплата чашками", callback_data="auk_guide:cups")
        kb.button(text="⬅️ Назад к оплате", callback_data="auk_guide_menu:payment")
        kb.adjust(1)

    elif page == "apply":
        kb.button(text="📸 Примеры (скрины)", callback_data="auk_guide:apply_photos")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "report_scam":
        kb.button(text="📨 Тексты жалоб", callback_data="auk_guide:report_scam_texts")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "report_scam_texts":
        kb.button(text="⬅️ Назад к гайду", callback_data="auk_guide:report_scam")
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "uid_craft":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    elif page == "autobid":
        kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
        kb.adjust(1)

    # 2) Назад к выбору аукциона (всегда)
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))

    # 3) Общая кнопка "Спасибо" (всегда внизу)
    kb.row(InlineKeyboardButton(
        text=f"🙏 Спасибо: {total} | 👥 {users}",
        callback_data=f"auk_guides_thanks:{page}",
    ))

    return kb.as_markup()


# =======================
# 📚 GUIDES (send content)
# =======================
async def _send_guide_luxury_perks(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_LUXURY_PERKS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("luxury_perks", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_standard(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_STANDARD_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_standard", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_exchange(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_EXCHANGE_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_exchange", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_type_any_card(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_ANY_CARD_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_any_card", total, users),
        disable_web_page_preview=True,
    )
async def _send_guide_autobid(message: types.Message) -> None:
    try:
        await message.answer_photo(
            (GUIDE_AUTOBID_PHOTO_ID or "").strip(),
            caption="🤖 <b>Гайд</b>: автоставки",
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        # если фото сломалось — не валим апдейт, просто пропускаем картинку
        pass

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_AUTOBID_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("autobid", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_treasures(message: types.Message) -> None:
    await message.answer_photo(
        GUIDE_TREASURES_PHOTO_ID,
        caption="🪙 <b>Гайд</b>: оплата сокровищами",
        parse_mode="HTML",
    )

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TREASURES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("treasures", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_cups(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_CUPS_PHOTOS):
        if i == 0:
            media.append(types.InputMediaPhoto(
                media=fid,
                caption="🍵 <b>Гайд</b>: оплата чашками",
                parse_mode="HTML",
            ))
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_CUPS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("cups", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_diamonds(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_DIAMONDS_PHOTOS):
        if i == 0:
            media.append(types.InputMediaPhoto(
                media=fid,
                caption="💎 <b>Гайд</b>: оплата алмазами",
                parse_mode="HTML",
            ))
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_DIAMONDS_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("diamonds", total, users),
        disable_web_page_preview=True,
    )
async def _send_guide_venom_rules(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_VENOM_RULES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("venom_rules", total, users),
        disable_web_page_preview=True,
    )
async def _send_guide_type_standard(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_STANDARD_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_standard", total, users),
        disable_web_page_preview=True,
    )
async def _send_guide_apply(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_APPLY_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("apply", total, users),
        disable_web_page_preview=True,
    )

async def _send_guide_report_scam(message: types.Message) -> None:
    if GUIDE_REPORT_SCAM_PHOTOS:
        media: list[types.InputMediaPhoto] = []
        for i, fid in enumerate(GUIDE_REPORT_SCAM_PHOTOS):
            if i == 0:
                media.append(types.InputMediaPhoto(
                    media=fid,
                    caption="🛡️ <b>Гайд</b>: жалоба на мошенника",
                    parse_mode="HTML",
                ))
            else:
                media.append(types.InputMediaPhoto(media=fid))
        await message.answer_media_group(media=media)

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_REPORT_SCAM_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("report_scam", total, users),
        disable_web_page_preview=True,
    )


async def _send_guide_report_scam_texts(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_REPORT_SCAM_TEMPLATES_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("report_scam_texts", total, users),
        disable_web_page_preview=True,
    )
async def _send_guide_apply_photos(message: types.Message) -> None:
    media: list[types.InputMediaPhoto] = []
    for i, fid in enumerate(GUIDE_APPLY_PHOTOS):
        if i == 0:
            media.append(types.InputMediaPhoto(
                media=fid,
                caption="📝 <b>Оформление заявки</b>: примеры (скрины)",
                parse_mode="HTML",
            ))
        else:
            media.append(types.InputMediaPhoto(media=fid))

    await message.answer_media_group(media=media)

    # Клавиатуру к альбому прикрепить нельзя, поэтому кидаем отдельным сообщением.
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        "⬆️ Примеры скринов отправлены.",
        parse_mode="HTML",
        reply_markup=guides_kb("apply", total, users),
    )


async def _send_guide_uid_craft(message: types.Message) -> None:
    await message.answer_photo(
        GUIDE_UID_CRAFT_PHOTO_ID,
        caption="🆔 <b>Гайд</b>: крафт по UID",
        parse_mode="HTML",
    )

    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_UID_CRAFT_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("uid_craft", total, users),
        disable_web_page_preview=True,
    )


# =======================
# 📚 GUIDES (handlers)
# =======================

async def _send_guides_menu(message: types.Message, page: str) -> None:
    total, users = await _get_guides_thanks_totals()
    text = GUIDES_MENU_TEXT.get(page, "📚 <b>Гайды</b>")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=guides_kb(page, total, users),
        disable_web_page_preview=True,
    )


# =======================
# 💬 DAVID ANSWERS (content)
# =======================

DAVID_ANSWERS: dict[str, dict[str, str]] = {
    "заявка": {
        "title": "Заявка не принята",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я отправил заявку в бот на аукцион, а её до сих пор не приняли, что делать?</b>\n\n"
            "😌 Не волнуйтесь: администрация КД видит вашу заявку, поэтому наберитесь терпения и немного подождите.\n"
            "⏳ Посты обрабатываются в норме в течение <b>24–48 часов</b>.\n\n"
            "📅 Если времени прошло больше, значит на ближайшие даты нет свободных мест.\n"
            "✅ Заявка будет принята, но чуть позже.\n\n"
            "⚙️ Помните: всё зависит от загруженности бота и количества поступивших заявок.\n\n"
            "🔑 <b>Код:</b> <code>заявка</code>"
        ),
    },

    "конец": {
        "title": "Когда пришлют итоги",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я выиграл на аукционе / мой аукцион прошёл. Когда мне отправят данные другого человека для сделки?</b>\n\n"
            "📩 Итоги отправляются через бота или вам в ЛС одним из админов в течение <b>24 часов</b> "
            "с момента завершения аукциона.\n\n"
            "🧠 Пожалуйста, подождите: чисто физически мы не можем сидеть весь рабочий день "
            "и скидывать итоги через 5 минут после завершения.\n\n"
            "⚙️ Всё зависит от нагрузки бота, количества аукционов и других нюансов.\n\n"
            "🆘 Если вам не отправили итоги в течение суток, пожалуйста, напишите Давиду, указав свой аукцион.\n\n"
            "🔑 <b>Код:</b> <code>конец</code>"
        ),
    },

    "отклон": {
        "title": "Почему отклонили заявку",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Мою заявку отклонили в боте на аукцион, почему?</b>\n\n"
            "✍🏻 Заявки отклоняют чаще всего в трёх случаях:\n"
            "1) <b>Неправильное доказательство</b> — вы отправили не тот скриншот (другое фото/изображение, не связанное с картой).\n"
            "2) <b>На скрине нет подарочной карты</b> — на доказательстве должна быть видна сама подарочная карточка.\n"
            "3) <b>Не совпадает выбор в боте и доказательство</b> — выбрали одно, а на скрине другое.\n\n"
            "📌 <b>Дополнение:</b>\n"
            "Комментарий в стиле «2 карты в одном лоте» тоже может быть причиной отказа, потому что обычным участникам "
            "разрешено выставлять только <b>1 карту</b> за раз.\n\n"
            "📷 <b>Важно:</b> доказательство отправляем <b>одним скрином</b> (в одном экземпляре), без «пачки фоток».\n\n"
            "👑 <b>Лакшери/VIP:</b> правило про 1 карту и строгость доказательства касается только участников без лакшери. "
            "С VIP статусом подтверждение не требуется.\n\n"
            "🔑 <b>Код:</b> <code>отклон</code>"
        ),
    },

    "другие": {
        "title": "Как подать на другие аукционы",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я хочу подать заявку на другие аукционы, как это сделать?</b>\n\n"
            "ℹ️ Если вы <b>без специальной подписки</b>, то подавать заявки на дополнительные виды аукционов у вас "
            "возможности нет.\n\n"
            "✅ Всем доступны:\n"
            "• <b>Стандартный аукцион</b>\n"
            "• <b>Биржа</b>\n\n"
            "🌑 Дополнительные аукционы (только для лакшери):\n"
            "• <b>Чёрный</b>\n"
            "• <b>Обратный</b>\n"
            "• <b>Быстрый</b>\n"
            "• <b>Свободный</b>\n\n"
            "👑 Подавать заявки туда могут только <b>Лакшери</b>.\n"
            "💳 Подписка на месяц: <b>199/299₽</b> (цена зависит от того, покупали ли вы подписку раньше).\n"
            "🔁 Возможна оплата и другой валютой.\n\n"
            "📩 Подробнее в ЛС: <b>@velassya</b>\n\n"
            "🔑 <b>Код:</b> <code>другие</code>"
        ),
    },

    "мошенники": {
        "title": "Просят отдать заранее",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>У меня прошёл аукцион, и победитель просит карту/другие ресурсы вперёд. Могу ли я отдать их?</b>\n\n"
            "⚠️ Администрация КД просит вас заранее оценивать, <b>с кем вы ведёте сделку</b>. "
            "Мы <b>не несём ответственности</b> за вашу карту или ресурсы (крутки, слоты и т.д.) и за ваши личные решения.\n\n"
            "✅ Рекомендуемое правило:\n"
            "• <b>Не отдавайте</b> карту/ресурсы до оплаты, если только это не ваш знакомый или человек с хорошей репутацией.\n\n"
            "🚫 Никто не застрахован от <b>мошенников</b>. В случае обмана вернуть ресурсы чаще всего <b>невозможно</b>.\n\n"
            "📌 Запомните простую формулу: <b>сначала оплата, потом товар</b>.\n\n"
            "🔑 <b>Код:</b> <code>мошенники</code>"
        ),
    },
    "отмена": {
        "title": "Отмена лота",
        "text": (
            "💬 <b>Ответы от Давида</b>\n\n"
            "❓ <b>Я хочу убрать карту с аукциона. В каких случаях мне могут отказать?</b>\n\n"
            "✅ Если вы отправили запрос на отмену лота по своим личным причинам <b>за сутки и раньше</b>, то без проблем, мы уберём его.\n\n"
            "⛔️ Но если до выхода анонса осталось <b>меньше суток</b> или анонс-пост с вашей картой уже опубликован — отмена невозможна. Ваша заявка будет отклонена.\n"
            "В этом случае нужно либо участвовать в аукционе, как запланировали, либо получить бан в КД за отказ продавать карту.\n\n"
            "📌 Это правило касается также <b>бирж</b>, но не других аукционов (свободный, быстрый, обратный, чёрный), так как они идут не по расписанию.\n\n"
            "🧠 Пожалуйста, планируйте продажи заранее и трезво оценивайте свои желания и возможности.\n\n"
            "🔑 <b>Код:</b> <code>отмена</code>"
        ),
    },
}

DAVID_PAGE_SIZE = 5


def _david_codes() -> list[str]:
    # порядок можно расширять: новые коды добавляй в dict, список сам подхватит
    order = ["заявка", "конец", "отклон", "другие", "мошенники", "отмена"]
    rest = [c for c in DAVID_ANSWERS.keys() if c not in order]
    return order + sorted(rest)


def _david_pages_total() -> int:
    n = len(_david_codes())
    return max(1, (n + DAVID_PAGE_SIZE - 1) // DAVID_PAGE_SIZE)


def david_list_text(page: int) -> str:
    pages = _david_pages_total()
    return (
        "💬 <b>Ответы от Давида</b>\n"
        "Выберите вопрос кнопкой ниже.\n\n"
        "🧾 Быстрый вызов в чате:\n"
        "• <code>Макс ответ заявка</code>\n"
        "• <code>Макс ответ конец</code>\n"
        "• <code>Макс ответ отклон</code>\n"
        "• <code>Макс ответ другие</code>\n"
        "• <code>Макс ответ отмена</code>\n"
        "• <code>Макс ответ мошенники</code>\n\n"
        f"📖 Страница: <b>{page + 1}/{pages}</b>"
    )


def david_list_kb(page: int, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    codes = _david_codes()
    pages = _david_pages_total()
    page = max(0, min(page, pages - 1))

    start = page * DAVID_PAGE_SIZE
    chunk = codes[start:start + DAVID_PAGE_SIZE]

    for code in chunk:
        title = DAVID_ANSWERS[code]["title"]
        kb.button(text=f"💬 {title}", callback_data=f"auk_david:show:{code}")
    kb.adjust(1)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"auk_david:page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="auk_david:noop"))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"auk_david:page:{page + 1}"))
    if nav_row:
        kb.row(*nav_row)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))
    kb.row(InlineKeyboardButton(
        text=f"🙏 Спасибо: {total} | 👥 {users}",
        callback_data=f"auk_david_thanks:list:{page}",
    ))
    return kb.as_markup()


def david_answer_kb(code: str, total: int, users: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К списку ответов", callback_data="auk_david:page:0")
    kb.button(text="⬅️ Назад к гайдам", callback_data="auk_guide_menu:root")
    kb.adjust(1)

    kb.row(InlineKeyboardButton(text="⬅️ Назад к выбору аукциона", callback_data="auk_guides_back"))
    kb.row(InlineKeyboardButton(
        text=f"🙏 Спасибо: {total} | 👥 {users}",
        callback_data=f"auk_david_thanks:show:{code}",
    ))
    return kb.as_markup()


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guide_menu:david")
async def auk_guides_david_open(call: types.CallbackQuery) -> None:
    await call.answer()
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = david_list_text(0)
    await call.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=david_list_kb(0, total, users),
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david:page:"))
async def auk_david_page(call: types.CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":")[-1])
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = david_list_text(page)

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=david_list_kb(page, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=david_list_kb(page, total, users),
            disable_web_page_preview=True,
        )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david:show:"))
async def auk_david_show(call: types.CallbackQuery) -> None:
    await call.answer()
    code = call.data.split(":")[-1].strip().lower()

    item = DAVID_ANSWERS.get(code)
    if not item:
        await call.answer("Неизвестный код 🤔", show_alert=True)
        return

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = item["text"] + DAVID_SIGN

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=david_answer_kb(code, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=david_answer_kb(code, total, users),
            disable_web_page_preview=True,
        )


_ADMIN_THANKS_READY = False


async def _ensure_admin_thanks_tables() -> None:
    global _ADMIN_THANKS_READY
    if _ADMIN_THANKS_READY:
        return

    await execute("""
                  CREATE TABLE IF NOT EXISTS public.admin_thanks_totals
                  (
                      author
                      TEXT
                      PRIMARY
                      KEY,
                      thanks_total
                      BIGINT
                      NOT
                      NULL
                      DEFAULT
                      0,
                      users_total
                      BIGINT
                      NOT
                      NULL
                      DEFAULT
                      0,
                      updated_at
                      TIMESTAMP
                      WITHOUT
                      TIME
                      ZONE
                      DEFAULT
                      CURRENT_TIMESTAMP
                  );

                  CREATE TABLE IF NOT EXISTS public.admin_thanks_users
                  (
                      author
                      TEXT
                      NOT
                      NULL,
                      user_id
                      BIGINT
                      NOT
                      NULL,
                      created_at
                      TIMESTAMP
                      WITHOUT
                      TIME
                      ZONE
                      DEFAULT
                      CURRENT_TIMESTAMP,
                      PRIMARY
                      KEY
                  (
                      author,
                      user_id
                  )
                      );
                  """)

    _ADMIN_THANKS_READY = True


async def _inc_admin_thanks(author: str, user_id: int) -> None:
    """+1 спасибо автору, и +1 уникальному юзеру (если первый раз)."""
    await _ensure_admin_thanks_tables()

    author = (author or "").strip().lstrip("@")
    if not author:
        return

    author = author.lower()

    await execute("""
                  WITH ins AS (
                  INSERT
                  INTO public.admin_thanks_users (author, user_id)
                  VALUES ($1, $2)
                  ON CONFLICT DO NOTHING
                      RETURNING 1
                      )
                  INSERT
                  INTO public.admin_thanks_totals (author, thanks_total, users_total)
                  VALUES ($1, 1, COALESCE ((SELECT COUNT (*) FROM ins), 0))
                  ON CONFLICT (author)
                      DO
                  UPDATE SET
                      thanks_total = public.admin_thanks_totals.thanks_total + 1,
                      users_total = public.admin_thanks_totals.users_total + COALESCE ((SELECT COUNT (*) FROM ins), 0),
                      updated_at = CURRENT_TIMESTAMP;
                  """, author, user_id)


ADMIN_THANKS_PAGE_SIZE = 10


async def _get_admin_thanks_page(page: int) -> tuple[list[tuple[str, int, int]], int]:
    """Возвращает [(author, thanks_total, users_total)], total_pages — уже БЕЗ дублей."""
    await _ensure_admin_thanks_tables()

    # сколько уникальных авторов после нормализации
    row = await fetchrow("""
                         WITH t AS (SELECT lower(trim(leading '@' from author)) AS k
                                    FROM public.admin_thanks_totals
                                    GROUP BY 1)
                         SELECT COUNT(*) AS c
                         FROM t
                         """)
    total_items = int(row["c"] or 0)
    total_pages = max(1, (total_items + ADMIN_THANKS_PAGE_SIZE - 1) // ADMIN_THANKS_PAGE_SIZE)

    page = max(0, min(page, total_pages - 1))
    offset = page * ADMIN_THANKS_PAGE_SIZE

    rows = await fetch("""
                       WITH totals AS (SELECT lower(trim(leading '@' from author)) AS k,
                                              SUM(thanks_total) ::BIGINT AS thanks_total
                                       FROM public.admin_thanks_totals
                                       GROUP BY 1),
                            users AS (SELECT lower(trim(leading '@' from author)) AS k,
                                             COUNT(DISTINCT user_id) ::BIGINT AS users_total
                                      FROM public.admin_thanks_users
                                      GROUP BY 1)
                       SELECT totals.k                       AS author,
                              totals.thanks_total,
                              COALESCE(users.users_total, 0) AS users_total
                       FROM totals
                                LEFT JOIN users USING (k)
                       ORDER BY totals.thanks_total DESC, COALESCE(users.users_total, 0) DESC, totals.k ASC
                           LIMIT $1
                       OFFSET $2
                       """, ADMIN_THANKS_PAGE_SIZE, offset)

    data: list[tuple[str, int, int]] = [
        (str(r["author"]), int(r["thanks_total"]), int(r["users_total"]))
        for r in rows
    ]
    return data, total_pages


DAVID_CALL_RE = re.compile(r"(?i)^\s*(?:макс|max)\s+ответ\s+(?P<code>[\wа-яё]+)\s*$")


@router.message(F.text.regexp(r"(?i)^\s*(?:макс|max)\s+ответ\s+[\wа-яё]+\s*$"))
async def msg_david_answer_call(message: types.Message) -> None:
    m = DAVID_CALL_RE.match(message.text or "")
    if not m:
        return

    code = (m.group("code") or "").strip().lower()

    if code == "аукцион":
        await message.reply(
            GUIDE_APPLY_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    item = DAVID_ANSWERS.get(code)
    if not item:
        return

    await message.reply(
        item["text"] + DAVID_SIGN,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_david:noop")
async def auk_david_noop(call: types.CallbackQuery) -> None:
    await call.answer()


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_david_thanks:"))
async def auk_david_thanks(call: types.CallbackQuery) -> None:
    # auk_david_thanks:list:<page>  или  auk_david_thanks:show:<code>
    parts = call.data.split(":")
    mode = parts[1]
    tail = parts[2] if len(parts) > 2 else ""

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)

    try:
        if mode == "list":
            page = int(tail or "0")
            await call.message.edit_reply_markup(reply_markup=david_list_kb(page, total, users))
        elif mode == "show":
            code = (tail or "").strip().lower()
            await call.message.edit_reply_markup(reply_markup=david_answer_kb(code, total, users))
    except Exception:
        pass

    await call.answer("🙏 +1")


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data == "auk_guides")
async def auk_guides_open(call: types.CallbackQuery) -> None:
    await call.answer()
    await _send_guides_menu(call.message, "menu_root")


@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guide_menu:"))
async def auk_guides_menu(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    dest = call.data.split(":", 1)[1].strip()

    if dest == "root":
        page = "menu_root"
    elif dest == "payment":
        page = "menu_payment"
    elif dest == "types":
        page = "menu_types"
    else:
        page = "menu_root"

    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)
    text = GUIDES_MENU_TEXT.get(page, "📚 <b>Гайды</b>")

    # ✅ Ключевое: из выбора колоды НЕ редактируем сообщение (чтобы не исчез список колод)
    current_state = await state.get_state()
    if current_state == UserAddLotFSM.waiting_for_deck.state:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )
        return

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )
    except Exception:
        await call.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=guides_kb(page, total, users),
            disable_web_page_preview=True,
        )
async def _send_guide_type_exchange(message: types.Message) -> None:
    total, users = await _get_guides_thanks_totals()
    await message.answer(
        GUIDE_TYPE_EXCHANGE_TEXT,
        parse_mode="HTML",
        reply_markup=guides_kb("type_exchange", total, users),
        disable_web_page_preview=True,
    )

@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_auk_types")
async def cb_user_auk_types_from_decks(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _send_guides_menu(call.message, "menu_types")
@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guide:"))
async def auk_guide_open(call: types.CallbackQuery) -> None:
    await call.answer()
    page = call.data.split(":", 1)[1].strip()
    if page == "type_standard":
        await _send_guide_type_standard(call.message)
        return
    if page == "luxury_perks":
        if not await is_luxury_user(call.from_user.id):
            await call.answer(
                "👑 Доступно только для Лакшери.\n\n"
                "Если вы уже купили Лакшери — обновите статус:\n"
                "/luxury_check",
                show_alert=True
            )
            return

        await _send_guide_luxury_perks(call.message)
        return

    if page == "type_standard":
        await _send_guide_type_standard(call.message)
        return

    if page == "type_exchange":
        await _send_guide_type_exchange(call.message)
        return

    if page == "type_any_card":
        await _send_guide_type_any_card(call.message)
        return
    if page == "treasures":
        await _send_guide_treasures(call.message)
        return
    if page == "cups":
        await _send_guide_cups(call.message)
        return
    if page == "diamonds":
        await _send_guide_diamonds(call.message)
        return

    if page == "autobid":
        await _send_guide_autobid(call.message)
        return
    if page == "type_exchange":
        await _send_guide_type_exchange(call.message)
        return
    if page == "apply":
        await _send_guide_apply(call.message)
        return

    if page == "uid_craft":
        await _send_guide_uid_craft(call.message)
        return

    if page == "apply_photos":
        await _send_guide_apply_photos(call.message)
        return
    if page == "report_scam":
        await _send_guide_report_scam(call.message)
        return
    if page == "venom_rules":
        await _send_guide_venom_rules(call.message)
        return
    if page == "report_scam_texts":
        await _send_guide_report_scam_texts(call.message)
        return
    await call.answer("Неизвестный гайд 🤔", show_alert=True)


@router.callback_query(StateFilter(UserAddLotFSM), F.data.startswith("auk_guides_thanks:"))
async def auk_guides_thanks(call: types.CallbackQuery) -> None:
    page = call.data.split(":", 1)[1].strip()
    total, users = await _inc_guides_thanks(call.from_user.id, author=GUIDE_AUTHOR_USERNAME)

    try:
        await call.message.edit_reply_markup(reply_markup=guides_kb(page, total, users))
    except Exception:
        pass

    await call.answer("🙏 +1")


@router.callback_query(StateFilter(UserAddLotFSM), F.data == "auk_guides_back")
async def auk_guides_back(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)

    await state.update_data(auction_kind=None)
    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)

    await call.message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )


async def get_user_luxury_level(bot: Bot, user_id: int) -> int:
    # Лакшери 2: членство в отдельном чате
    if LUXURY_CHAT_ID_LVL2 and await is_luxury_member(bot, user_id, LUXURY_CHAT_ID_LVL2):
        return 2

    # Лакшери 1: либо отдельный чат, либо флаг в базе (для обратного аука)
    if LUXURY_CHAT_ID and await is_luxury_member(bot, user_id, LUXURY_CHAT_ID):
        return 1

    if await is_luxury_user(user_id):
        return 1

    return 0


EXCHANGE_RESOURCE_DECK_LIMIT = 3
EX_DECKS = [22, 24, 26]  # fallback, если БД временно недоступна


def _deck_id_from_row(deck: dict) -> int:
    try:
        return int(deck.get("deck_id") or deck.get("id") or 0)
    except Exception:
        return 0


def _deck_name_from_row(deck: dict) -> str:
    return (deck.get("name") or deck.get("title") or deck.get("deck_name") or "").strip()


def _latest_resource_deck_ids_from_rows(decks: list[dict] | None) -> list[int]:
    ids: set[int] = set()
    for d in decks or []:
        deck_type = (d.get("deck_type") or "").strip().lower()
        deck_id = _deck_id_from_row(d)
        if deck_id and deck_id % 2 == 0 and deck_type == "resource":
            ids.add(deck_id)

    latest_desc = sorted(ids, reverse=True)[:EXCHANGE_RESOURCE_DECK_LIMIT]
    return sorted(latest_desc)


async def _get_exchange_deck_ids(decks: list[dict] | None = None) -> list[int]:
    ids = _latest_resource_deck_ids_from_rows(decks)
    if ids:
        return ids

    try:
        rows = await fetchall(
            """
            SELECT id AS deck_id
            FROM public.decks
            WHERE lower(COALESCE(deck_type, '')) = 'resource'
              AND id % 2 = 0
            ORDER BY id DESC
            LIMIT $1
            """,
            EXCHANGE_RESOURCE_DECK_LIMIT,
        )
        ids = sorted({int(r["deck_id"]) for r in (rows or []) if r.get("deck_id") is not None})
        if ids:
            return ids
    except Exception:
        pass

    if decks is None:
        try:
            ids = _latest_resource_deck_ids_from_rows(await get_all_decks())
            if ids:
                return ids
        except Exception:
            pass

    return list(EX_DECKS)


async def _get_exchange_decks_for_menu() -> list[dict]:
    try:
        decks_all = await get_all_decks()
    except Exception:
        decks_all = []

    allowed_ids = await _get_exchange_deck_ids(decks_all)
    by_id = {_deck_id_from_row(d): dict(d) for d in (decks_all or []) if _deck_id_from_row(d)}

    result: list[dict] = []
    for deck_id in allowed_ids:
        row = by_id.get(deck_id, {"deck_id": deck_id, "name": f"{deck_id} колода"})
        row["deck_id"] = deck_id
        if not _deck_name_from_row(row):
            row["name"] = f"{deck_id} колода"
        result.append(row)

    return result

# Фикс-прайс биржи (стоимость всегда в 💎)
# ключ: (rarity_norm, obtain_type, obtain_amount) -> price_diamonds
EX_FIXED_PRICE_BY_CARD: dict[tuple[str, str, int], int] = {
    # 16/18 колоды: прайс по редкости и награде
    ("bronze", "diamonds", 20): 390,
    ("bronze", "cups", 2): 660,

    ("silver", "diamonds", 40): 780,
    ("silver", "cups", 4): 1200,

    ("gold", "diamonds", 80): 900,
    ("diamond", "diamonds", 120): 1200,
}

EX_WHOLE_DECK_PRICE: dict[int, int] = {
    22: 4100,
    18: 5200,
    20: 3600,
}


# 20 колода: отдельный фикс-прайс (есть отличия от 16/18)
# Сначала пробуем точное попадание по (hero_name + card_name),
# затем (на случай несовпадения апострофов/дефисов) добиваем по награде.
def _ex20_key(s: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (s or "").strip().lower())


EX_FIXED_PRICE_DECK20_BY_HERO_CARD: dict[tuple[int, str, str], int] = {
    (20, _ex20_key("Гейл"), _ex20_key("Рискованное предложение")): 600,
    (20, _ex20_key("Чанд"), _ex20_key("Обращаться бережно")): 600,
    (20, _ex20_key("Д’Марио"), _ex20_key("Обряд единения")): 600,
    (20, _ex20_key("Глэстин"), _ex20_key("Гость из-за Холмов")): 600,

    (20, _ex20_key("Мария"), _ex20_key("Секретная формула")): 660,
    (20, _ex20_key("Матиас"), _ex20_key("Романтик на удалёнке")): 660,
    (20, _ex20_key("Мессир"), _ex20_key("Хранитель памяти")): 660,

    (20, _ex20_key("Сторция"), _ex20_key("Личный инструктор")): 810,
    (20, _ex20_key("Ксандр"), _ex20_key("Во всеоружии")): 810,

    (20, _ex20_key("Масамунэ"), _ex20_key("Ты имя любви")): 1020,
}

EX_FIXED_PRICE_DECK20_BY_GAIN: dict[tuple[str, int], int] = {
    ("cups", 2): 600,
    ("diamonds", 40): 660,
    ("diamonds", 80): 810,
    ("diamonds", 120): 1020,
}

EX_MODE_LABEL: dict[str, str] = {
    "card": "🃏 Карта",
    "deck": "📚 Колода целиком",
    "deck_split": "🧾 Вся колода (карты отдельно)",
}


def exchange_deck_keyboard(
        decks: list[dict] | None = None,
        allowed_deck_ids: list[int] | None = None,
) -> InlineKeyboardMarkup:
    """
    Биржа работает только с последними ресурсными колодами.
    Если decks не передали — рисуем fallback из EX_DECKS.
    """
    allowed = set(allowed_deck_ids or EX_DECKS)

    if not decks:
        decks = [{"deck_id": d, "name": f"{d} колода"} for d in sorted(allowed)]
    else:
        # фильтруем только биржевые колоды
        decks = [d for d in decks if _deck_id_from_row(d) in allowed]

    b = InlineKeyboardBuilder()
    for d in decks:
        deck_id = _deck_id_from_row(d)
        if deck_id not in allowed:
            continue

        name = (_deck_name_from_row(d) or f"{deck_id} колода").strip()
        text = f"{deck_id}. {name}" if not name.startswith(f"{deck_id}.") else name

        # ВАЖНО: у тебя хендлер ждёт "ex_deck:"
        b.button(text=text, callback_data=f"ex_deck:{deck_id}")

    b.adjust(3)
    return b.as_markup()


def exchange_mode_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🃏 Карта", callback_data="ex_mode:card")
    kb.button(text="📚 Колода целиком", callback_data="ex_mode:deck")
    kb.button(text="🧾 Вся колода: оформить карты отдельно", callback_data="ex_mode:deck_split")
    kb.adjust(1)
    return kb.as_markup()


def exchange_copies_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in (1, 2, 3, 4, 5, 10):
        kb.button(text=f"×{n}", callback_data=f"ex_copies:{n}")
    kb.button(text="✍️ Другое число", callback_data="ex_copies:other")
    kb.adjust(3, 3, 1)
    return kb.as_markup()


def _exchange_cards_kb(cards: list[dict], deck_id: int | None = None) -> InlineKeyboardMarkup:
    """
    Кнопки карт как на стандарте:
    "161. Ливий (gold)" и т.п., плюс кнопки всей колоды.
    """
    b = InlineKeyboardBuilder()

    for c in cards or []:
        cid = c.get("card_id")
        if cid is None:
            continue
        try:
            cid_i = int(cid)
        except Exception:
            continue

        hero = (c.get("hero_name") or "—").strip()
        rn = _rarity_norm(c.get("rarity") or c.get("rarity_norm"))

        b.button(
            text=f"{cid_i}. {hero} ({rn})",
            callback_data=f"ex_card:{cid_i}",
        )

    b.adjust(1)
    return b.as_markup()


def _gift_emoji(obtain_type: str) -> str:
    t = (obtain_type or "").strip().lower()
    if t == "diamonds":
        return "💎"
    if t == "cups":
        return "🍵"
    if t == "treasures":
        return "🪙"
    return "🎁"


def _norm_ex_obtain_type(val: str | None) -> str:
    v = (val or "").strip().lower()

    # чай/чашки -> cups (как в EX_FIXED_PRICE_BY_CARD)
    if v in {"tea", "cups", "cup", "чай", "чашки", "чашка"}:
        return "cups"

    if v in {"diamonds", "diamond", "алмазы", "алмаз"}:
        return "diamonds"

    if v in {"treasures", "treasure", "сокровища", "сокровище"}:
        return "treasures"

    return v


def _exchange_price_for_card(card: dict) -> int:
    """
    Цена на бирже фиксированная (в 💎).

    20 колода:
      - сначала по (hero_name + card_name) из утверждённого прайса
      - если не совпали символы/апострофы/дефисы, добиваем по награде

    16/18 колоды:
      - по (rarity + obtain_type + obtain_amount) через EX_FIXED_PRICE_BY_CARD
    """
    # 0) спец-прайс 20 колоды
    try:
        if int(card.get("deck_id") or 0) == 20:
            hero_k = _ex20_key(card.get("hero_name"))
            name_k = _ex20_key(card.get("card_name"))
            p = EX_FIXED_PRICE_DECK20_BY_HERO_CARD.get((20, hero_k, name_k))
            if p:
                return int(p)

            ot = _norm_ex_obtain_type(str(card.get("obtain_type") or ""))
            oa = int(card.get("obtain_amount") or 0)
            p2 = EX_FIXED_PRICE_DECK20_BY_GAIN.get((ot, oa))
            if p2:
                return int(p2)
    except Exception:
        pass

    # 1) явное поле цены (на всякий случай)
    for k in ("price_diamonds", "exchange_price_diamonds", "price"):
        v = card.get(k)
        if v is None:
            continue
        try:
            iv = int(v)
            if iv > 0:
                return iv
        except Exception:
            pass

    # 2) фикс по редкости/награде
    key = _exchange_key_for_card(card)
    return int(EX_FIXED_PRICE_BY_CARD.get(key, 0))


def _exchange_gain_for_card(card: dict) -> tuple[str, int]:
    ot = _norm_ex_obtain_type(str(card.get("obtain_type", "")))
    oa = int(card.get("obtain_amount") or 0)
    return ot, oa


def _sum_gains(cards: list[dict]) -> tuple[int, int, int]:
    """
    returns: (diamonds_sum, cups_sum, treasures_sum)
    """
    d = c = t = 0
    for card in cards:
        ot, oa = _exchange_gain_for_card(card)
        if ot == "diamonds":
            d += oa
        elif ot == "cups":
            c += oa
        elif ot == "treasures":
            t += oa
    return d, c, t


def _format_gain_line(diamonds_sum: int, cups_sum: int, treasures_sum: int) -> str:
    parts = []
    if diamonds_sum:
        parts.append(f"+{diamonds_sum}💎")
    if cups_sum:
        parts.append(f"+{cups_sum}🍵")
    if treasures_sum:
        parts.append(f"+{treasures_sum}🪙")
    return ", ".join(parts) if parts else "—"


async def _load_full_cards_for_deck(deck_id: int) -> list[dict]:
    """
    Берём список карт деки и догружаем полные записи через get_card_by_id,
    чтобы гарантированно были rarity/obtain_* даже если get_exchange_cards_for_deck их не возвращает.
    """
    rows = await get_exchange_cards_for_deck(deck_id, offset=0, limit=5000)
    ids = [int(r["card_id"]) for r in rows if r.get("card_id") is not None]

    full: list[dict] = []
    for cid in ids:
        full.append(await get_card_by_id(cid))
    return full


@router.callback_query(ExchangeFSM.waiting_for_deck, F.data.startswith("ex_deck:"))
async def ex_deck_selected(call: types.CallbackQuery, state: FSMContext) -> None:
    deck_id = int(call.data.split(":", 1)[1])
    if deck_id not in await _get_exchange_deck_ids():
        await call.answer("Эта колода недоступна для биржи.", show_alert=True)
        return

    await state.update_data(ex_deck_id=deck_id)
    await state.set_state(ExchangeFSM.waiting_for_mode)

    await call.message.answer(
        f"🛒 Биржа\nКолода {deck_id}. Что выставляем?",
        reply_markup=exchange_mode_keyboard()
    )
    await call.answer()


def _format_exchange_cards_list(full_cards: list[dict]) -> str:
    lines: list[str] = []
    for i, c in enumerate(full_cards, start=1):
        hero = (c.get("hero_name") or "—").strip()
        card = (c.get("card_name") or "—").strip()
        rarity = (c.get("rarity") or "").strip()
        rarity_txt = f" • {rarity}" if rarity else ""
        lines.append(f"{i}. {hero} — {card}{rarity_txt}")
    return "\n".join(lines)


def _kb_exchange_cards_numbers(full_cards: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, c in enumerate(full_cards, start=1):
        card_id = c.get("card_id")
        if card_id is None:
            continue
        b.button(text=str(i), callback_data=f"ex_card:{int(card_id)}")
    b.adjust(5)  # 5 кнопок в ряд
    return b.as_markup()


@router.callback_query(ExchangeFSM.waiting_for_mode, F.data.startswith("ex_mode:"))
@router.callback_query(ExchangeFSM.waiting_for_card, F.data.startswith("ex_mode:"))
async def ex_mode_selected(call: CallbackQuery, state: FSMContext):
    """
    Выбор режима биржи: карточка / колода / сплит-колода.
    Здесь же сохраняем card_ids и фикс. стоимость в state.
    """
    data_raw = (call.data or "").strip()

    # payload после ":" или "|"
    if ":" in data_raw:
        _, mode = data_raw.split(":", 1)
    else:
        parts = data_raw.split("|", 1)
        mode = parts[1] if len(parts) == 2 else "card"

    mode = (mode or "card").strip() or "card"

    st = await state.get_data()
    deck_id = st.get("ex_deck_id") or st.get("deck_id")
    if not deck_id:
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Не смог определить колоду. Попробуй заново.")
        await call.answer()
        return

    try:
        deck_id_i = int(deck_id)
    except (TypeError, ValueError):
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Колода указана некорректно. Попробуй заново.")
        await call.answer()
        return

    # 1) Одна карта
    if mode == "card":
        await state.update_data(exchange_kind="card", mode="card")

        cards = await _load_full_cards_for_deck(deck_id_i)
        if not cards:
            await state.clear()
            await safe_edit_text(call.message, "⚠️ В этой колоде нет карт. Попробуй заново.")
            await call.answer()
            return

        await state.update_data(ex_cards_cache=cards)
        await state.set_state(ExchangeFSM.waiting_for_card)

        kb = _exchange_cards_kb(cards, deck_id=deck_id_i)
        await safe_edit_text(
            call.message,
            "Выберите карту или «Вся колода»:",
            reply_markup=kb,
        )
        await call.answer()
        return

    # 2) Колода / Сплит (берём все карты колоды)
    try:
        full_cards = await _load_full_cards_for_deck(deck_id_i)
        if not full_cards:
            await state.clear()
            await safe_edit_text(call.message, "⚠️ Не нашёл карты этой колоды. Попробуй заново.")
            await call.answer()
            return

        card_ids: list[int] = [
            int(c["card_id"])
            for c in full_cards
            if c and c.get("card_id") is not None
        ]

        # фикс. цена: считаем детерминированно
        if mode == "deck":
            price_i = int(await _deck_price_for_deck(deck_id_i))  # ВАЖНО: await
            title = "🛒 Биржа"
        else:  # deck_split
            price_i = int(sum(_exchange_price_for_card(c) for c in full_cards))
            title = "🛒 Биржа (Сплит)"

        # ✅ правильный профит (по всем картам и с учётом типа валют)
        diamonds_sum, cups_sum, treasures_sum = _sum_gains(full_cards)
        gain_line = _format_gain_line(diamonds_sum, cups_sum, treasures_sum)

        await state.update_data(
            exchange_kind=mode,
            mode=mode,
            split_mode=("per_card" if mode == "deck_split" else "many_in_one"),
            ex_card_ids=card_ids,
            ex_price=price_i,
            ex_price_diamonds=price_i,
            # чтобы не ломать старые места (если где-то есть), храним алмазы отдельно
            ex_gain=int(diamonds_sum),
            ex_gain_line=gain_line,
            currency="алмазы",
        )
        await state.set_state(ExchangeFSM.waiting_for_comment)

        deck_title = f"{deck_id_i} колода"
        try:
            d = await get_deck_by_id(int(deck_id_i))
            nm = (d.get("name") or "").strip() if d else ""
            if nm:
                deck_title = nm if nm.lower().startswith(str(int(deck_id_i))) else f"{deck_id_i} колода — {nm}"
        except Exception:
            pass

        text_to_user = (
            f"{title}\n"
            f"Колода: {deck_title} ({len(full_cards)} карт)\n"
            f"Стоимость: {price_i} 💎 (фикс.)\n"
            f"Колода даёт: {gain_line}\n\n"
            "Комментарий (если не нужен, отправь 0):"
        )

        # ✅ заставка по колоде 16/18/20
        cover_id = await _exchange_deck_cover_id(deck_id_i)

        sent = None
        if cover_id:
            try:
                sent = await _answer_media_any(call.message, cover_id, caption=text_to_user, reply_markup=None)
            except Exception:
                sent = None

        if not sent:
            await safe_edit_text(call.message, text_to_user)

        await call.answer()
        return

    except Exception:
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Ошибка при расчёте стоимости. Попробуй заново.")
        await call.answer()
        return


@router.callback_query(
    ExchangeFSM.waiting_for_card,
    (F.data.startswith("ex_card:") | F.data.startswith("ex_card|")),
)
async def ex_card_selected(call: CallbackQuery, state: FSMContext):
    data_raw = (call.data or "").strip()

    if ":" in data_raw:
        _, card_id_s = data_raw.split(":", 1)
    else:
        _, card_id_s = data_raw.split("|", 1)

    try:
        card_id = int(card_id_s)
    except Exception:
        await call.answer("Некорректный card_id.", show_alert=True)
        return

    card = await get_card_by_id(card_id)
    if not card:
        await call.answer("Карта не найдена.", show_alert=True)
        return

    try:
        price = int(_exchange_price_for_card(card))
        _t, amt = _exchange_gain_for_card(card)
        gift = int(amt or 0)
    except Exception:
        await state.clear()
        await call.message.answer("⚠️ Ошибка при выборе карты. Попробуй заново.")
        await call.answer()
        return

    await state.update_data(
        exchange_kind="card",
        mode="card",
        split_mode="one",
        copies=1,
        ex_card_id=card_id,
        ex_card_ids=[card_id],
        ex_price=price,
        ex_price_diamonds=int(price),
        ex_gain=gift,
        currency="алмазы",
    )

    hero = escape((card.get("hero_name") or "").strip(), quote=False)
    name = escape((card.get("card_name") or "").strip(), quote=False)

    await call.message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Карта: <b>{hero} — {name}</b>\n"
        f"Стоимость: <b>{price}</b> 💎 (фикс.)\n"
        f"Карта даёт: <b>+{gift}</b> 💎\n\n"
        "Сколько таких карт выставляем?",
        parse_mode="HTML",
        reply_markup=exchange_copies_keyboard(),
    )
    await state.set_state(ExchangeFSM.waiting_for_copies)
    await call.answer()


@router.message(ExchangeFSM.waiting_for_card)
async def ex_card_by_number(message: Message, state: FSMContext):
    t = (message.text or "").strip()
    if not t.isdigit():
        return

    card_id = int(t)
    card = await get_card_by_id(card_id)
    if not card:
        await message.answer("⚠️ Карта не найдена. Выбери кнопкой из списка.")
        return

    price = _exchange_price_for_card(card)
    gain_type, gain_amount = _exchange_gain_for_card(card)
    emoji = _gift_emoji(gain_type)

    await state.update_data(
        mode="card",
        exchange_kind="card",
        split_mode="one",
        copies=1,
        ex_card_ids=[card_id],
        ex_price=int(price),
        ex_price_diamonds=int(price),
        ex_gain=int(gain_amount),
        ex_gift=(gain_type, int(gain_amount)),
        currency="алмазы",
    )

    hero = escape((card.get("hero_name") or "—").strip(), quote=False)
    name = escape((card.get("card_name") or "—").strip(), quote=False)

    await message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Карта: <b>{hero} — {name}</b>\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> 💎\n"
        f"Карта даёт: <b>+{int(gain_amount)}</b> {_gift_emoji(gain_type)}\n\n"
        "Сколько таких карт выставляем?",
        parse_mode="HTML",
        reply_markup=exchange_copies_keyboard(),
    )
    await state.set_state(ExchangeFSM.waiting_for_copies)


def _currency_emoji(cur: str) -> str:
    c = (cur or "").strip().lower()
    if "алмаз" in c or c in ("💎", "diamond", "diamonds"):
        return "💎"
    if "чаш" in c or c in ("🍵", "cups"):
        return "🍵"
    if "сокров" in c or c in ("🪙", "treasures"):
        return "🪙"
    return "💎"


_RARITY_EMOJI = {
    "эпик": "💠",
    "легендар": "👑",
    "редк": "🔷",
    "обыч": "🔹",
    "ивент": "🎟️",
}


def _safe_int(v: object, default: int = 0) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except Exception:
        return default


def _h(s: str | None) -> str:
    return html.escape((s or "").strip(), quote=False)


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_res_diamonds_for_tea")
async def user_preset_res_diamonds_for_tea(call: types.CallbackQuery, state: FSMContext):
    # “получаем 💎, платят 🍵”
    await state.update_data(
        service=None,
        deck_type="resource",
        rarity="any",
        forced_obtain_type="алмазы",  # для потолков RESOURCE_CAP_BY_OBTAIN["алмазы"]
        currency="чашки",
        card_id=None,
        card_name="Ресурсная карта (💎 за 🍵)",
        hero_name="Ресурсная карта",
    )

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, "🍵")
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await call.message.answer(
        f"Формат: <b>алмазы за чай</b>\n"
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} 🍵</b>\n"
        f"({hint})\n\n"
        f"Введите стартовую цену:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_start_price)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_res_tea_for_diamonds")
async def user_preset_res_tea_for_diamonds(call: types.CallbackQuery, state: FSMContext):
    # “получаем 🍵, платят 💎”
    await state.update_data(
        service=None,
        deck_type="resource",
        rarity="any",
        forced_obtain_type="чашки",  # для потолков RESOURCE_CAP_BY_OBTAIN["чашки"]
        currency="алмазы",
        card_id=None,
        card_name="Ресурсная карта (🍵 за 💎)",
        hero_name="Ресурсная карта",
    )

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, "💎")
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await call.message.answer(
        f"Формат: <b>чай за алмазы</b>\n"
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} 💎</b>\n"
        f"({hint})\n\n"
        f"Введите стартовую цену:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_start_price)
    await call.answer()


def _exchange_key_for_card(card: dict) -> tuple[str, str, int]:
    rarity = _norm_rarity(card.get("rarity") or card.get("rarity_norm"))
    ot = _norm_ex_obtain_type(str(card.get("obtain_type") or ""))
    oa = int(card.get("obtain_amount") or 0)
    return rarity, ot, oa


async def _db_create_exchange_batch(
        *,
        user_id: int,
        username: str,  # можно не использовать, но оставим сигнатуру как у тебя
        deck_id: int,
        mode: str,
        card_ids: list[int],
        price: int,
        currency: str,
        comment: str,
        proof_photo_id: str,
) -> int:
    """
    Создаёт batch в БД и привязывает к нему список card_ids.
    Возвращает batch_id.
    """
    batch_id = await create_exchange_batch(
        user_id=int(user_id),
        deck_id=int(deck_id),
        mode=(mode or "card").strip() or "card",
        currency=(currency or "алмазы").strip() or "алмазы",
        price=int(price or 0),
        comment=(comment or "-").strip() or "-",
        proof_photo_id=(proof_photo_id or "NO_PROOF").strip() or "NO_PROOF",
    )

    # Привязываем карты к batch
    for cid in (card_ids or []):
        await add_exchange_item_for_card(batch_id=int(batch_id), card_id=int(cid))

    return int(batch_id)


# auctions.py

async def _finalize_exchange_request(
        message: Message,
        state: FSMContext,
        bot: Bot,
        proof_photo_id: str | None = None,
) -> None:
    data = await state.get_data()
    user_id = int(message.from_user.id)

    deck_id = data.get("ex_deck_id") or data.get("deck_id")
    if not deck_id:
        await state.clear()
        await message.answer("⚠️ Не смог определить колоду. Попробуй заново.")
        return
    deck_id_i = int(deck_id)

    mode = (data.get("mode") or data.get("exchange_kind") or "card").strip() or "card"
    currency = (data.get("currency") or "алмазы").strip()
    comment = ((data.get("ex_comment") or "") or (data.get("comment") or "")).strip()

    split_mode = (data.get("split_mode") or ("per_card" if mode == "deck_split" else "one")).strip()
    copies = int(data.get("copies") or 1)
    copies = max(1, min(copies, 20))

    proof_photo_id = (proof_photo_id or "").strip() or "NO_PROOF"

    card_ids = _normalize_card_ids(data.get("ex_card_ids") or data.get("card_ids"))
    if not card_ids and data.get("ex_card_id"):
        card_ids = [int(data["ex_card_id"])]

    if not card_ids and mode in {"deck", "deck_split"}:
        card_ids = await get_cards_ids_by_deck(deck_id_i)
        if card_ids:
            await state.update_data(ex_card_ids=card_ids)

    if not card_ids:
        await state.clear()
        await message.answer("⚠️ Не смог определить карты. Попробуй заново.")
        return

    full_cards = await get_cards_by_ids([int(x) for x in card_ids])
    by_id = {int(c["card_id"]): c for c in full_cards if c and c.get("card_id") is not None}

    async def _send_exchange_log_one(batch_id: int, *, items_count: int, price: int) -> None:
        # deck_name
        deck_name = None
        try:
            drow = await fetchrow("SELECT name FROM public.decks WHERE id=$1", deck_id_i)
            if drow:
                deck_name = (drow.get("name") or "").strip() or None
        except Exception:
            deck_name = None

        created_at_msk = _fmt_dt_msk(datetime.now(timezone.utc))
        log_text = format_exchange_new_request_log(
            batch_id=int(batch_id),
            created_at_msk=created_at_msk,
            sender_username=message.from_user.username,
            sender_id=message.from_user.id,
            deck_id=deck_id_i,
            deck_name=deck_name,
            mode=mode,
            items_count=int(items_count),
            price=int(price),
            currency=currency,
            has_proof=bool(proof_photo_id) and str(proof_photo_id).upper() != "NO_PROOF",
            comment=comment,
        )
        try:
            await send_admin_log(message.bot, log_text)  # ✅ НЕ импортированный bot и не аргумент “bot”
        except Exception:
            pass

    # 1) deck_split = каждая карта отдельной заявкой
    if split_mode == "per_card" or mode == "deck_split":
        created: list[tuple[int, dict, int]] = []

        for cid in card_ids:
            c = by_id.get(int(cid))
            if not c:
                continue

            price_one = int(_exchange_price_for_card(c) or 0)

            batch_id = await create_exchange_batch(
                user_id=user_id,
                deck_id=deck_id_i,
                mode=mode,
                currency=currency,
                price=price_one,
                comment=comment,
                proof_photo_id=proof_photo_id,
            )
            await add_exchange_item_for_card(batch_id=batch_id, card_id=int(cid))
            created.append((batch_id, c, price_one))

            # ✅ лог на каждый созданный batch
            await _send_exchange_log_one(batch_id, items_count=1, price=price_one)

        await _send_user_exchange_confirmation_deck_split(
            message,
            created=created,
            user_id=user_id,
            deck_id=deck_id_i,
        )

        await state.clear()
        return

    # 2) copies = N одинаковых заявок
    if len(card_ids) == 1 and copies > 1:
        cid = int(card_ids[0])
        c = by_id.get(cid) or (full_cards[0] if full_cards else None)
        if not c:
            await state.clear()
            await message.answer("⚠️ Карта не найдена. Попробуй заново.")
            return

        price_one = int(_exchange_price_for_card(c) or 0)
        batch_ids: list[int] = []

        for _ in range(copies):
            batch_id = await create_exchange_batch(
                user_id=user_id,
                deck_id=deck_id_i,
                mode=mode,
                currency=currency,
                price=price_one,
                comment=comment,
                proof_photo_id=proof_photo_id,
            )
            await add_exchange_item_for_card(batch_id=batch_id, card_id=cid)
            batch_ids.append(batch_id)

            # ✅ лог на каждый batch
            await _send_exchange_log_one(batch_id, items_count=1, price=price_one)

        await _send_user_exchange_confirmation_copies(
            message,
            batch_ids=batch_ids,
            user_id=user_id,
            card=c,
            price=price_one,
            currency=currency,
            comment=comment,
            deck_id=deck_id_i,
        )

        await state.clear()
        return

    # 3) обычный режим: одна заявка
    price_i = _digits_int(data.get("ex_price") or data.get("ex_price_diamonds") or 0)
    if not price_i:
        if mode == "card":
            price_i = int(_exchange_price_for_card(full_cards[0]) if full_cards else 0)
        else:
            price_i = await _deck_price_for_deck(deck_id_i)

    batch_id = await create_exchange_batch(
        user_id=user_id,
        deck_id=deck_id_i,
        mode=mode,
        currency=currency,
        price=int(price_i or 0),
        comment=comment,
        proof_photo_id=proof_photo_id,
    )

    for cid in card_ids:
        await add_exchange_item_for_card(batch_id=batch_id, card_id=int(cid))

    await _send_user_exchange_confirmation(
        message,
        batch_id=batch_id,
        user_id=user_id,
        cards=full_cards,
        price=int(price_i or 0),
        currency=currency,
        comment=comment,
        deck_id=deck_id_i,
    )

    # ✅ лог гарантированно улетает в лог-чаты
    await _send_exchange_log_one(batch_id, items_count=len(card_ids or []), price=int(price_i or 0))

    await state.clear()


# admin_actions.py

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

    cur_print = (currency or "алмазы").strip()
    cur = cur_print.lower()
    cur_emoji = _cur_emoji(cur)

    mode_key = (mode or "").strip().lower()
    mode_lbl = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, (mode or "—"))

    proof_line = "✅ Да" if has_proof else "❌ Нет"
    price_line = f"{int(price)} {cur_emoji} ({cur_print})" if price is not None else f"— {cur_emoji} ({cur_print})"

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


BR_RE = re.compile(r"(?i)<br\s*/?>")


def tg_clean(text: str) -> str:
    return BR_RE.sub("\n", text or "")


@router.message(ExchangeFSM.waiting_for_comment)
async def ex_comment_input(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    text_low = text.lower()

    exit_texts = {"🏠 меню", "меню", "/start", "🛒 биржа", "биржа", "📦 аукцион", "аукцион"}
    if text_low in exit_texts:
        await state.clear()
        await message.answer("Ок, выхожу из оформления заявки биржи.")
        return

    # Команды не жрём FSM-ом
    if text.startswith("/"):
        raise SkipHandler()

    # "0" = без комментария
    if text == "0":
        text = ""

    await state.update_data(ex_comment=text)

    # лакшери = без пруфа
    user_id = int(message.from_user.id)
    try:
        lux = bool(await is_luxury_user(user_id))
    except Exception:
        lux = False

    if lux:
        await _finalize_exchange_request(message, state, bot, proof_photo_id="NO_PROOF")
        return

    await ex_request_proof(message, state)


_EXIT_TEXTS = {
    "🏠 меню",
    "меню",
    "/start",
    "🛒 биржа",
    "биржа",
    "📦 аукцион",
    "аукцион",
}


async def ex_request_proof(message: Message, state: FSMContext) -> None:
    """
    Переводит пользователя на шаг пруфа.
    """
    await state.set_state(ExchangeFSM.waiting_for_proof)
    await message.answer(
        "📸 Пришли, пожалуйста, <b>пруф</b> (фото).\n"
        "Если передумал — /cancel",
        parse_mode="HTML",
    )


@router.message(ExchangeFSM.waiting_for_proof)
async def ex_proof_any(message: Message, state: FSMContext, bot: Bot) -> None:
    proof_photo_id: str | None = None

    # фото
    if message.photo:
        proof_photo_id = message.photo[-1].file_id

    # документ (скрин)
    elif message.document:
        proof_photo_id = message.document.file_id

    # видео
    elif message.video:
        proof_photo_id = message.video.file_id

    # гиф/анимация
    elif message.animation:
        proof_photo_id = message.animation.file_id

    # текст
    else:
        t = (message.text or "").strip().lower()
        if t in {"0", "нет", "-", "skip", "пропуск"}:
            proof_photo_id = "NO_PROOF"
        else:
            await message.answer(
                "Нужно <b>фото/скрин</b> пруфа или <b>0</b> (если пруфа нет).",
                parse_mode="HTML",
            )
            return

    await _finalize_exchange_request(message, state, bot, proof_photo_id=proof_photo_id)


@router.message(ExchangeFSM.waiting_for_proof)
async def ex_proof_fallback(message: Message) -> None:
    await message.answer(
        "Нужно прислать <b>фото/скрин</b> пруфа или <code>0</code>.",
        parse_mode="HTML",
    )


def _sum_exchange_prices(cards: list[dict]) -> tuple[int, list[tuple[tuple[str, str, int], int, str, str]]]:
    """
    Сумма фикс-цен (в 💎) по списку карт.
    missing: [(exchange_key, card_id, hero_name, card_name), ...] для карт, где цена не определилась.
    """
    total = 0
    missing: list[tuple[tuple[str, str, int], int, str, str]] = []

    for card in cards or []:
        price = int(_exchange_price_for_card(card) or 0)
        if price > 0:
            total += price
            continue

        key = _exchange_key_for_card(card)
        cid = int(card.get("card_id") or 0)
        hero = str(card.get("hero_name") or "").strip()
        name = str(card.get("card_name") or "").strip()
        missing.append((key, cid, hero, name))

    return int(total), missing


@router.callback_query(F.data.startswith("pending_menu:"))
@admin_only
async def pending_menu_pick(call: types.CallbackQuery, state: FSMContext):
    kind = call.data.split(":", 1)[1].strip()
    await call.answer()

    if kind == "auction":
        await show_pending_auction_lots(call.message)
        return

    if kind == "exchange":
        await show_pending_exchange_requests(call.message)
        return

    await call.message.answer("Неизвестный тип заявок.")


@router.callback_query(F.data.startswith("exchange_proof|"))
@admin_only
async def exchange_show_proof(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    proof_id = (batch.get("proof_photo_id") or "").strip()
    if not proof_id or proof_id.upper() == "NO_PROOF":
        await call.answer("Пруфа нет", show_alert=True)
        return

    # ✅ отправляем ОДИН раз
    try:
        await call.message.answer_photo(
            proof_id,
            caption=f"📸 Пруф заявки (биржа) • #{batch_id}",
            protect_content=False,  # если хочешь разрешить пересылку/скрины
        )
    except Exception:
        await call.answer("Пруф битый (file_id неверный).", show_alert=True)
        return

    await call.answer()


@router.callback_query(F.data.startswith("exchange_approve|"))
@admin_only
async def exchange_approve(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
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

    # ---------- данные для лога ----------
    when_msk = _fmt_dt_msk(datetime.now(timezone.utc))

    admin_html = _user_link(call.from_user.id, call.from_user.username)

    user_id = int(batch.get("user_id") or 0)
    user_html = _user_link(user_id, batch.get("username")) if user_id else "—"

    deck_id = int(batch.get("deck_id") or 0)
    deck_name = None
    try:
        if deck_id:
            d = await get_deck_by_id(deck_id)
            deck_name = (d.get("name") or "").strip() if d else None
    except Exception:
        deck_name = None
    deck_title = deck_name or (f"{deck_id}" if deck_id else "—")

    mode = (batch.get("mode") or "").strip()
    currency = str(batch.get("currency") or "алмазы").strip()
    price = batch.get("price")
    comment = (batch.get("comment") or "").strip() or "-"

    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

    # состав (короткое превью до 10 строк)
    items_cnt = 0
    preview: list[str] = []
    try:
        items = await get_exchange_items_by_batch_id(batch_id)
        items_cnt = len(items)
        for i, it in enumerate(items[:10], start=1):
            card_name = tg_clean(str(it.get("card_name") or "-"))
            hero_name = tg_clean(str(it.get("hero_name") or "-"))
            preview.append(f"{i}. <b>{card_name}</b> — {hero_name}")
        more = items_cnt - min(items_cnt, 10)
        if more > 0:
            preview.append(f"…и ещё <b>{more}</b> шт.")
    except Exception:
        items_cnt = int(batch.get("items_count") or 0) if batch.get("items_count") is not None else 0
        preview = []

    # ---------- отправка лога в лог-чаты ----------
    try:
        log_text = format_exchange_approved_log(
            created_at_msk=when_msk,
            batch_id=batch_id,
            admin_html=admin_html,
            user_html=user_html,
            deck_title=deck_title,
            mode=mode,
            items_count=items_cnt,
            price=int(price) if price is not None else None,
            currency=currency,
            has_proof=has_proof,
            comment=comment,
            items_preview=preview,
        )
        await send_admin_log(call.bot, log_text)
    except Exception:
        pass

    # ---------- уведомление пользователю (как раньше, можно оставить твою версию) ----------
    moderator_tag_str = admin_tag(call.from_user)
    thanks_kb = await build_thanks_kb(int(batch_id), moderator_tag_str)

    mode_key = (mode or "").strip().lower()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, mode or "—")

    cur_emoji = _cur_emoji(currency.lower())
    price_line = f"{int(price)} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    notify_text = (
        "✅ <b>Ваша заявка на биржу одобрена и добавлена в биржу!</b>\n"
        f"🆔 Batch: <code>{batch_id}</code>\n\n"
        f"📚 Колода: <b>{html.escape(deck_title)}</b>\n"
        f"🎛 Режим: <b>{html.escape(str(mode_ru))}</b>\n"
        f"🃏 Карт: <b>{items_cnt}</b>\n"
        f"💰 Цена: <b>{html.escape(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{html.escape(comment)}</i>\n\n"
        f"<b>Модератор:</b> {admin_html}"
        f"Если хочешь, можешь сказать спасибо ниже ❤️\n\n"
    )

    media_id = None
    kind = "photo"
    try:
        cover_id, cover_kind = await _get_exchange_cover_media(batch_id)
        if cover_id:
            media_id = cover_id
            kind = cover_kind
    except Exception:
        media_id = None

    if not media_id and has_proof:
        media_id = proof_id
        kind = "photo"

    try:
        if user_id:
            if media_id:
                await safe_send_media(
                    call.bot,
                    chat_id=user_id,
                    file_id=str(media_id),
                    caption=notify_text,
                    reply_markup=thanks_kb,
                    parse_mode="HTML",
                    protect_content=False,
                )
            else:
                await call.bot.send_message(
                    user_id,
                    notify_text,
                    parse_mode="HTML",
                    reply_markup=thanks_kb,
                    disable_web_page_preview=True,
                )
    except Exception:
        pass

    # обновим кнопки у админа
    try:
        await call.message.edit_reply_markup(reply_markup=_approved_kb(batch_id, has_proof=has_proof))
    except Exception:
        pass

    await call.answer("Одобрено ✅", show_alert=False)


@router.callback_query(F.data.startswith("exchange_items|"))
@admin_only
async def exchange_items(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    items = await get_exchange_items_by_batch_id(batch_id)
    if not items:
        await call.message.answer(
            f"🃏 <b>Состав заявки</b> • ID <code>{batch_id}</code>\n— пусто",
            parse_mode="HTML",
        )
        await call.answer()
        return

    lines: list[str] = []
    hard_limit = 60
    for i, it in enumerate(items[:hard_limit], start=1):
        name = html.escape(str(it.get("card_name") or "-"))
        hero = html.escape(str(it.get("hero_name") or "-"))
        cid = it.get("card_id")
        tail = f" (id={cid})" if cid else ""
        lines.append(f"{i}. <b>{name}</b> — {hero}{tail}")

    more = len(items) - min(len(items), hard_limit)
    more_line = f"\n…и ещё <b>{more}</b> шт." if more > 0 else ""

    await call.message.answer(
        f"🃏 <b>Состав заявки</b> • ID <code>{batch_id}</code>\n\n" + "\n".join(lines) + more_line,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data.startswith("exchange_reject|"))
@admin_only
async def exchange_reject_start(call: types.CallbackQuery, state: FSMContext):
    batch_id = int(call.data.split("|")[1])
    await state.update_data(exchange_batch_id=batch_id)
    await state.set_state(ModActionFSM.waiting_for_reject_exchange_reason)
    await call.message.answer("Напиши причину отклонения заявки на биржу:")
    await call.answer()


@router.message(ModActionFSM.waiting_for_reject_exchange_reason, F.chat.type == "private")
@admin_only
async def exchange_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    batch_id = int(data.get("exchange_batch_id") or 0)
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
        await message.answer("Не удалось обновить статус в базе.")
        return

    # 1) уведомим пользователя — КРАСИВО, как у аукциона
    try:
        await notify_exchange_user_moderation(
            message.bot,
            batch=batch,
            admin_user=message.from_user,
            title="отклонена",
            reason=reason,
        )
    except Exception:
        pass

    # 2) лог в лог-чат — единый стиль, как у обычной заявки
    try:
        deck_id = int(batch.get("deck_id") or 0)

        # deck_name (опционально)
        deck_name = None
        try:
            drow = await fetchrow("SELECT name FROM public.decks WHERE id=$1", deck_id)
            if drow:
                deck_name = (drow.get("name") or "").strip() or None
        except Exception:
            deck_name = None
        deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

        # items count
        items_cnt = 0
        try:
            r = await fetchrow("SELECT COUNT(*) AS cnt FROM public.exchange_items WHERE batch_id=$1", batch_id)
            items_cnt = int((r or {}).get("cnt") or 0)
        except Exception:
            items_cnt = 0

        proof_id = (batch.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

        when_msk = _fmt_dt_msk(datetime.now(timezone.utc))

        log_text = format_exchange_moderation_log(
            action_title="Отклонена заявка на биржу",
            action_code="exchange_reject через бота",
            when_msk=when_msk,
            admin_user=message.from_user,
            batch_id=batch_id,
            sender_username=batch.get("username"),
            sender_id=batch.get("user_id"),
            deck_name=deck_title,
            deck_id=deck_id,
            mode=str(batch.get("mode") or "—"),
            items_count=items_cnt,
            price=int(batch["price"]) if batch.get("price") is not None else None,
            currency=str(batch.get("currency") or "алмазы"),
            has_proof=has_proof,
            comment=str(batch.get("comment") or ""),
            moderator_comment=reason,
        )
        await send_admin_log(message.bot, log_text)
    except Exception:
        pass

    await message.answer(f"Отклонено ❌ (Batch {batch_id})")
    await state.clear()


EX_CB_PROOF = "ex:proof"
EX_CB_APPROVE = "ex:approve"
EX_CB_REJECT = "ex:reject"


def _ex_admin_kb(batch_id: int, has_proof: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"{EX_CB_APPROVE}:{batch_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{EX_CB_REJECT}:{batch_id}"),
        ]
    ]
    if has_proof:
        rows.append([InlineKeyboardButton(text="📸 Фото подтверждения", callback_data=f"{EX_CB_PROOF}:{batch_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_exchange_batch_text(batch: dict, items: list[dict]) -> str:
    username = f"@{batch['username']}" if batch.get("username") else ""
    user_line = f"{batch['user_id']} {username}".strip()
    deck = batch.get("deck_name") or f"ID {batch.get('deck_id')}"
    comment = batch.get("comment") or "—"

    items_lines = []
    for it in items:
        hero = (it.get("hero_name") or "").strip()
        card = (it.get("card_name") or "").strip()
        if hero and card:
            items_lines.append(f"• {hero} — {card}")
        elif hero or card:
            items_lines.append(f"• {hero or card}")
    items_block = "\n".join(items_lines) if items_lines else "—"

    return (
        f"🛒 Заявка на Биржу #{batch['batch_id']}\n"
        f"👤 {user_line}\n"
        f"🗂 Колода: {deck}\n"
        f"🎛 Режим: {batch.get('mode')}\n"
        f"💰 Валюта: {batch.get('currency')} | Цена: {batch.get('price')}\n"
        f"💬 Комментарий: {comment}\n"
        f"🃏 Карты:\n{items_block}\n"
        f"🕒 Создано: {batch.get('created_at')}\n"
        f"📌 Статус: {batch.get('status')}"
    )


@router.callback_query(F.data.startswith(f"{EX_CB_PROOF}:"))
async def cb_exchange_proof(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(call.data.rsplit(":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    proof = batch.get("proof_photo_id")
    if not proof:
        await call.message.answer("Фото подтверждения не прикреплено.")
        return

    await call.message.answer_photo(proof, caption=f"📸 Фото подтверждения (Биржа #{batch_id})")


async def cb_exchange_approve(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(call.data.rsplit(":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    await set_exchange_batch_status(batch_id, "approved")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ Биржа-заявка #{batch_id} принята.")

    # ✅ логи
    try:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_text = (
            "🛒 <b>Биржа: одобрено</b>\n"
            f"🕒 {now_str} (МСК)\n"
            f"Batch: <code>{batch_id}</code>\n"
            f"Админ: {_user_link(call.from_user.id, call.from_user.username)}\n"
            f"Пользователь: {_user_link(int(batch.get('user_id')), batch.get('username'))}\n"
            "Действие: exchange_approve через бота."
        )
        await send_admin_log(call.bot, log_text)
        await log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_approve",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


async def cb_exchange_reject(call: CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id):
        await call.answer("Только для админов.", show_alert=True)
        return

    batch_id = int(call.data.rsplit(":", 1)[-1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.message.answer(f"Заявка #{batch_id} не найдена.")
        return

    await set_exchange_batch_status(batch_id, "rejected")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"❌ Биржа-заявка #{batch_id} отклонена.")

    # ✅ логи
    try:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        log_text = (
            "🛒 <b>Биржа: отклонено</b>\n"
            f"🕒 {now_str} (МСК)\n"
            f"Batch: <code>{batch_id}</code>\n"
            f"Админ: {_user_link(call.from_user.id, call.from_user.username)}\n"
            f"Пользователь: {_user_link(int(batch.get('user_id')), batch.get('username'))}\n"
            "Действие: exchange_reject через бота."
        )
        await send_admin_log(call.bot, log_text)
        await log_admin_action(
            user_id=call.from_user.id,
            action_type="exchange_reject",
            auction_id=None,
            details=f"batch_id={batch_id}; user_id={batch.get('user_id')}",
        )
    except Exception:
        pass


PENDING_EXCHANGE_PAGE_SIZE = 5


def _fmt_dt_msk(dt: Any) -> str:
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(ANNOUNCE_TZ).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)


def _user_ref(username: Optional[str], user_id: Any) -> str:
    uid = str(user_id) if user_id is not None else "?"
    uname = (username or "").strip()
    if uname and not uname.startswith("@"):
        uname = "@" + uname
    return f"{html.escape(uname)} <code>{uid}</code>" if uname else f"<code>{uid}</code>"


async def _edit_or_send(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup]) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


def _fmt_age_short(created_at: Any) -> str:
    """Сколько заявка висит на модерации: 0м / 1ч 12м / 2д 3ч."""
    if not isinstance(created_at, datetime):
        return "—"
    try:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt.astimezone(UTC)
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0

        mins = seconds // 60
        hours = mins // 60
        days = hours // 24
        mins = mins % 60
        hours = hours % 24

        if days > 0:
            return f"{days}д {hours}ч"
        if hours > 0:
            return f"{hours}ч {mins}м"
        return f"{mins}м"
    except Exception:
        return "—"


def format_pending_exchange_batch_card(batch: dict, *, items_count: int) -> str:
    batch_id = int(batch.get("batch_id") or 0)
    created = batch.get("created_at")
    age = _fmt_age_short(created)

    username = (batch.get("username") or "").strip()
    user_id = batch.get("user_id")
    user_line = _user_ref(username, user_id)

    deck_id = batch.get("deck_id")
    deck_name = (batch.get("deck_name") or "").strip()
    deck_title = deck_name or (f"ID {deck_id}" if deck_id else "-")

    mode_labels = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }
    mode = (batch.get("mode") or "").strip()
    mode_title = mode_labels.get(mode, mode or "-")

    currency = (batch.get("currency") or "алмазы").strip().lower()
    cur_emoji = _cur_emoji(currency)

    price = batch.get("price")
    comment = (batch.get("comment") or "").strip() or "-"

    created_str = _fmt_dt_msk(created)

    # стиль "как заявка на аукцион": короткие строки + иконки
    return (
        "🧾 <b>Заявка на биржу</b>\n"
        f"🆔 <b>ID Batch:</b> <code>{batch_id}</code>\n"
        f"🕒 <b>Отправлено:</b> {html.escape(created_str)} (МСК)\n"
        f"⏳ <b>На модерации:</b> {html.escape(age)}\n"
        f"👤 <b>Пользователь:</b> {user_line}\n"
        f"📚 <b>Колода:</b> <b>{html.escape(deck_title)}</b>\n"
        f"🎛 <b>Режим:</b> <b>{html.escape(mode_title)}</b>\n"
        f"💰 <b>Цена:</b> <b>{html.escape(str(price))} {cur_emoji}</b> ({html.escape(currency)})\n"
        f"🃏 <b>Карт:</b> <b>{items_count}</b>\n"
        f"💬 <b>Комментарий:</b> {html.escape(comment)}"
    )


def pending_exchange_kb(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    row1: list[InlineKeyboardButton] = []
    if has_proof:
        row1.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    row1.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    b.row(*row1)

    b.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"exchange_approve|{batch_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"exchange_reject|{batch_id}"),
    )

    b.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}"))
    return b.as_markup()


async def _get_exchange_cover_media(batch_id: int) -> tuple[str | None, str]:
    """
    Обложка для биржи (file_id, kind):
    1) cards.image_id (+ media_type) по первой карте в exchange_items
    2) fallback: exchange_batches.proof_photo_id (как фото)
    """
    # 1) обложка по карте
    row = await fetchrow(
        """
        SELECT c.image_id                                  AS media_id,
               COALESCE(NULLIF(c.media_type, ''), 'photo') AS kind
        FROM public.exchange_items ei
                 JOIN public.cards c ON c.card_id = ei.card_id
        WHERE ei.batch_id = $1
        ORDER BY COALESCE(ei.item_id, 0) ASC LIMIT 1
        """,
        batch_id,
    )
    if row:
        media_id = (row.get("media_id") or "").strip()
        kind = (row.get("kind") or "photo").strip().lower()
        if kind not in {"photo", "video", "animation"}:
            kind = "photo"
        if media_id:
            return media_id, kind

    # 2) fallback на пруф
    b = await fetchrow(
        """
        SELECT proof_photo_id
        FROM public.exchange_batches
        WHERE batch_id = $1
        """,
        batch_id,
    )
    proof_id = (b.get("proof_photo_id") or "").strip() if b else ""
    if proof_id and proof_id.upper() != "NO_PROOF":
        return proof_id, "photo"

    return None, "photo"


def _format_exchange_user_notice(
        *,
        batch: dict,
        deck_name: str,
        items_count: int,
        currency: str,
        price: int | None,
        has_proof: bool,
        comment: str,
        title: str,  # "отклонена" / "удалена"
        reason: str | None,
        moderator_html: str,
) -> str:
    batch_id = int(batch["batch_id"])
    cur_emoji = _cur_emoji(currency)
    price_line = f"{price} {cur_emoji} ({html.escape(currency)})" if price is not None else f"— {cur_emoji} ({html.escape(currency)})"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    created_at = batch.get("created_at")
    created_at_msk = _fmt_msk_dt(created_at) if created_at else "—"

    # сколько висело на модерации
    try:
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            waited = _human_wait(int((datetime.now(timezone.utc) - created_at).total_seconds()))
        else:
            waited = "—"
    except Exception:
        waited = "—"

    cmt = (comment or "").strip() or "-"

    lines = [
        f"❌ <b>Ваша заявка на биржу {html.escape(title)}</b>",
        f"🕒 Отправлено: {created_at_msk} (МСК)",
        f"⏳ На модерации: {waited}",
        f"🆔 Batch: <code>{batch_id}</code>",
        "",
        f"📚 Колода: <b>{html.escape(deck_name)}</b>",
        f"🎛 Режим: <b>{html.escape(str(batch.get('mode') or '—'))}</b>",
        f"🃏 Карт: <b>{items_count}</b>",
        f"💰 Цена: <b>{price_line}</b>",
        f"📸 Пруф: <b>{proof_line}</b>",
        f"💬 Комментарий: <i>{html.escape(cmt)}</i>",
    ]

    if reason is not None:
        rsn = (reason or "").strip() or "—"
        lines += [
            f"🔒 Причина: <i>{html.escape(rsn)}</i>",
        ]

    lines += [
        "",
        "Если есть вопросы — обратитесь к администрации.",
        f"Модератор: {moderator_html}",
        "",
        "Если хочешь, можешь сказать спасибо ниже ❤️\n",
    ]
    return "\n".join(lines)


async def show_pending_exchange_requests_all(message: types.Message, limit: int = 50) -> None:
    """Показывает ВСЕ (точнее первые limit) заявки биржи одной лентой."""
    limit = max(1, min(int(limit or 50), 200))

    total_row = await fetchrow(
        """
        SELECT COUNT(*) ::int AS cnt
        FROM public.exchange_batches
        WHERE COALESCE(status, 'pending') = 'pending'
        """
    )
    total = int((total_row or {}).get("cnt") or 0)
    if total <= 0:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    rows = await fetch(
        """
        SELECT eb.batch_id,
               eb.user_id,
               u.username,
               u.full_name,
               u.is_luxury,
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
            LIMIT $1
        """,
        limit,
    )
    rows = [dict(r) for r in (rows or [])]
    shown = len(rows)

    head = (
        "🛒 <b>Заявки на биржу</b>\n"
        f"Всего: <b>{total}</b>\n"
        f"Показываю: <b>{shown}</b>"
    )
    if total > shown:
        head += f"\n\n⚠️ Заявок больше, чем лимит. Остальные удобнее листать в режиме «По одному»."

    await message.answer(head, parse_mode="HTML")

    for r in rows:
        batch_id = int(r.get("batch_id") or 0)

        proof_id = (r.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
        items_count = int(r.get("items_count") or 0)

        status_line = "👑 <b>Статус пользователя:</b> " + ("Лакшери" if bool(r.get("is_luxury")) else "Обычный")
        text = status_line + "\n\n" + format_pending_exchange_batch_card(r, items_count=items_count)

        kb = pending_exchange_kb(batch_id, has_proof=has_proof)

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


async def show_pending_exchange_requests(message: types.Message, page: int = 0) -> None:
    # page намеренно игнорируем: показываем всё сразу, без навигации
    rows = await fetch(
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
        """
    )
    rows = [dict(r) for r in (rows or [])]

    if not rows:
        await message.answer("Нет заявок на биржу на модерацию.")
        return

    await message.answer(
        f"🛒 <b>Заявки на биржу</b>\n"
        f"Всего: <b>{len(rows)}</b>\n\n"
        "Ниже все заявки одной лентой (без страниц).",
        parse_mode="HTML",
    )

    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        proof_id = (r.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

        items_count = int(r.get("items_count") or 0)
        text = format_pending_exchange_batch_card(r, items_count=items_count)

        # ✅ только кнопки действий, без “Обновить/стрелки”
        kb = pending_exchange_kb_simple(batch_id=batch_id, has_proof=has_proof)

        # ✅ без фото-карты вообще, просто текстом
        await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def pending_exchange_kb_simple(*, batch_id: int, has_proof: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # верхний ряд: показать пруф / состав (если у тебя есть кнопка состава)
    if has_proof:
        kb.button(text="📸 Пруф", callback_data=f"exchange_proof|{batch_id}")
    kb.button(text="🧾 Состав", callback_data=f"exchange_items|{batch_id}")

    # второй ряд: одобрить / отклонить
    kb.button(text="✅ Одобрить", callback_data=f"exchange_approve|{batch_id}")
    kb.button(text="❌ Отклонить", callback_data=f"exchange_reject|{batch_id}")

    # третий ряд: удалить
    kb.button(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}")

    # раскладка
    kb.adjust(2, 2, 1)
    return kb.as_markup()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data == "craft_uid:help")
async def addlot_craft_uid_help(call: CallbackQuery):
    await call.answer()

    await call.message.answer_photo(
        GUIDE_UID_CRAFT_PHOTO_ID,
        caption="🆔 <b>Гайд</b>: крафт по UID",
        parse_mode="HTML",
    )
    await call.message.answer(
        GUIDE_UID_CRAFT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data.startswith("craft_uid:"))
async def addlot_craft_uid_answer(call: CallbackQuery, state: FSMContext):
    raw = (call.data or "").split(":", 1)[-1].strip().lower()
    craft_ok = raw in {"yes", "1", "true", "да"}

    await state.update_data(craft_uid_possible=craft_ok)

    # убираем кнопки, чтобы не тыкали повторно
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    d = await state.get_data()
    currency = d.get("currency", "алмазы")
    emoji = _cur_emoji(currency)
    kind_key = str(d.get("auction_kind") or "standard").strip().lower()
    accepted_label = html.escape(
        currency_choices_label(d.get("accepted_currencies"), fallback=currency, custom_terms=d.get("custom_offer_terms"))
    )

    craft_text = "✅ Да" if craft_ok else "❌ Нет"
    comment = (d.get("comment") or "").strip()

    if kind_key == AuctionKind.REVERSE.value:
        price_line = (
            f"Валюта ставок: {accepted_label}\n"
            "Побеждает минимальная ставка.\n"
        )
    elif kind_key == AuctionKind.FREE.value:
        price_line = f"Принимаются предложения: {accepted_label}\n"
    else:
        price_line = f"Минимальная ставка: {d.get('start_price')} {emoji}\n"

    preview = (
        f"<b>Лот:</b> {html.escape(str(d.get('card_name') or '-'))}\n"
        f"{price_line}"
        f"Крафт на UID: {craft_text}\n"
        f"Комментарий: {html.escape(comment or '-')}\n"
        "Всё верно? Отправить заявку на модерацию?"
    )

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Подтвердить"), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await call.message.answer(preview, reply_markup=kb, parse_mode="HTML")
    await state.set_state(UserAddLotFSM.waiting_for_confirmation)
    await call.answer()


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


async def _bot_send_media_any(
        bot: Bot,
        *,
        chat_id: int | str,
        file_id: str,
        caption: str,
        reply_markup=None,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
) -> types.Message | None:
    file_id = (file_id or "").strip()
    if not file_id:
        return None

    # 1) как фото
    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
        )
    except TelegramBadRequest as e:
        s = str(e)
        # Нормальный кейс: file_id указывает на видео
        if (
                "Video as Photo" not in s
                and "type Video" not in s
                and "can't use file of type Video as Photo" not in s
        ):
            raise
    except Exception:
        pass

    # 2) как видео
    try:
        return await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            supports_streaming=True,
            disable_notification=disable_notification,
        )
    except Exception:
        pass

    # 3) как анимация (gif)
    try:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
        )
    except Exception:
        return None


CB_WIN_THANKS = "win:thanks"


def admin_tag(user: User) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else f"id{user.id}"


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    await _ensure_admin_thanks_tables()
    row = await fetchrow("""
                         SELECT COALESCE(SUM(thanks_count), 0) AS total,
                                COUNT(*)                       AS users
                         FROM public.admin_thanks_users
                         WHERE author = $1
                         """, author)
    return int(row["total"] or 0), int(row["users"] or 0)


async def build_thanks_kb(auction_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    total, users = await get_admin_thanks_totals(moderator_tag)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"{CB_WIN_THANKS}:{auction_id}:{moderator_tag}",
        )
    ]])


def _tg_clean(text: str) -> str:
    return _BR_RE.sub("\n", text or "")


from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError


def _media_kind_from_error(e: Exception) -> str | None:
    s = str(e).lower()
    if "video as photo" in s:
        return "video"
    if "animation as photo" in s or "gif as photo" in s:
        return "animation"
    return None


def _fmt_msk_dt(dt: object) -> str:
    # если у тебя уже есть _fmt_dt_msk — используй его вместо этого
    try:
        return _fmt_dt_msk(dt)  # type: ignore[name-defined]
    except Exception:
        # fallback: просто локальный формат, без TZ магии
        if isinstance(dt, datetime):
            return dt.strftime("%d.%m.%Y %H:%M")
        return "—"


def _human_wait(delta_sec: int) -> str:
    if delta_sec < 0:
        delta_sec = 0
    days, sec = divmod(delta_sec, 86400)
    hours, sec = divmod(sec, 3600)
    mins, _ = divmod(sec, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


def _user_link(user_id: int, username: Optional[str]) -> str:
    label = f"@{username}" if username else f"id:{user_id}"
    return f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'


def _items_block(items: list[dict], *, max_lines: int = 30) -> str:
    lines: list[str] = []
    for it in items[:max_lines]:
        hero = html.escape(str(it.get("hero_name") or "—"))
        card = html.escape(str(it.get("card_name") or "—"))
        qty = int(it.get("qty") or 1)
        lines.append(f"• {hero} — {card} × {qty}")
    if len(items) > max_lines:
        lines.append(f"…и ещё {len(items) - max_lines} шт.")
    return "\n".join(lines) if lines else "—"


def _format_exchange_channel_post(batch: dict, deck_name: str, items: list[dict]) -> str:
    batch_id = int(batch["batch_id"])
    price = batch.get("price")
    currency = (batch.get("currency") or "").lower()
    em = CURRENCY_EMOJI.get(currency, "💰")
    comment = (batch.get("comment") or "").strip()

    parts = [
        f"🛒 <b>Биржа</b> • <code>{batch_id}</code>",
        f"🗂 <b>Колода:</b> {html.escape(deck_name)}",
        "",
        "🃏 <b>Состав:</b>",
        _items_block(items, max_lines=35),
        "",
        f"💵 <b>Цена:</b> {html.escape(str(price))} {em} ({html.escape(currency or '—')})",
    ]
    if comment:
        parts.append(f"📝 <b>Комментарий:</b> {html.escape(comment)}")

    return "\n".join(parts)


def _approved_kb(batch_id: int, *, has_proof: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    row1: list[InlineKeyboardButton] = []
    if has_proof:
        row1.append(InlineKeyboardButton(text="📸 Подтверждение", callback_data=f"exchange_proof|{batch_id}"))
    row1.append(InlineKeyboardButton(text="🃏 Состав", callback_data=f"exchange_items|{batch_id}"))
    b.row(*row1)

    b.row(
        InlineKeyboardButton(text="📣 Рассылка", callback_data=f"exchange_broadcast|{batch_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"exchange_delete|{batch_id}"),
    )
    return b.as_markup()


def _delete_confirm_kb(batch_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"exchange_delete_yes|{batch_id}"),
        InlineKeyboardButton(text="⬅️ Нет", callback_data=f"exchange_delete_no|{batch_id}"),
    )
    return b.as_markup()


@router.callback_query(F.data.startswith("exchange_items|"))
@admin_only
async def exchange_items(call: CallbackQuery):
    batch_id = int(call.data.split("|", 1)[1])
    items = await get_exchange_cards_for_batch(batch_id)
    if not items:
        await call.answer("Состав пустой (или не найден).", show_alert=True)
        return

    text = "🃏 <b>Состав заявки биржи</b>\n" + _items_block(items, max_lines=80)
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("exchange_approve|"))
@admin_only
async def exchange_approve(call: CallbackQuery, bot: Bot):
    batch_id = int(call.data.split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    admin = call.from_user
    ok = await set_exchange_batch_moderation(
        batch_id,
        "approved",
        moderator_id=admin.id,
        moderator_username=admin.username or admin.full_name,
        moderator_comment="",
    )
    if not ok:
        await call.answer("Не удалось обновить статус в БД.", show_alert=True)
        return

    # уведомим пользователя
    try:
        await bot.send_message(
            int(batch["user_id"]),
            f"✅ Ваша заявка на биржу <code>{batch_id}</code> одобрена.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # лог
    log_text = (
        "🛒 <b>Биржа: одобрено</b>\n"
        f"Batch: <code>{batch_id}</code>\n"
        f"Админ: {_user_link(admin.id, admin.username)}\n"
        f"Пользователь: {_user_link(int(batch['user_id']), batch.get('username'))}\n"
    )
    await send_admin_log(bot, log_text)

    # обновим клавиатуру на “одобрено”
    proof = (batch.get("proof_photo_id") or "").strip()
    try:
        await call.message.edit_reply_markup(reply_markup=_approved_kb(batch_id, has_proof=bool(proof)))
    except Exception:
        pass

    await call.answer("Одобрено")


@router.callback_query(F.data.startswith("exchange_reject|"))
@admin_only
async def exchange_reject_start(call: CallbackQuery, state: FSMContext):
    batch_id = int(call.data.split("|", 1)[1])
    await state.update_data(
        ex_batch_id=batch_id,
        ex_origin_chat_id=call.message.chat.id,
        ex_origin_msg_id=call.message.message_id,
    )
    await state.set_state(ModActionFSM.waiting_for_reject_exchange_reason)
    await call.message.answer(
        f"Напиши причину отклонения заявки биржи <code>{batch_id}</code>:",
        parse_mode="HTML",
    )
    await call.answer()


async def safe_send_media(
        bot: Bot,
        *,
        chat_id: int,
        file_id: str,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str = "HTML",
        protect_content: bool = False,
) -> bool:
    """
    Надёжно отправляет file_id как медиа.
    Порядок: photo -> video -> animation -> fallback text.
    Возвращает True если отправилось медиа, False если ушло текстом.
    """
    file_id = (file_id or "").strip()
    if not file_id:
        await bot.send_message(
            chat_id,
            caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return False

    # 1) photo
    try:
        await bot.send_photo(
            chat_id,
            photo=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
        return True
    except Exception:
        pass

    # 2) video
    try:
        await bot.send_video(
            chat_id,
            video=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
            supports_streaming=True,
        )
        return True
    except Exception:
        pass

    # 3) animation (gif)
    try:
        await bot.send_animation(
            chat_id,
            animation=file_id,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
        return True
    except Exception:
        pass

    # 4) fallback text
    await bot.send_message(
        chat_id,
        caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    return False


@router.message(ModActionFSM.waiting_for_reject_exchange_reason, F.chat.type == "private")
@admin_only
async def exchange_reject_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()

    # поддержим старые ключи на всякий случай, но дальше в проекте используй ТОЛЬКО exchange_batch_id
    batch_id = int(data.get("exchange_batch_id") or data.get("ex_batch_id") or 0)
    reason = (message.text or "").strip()

    if not batch_id:
        await message.answer("Не найден ID заявки (batch_id).")
        await state.clear()
        return

    if not reason:
        await message.answer("Нужна причина текстом.")
        return

    # бери ту функцию, которая у тебя реально есть (у тебя встречались обе)
    batch = await get_exchange_batch_by_id(
        batch_id) if "get_exchange_batch_by_id" in globals() else await get_exchange_batch(batch_id)
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
        await message.answer("Не удалось обновить статус в базе.")
        return

    # 1) Уведомление пользователю — КРАСИВО (как у аукциона)
    try:
        await notify_exchange_user_moderation(
            message.bot,
            batch=batch,
            admin_user=message.from_user,
            title="отклонена",
            reason=reason,
        )
    except Exception:
        # даже если уведомление не ушло, модерация уже применена — не валим весь хендлер
        pass

    # 2) ЛОГ в лог-чат — в едином стиле (как у обычной заявки)
    try:
        deck_id = int(batch.get("deck_id") or 0)

        deck_name = None
        try:
            drow = await fetchrow("SELECT name FROM public.decks WHERE id=$1", deck_id)
            if drow:
                deck_name = (drow.get("name") or "").strip() or None
        except Exception:
            deck_name = None
        deck_title = deck_name or (f"#{deck_id}" if deck_id else "—")

        # сколько карт
        items_cnt = 0
        try:
            r = await fetchrow("SELECT COUNT(*) AS cnt FROM public.exchange_items WHERE batch_id=$1", batch_id)
            items_cnt = int((r or {}).get("cnt") or 0)
        except Exception:
            items_cnt = 0

        proof_id = (batch.get("proof_photo_id") or "").strip()
        has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

        when_msk = _fmt_dt_msk(datetime.now(timezone.utc))

        log_text = format_exchange_moderation_log(
            action_title="Отклонена заявка на биржу",
            action_code="exchange_reject через бота",
            when_msk=when_msk,
            admin_user=message.from_user,
            batch_id=batch_id,
            sender_username=batch.get("username"),
            sender_id=batch.get("user_id"),
            deck_name=deck_title,
            deck_id=deck_id,
            mode=str(batch.get("mode") or "—"),
            items_count=items_cnt,
            price=int(batch["price"]) if batch.get("price") is not None else None,
            currency=str(batch.get("currency") or "алмазы"),
            has_proof=has_proof,
            comment=str(batch.get("comment") or ""),
            moderator_comment=reason,
        )
        await send_admin_log(message.bot, log_text)
    except Exception:
        pass

    # 3) Ответ админу
    await message.answer(f"Отклонено ❌ (Batch {batch_id})")
    await state.clear()


@router.callback_query(F.data.startswith("exchange_delete|"))
@admin_only
async def exchange_delete_ask(call: CallbackQuery):
    batch_id = int(call.data.split("|", 1)[1])
    await call.message.answer(
        f"Точно удалить заявку биржи <code>{batch_id}</code>?",
        parse_mode="HTML",
        reply_markup=_delete_confirm_kb(batch_id),
    )
    await call.answer()


@router.callback_query(ExchangeFSM.waiting_for_copies, F.data.startswith("ex_copies:"))
async def ex_copies_selected(call: CallbackQuery, state: FSMContext) -> None:
    payload = (call.data or "").split(":", 1)[1].strip()

    if payload == "other":
        await call.message.answer("Введи число (например 2). Минимум 1, максимум 50.")
        await call.answer()
        return

    try:
        copies = int(payload)
    except Exception:
        await call.answer("Некорректное число.", show_alert=True)
        return

    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await call.message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await call.answer()


@router.message(ExchangeFSM.waiting_for_copies)
async def ex_copies_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    text_low = text.lower()

    # выход
    if text_low in {"🏠 меню", "меню", "/start", "🛒 биржа", "биржа", "📦 аукцион", "аукцион"}:
        await state.clear()
        await message.answer("Ок, выхожу из оформления заявки биржи.")
        return

    # команды не жрём FSM-ом
    if text.startswith("/"):
        raise SkipHandler()

    if not text.isdigit():
        await message.answer("Нужно число. Например: 2")
        return

    copies = int(text)
    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(F.data.startswith("exchange_delete_no|"))
@admin_only
async def exchange_delete_no(call: CallbackQuery):
    await call.answer("Ок, не удаляем")


@router.callback_query(F.data.startswith("exchange_delete_yes|"))
@admin_only
async def exchange_delete_yes(call: CallbackQuery, bot: Bot):
    batch_id = int(call.data.split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    admin = call.from_user

    # если уже публиковали, попробуем снести пост
    posted_chat_id = batch.get("posted_chat_id")
    posted_message_id = batch.get("posted_message_id")
    if posted_chat_id and posted_message_id:
        try:
            await bot.delete_message(int(posted_chat_id), int(posted_message_id))
        except Exception:
            pass

    await set_exchange_batch_moderation(
        batch_id,
        "deleted",
        moderator_id=admin.id,
        moderator_username=admin.username or admin.full_name,
        moderator_comment="deleted",
    )
    await set_exchange_batch_deleted(batch_id)

    # уведомим пользователя (мягко)
    try:
        await bot.send_message(
            int(batch["user_id"]),
            f"🗑 Ваша заявка на биржу <code>{batch_id}</code> удалена модератором.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # лог
    log_text = (
        "🛒 <b>Биржа: удалено</b>\n"
        f"Batch: <code>{batch_id}</code>\n"
        f"Админ: {_user_link(admin.id, admin.username)}\n"
        f"Пользователь: {_user_link(int(batch['user_id']), batch.get('username'))}\n"
    )
    await send_admin_log(bot, log_text)

    await call.answer("Удалено")


@router.callback_query(F.data.startswith("exchange_broadcast|"))
@admin_only
async def exchange_broadcast(call: CallbackQuery, bot: Bot):
    batch_id = int(call.data.split("|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    deck = await get_deck_by_id(int(batch.get("deck_id") or 0))
    deck_name = str((deck or {}).get("deck_name") or f"#{batch.get('deck_id')}")

    items = await get_exchange_cards_for_batch(batch_id)

    text = _format_exchange_channel_post(batch, deck_name, items)

    proof = (batch.get("proof_photo_id") or "").strip()
    try:
        if proof and proof.upper() != "NO_PROOF":
            msg = await bot.send_photo(
                AUCTION_CHANNEL_ID,
                photo=proof,
                caption=text[:1024],
                parse_mode="HTML",
            )
        else:
            msg = await bot.send_message(AUCTION_CHANNEL_ID, text, parse_mode="HTML")
    except Exception:
        await call.answer("Не удалось отправить в канал.", show_alert=True)
        return

    await set_exchange_batch_posted(batch_id, chat_id=AUCTION_CHANNEL_ID, message_id=msg.message_id)

    link = ""
    if AUCTION_CHANNEL_USERNAME:
        link = f"\n🔗 https://t.me/{AUCTION_CHANNEL_USERNAME}/{msg.message_id}"

    await send_admin_log(
        bot,
        "🛒 <b>Биржа: рассылка</b>\n"
        f"Batch: <code>{batch_id}</code>\n"
        f"Канал msg_id: <code>{msg.message_id}</code>{html.escape(link)}\n",
    )

    await call.answer("Отправлено")


def _default_step_for_currency(currency: str) -> int:
    cur = _norm_currency(currency)
    if cur == "алмазы":
        return 90
    if cur == "чашки":
        return 2
    return 1


@router.message(Command("autobid_set"))
@admin_only
async def cmd_autobid_set(message: types.Message):
    args = (message.text or "").split()
    if len(args) < 5:
        await message.answer(
            "Формат:\n"
            "/autobid_set <лот_id> <@username> <max_amount> <password>\n"
            "Пример:\n"
            "/autobid_set 5981 @aam_cheshire 1800 2069"
        )
        return

    try:
        auction_id = int(args[1])
        username = args[2].lstrip("@").strip()
        max_amount = int(args[3])
        password = args[4].strip()
    except Exception:
        await message.answer(
            "Кривые аргументы.\n"
            "Надо так: /autobid_set <лот_id> <@username> <max_amount> <password>"
        )
        return

    if password != str(AUTOBID_SET_PASSWORD):
        await message.answer("Неверный пароль.")
        return

    lot = await get_lot_by_id(auction_id)
    if not lot:
        await message.answer("Лот не найден.")
        return

    user = await get_user_by_username(username)
    if not user:
        await message.answer(
            "Пользователь не найден в базе.\n"
            "Сначала добавь/обнови юзеров (refresh_users или /start у него)."
        )
        return

    currency = (lot.get("currency") or "").strip().lower()

    # шаг автоставки (твоя логика: для алмазов двигаемся +30, это кратно шагу аукциона)
    if currency == "diamonds":
        step = 30
    elif currency == "tea":
        step = 2
    elif currency == "treasures":
        step = 10
    else:
        step = 1

    await upsert_autobid(
        auction_id=auction_id,
        target_user_id=int(user["user_id"]),
        target_username=username,  # без @ (ты уже сделал lstrip("@"))
        max_amount=max_amount,
        step=step,
        created_by=int(message.from_user.id),  # кто поставил автоставку (админ)
        is_active=True,  # можно не писать, по умолчанию True
    )

    await message.answer(
        "✅ Автоставка включена:\n"
        f"• Лот: {auction_id}\n"
        f"• Кому засчитывать: @{username}\n"
        f"• Максимум: {max_amount}\n"
        f"• Шаг автоставки: {step}\n"
        "Правило cap для алмазов (+90) применяется в юзерботе."
    )


@router.message(Command("autobid_stop"), F.chat.type == "private")
async def cmd_autobid_stop(message: types.Message):
    uid = message.from_user.id
    if not await is_admin(uid):
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: /autobid_stop <lot_id> <@username>")
        return

    auction_id = int(parts[1])
    username = parts[2].lstrip("@").strip()
    user = await get_user_by_username(username)
    if not user:
        await message.answer("Юзер не найден в БД.")
        return

    ok = await disable_autobid(auction_id=auction_id, target_user_id=int(user["user_id"]))
    await message.answer("🛑 Отключено." if ok else "Не нашёл активную автоставку для этого пользователя/лота.")


@router.message(Command("autobid_list"), F.chat.type == "private")
@admin_only
async def cmd_autobid_list(message: types.Message):
    def _cur_emoji(cur: str | None) -> str:
        c = (cur or "").lower()
        if "алмаз" in c:
            return "💎"
        if "чаш" in c or "чай" in c:
            return "🍵"
        if "сокров" in c:
            return "🗝️"
        return ""

    bids = await list_autobids()  # по умолчанию only_active=True
    if not bids:
        await message.answer("🤖 Активных автоставок нет.")
        return

    lines = ["🤖 Активные автоставки", "Формат: lot_id • @username • base → base+step", ""]
    for row in bids:
        lot_id = int(row["auction_id"])
        uid = int(row["target_user_id"])
        uname = (row.get("target_username") or "—").lstrip("@")
        base = int(row.get("max_amount") or 0)
        step = int(row.get("step") or 1)
        emoji = _cur_emoji(row.get("auction_currency"))
        lines.append(f"• {lot_id} • @{uname} (id {uid}) • {base} → {base + step}{emoji}")

    await message.answer("\n".join(lines))


async def _uid_verification_badge(user_id: int) -> str:
    try:
        from db.db import get_user_verified_uid, is_user_uid_banned
        if await is_user_uid_banned(int(user_id)):
            return "⛔️ UID в ЧС"
        uid = await get_user_verified_uid(int(user_id))
        return "✅ UID верифицирован" if uid else "❌ НЕТ ВЕРИФИКАЦИИ"
    except Exception:
        return "❌ НЕТ ВЕРИФИКАЦИИ"


async def _format_user_status(bot: Bot, user_id: int) -> str:
    # 1) админ
    try:
        if await is_admin(int(user_id)):
            return "🛡 Админ"
    except Exception:
        pass

    # 2) лакшери по чатам (самый надёжный источник)
    try:
        if LUXURY_CHAT_ID_LVL2 and await is_luxury_member(bot, user_id, LUXURY_CHAT_ID_LVL2):
            return "👑 Лакшери 2"
        if LUXURY_CHAT_ID and await is_luxury_member(bot, user_id, LUXURY_CHAT_ID):
            return "👑 Лакшери"
    except Exception:
        pass

    # 3) fallback на БД
    try:
        row = await fetchrow(
            "SELECT is_luxury, is_trusted FROM public.users WHERE user_id = $1",
            int(user_id),
        )
        if row:
            if bool(row.get("is_luxury")):
                return "👑 Лакшери"
            if bool(row.get("is_trusted")):
                return "🤝 Доверенный"
    except Exception:
        pass

    badge = await _uid_verification_badge(int(user_id))
    return f"👤 Обычный • {badge}"


# auctions.py

async def _send_user_exchange_confirmation(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = _h(preview.get("hero_name") or "—")
    card_name = _h(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            deck_line = f"🧩 {int(deck_id)} колода — {name}" if name else f"🧩 {int(deck_id)} колода"
        except Exception:
            deck_line = f"🧩 {int(deck_id)} колода"

    # редкость
    rn = _rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    # подарок/профит
    obtain_type, obtain_amount = _exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = _h(preview.get("story") or "—")
    quote = _h(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅ можно пересылать/скринить
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_multi(
        message: Message,
        *,
        user_id: int,
        created: list[dict],  # [{"batch_id": int, "card": dict, "price": int, "gain": int}]
        currency: str,
        comment: str,
        deck_id: int | None,
        mode: str,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"

    # статус пользователя (нормальный)
    status_line = await _format_user_status(message.bot, int(user_id))

    # режим по-русски
    mode_key = (mode or "").strip().lower()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, mode or "—")

    # колода
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            name = (d.get("name") or "").strip() if d else ""
            if name:
                if name.lower().startswith(str(int(deck_id))):
                    deck_line = f"🧩 {h(name)}"
                else:
                    deck_line = f"🧩 {int(deck_id)} колода — {h(name)}"
        except Exception:
            pass

    # превью для медиа
    preview_card = (created[0].get("card") or {}) if created else {}
    file_id = (preview_card.get("image_id") or "").strip()

    # определяем: это “копии одной карты”?
    same_card = False
    if created:
        c0 = created[0].get("card") or {}
        cid0 = c0.get("card_id")
        same_card = all(((x.get("card") or {}).get("card_id") == cid0) for x in created)

    caption = (
        "✅ <b>Заявки отправлены на модерацию</b>\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Режим: <b>{_h(mode_ru)}</b>\n\n"
    )

    if same_card and created:
        c = created[0]["card"]
        hero = _h(c.get("hero_name"))
        name = _h(c.get("card_name"))
        price = int(created[0].get("price") or 0)
        caption += (
                f"Карта: <b>{hero} — {name}</b>\n"
                f"Экземпляров: <b>{len(created)}</b>\n"
                f"Стоимость (фикс.) за 1: <b>{price}</b> {cur_emoji}\n\n"
                "IDs лотов: " + ", ".join(f"<code>{int(x['batch_id'])}</code>" for x in created) + "\n"
        )
    else:
        caption += f"Создано лотов: <b>{len(created)}</b>\n\n"
        for x in created:
            bid = int(x["batch_id"])
            c = x.get("card") or {}
            hero = _h(c.get("hero_name"))
            name = _h(c.get("card_name"))
            rn = _rarity_norm(c.get("rarity") or c.get("rarity_norm"))
            price = int(x.get("price") or 0)
            caption += f"• <b>{hero} — {name}</b> ({_h(rn)}) → №<code>{bid}</code> • <b>{price}</b> {cur_emoji}\n"

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    sent = None
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_copies(
        message: Message,
        *,
        batch_ids: list[int],
        user_id: int,
        card: dict,
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None,
) -> None:
    cur_emoji = currency_to_emoji(currency) or "💎"
    status_line = await _format_user_status(message.bot, int(user_id))

    hero = h(card.get("hero_name") or "—")
    name = h(card.get("card_name") or "—")

    rn = _rarity_norm(card.get("rarity") or card.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {h(rn or '—')}"

    sold = "—"
    try:
        if card.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(card["card_id"])) or 0))
    except Exception:
        pass

    ot, oa = _exchange_gain_for_card(card)
    gift_line = f"🎁 +{int(oa)} {_gift_emoji(ot)}" if oa else "—"

    story = h(card.get("story") or "—")
    quote = h(card.get("quote") or "—")

    # колода красиво
    deck_line = "—"
    if deck_id is not None:
        deck_line = f"🧩 {int(deck_id)} колода"
        try:
            d = await get_deck_by_id(int(deck_id))
            nm = (d.get("name") or "").strip() if d else ""
            if nm:
                deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                    str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
        except Exception:
            pass

    ids_line = ", ".join(str(int(x)) for x in batch_ids)

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лоты биржи №<b>{h(ids_line)}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {name}\n"
        f"Экземпляров: <b>{len(batch_ids)}</b>\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {h(sold)}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {h(comment.strip())}"

    file_id = (card.get("image_id") or "").strip()
    if file_id:
        sent = await _answer_media_any(
            message,
            file_id,
            caption=caption,
            reply_markup=None,
            protect_content=False,  # ✅
        )
        if sent:
            return

    await message.answer(caption, parse_mode="HTML")


# auctions.py

async def _send_user_exchange_confirmation_deck_split(
        message: Message,
        *,
        created: list[tuple[int, dict, int]],  # (batch_id, card, price)
        user_id: int,
        deck_id: int,
) -> None:
    status_line = await _format_user_status(message.bot, int(user_id))

    deck_line = f"🧩 {int(deck_id)} колода"
    try:
        d = await get_deck_by_id(int(deck_id))
        nm = (d.get("name") or "").strip() if d else ""
        if nm:
            deck_line = f"🧩 {h(nm)}" if nm.lower().startswith(
                str(int(deck_id))) else f"🧩 {int(deck_id)} колода — {h(nm)}"
    except Exception:
        pass

    lines = [
        "✅ <b>Заявки отправлены на модерацию</b>\n",
        f"Статус пользователя: {status_line}",
        f"Колода: {deck_line}",
        "Режим: <b>Разбор колоды</b>\n",
        f"Создано лотов: <b>{len(created)}</b>\n",
    ]

    for bid, c, price in created:
        hero = h(c.get("hero_name") or "—")
        name = h(c.get("card_name") or "—")
        rn = _rarity_norm(c.get("rarity") or c.get("rarity_norm"))
        ot, oa = _exchange_gain_for_card(c)
        gain = f"+{int(oa)}{_gift_emoji(ot)}" if oa else "—"
        lines.append(f"• №<code>{int(bid)}</code> {hero} — {name} ({h(rn)}) • <b>{int(price)}</b>💎 • {gain}")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _send_user_exchange_confirmation_card(
        message: Message,
        *,
        batch_id: int,
        user_id: int,
        cards: list[dict],
        price: int,
        currency: str,
        comment: str,
        deck_id: int | None = None,
) -> None:
    # Это твоя старая логика “по карте” (Ливий, редкость, цитата, история…)
    # Можно просто перенести сюда код из старого дубля, который сейчас у тебя в auctions.py.
    cur_emoji = currency_to_emoji(currency) or "💎"
    preview = (cards or [{}])[0]

    hero = _h(preview.get("hero_name") or "—")
    card_name = _h(preview.get("card_name") or preview.get("title") or "—")

    # статус пользователя
    try:
        lux = int(await get_user_luxury_level(user_id) or 0)
    except Exception:
        lux = 0
    status_line = f"👑 Лакшери {lux}" if lux > 0 else "👤 Обычный"

    # колода
    deck_line = "—"
    if deck_id is not None:
        try:
            d = await get_deck_by_id(int(deck_id))
            if d and d.get("name"):
                deck_line = f"🧩 {deck_id} колода — {d['name']}"
            else:
                deck_line = f"🧩 {deck_id} колода"
        except Exception:
            deck_line = f"🧩 {deck_id} колода"

    # редкость
    rn = _rarity_norm(preview.get("rarity") or preview.get("rarity_norm"))
    rarity_line = f"{_rarity_badge(rn)} {rn or '—'}"

    # продано ранее
    sold = "—"
    try:
        if preview.get("card_id") is not None:
            sold = str(int(await count_sold_by_card_id(int(preview["card_id"])) or 0))
        else:
            sold = str(int(await count_sold_same_card(hero, card_name) or 0))
    except Exception:
        pass

    obtain_type, obtain_amount = _exchange_gift_for_card(preview)
    obtain_emoji = currency_to_emoji(obtain_type) or "💎"
    gift_line = f"🎁 +{obtain_amount} {obtain_emoji}" if obtain_amount else "—"

    story = _h(preview.get("story") or "—")
    quote = _h(preview.get("quote") or "—")

    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"Лот биржи №<b>{batch_id}</b>\n"
        "⚙️ Тип: <b>Биржа</b>\n\n"
        f"{hero} — {card_name}\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> {cur_emoji}\n\n"
        f"Статус пользователя: {status_line}\n"
        f"Колода: {deck_line}\n"
        f"Редкость: {rarity_line}\n"
        f"Продано ранее: 🧿 {sold}\n"
        f"При получении в подарок даёт: {gift_line}\n"
        f"История: 📜 {story}\n"
        f"Цитата: 💬 {quote}\n"
    )

    if comment and comment.strip() and comment.strip() != "0":
        caption += f"\nКомментарий: {_h(comment)}"

    file_id = (preview.get("image_id") or "").strip()
    sent = None
    if file_id:
        try:
            sent = await _answer_media_any(message, file_id, caption=caption, reply_markup=None)
        except Exception:
            sent = None

    if not sent:
        await message.answer(caption, parse_mode="HTML")


# =======================
# 🆔 file_id helper (reply to media)
# =======================
@router.message(Command("fileid"), F.chat.type == "private")
async def cmd_fileid(message: Message):
    """Админская команда: ответь на медиа и получи file_id/unique_id.
    Работает для video/animation/photo/document/voice/video_note/sticker.
    """
    if not await is_admin(int(message.from_user.id)):
        return

    rep = message.reply_to_message
    if not rep:
        await message.answer("Ответь на сообщение с медиа (видео/фото/гиф/документ) и напиши /fileid.")
        return

    kind = None
    file_id = None
    unique_id = None

    if rep.video:
        kind = "video"
        file_id = rep.video.file_id
        unique_id = rep.video.file_unique_id
    elif rep.animation:
        kind = "animation"
        file_id = rep.animation.file_id
        unique_id = rep.animation.file_unique_id
    elif rep.photo:
        kind = "photo"
        ph = rep.photo[-1]
        file_id = ph.file_id
        unique_id = ph.file_unique_id
    elif rep.document:
        kind = "document"
        file_id = rep.document.file_id
        unique_id = rep.document.file_unique_id
    elif rep.voice:
        kind = "voice"
        file_id = rep.voice.file_id
        unique_id = rep.voice.file_unique_id
    elif rep.video_note:
        kind = "video_note"
        file_id = rep.video_note.file_id
        unique_id = rep.video_note.file_unique_id
    elif rep.sticker:
        kind = "sticker"
        file_id = rep.sticker.file_id
        unique_id = rep.sticker.file_unique_id

    if not file_id:
        await message.answer("Не вижу медиа в ответе. Нужен reply на видео/фото/гиф/документ.")
        return

    await message.answer(
        f"✅ <b>{kind}</b>\n"
        f"<b>file_id:</b> <code>{h(file_id, '')}</code>\n"
        f"<b>unique_id:</b> <code>{h(unique_id, '')}</code>",
        parse_mode="HTML",
    )


# admin_actions.py

def format_exchange_approved_log(*,
                                 created_at_msk: str,
                                 batch_id: int,
                                 admin_html: str,
                                 user_html: str,
                                 deck_title: str,
                                 mode: str,
                                 items_count: int,
                                 price: int | None,
                                 currency: str,
                                 has_proof: bool,
                                 comment: str | None,
                                 items_preview: list[str] | None = None) -> str:
    mode_key = (mode or "").strip().lower()
    mode_lbl = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, (mode or "—"))

    cur_print = (currency or "алмазы").strip()
    cur = cur_print.lower()
    cur_emoji = _cur_emoji(cur)

    proof_line = "✅ Да" if has_proof else "❌ Нет"
    price_line = f"{int(price)} {cur_emoji} ({tg_clean(cur_print)})" if price is not None else f"— {cur_emoji} ({tg_clean(cur_print)})"

    cmt = (comment or "").strip()
    if not cmt:
        cmt = "-"

    preview_lines = items_preview or []
    items_block = ""
    if preview_lines:
        items_block = "\n\n🃏 <b>Состав (превью):</b>\n" + "\n".join(preview_lines)

    return (
        "✅ <b>Биржа: заявка одобрена</b>\n"
        f"🕒 {tg_clean(created_at_msk)} (МСК)\n"
        f"🧑‍💼 Админ: {admin_html}\n"
        f"👤 Пользователь: {user_html}\n"
        f"🆔 Batch: <code>{int(batch_id)}</code>\n\n"
        f"📚 Колода: <b>{tg_clean(deck_title)}</b>\n"
        f"🎛 Режим: <b>{tg_clean(mode_lbl)}</b>\n"
        f"🃏 Карт: <b>{int(items_count)}</b>\n"
        f"💰 Цена: <b>{tg_clean(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{tg_clean(cmt)}</i>"
        f"{items_block}\n\n"
        "Действие: <code>exchange_approve</code> через бота"
    )


def _kb_exchange_approved_root() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 По колодам", callback_data="ex_appr:decks")
    kb.button(text="📄 Списком (все лоты)", callback_data="ex_appr:list:all:0")
    kb.button(text="⬅️ Назад", callback_data="admreq_back")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


async def _q_exchange_approved_decks() -> list[dict]:
    """
    Для меню "принятые лоты -> по колодам":
    всегда показываем последние ресурсные биржевые колоды,
    даже если по ним пока 0 принятых лотов.
    """
    exchange_deck_ids = await _get_exchange_deck_ids()

    rows = await fetchall(
        """
        SELECT eb.deck_id,
               COUNT(*)::int AS cnt
        FROM public.exchange_batches eb
        WHERE COALESCE(eb.status, 'pending') = 'approved'
          AND eb.deleted_at IS NULL
          AND eb.deck_id = ANY($1::int[])
        GROUP BY eb.deck_id
        ORDER BY eb.deck_id ASC
        """,
        exchange_deck_ids,
    )

    counts = {int(r["deck_id"]): int(r["cnt"] or 0) for r in (rows or [])}

    decks_all = await get_all_decks()
    deck_names = {
        int(d.get("deck_id") or 0): (d.get("name") or "").strip()
        for d in (decks_all or [])
    }

    result = []
    for deck_id in exchange_deck_ids:
        result.append({
            "deck_id": deck_id,
            "deck_name": deck_names.get(deck_id, ""),
            "cnt": counts.get(deck_id, 0),
        })

    return result


def _kb_exchange_approved_decks(decks: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    for d in decks:
        deck_id = int(d.get("deck_id") or 0)
        name = (d.get("deck_name") or "").strip()
        cnt = int(d.get("cnt") or 0)

        title = f"{deck_id} колода" + (f" — {name}" if name else "")
        kb.button(
            text=f"📚 {title} • {cnt}",
            callback_data=f"ex_appr:deck:{deck_id}"
        )

    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(1)
    return kb.as_markup()


def _kb_exchange_approved_cards(deck_id: int, cards: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in cards:
        card_id = int(c.get("card_id") or 0)
        card_name = (c.get("card_name") or "—").strip()
        hero_name = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)
        kb.button(
            text=f"🃏 {card_name} — {hero_name} • {cnt}",
            callback_data=f"ex_appr:card:{deck_id}:{card_id}",
        )
    kb.button(text="⬅️ Назад", callback_data="ex_appr:decks")
    kb.adjust(1)
    return kb.as_markup()


async def _q_exchange_approved_batches_by_card(deck_id: int, card_id: int) -> list[int]:
    # Берём batch_id ТОЛЬКО карточных лотов (card / deck_split) и исключаем "колода целиком" по mode
    rows = await fetch(
        """
        SELECT DISTINCT eb.batch_id
        FROM public.exchange_batches eb
                 JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
        WHERE COALESCE(eb.status, 'pending') = $1
          AND eb.deleted_at IS NULL
          AND eb.deck_id = $2
          AND ei.card_id = $3
          AND COALESCE(eb.mode, '') = ANY ($4::text[])
        ORDER BY eb.batch_id DESC
        """,
        EX_STATUS_APPROVED,
        int(deck_id),
        int(card_id),
        list(EX_MODE_CARDLIKE),
    )
    return [int(r["batch_id"]) for r in rows]


async def _q_exchange_deck_cards_with_counts(deck_id: int) -> list[dict]:
    # все карты колоды (из справочника cards)
    all_cards = await get_cards_by_deck(int(deck_id))  # уже импортирован в auctions.py
    all_cards = [dict(c) for c in (all_cards or [])]

    # счётчики только по карточным лотам (card/deck_split)
    counted = await get_exchange_approved_cards_by_deck(int(deck_id))  # вернёт только те, где cnt > 0
    counted = [dict(c) for c in (counted or [])]
    cnt_map = {int(c["card_id"]): int(c.get("cnt") or 0) for c in counted if c.get("card_id") is not None}

    # собираем итог: все карты + cnt (0 если нет лотов)
    result: list[dict] = []
    for c in all_cards:
        cid = int(c.get("card_id") or 0)
        result.append(
            {
                "card_id": cid,
                "card_name": (c.get("card_name") or "—").strip(),
                "hero_name": (c.get("hero_name") or "—").strip(),
                "cnt": int(cnt_map.get(cid, 0)),
                "num": c.get("num"),  # если есть
            }
        )

    # сортировка “как в колоде”
    result.sort(key=lambda x: (x["num"] is None, x["num"], x["card_id"]))
    return result


def _kb_exchange_approved_batches(deck_id: int, card_id: int, batch_ids: list[int], page: int) -> InlineKeyboardMarkup:
    page = max(0, int(page or 0))
    per_page = 12
    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)
    page = min(page, last)

    start = page * per_page
    chunk = batch_ids[start:start + per_page]

    kb = InlineKeyboardBuilder()

    # кнопки лотов по batch_id
    for bid in chunk:
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{bid}")

    # навигация по списку batch_id
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:card:{deck_id}:{card_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:card:{deck_id}:{card_id}:{page + 1}")

    # режимы просмотра
    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="📄 Показать списком", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:0")
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}")
    kb.adjust(3, 3, 3, 3, 1, 1)
    return kb.as_markup()


def _kb_exchange_approved_lot_actions(*, batch_id: int, back_cb: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Редактировать", callback_data=f"ex_edit:{batch_id}")
    kb.button(text="🗑 Удалить", callback_data=f"ex_del:{batch_id}")
    kb.button(text="⬅️ Назад", callback_data=back_cb)
    kb.adjust(2, 1)
    return kb.as_markup()


async def _format_exchange_approved_lot_caption(batch_id: int) -> str:
    b = await get_exchange_batch_by_id(int(batch_id))
    if not b:
        return f"🛒 <b>Биржа</b>\n\nBatch: <code>{int(batch_id)}</code>\n⚠️ Лот не найден."

    # базовые поля
    deck_id = int(b.get("deck_id") or 0)
    d = await get_deck_by_id(deck_id)
    deck_name = (d.get("name") or "").strip() if d else ""
    deck_line = deck_name or (f"{deck_id} колода" if deck_id else "—")

    mode = (b.get("mode") or EX_MODE_CARD).strip().lower()
    if mode == EX_MODE_DECK_SPLIT:
        mode = EX_MODE_CARD

    mode_ru = {
        EX_MODE_CARD: "Одна карта",
        EX_MODE_DECK: "Колода целиком",
    }.get(mode, mode or "—")

    currency = (b.get("currency") or "алмазы").strip()
    cur_emoji = currency_to_emoji(currency) or "💎"
    price = b.get("price")
    price_line = f"{int(price)} {cur_emoji}" if price is not None else f"— {cur_emoji}"

    proof_id = (b.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    proof_line = "✅ Да" if has_proof else "❌ Нет"

    comment = (b.get("comment") or "").strip() or "—"

    # статус пользователя (обычный/лакшери)
    user_id = int(b.get("user_id") or 0)
    lux = False
    try:
        lux = bool(await is_luxury_user(user_id))
    except Exception:
        lux = False
    user_status = "👑 Лакшери" if lux else "👤 Обычный"
    # кликабельный пользователь (username или ID)
    uname = (b.get("username") or "").strip().lstrip("@")
    if uname:
        user_label = f"@{uname}"
    elif user_id:
        user_label = f"ID {user_id}"
    else:
        user_label = "—"

    if user_id and user_label != "—":
        user_link = f'<a href="tg://user?id={user_id}">{html.escape(user_label)}</a>'
    elif uname:
        # на всякий случай, если user_id вдруг пустой
        user_link = f'<a href="https://t.me/{html.escape(uname)}">{html.escape(user_label)}</a>'
    else:
        user_link = html.escape(user_label)

    created_at = b.get("created_at")
    try:
        if isinstance(created_at, datetime):
            msk = ZoneInfo("Europe/Moscow")
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
            created_msk = created_at.astimezone(msk).strftime("%d.%m.%Y %H:%M (МСК)")
        else:
            created_msk = "—"
    except Exception:
        created_msk = "—"

    # состав + дополнительные поля по первой карте
    items = []
    try:
        items = await get_exchange_items_by_batch_id(int(batch_id))
    except Exception:
        items = []

    items_count = len(items)
    first = items[0] if items else {}

    hero = (first.get("hero_name") or "").strip()
    card_name = (first.get("card_name") or "").strip()

    rarity = (first.get("rarity") or first.get("rarity_norm") or "").strip()
    if rarity:
        rarity_line = f"{rarity}"
    else:
        rarity_line = "—"

    story = (first.get("story") or "").strip() or "—"
    quote = (first.get("quote") or "").strip() or "—"

    # подарок/экономика (если есть поля)
    gift_line = "—"
    try:
        obtain_type = (first.get("obtain_type") or "").strip()
        obtain_amount = first.get("obtain_amount")
        if obtain_type and obtain_amount is not None:
            gift_line = f"+{int(obtain_amount)} {currency_to_emoji(obtain_type) or '💎'}"
    except Exception:
        pass

    # состав строками
    comp_lines: list[str] = []
    if items:
        for i, it in enumerate(items[:12], start=1):
            hn = (it.get("hero_name") or "—").strip()
            cn = (it.get("card_name") or "—").strip()
            comp_lines.append(f"{i}. {hn} — {cn}")
        if len(items) > 12:
            comp_lines.append(f"…и ещё {len(items) - 12}")
    comp_block = "\n".join(comp_lines) if comp_lines else "—"

    header = "✅ <b>Биржа • Лот принят</b>"
    if hero or card_name:
        header = f"✅ <b>Биржа • Лот принят</b>\n{html.escape(hero)} — {html.escape(card_name)}" if hero else f"✅ <b>Биржа • Лот принят</b>\n{html.escape(card_name)}"

    text = (
        f"{header}\n"
        f"🆔 Batch: <code>{int(batch_id)}</code>\n"
        f"🕒 Дата заявки: {html.escape(created_msk)}\n\n"
        f"📚 Колода: <b>{html.escape(deck_line)}</b>\n"
        f"🎛 Режим: <b>{html.escape(mode_ru)}</b>\n"
        f"🃏 Карт: <b>{items_count}</b>\n"
        f"💰 Цена: <b>{html.escape(price_line)}</b>\n"
        f"📎 Пруф: <b>{proof_line}</b>\n"
        f"👤 Статус пользователя: <b>{user_status}</b>\n"
        f"👤 Пользователь: {user_link}\n"
        f"💬 Комментарий: <b>{html.escape(comment)}</b>\n\n"
        f"🏷 Редкость: <b>{html.escape(rarity_line)}</b>\n"
        f"🎁 При получении в подарок даёт: <b>{html.escape(gift_line)}</b>\n"
        f"📜 История: {html.escape(story)}\n"
        f"💬 Цитата: {html.escape(quote)}\n\n"
        f"🧾 <b>Состав:</b>\n{html.escape(comp_block)}"
    )
    return text


def _kb_exchange_view_cards(deck_id: int, cards: list[dict], whole_deck_count: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # показываем “колода целиком” только если она реально есть
    if int(whole_deck_count or 0) > 0:
        kb.button(
            text=f"📚 Колода целиком ({int(whole_deck_count)})",
            callback_data=f"ex_view:deck_whole:{int(deck_id)}",
        )

    for c in cards or []:
        card_id = int(c.get("card_id") or 0)
        cn = (c.get("card_name") or "—").strip()
        hn = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)

        kb.button(
            text=f"🃏 {cn} — {hn} ({cnt})",
            callback_data=f"ex_view:card:{int(deck_id)}:{card_id}",
        )

    kb.button(text="⬅️ Назад", callback_data="ex_view:decks")
    kb.adjust(1)
    return kb.as_markup()


def _kb_exchange_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:deck:{deck_id}")
    kb.adjust(1)
    return kb.as_markup()


EX_WHOLE_DECK_MODE = "deck"  # строго колода целиком
EX_WHOLE_DECK_MODES = ("deck", "whole_deck", "full_deck")


async def _q_exchange_has_whole_deck_lot(deck_id: int) -> bool:
    row = await fetchrow(
        """
        SELECT 1
        FROM exchange_batches eb
        WHERE eb.status = $1
          AND eb.mode = $2
          AND eb.deck_id = $3 LIMIT 1
        """,
        EX_STATUS_APPROVED,
        EX_MODE_DECK,
        int(deck_id),
    )
    return bool(row)


async def _q_exchange_whole_deck_count(deck_id: int) -> int:
    row = await fetchrow(
        """
        SELECT COUNT(*) ::int AS cnt
        FROM public.exchange_batches eb
        WHERE COALESCE(eb.status, 'pending') = $1
          AND eb.deleted_at IS NULL
          AND eb.deck_id = $2
          AND COALESCE(eb.mode, '') = ANY ($3::text[])
        """,
        EX_STATUS_APPROVED,
        deck_id,
        list(EX_WHOLE_DECK_MODES),
    )
    return int((row or {}).get("cnt") or 0)


@router.callback_query(F.data.startswith("ex_appr:deck:"))
@admin_only
async def ex_appr_deck(call: types.CallbackQuery):
    # ex_appr:deck:<deck_id>
    parts = (call.data or "").split(":")
    deck_id = int(parts[2])

    cards = await _q_exchange_approved_cards_by_deck(deck_id)
    whole_deck_count = await _q_exchange_whole_deck_count(deck_id)

    if not cards and int(whole_deck_count or 0) <= 0:
        await _safe_edit_text_or_caption(
            call.message,
            text="Принятых лотов по этой колоде нет.",
            reply_markup=_kb_exchange_approved_decks(await _q_exchange_approved_decks()),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text=(
            "🛒 <b>Биржа</b>\n"
            f"📚 Колода: <b>{deck_id}</b>\n\n"
            "Выберите:"
        ),
        reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
    )
    await call.answer()


async def _q_exchange_approved_whole_deck_batch_ids(deck_id: int) -> list[int]:
    rows = await fetchall(
        """
        SELECT eb.batch_id
        FROM public.exchange_batches eb
        WHERE COALESCE(eb.status, 'pending') = 'approved'
          AND eb.deleted_at IS NULL
          AND eb.deck_id = $1
          AND COALESCE(eb.mode, '') = $2
        ORDER BY eb.batch_id DESC
        """,
        int(deck_id),
        EX_MODE_DECK,
    )
    return [int(r["batch_id"]) for r in (rows or [])]


def _kb_exchange_approved_whole_batches(deck_id: int, batch_ids: list[int], page: int) -> InlineKeyboardMarkup:
    page = max(0, int(page or 0))
    per_page = 12
    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)

    if page > last:
        page = last

    start = page * per_page
    chunk = batch_ids[start:start + per_page]

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"ID {bid}", callback_data=f"ex_appr:lot:{deck_id}:0:{bid}")

    kb.adjust(3)

    # пагинация
    if total > per_page:
        kb.row(
            types.InlineKeyboardButton(
                text="⬅️" if page > 0 else " ",
                callback_data=f"ex_appr:deck_whole:{deck_id}:{page - 1}" if page > 0 else "noop",
            ),
            types.InlineKeyboardButton(text=f"{page + 1}/{last + 1}", callback_data="noop"),
            types.InlineKeyboardButton(
                text="➡️" if page < last else " ",
                callback_data=f"ex_appr:deck_whole:{deck_id}:{page + 1}" if page < last else "noop",
            ),
        )

    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_appr:deck:{deck_id}"))
    return kb.as_markup()


@router.callback_query(F.data.startswith("ex_appr:deck_whole:"))
@admin_only
async def ex_appr_deck_whole(call: types.CallbackQuery):
    # ex_appr:deck_whole:<deck_id>:<page>
    parts = (call.data or "").split(":")
    deck_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    batch_ids = await _q_exchange_approved_whole_deck_batch_ids(deck_id)
    if not batch_ids:
        await _safe_edit_text_or_caption(
            call.message,
            text="Нет принятых лотов «колода целиком».",
            reply_markup=_kb_exchange_approved_deck_menu(deck_id, await _q_exchange_approved_cards_by_deck(deck_id), 0),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text=(
            "📚 <b>Биржа → Колода целиком</b>\n\n"
            "Выбери лот по Batch-ID:"
        ),
        reply_markup=_kb_exchange_approved_whole_batches(deck_id, batch_ids, page),
    )
    await call.answer()


async def get_exchange_card_info(card_id: int) -> dict:
    """
    Мини-инфо по карте для биржи.
    Нужна, потому что в файле её вызывают, а определения нет.
    """
    card = await get_card_by_id(card_id)
    if not card:
        return {}

    deck_id = card.get("deck_id")
    deck_title = ""
    if deck_id is not None:
        deck = await get_deck_by_id(int(deck_id))
        if deck:
            deck_title = (deck.get("title") or deck.get("name") or "").strip()

    return {
        "card_id": int(card_id),
        "deck_id": int(deck_id) if deck_id is not None else None,
        "deck_title": deck_title,
        "card_name": (card.get("card_name") or card.get("title") or card.get("name") or "").strip(),
        "hero_name": (card.get("hero_name") or card.get("hero") or "").strip(),
        "rarity": (card.get("rarity") or "").strip(),
    }


def _kb_exchange_view_batches(deck_id: int, card_id: int, batch_ids: list[int], page: int = 0):
    per_page = 12
    total = len(batch_ids)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))

    s = page * per_page
    e = s + per_page
    chunk = batch_ids[s:e]

    kb = InlineKeyboardBuilder()

    for bid in chunk:
        kb.button(text=f"ID {bid}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{bid}")
    kb.adjust(3)

    if pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_view:card:{deck_id}:{card_id}:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="ex_view:noop"))
        if page < pages - 1:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_view:card:{deck_id}:{card_id}:{page + 1}"))
        kb.row(*row)

    kb.row(InlineKeyboardButton(text="📄 Показать списком", callback_data=f"ex_view:card_list:{deck_id}:{card_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_view:deck:{deck_id}"))
    return kb.as_markup()


@router.callback_query(F.data.startswith("ex_view:deck:"))
async def ex_view_deck(call: types.CallbackQuery):
    if await is_admin(call.from_user.id):
        raise SkipHandler

    deck_id = int(call.data.split(":")[2])
    await _render_exchange_deck(call, deck_id)
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:deck:"))
@admin_only
async def ex_view_deck_admin(call: types.CallbackQuery):
    deck_id = int(call.data.split(":")[2])
    await _render_exchange_deck(call, deck_id)
    await call.answer()


@router.callback_query(F.data == "ex_view:noop")
async def ex_view_noop(call: types.CallbackQuery):
    await call.answer()


async def _q_exchange_approved_cards_by_deck(deck_id: int) -> list[dict]:
    rows = await fetchall(
        """
        WITH cnts AS (SELECT ei.card_id, COUNT(*) ::int AS cnt
                      FROM public.exchange_items ei
                               JOIN public.exchange_batches eb ON eb.batch_id = ei.batch_id
                      WHERE COALESCE(eb.status, 'pending') = $1
                        AND eb.deleted_at IS NULL
                        AND eb.deck_id = $2
                        AND COALESCE(eb.mode, '') = ANY ($3::text[])
                      GROUP BY ei.card_id)
        SELECT c.card_id,
               c.card_name,
               c.hero_name,
               COALESCE(cnts.cnt, 0) ::int AS cnt
        FROM public.cards c
                 LEFT JOIN cnts ON cnts.card_id = c.card_id
        WHERE c.deck_id = $2
        ORDER BY COALESCE(cnts.cnt, 0) DESC, c.card_id ASC
        """,
        EX_STATUS_APPROVED,
        deck_id,
        list(EX_MODE_CARDLIKE),
    )
    return [dict(r) for r in rows]


async def _q_exchange_card_batches(deck_id: int, card_id: int, limit: int = 80) -> list[dict]:
    rows = await fetch(
        """
        SELECT DISTINCT eb.batch_id,
                        eb.user_id,
                        COALESCE(u.username, '')  AS username,
                        eb.price,
                        COALESCE(eb.currency, '') AS currency
        FROM public.exchange_batches eb
                 JOIN public.exchange_items ei
                      ON ei.batch_id = eb.batch_id
                          AND ei.card_id = $3
                 LEFT JOIN public.users u ON u.user_id = eb.user_id
        WHERE COALESCE(eb.status, 'pending') = $1
          AND eb.deleted_at IS NULL
          AND eb.deck_id = $2
          AND COALESCE(eb.mode, '') = ANY ($4::text[])
        ORDER BY eb.batch_id DESC
            LIMIT $5
        """,
        EX_STATUS_APPROVED,
        int(deck_id),
        int(card_id),
        list(EX_MODE_CARDLIKE),
        int(limit),
    )
    return [dict(r) for r in rows]


@router.callback_query(F.data.startswith("ex_appr:card:"))
@admin_only
async def ex_appr_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0

    # card_id=0 => колода целиком
    if card_id == 0:
        rows = await _q_exchange_whole_deck_batches(deck_id, limit=5000)
        batch_ids = [int(r["batch_id"]) for r in rows]

        if not batch_ids:
            cards = await _q_exchange_approved_cards_by_deck(deck_id)
            whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
            await _safe_edit_text_or_caption(
                call.message,
                text="Нет принятых лотов <b>колодой целиком</b>.",
                reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
            )
            await call.answer()
            return

        total = len(batch_ids)
        per_page = 12
        last = max(0, (total - 1) // per_page)
        page = max(0, min(page, last))
        chunk = batch_ids[page * per_page: page * per_page + per_page]

        # текст как у тебя на скрине
        lines = [
            "✅ <b>Биржа — Колода целиком</b>",
            f"📚 <b>Колода:</b> {deck_id}",
            f"Страница: {page + 1}/{last + 1} • Всего: {total}",
            "",
            "Выбери лот по Batch-ID:",
        ]

        # короткий список строк по текущей странице (опционально, но удобно)
        row_by_id = {int(r["batch_id"]): r for r in rows}
        for bid in chunk:
            r = row_by_id.get(int(bid), {})
            price = r.get("price")
            cur = r.get("currency") or ""
            uname = (r.get("username") or "").strip()
            cur_e = _cur_emoji(cur)
            price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
            who = f"@{uname}" if uname else "—"
            lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who}")

        await _safe_edit_text_or_caption(
            call.message,
            text="\n".join(lines),
            reply_markup=_kb_exchange_approved_batches(deck_id, card_id, batch_ids, page),
        )
        await call.answer()
        return

    # обычная карта
    batch_ids = await _q_exchange_approved_batches_by_card(deck_id, card_id)
    if not batch_ids:
        cards = await _q_exchange_approved_cards_by_deck(deck_id)
        whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
        await _safe_edit_text_or_caption(
            call.message,
            text="Нет принятых лотов по этой карте.",
            reply_markup=_kb_exchange_approved_deck_menu(deck_id, cards, whole_deck_count),
        )
        await call.answer()
        return

    await _safe_edit_text_or_caption(
        call.message,
        text="Выбери лот по Batch-ID:",
        reply_markup=_kb_exchange_approved_batches(deck_id, card_id, batch_ids, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:lot:"))
@admin_only
async def ex_appr_lot_show(call: types.CallbackQuery):
    # ex_appr:lot:<deck_id>:<card_id>:<batch_id>
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    batch_id = int(parts[4])

    caption = await _format_exchange_approved_lot_caption(batch_id)

    if card_id == 0:
        back_cb = f"ex_appr:deck_whole:{deck_id}:0"
    else:
        back_cb = f"ex_appr:card:{deck_id}:{card_id}:0"

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
            await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.answer(caption, parse_mode="HTML", reply_markup=kb)

    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:list:all:"))
@admin_only
async def ex_appr_list_all(call: types.CallbackQuery):
    # ex_appr:list:all:<page>
    page = int(call.data.split(":")[3])
    page = max(0, page)
    per_page = 12

    rows = await fetchall(
        """
        SELECT eb.batch_id,
               eb.deck_id,
               COALESCE(d.name, '') AS deck_name,
               eb.price,
               eb.currency,
               eb.mode,
               eb.created_at,
               (SELECT COUNT(*) FROM public.exchange_items ei WHERE ei.batch_id = eb.batch_id) ::int AS items_count
        FROM public.exchange_batches eb
                 LEFT JOIN public.decks d ON d.id = eb.deck_id
        WHERE COALESCE(eb.status, 'pending') = 'approved'
        ORDER BY eb.batch_id DESC
        """
    )
    rows = [dict(r) for r in (rows or [])]

    total = len(rows)
    if total <= 0:
        await call.message.edit_text(
            "Нет принятых лотов биржи.",
            reply_markup=_kb_exchange_approved_root(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    last = max(0, (total - 1) // per_page)
    page = min(page, last)
    chunk = rows[page * per_page: page * per_page + per_page]

    lines = [
        f"📄 <b>Биржа • Принятые лоты (списком)</b>\nСтраница: <b>{page + 1}/{last + 1}</b> • Всего: <b>{total}</b>\n"]
    for r in chunk:
        bid = int(r["batch_id"])
        deck_id = int(r.get("deck_id") or 0)
        deck_name = (r.get("deck_name") or "").strip()
        deck_title = f"{deck_id}" + (f" — {html.escape(deck_name)}" if deck_name else "")
        mode = (r.get("mode") or "").strip().lower()
        mode_ru = {"card": "карта", "deck": "колода", "deck_split": "разбор"}.get(mode, mode or "—")
        cur = str(r.get("currency") or "алмазы").strip()
        cur_emoji = _cur_emoji(cur.lower())
        price = r.get("price")
        price_line = f"{int(price)}{cur_emoji}" if price is not None else f"—{cur_emoji}"
        cnt = int(r.get("items_count") or 0)
        lines.append(
            f"• <code>{bid}</code> • 📚 {deck_title} • 🎛 {html.escape(mode_ru)} • 🃏 {cnt} • 💰 {html.escape(price_line)}")

    kb = InlineKeyboardBuilder()
    # быстрый выбор batch_id
    for r in chunk:
        bid = int(r["batch_id"])
        kb.button(text=f"🆔 {bid}", callback_data=f"ex_appr:lot:0:0:{bid}")

    # навигация
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:list:all:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:list:all:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(3, 3, 3, 3, 3, 1, 1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_appr:list:card:"))
@admin_only
async def ex_appr_list_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[3])
    card_id = int(parts[4])
    page = int(parts[5]) if len(parts) > 5 else 0

    per_page = 12

    # card_id=0 => колода целиком
    if card_id == 0:
        rows = await _q_exchange_whole_deck_batches(deck_id, limit=5000)
        batch_ids = [int(r["batch_id"]) for r in rows]

        if not batch_ids:
            await call.message.edit_text("Нет принятых лотов <b>колодой целиком</b>.", parse_mode="HTML")
            await call.answer()
            return

        total = len(batch_ids)
        last = max(0, (total - 1) // per_page)
        page = max(0, min(page, last))
        chunk = batch_ids[page * per_page: page * per_page + per_page]

        row_by_id = {int(r["batch_id"]): r for r in rows}

        lines = [
            "✅ <b>Биржа — Колода целиком</b>",
            f"📚 <b>Колода:</b> {deck_id}",
            f"Страница: {page + 1}/{last + 1} • Всего: {total}",
            "",
        ]
        for bid in chunk:
            r = row_by_id.get(int(bid), {})
            price = r.get("price")
            cur = r.get("currency") or ""
            uname = (r.get("username") or "").strip()
            cur_e = _cur_emoji(cur)
            price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
            who = f"@{uname}" if uname else "—"
            lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who}")

        kb = InlineKeyboardBuilder()
        for bid in chunk:
            kb.button(text=f"🆔 {int(bid)}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{int(bid)}")

        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="⬅️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page - 1}")
        nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
        if page < last:
            nav.button(text="➡️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page + 1}")

        kb.adjust(3)
        kb.row(*nav.buttons, width=3)
        kb.button(text="⬅️ Назад", callback_data=f"ex_appr:card:{deck_id}:{card_id}:0")
        kb.adjust(3, 3, 3, 3, 1)

        await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
        await call.answer()
        return

    # обычная карта (как было)
    card = await get_exchange_card_info(card_id)
    card_name = html.escape((card or {}).get("card_name") or f"ID {card_id}")
    hero_name = html.escape((card or {}).get("hero_name") or "")

    batch_ids = await _q_exchange_approved_batches_by_card(deck_id, card_id)
    if not batch_ids:
        await call.message.edit_text("Нет принятых лотов по этой карте.", parse_mode="HTML")
        await call.answer()
        return

    total = len(batch_ids)
    last = max(0, (total - 1) // per_page)
    page = max(0, min(page, last))
    chunk = batch_ids[page * per_page: page * per_page + per_page]

    lines = [
        "✅ <b>Биржа • Принятые лоты</b>",
        f"🃏 <b>Карта:</b> {card_name}" + (f" — {hero_name}" if hero_name else ""),
        f"Страница: {page + 1}/{last + 1} • Всего: {total}",
        "",
    ]

    for bid in chunk:
        batch = await get_exchange_batch_by_id(int(bid))
        if not batch:
            continue
        price = batch.get("price")
        cur = batch.get("currency") or ""
        uname = (batch.get("username") or "").strip()
        mode = (batch.get("mode") or "").strip()

        cur_e = _cur_emoji(cur)
        price_txt = f"{int(price)} {cur_e}" if price is not None else f"— {cur_e}".strip()
        who = f"@{uname}" if uname else "—"

        mode_ru = {
            "card": "Карта",
            "deck_split": "Карта",
            "deck": "Колода целиком",
            "whole_deck": "Колода целиком",
            "full_deck": "Колода целиком",
        }.get(mode, mode or "—")

        lines.append(f"• <b>#{int(bid)}</b> — {price_txt} — {who} • {mode_ru}")

    kb = InlineKeyboardBuilder()
    for bid in chunk:
        kb.button(text=f"🆔 {int(bid)}", callback_data=f"ex_appr:lot:{deck_id}:{card_id}:{int(bid)}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page - 1}")
    nav.button(text=f"{page + 1}/{last + 1}", callback_data="noop")
    if page < last:
        nav.button(text="➡️", callback_data=f"ex_appr:list:card:{deck_id}:{card_id}:{page + 1}")

    kb.adjust(3)
    kb.row(*nav.buttons, width=3)
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:card:{deck_id}:{card_id}:0")
    kb.adjust(3, 3, 3, 3, 1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("ex_del:"))
@admin_only
async def ex_appr_delete(call: types.CallbackQuery):
    batch_id = int(call.data.split(":")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Лот не найден.", show_alert=True)
        return

    ok = await set_exchange_batch_moderation(
        batch_id=batch_id,
        status="deleted",
        moderator_id=call.from_user.id,
        moderator_username=call.from_user.username or call.from_user.full_name,
        moderator_comment="deleted_by_admin",
    )
    if not ok:
        await call.answer("Не удалось удалить.", show_alert=True)
        return

    # лог как у расписания
    try:
        when_msk = _fmt_dt_msk(datetime.now(timezone.utc))
        admin_html = _user_link(call.from_user.id, call.from_user.username)
        user_id = int(batch.get("user_id") or 0)
        user_html = _user_link(user_id, batch.get("username")) if user_id else "—"
        log_text = (
            "🗑 <b>Биржа: лот удалён</b>\n"
            f"🕒 {html.escape(when_msk)} (МСК)\n"
            f"🧑‍💼 Админ: {admin_html}\n"
            f"👤 Пользователь: {user_html}\n"
            f"🆔 Batch: <code>{batch_id}</code>\n\n"
            "Действие: <code>exchange_delete</code> через бота"
        )
        await send_admin_log(call.bot, log_text)
    except Exception:
        pass

    await call.answer("Удалено ✅", show_alert=False)


@router.callback_query(F.data.startswith("ex_edit:"))
@admin_only
async def ex_appr_edit_entry(call: types.CallbackQuery, state: FSMContext):
    # точка входа в редактор принятой биржи
    # (дальше ты уже делал FSM на pending: цену/валюту/коммент/пруф/режим и т.д.)
    batch_id = int(call.data.split(":")[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Лот не найден.", show_alert=True)
        return

    await state.clear()
    await state.update_data(exchange_batch_id=batch_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="🎛 Тип (режим)", callback_data=f"ex_edit_mode:{batch_id}")
    kb.button(text="💰 Цена", callback_data=f"ex_edit_price:{batch_id}")
    kb.button(text="💱 Валюта", callback_data=f"ex_edit_currency:{batch_id}")
    kb.button(text="💬 Комментарий", callback_data=f"ex_edit_comment:{batch_id}")
    kb.button(text="📸 Пруф", callback_data=f"ex_edit_proof:{batch_id}")
    kb.button(text="⬅️ Назад", callback_data="ex_appr:root")
    kb.adjust(2, 2, 1, 1)

    await call.message.answer(
        f"✏️ <b>Редактор биржи</b>\nBatch: <code>{batch_id}</code>\n\n"
        "Выбери, что редактировать:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


async def _safe_edit_text_or_caption(
        msg: types.Message,
        *,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Правильно редактируем:
    - если это текстовое сообщение -> edit_text
    - если это фото/видео с подписью -> edit_caption
    - иначе -> шлём новым сообщением
    """
    try:
        if msg.text is not None:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
            return
        if msg.caption is not None:
            await msg.edit_caption(text, parse_mode="HTML", reply_markup=reply_markup)
            return
    except Exception:
        pass

    await msg.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def _q_exchange_whole_deck_batches(deck_id: int, limit: int = 50) -> list[dict]:
    rows = await fetchall(
        """
        SELECT eb.batch_id,
               eb.deck_id,
               eb.deck_name,
               eb.user_id,
               COALESCE(u.username, '') AS username,
               eb.mode,
               eb.status,
               eb.price,
               eb.currency,
               eb.comment,
               eb.created_at
        FROM public.exchange_batches eb
                 LEFT JOIN public.users u ON u.user_id = eb.user_id
        WHERE COALESCE(eb.status, 'pending') = $1
          AND eb.deleted_at IS NULL
          AND eb.deck_id = $2
          AND COALESCE(eb.mode, '') = ANY ($3::text[])
        ORDER BY eb.batch_id DESC
            LIMIT $4
        """,
        EX_STATUS_APPROVED,
        deck_id,
        list(EX_WHOLE_DECK_MODES),
        int(limit),
    )
    return [dict(r) for r in rows]


async def _q_exchange_deck_total_cards(deck_id: int) -> int:
    row = await fetchrow(
        """
        SELECT COUNT(*) ::int AS cnt
        FROM public.cards c
        WHERE c.deck_id = $1
        """,
        int(deck_id),
    )
    return int((row or {}).get("cnt") or 0)


async def _q_exchange_batch_items_count(batch_id: int) -> int:
    row = await fetchrow(
        """
        SELECT COUNT(*) ::int AS cnt
        FROM public.exchange_items ei
        WHERE ei.batch_id = $1
        """,
        int(batch_id),
    )
    return int((row or {}).get("cnt") or 0)


@router.callback_query(F.data.startswith("ex_view:card_list:"))
async def ex_view_card_list(call: types.CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) < 4:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])
    card_id = int(parts[3])

    # если есть “колода целиком” — карточные лоты скрыты
    if await _q_exchange_whole_deck_count(deck_id) > 0:
        await call.message.edit_text(
            "🛒 Биржа → Лоты по карте (списком)\n\nКарточные лоты скрыты из-за «колоды целиком».",
            reply_markup=_kb_back_to_deck(deck_id),
        )
        await call.answer()
        return

    rows = await _q_exchange_card_batches(deck_id, card_id, limit=80)
    if not rows:
        await call.message.edit_text(
            "🛒 Биржа → Лоты по карте (списком)\n\nЛотов нет.",
            reply_markup=_kb_back_to_card(deck_id, card_id),
        )
        await call.answer()
        return

    lines = ["🛒 Биржа → Лоты по карте (списком)\n"]
    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        price = r.get("price")
        cur = _currency_label(r.get("currency") or "алмазы")
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id:{int(r.get('user_id') or 0)}"
        lines.append(f"• #{batch_id} — {who} — {price} {cur}")

    await call.message.edit_text("\n".join(lines), reply_markup=_kb_back_to_card(deck_id, card_id))
    await call.answer()


async def _render_exchange_deck(call: types.CallbackQuery, deck_id: int) -> None:
    deck = await get_deck_by_id(deck_id)
    deck_title = html.escape((deck or {}).get("title") or (deck or {}).get("name") or f"Колода {deck_id}")

    whole_deck_count = await _q_exchange_whole_deck_count(deck_id)
    cards = await _q_exchange_approved_cards_by_deck(deck_id)

    lines = [
        "🛒 <b>Биржа</b>",
        f"📚 <b>Колода:</b> {deck_title}",
        "",
        "<b>Выберите:</b>",
    ]
    if whole_deck_count:
        lines.append("📦 Есть лоты «колода целиком».")
    # карточные лоты НЕ скрываем, просто показываем всё вместе

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_exchange_view_cards(deck_id, cards, whole_deck_count),
    )


def _kb_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_back_to_card(deck_id: int, card_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:card:{int(deck_id)}:{int(card_id)}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_back_to_decks() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="ex_view:decks")
    kb.adjust(1)
    return kb.as_markup()


async def _q_exchange_decks_with_approved() -> list[int]:
    exchange_deck_ids = await _get_exchange_deck_ids()
    rows = await fetch(
        """
        SELECT DISTINCT eb.deck_id
        FROM public.exchange_batches eb
        WHERE COALESCE(eb.status, 'pending') = $1
          AND eb.deleted_at IS NULL
          AND eb.deck_id IS NOT NULL
          AND eb.deck_id = ANY($2::int[])
        ORDER BY eb.deck_id
        """,
        EX_STATUS_APPROVED,
        exchange_deck_ids,
    )
    return [int(r["deck_id"]) for r in rows if r.get("deck_id") is not None]


@router.callback_query(F.data == "ex_view:decks")
async def ex_view_decks(call: types.CallbackQuery):
    decks = await _q_exchange_decks_with_approved()
    kb = InlineKeyboardBuilder()
    for d in decks:
        kb.button(text=f"📚 Колода {d}", callback_data=f"ex_view:deck:{d}")
    kb.adjust(2 if len(decks) >= 2 else 1)
    kb.button(text="⬅️ Назад", callback_data="admin_panel")  # если нужно, подстрой под свой “назад”
    kb.adjust(2 if len(decks) >= 2 else 1, 1)

    await call.message.edit_text(
        "🛒 <b>Биржа</b>\n\nВыберите колоду:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:deck_whole:"))
async def ex_view_deck_whole(call: types.CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    deck_id = int(parts[2])

    rows = await _q_exchange_whole_deck_batches(deck_id, limit=50)
    if not rows:
        await call.message.edit_text(
            f"🛒 <b>Биржа → Колода целиком</b>\n\n📚 Колода: <b>{deck_id}</b>\n\nЛотов нет.",
            parse_mode="HTML",
            reply_markup=_kb_back_to_deck(deck_id),
        )
        await call.answer()
        return

    lines = ["🛒 <b>Биржа → Колода целиком</b>\n"]
    for r in rows:
        batch_id = int(r.get("batch_id") or 0)
        price = r.get("price")
        cur = _currency_label(r.get("currency") or "алмазы")
        uname = (r.get("username") or "").strip()
        who = f"@{uname}" if uname else f"id:{int(r.get('user_id') or 0)}"
        lines.append(f"• <b>#{batch_id}</b> — {price} {cur} — {who}")

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_back_to_deck(deck_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:card:"))
async def ex_view_card(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])
    page = int(parts[4]) if len(parts) >= 5 else 0

    rows = await _q_exchange_card_batches(deck_id, card_id)
    batch_ids = [int(r["batch_id"]) for r in rows]

    card = await get_exchange_card_info(card_id)
    card_name = html.escape((card or {}).get("card_name") or f"ID {card_id}")
    hero_name = html.escape((card or {}).get("hero_name") or "")

    lines = [
        "🛒 <b>Биржа → Карта → Лоты</b>",
        f"📚 <b>Колода:</b> {deck_id}",
        f"🃏 <b>Карта:</b> {hero_name + ' — ' if hero_name else ''}{card_name}",
        "",
        "Выбери лот по Batch-ID:",
    ]

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_kb_exchange_view_batches(deck_id, card_id, batch_ids, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_view:card_dump:"))
async def ex_view_card_dump(call: types.CallbackQuery):
    parts = call.data.split(":")
    deck_id = int(parts[2])
    card_id = int(parts[3])

    rows = await _q_exchange_card_batches(deck_id, card_id)
    if not rows:
        await call.message.edit_text("Нет принятых лотов по этой карте.", parse_mode="HTML")
        await call.answer()
        return

    lines = [
        "🛒 <b>Биржа → Карта → Лоты (списком)</b>",
        f"📚 <b>Колода:</b> {deck_id}",
        "",
    ]

    for r in rows[:60]:
        bid = int(r["batch_id"])
        amt = r.get("amount")
        cur = (r.get("currency") or "").strip()
        uname = (r.get("user_username") or "").strip()
        cur_emo = _currency_emoji(cur)
        price = f"{amt} {cur_emo}" if amt is not None else "—"
        who = f"@{uname}" if uname else "—"
        lines.append(f"• <b>#{bid}</b> — {price} — {who}")

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_view:card:{deck_id}:{card_id}:0")
    kb.adjust(1)

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()


def _kb_exchange_approved_deck_menu(deck_id: int, cards: list[dict], whole_deck_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # "Колода целиком" отдельной кнопкой, но НИЧЕГО не прячем
    if int(whole_deck_count or 0) > 0:
        kb.button(
            text=f"📚 Колода целиком ({int(whole_deck_count)})",
            callback_data=f"ex_appr:deck_whole:{deck_id}:0",
        )

    for c in cards:
        card_id = int(c.get("card_id") or 0)
        card_name = (c.get("card_name") or "—").strip()
        hero_name = (c.get("hero_name") or "—").strip()
        cnt = int(c.get("cnt") or 0)

        kb.button(
            text=f"🃏 {card_name} — {hero_name} • {cnt}",
            callback_data=f"ex_appr:card:{deck_id}:{card_id}:0",
        )

    kb.button(text="⬅️ Назад", callback_data="ex_appr:decks")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "ex_appr:root")
@admin_only
async def ex_appr_root(call: types.CallbackQuery):
    await _safe_edit_text_or_caption(
        call.message,
        text="🛒 <b>Биржа</b>\n\nОткрываю принятые лоты:",
        reply_markup=_kb_exchange_approved_root(),
    )
    await call.answer()


@router.callback_query(F.data == "ex_appr:decks")
@admin_only
async def ex_appr_decks(call: types.CallbackQuery):
    decks = await _q_exchange_approved_decks()
    if not decks:
        await _safe_edit_text_or_caption(
            call.message,
            text="🛒 <b>Биржа</b>\n\nПринятых лотов пока нет.",
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


def _kb_ex_appr_back_to_deck(deck_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"ex_appr:deck:{int(deck_id)}")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("print_ex_multi"))
@admin_only
async def cmd_print_ex_multi(message: types.Message):
    bot = message.bot
    parts = (message.text or "").split()
    args = parts[1:]

    if not args:
        await message.answer("Формат: /print_ex_multi <winner_id|@username> <batch_id> <batch_id> ...")
        return

    winner_id: int | None = None
    winner_un: str | None = None

    # 1) пробуем взять победителя из reply (если админ ответил на пересланное сообщение победителя)
    if message.reply_to_message:
        u = getattr(message.reply_to_message, "forward_from", None) or getattr(message.reply_to_message, "from_user",
                                                                               None)
        if u:
            winner_id = u.id
            winner_un = u.username or u.full_name

    # 2) если победителя нет из reply, читаем первым аргументом
    batch_tokens: list[str]
    if winner_id is None:
        if len(args) < 2:
            await message.answer("Нужно: /print_ex_multi <winner_id|@username> <batch_ids...>")
            return

        winner_token = args[0].strip()
        batch_tokens = args[1:]

        if winner_token.startswith("@"):
            uname = winner_token[1:]
            u = await get_user_by_username(uname)
            if not u:
                await message.answer("Победитель по @username не найден в БД.")
                return
            winner_id = int(u["user_id"])
            winner_un = u.get("username") or str(winner_id)
        elif winner_token.isdigit():
            winner_id = int(winner_token)
            u = await get_user(winner_id)
            winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)
        else:
            await message.answer("Победитель должен быть @username или числовой id.")
            return
    else:
        batch_tokens = args

    batch_ids = _parse_batch_ids(batch_tokens)
    if not batch_ids:
        await message.answer("Не вижу batch-id. Пример: /print_ex_multi 123456 149 143 122")
        return

    # нормализуем имя победителя
    if not winner_un and winner_id:
        u = await get_user(winner_id)
        winner_un = (u.get("username") or u.get("full_name")) if u else str(winner_id)

    winner_mention = _safe_user_mention(winner_id, winner_un or str(winner_id))
    moderator = admin_tag(message.from_user)
    thanks_kb = await build_thanks_kb(batch_ids[0], moderator)

    missing: list[int] = []
    lots: list[dict] = []
    owner_username: dict[int, str] = {}

    for bid in batch_ids:
        batch = await get_exchange_batch_by_id(bid)
        if not batch:
            missing.append(bid)
            continue

        items = await get_exchange_items_by_batch_id(bid)
        owner_id = int(batch.get("user_id") or 0)

        uo = await get_user(owner_id)
        owner_un = (uo.get("username") or uo.get("full_name")) if uo else str(owner_id)
        owner_username[owner_id] = owner_un

        deck_id = int(batch.get("deck_id") or 0)
        deck = await get_deck_by_id(deck_id) if deck_id else None
        deck_name = deck["name"] if deck else (str(deck_id) if deck_id else "—")

        cur = (batch.get("currency") or "diamonds").strip()
        price = int(batch.get("price") or 0)
        mode = (batch.get("mode") or "").strip()

        card_count = 0
        for it in items or []:
            card_count += int(it.get("qty") or 1)

        lots.append(
            {
                "batch_id": bid,
                "owner_id": owner_id,
                "owner_mention": _safe_user_mention(owner_id, owner_un),
                "deck_name": deck_name,
                "mode": mode,
                "mode_label": _ex_mode_label(mode),
                "currency": cur,
                "price": price,
                "items": items,
                "cards_preview": _cards_preview(items),
                "card_count": card_count,
            }
        )

    if not lots:
        await message.answer("Не нашла ни одного валидного batch-id.")
        return

    # группировка оплат и лотов
    pay_map: dict[tuple[int, str], int] = defaultdict(int)
    lots_by_owner: dict[int, list[dict]] = defaultdict(list)
    total_cards = 0

    for lot in lots:
        pay_map[(lot["owner_id"], lot["currency"])] += lot["price"]
        lots_by_owner[lot["owner_id"]].append(lot)
        total_cards += lot["card_count"]

    # платежи победителю
    pay_lines: list[str] = []
    for (oid, cur), amount in sorted(pay_map.items(), key=lambda x: (-x[1], x[0][0])):
        om = _safe_user_mention(oid, owner_username.get(oid, str(oid)))
        pay_lines.append(f"• {om}: <b>{amount}</b> {currency_to_emoji(cur)}")

    # состав по лотам
    lot_lines: list[str] = []
    for lot in lots:
        price_line = f"<b>{lot['price']}</b> {currency_to_emoji(lot['currency'])}"
        lot_lines.append(
            f"• <code>{lot['batch_id']}</code> — {price_line} • {lot['mode_label']} • {lot['deck_name']}\n"
            f"  Владелец: {lot['owner_mention']}\n"
            f"  Карты: {lot['cards_preview']}"
        )

    winner_text = (
            "🎉 <b>Биржа</b> • ты выбран победителем по нескольким лотам\n"
            f"Победитель: {winner_mention}\n"
            f"Лотов: <b>{len(lots)}</b> • Карт: <b>{total_cards}</b>\n\n"
            "💳 <b>Кому и сколько платить:</b>\n"
            + "\n".join(pay_lines)
            + "\n\n📦 <b>Состав по лотам:</b>\n"
            + "\n".join(lot_lines)
            + "\n\n"
              f"🛡️ <b>Модератор биржи:</b> {moderator}\n"
              "Если хочешь, можешь сказать спасибо модератору ниже ❤️\n"
    )

    # отправка победителю
    ok_winner = True
    try:
        await bot.send_message(winner_id, winner_text, parse_mode="HTML", reply_markup=thanks_kb)
    except Exception:
        ok_winner = False

    # отправка каждому владельцу
    owners_ok = 0
    owners_fail = 0

    for owner_id, owner_lots in lots_by_owner.items():
        totals_by_cur: dict[str, int] = defaultdict(int)
        owner_cards = 0
        for lot in owner_lots:
            totals_by_cur[lot["currency"]] += lot["price"]
            owner_cards += lot["card_count"]

        totals_line = ", ".join(f"<b>{amt}</b> {currency_to_emoji(cur)}" for cur, amt in totals_by_cur.items())

        owner_lot_lines: list[str] = []
        for lot in owner_lots:
            price_line = f"<b>{lot['price']}</b> {currency_to_emoji(lot['currency'])}"
            owner_lot_lines.append(
                f"• <code>{lot['batch_id']}</code> — {price_line} • {lot['mode_label']} • {lot['deck_name']}\n"
                f"  Карты: {lot['cards_preview']}"
            )

        owner_text = (
                "✅ <b>Биржа</b> • у тебя выкупают несколько лотов\n"
                f"Покупатель: {winner_mention}\n"
                f"Лотов: <b>{len(owner_lots)}</b> • Карт: <b>{owner_cards}</b>\n"
                f"💰 <b>К оплате тебе:</b> {totals_line}\n\n"
                "📦 <b>Состав:</b>\n"
                + "\n".join(owner_lot_lines)
                + "\n\n"
                  f"🛡️ <b>Модератор биржи:</b> {moderator}\n"
                  "Если хочешь, можешь сказать спасибо модератору ниже ❤️"
        )

        try:
            await bot.send_message(owner_id, owner_text, parse_mode="HTML", reply_markup=thanks_kb)
            owners_ok += 1
        except Exception:
            owners_fail += 1

    # записываем победителя и помечаем как разосланное
    for lot in lots:
        try:
            await set_exchange_manual_winner(
                batch_id=int(lot["batch_id"]),
                winner_id=int(winner_id),
                winner_username=(winner_un or str(winner_id)),
                admin_id=message.from_user.id,
            )
        except Exception:
            pass
        try:
            await mark_exchange_manual_sent(int(lot["batch_id"]))
        except Exception:
            pass

    report = f"✅ /print_ex_multi готово. winner_ok={ok_winner}, owners_ok={owners_ok}, owners_fail={owners_fail}"
    if missing:
        report += f"\n⚠️ Не найдено batch-id: {', '.join(map(str, missing))}"
    await message.answer(report)


def _ex_mode_label(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("whole_deck", "deck"):
        return "Колода целиком"
    if m in ("card",):
        return "Карта"
    if m == "deck_split":
        return "Карта"
    return mode or "—"


def _cards_preview(items: list[dict], limit: int = 6) -> str:
    names: list[str] = []
    for it in items or []:
        hero = (it.get("hero_name") or "").strip()
        card = (it.get("card_name") or "").strip()
        qty = int(it.get("qty") or 1)
        base = f"{hero} — {card}".strip(" —")
        if not base:
            base = "—"
        if qty > 1:
            base = f"{base} ×{qty}"
        names.append(base)

    if not names:
        return "—"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" … +{len(names) - limit}"


def _parse_batch_ids(tokens: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for t in tokens:
        for part in (t or "").replace(";", ",").split(","):
            s = part.strip()
            if not s:
                continue
            if s.isdigit():
                i = int(s)
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out


# --- ADMIN: биржа -> владелец + проверка стандартного аука ---
@router.message(F.text.regexp(r"^/ex_lot\s+\d+$"))
@admin_only
async def cmd_ex_lot(message: Message):
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer(f"🛒 Заявка биржи <code>{batch_id}</code> не найдена.", parse_mode="HTML")
        return

    owner_id = int(batch.get("user_id") or 0)
    owner = await get_user(owner_id)
    owner_un = ((owner or {}).get("username") or "").strip() or None

    # безопасный mention (если где-то уже есть _safe_user_mention)
    try:
        owner_txt = _safe_user_mention(owner_id, owner_un)  # type: ignore[name-defined]
    except Exception:
        uname = f"@{html.escape(owner_un)}" if owner_un else str(owner_id)
        owner_txt = f"<a href='tg://user?id={owner_id}'>{uname}</a>"

    status = html.escape(str(batch.get("status") or "—"))
    mode = html.escape(str(batch.get("mode") or "—"))
    deck_id = batch.get("deck_id")
    price = batch.get("price")
    currency = html.escape(str(batch.get("currency") or "—"))
    created_at = batch.get("created_at")

    def _fmt_dt(dt_obj: object) -> str:
        if isinstance(dt_obj, datetime):
            return dt_obj.strftime("%d.%m.%Y %H:%M")
        return "—" if dt_obj is None else html.escape(str(dt_obj))

    # список карточек в заявке
    items = await get_exchange_items_by_batch_id(batch_id)
    items_lines: list[str] = []
    for it in (items or [])[:25]:
        cid = it.get("card_id")
        cn = (it.get("card_name") or "").strip()
        hn = (it.get("hero_name") or "").strip()
        title = " — ".join([x for x in [hn, cn] if x]) or "—"
        prefix = f"<code>{cid}</code> " if cid else ""
        items_lines.append(f"• {prefix}{html.escape(title)}")
    if items and len(items) > 25:
        items_lines.append(f"… и ещё {len(items) - 25} шт.")

    items_block = "\n".join(items_lines) if items_lines else "—"

    # стандартный аукцион по владельцу (активное/расписание/модерация)
    lots = await fetch(
        """
        SELECT a.auction_id, a.card_name, a.hero_name, a.status, a.start_time, a.end_time, a.auction_kind
        FROM auctions a
                 JOIN auction_owners ao ON ao.auction_id = a.auction_id
        WHERE ao.user_id = $1
          AND COALESCE(a.auction_kind, 'standard') = 'standard'
        ORDER BY a.start_time DESC LIMIT 80
        """,
        owner_id,
    )

    def _status_ru(st: str) -> str:
        s = (st or "").lower()
        return {
            "pending": "на модерации",
            "approved": "одобрено",
            "scheduled": "в расписании",
            "active": "идёт",
            "finished": "завершён",
            "rejected": "отклонён",
        }.get(s, s or "—")

    active_statuses = {"pending", "approved", "scheduled", "active"}
    std_active = [r for r in (lots or []) if (str(r.get("status") or "").lower() in active_statuses)]

    std_lines: list[str] = []
    if std_active:
        for r in std_active[:25]:
            aid = r.get("auction_id")
            st = _status_ru(str(r.get("status") or ""))
            cn = (r.get("card_name") or "").strip()
            hn = (r.get("hero_name") or "").strip()
            lot_title = " — ".join([x for x in [hn, cn] if x]) or "—"
            st_time = _fmt_dt(r.get("start_time"))
            std_lines.append(f"• <code>{aid}</code> | {html.escape(st)} | {html.escape(lot_title)} | {st_time}")
        if len(std_active) > 25:
            std_lines.append(f"… и ещё {len(std_active) - 25} шт.")
    else:
        std_lines.append("—")

    text = (
            f"🛒 <b>Биржа: проверка лота</b>\n"
            f"Batch: <code>{batch_id}</code> | статус: <b>{status}</b>\n"
            f"Владелец заявки: {owner_txt} (id:<code>{owner_id}</code>)\n"
            f"Колода: <code>{deck_id}</code> | mode: <code>{mode}</code>\n"
            f"Цена: <code>{price if price is not None else '—'}</code> {currency}\n"
            f"Создано: <code>{_fmt_dt(created_at)}</code> (как в БД)\n\n"
            f"🎴 <b>Состав заявки (первые 25)</b>:\n{items_block}\n\n"
            f"⭐ <b>Стандартный аукцион этого пользователя (активное/расписание/модерация)</b>:\n"
            + "\n".join(std_lines)
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text.regexp(r"^/ex_user\s+\S+$"), F.chat.type == "private")
@admin_only
async def cmd_ex_user(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    raw = (parts[1] if len(parts) > 1 else "").strip()

    if not raw:
        await message.answer("Формат: <code>/ex_user @username</code> или <code>/ex_user 123456789</code>",
                             parse_mode="HTML")
        return

    # 1) если цифры — считаем что это user_id
    u = None
    uid = None

    if raw.isdigit():
        uid = int(raw)
        u = await get_user(uid)
        if not u:
            await message.answer(f"Пользователь с id <code>{uid}</code> не найден в БД.", parse_mode="HTML")
            return
    else:
        # 2) иначе — username
        username = raw.lstrip("@").strip().lower()
        if not username:
            await message.answer("Формат: <code>/ex_user @username</code> или <code>/ex_user 123456789</code>",
                                 parse_mode="HTML")
            return

        u = await get_user_by_username(username)
        if not u:
            await message.answer(f"Пользователь @{html.escape(username)} не найден в БД.", parse_mode="HTML")
            return

        uid = int(u["user_id"])

    uname = ((u.get("username") or "").strip() or None)

    # сколько карточек на бирже (по exchange_items) + сколько заявок (по exchange_batches)
    cards_stat = await fetch(
        """
        SELECT COALESCE(eb.status, '') AS status, COUNT(*) ::int AS cards_cnt
        FROM exchange_items ei
                 JOIN exchange_batches eb ON eb.batch_id = ei.batch_id
        WHERE eb.user_id = $1
          AND COALESCE(eb.status, '') <> 'deleted'
          AND eb.deleted_at IS NULL
        GROUP BY COALESCE(eb.status, '')
        ORDER BY cards_cnt DESC
        """,
        uid,
    )
    batches_stat = await fetch(
        """
        SELECT COALESCE(status, '') AS status, COUNT(*) ::int AS batches_cnt
        FROM exchange_batches
        WHERE user_id = $1
          AND COALESCE(status, '') <> 'deleted'
          AND deleted_at IS NULL
        GROUP BY COALESCE(status, '')
        ORDER BY batches_cnt DESC
        """,
        uid,
    )

    def _stat_line(rows, key_cnt: str) -> str:
        if not rows:
            return "—"
        return ", ".join(
            f"{html.escape(str(r.get('status') or '—'))}: <code>{int(r.get(key_cnt) or 0)}</code>"
            for r in rows
        )

    batches = await fetch(
        """
        SELECT batch_id, status, deck_id, mode, price, currency, created_at
        FROM exchange_batches
        WHERE user_id = $1
          AND COALESCE(status, '') <> 'deleted'
          AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 12
        """,
        uid,
    )

    batch_lines: list[str] = []
    for b in batches or []:
        bid = int(b["batch_id"])
        b_status = html.escape(str(b.get("status") or "—"))
        b_deck = b.get("deck_id")
        b_mode = html.escape(str(b.get("mode") or "—"))
        b_price = b.get("price")
        b_cur = html.escape(str(b.get("currency") or "—"))

        items = await get_exchange_items_by_batch_id(bid)
        short_items: list[str] = []
        for it in (items or [])[:10]:
            cn = (it.get("card_name") or "").strip()
            hn = (it.get("hero_name") or "").strip()
            short_items.append(" — ".join([x for x in [hn, cn] if x]) or "—")

        items_txt = "; ".join(html.escape(x) for x in short_items) if short_items else "—"
        if items and len(items) > 10:
            items_txt += f" …(+{len(items) - 10})"

        batch_lines.append(
            f"• batch <code>{bid}</code> | {b_status} | deck <code>{b_deck}</code> | mode <code>{b_mode}</code> | "
            f"цена <code>{b_price if b_price is not None else '—'}</code> {b_cur}\n"
            f"  🎴 {items_txt}"
        )

    who = _safe_user_mention(uid, uname)

    text = (
            f"🛒 <b>Биржа: пользователь</b>\n"
            f"Пользователь: {who} (id:<code>{uid}</code>)\n\n"
            f"📦 <b>Заявки (batch) по статусам</b>: {_stat_line(batches_stat, 'batches_cnt')}\n"
            f"🎴 <b>Карты (items) по статусам</b>: {_stat_line(cards_stat, 'cards_cnt')}\n\n"
            f"📌 <b>Последние заявки (до 12)</b>:\n"
            + ("\n".join(batch_lines) if batch_lines else "—")
            + "\n\n"
              f"Подробно по конкретной заявке: <code>/ex_lot &lt;batch_id&gt;</code>"
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text.regexp(r"^/ex_dump(?:\s+\d+)?$"), F.chat.type == "private")
@admin_only
async def cmd_ex_dump(message: Message):
    """
    Админ-команда: сводка биржи по пользователям и ОДНОМУ пруфу (одно фото = пачка лотов).
    /ex_dump
    /ex_dump 2
    """
    import json

    parts = (message.text or "").split()
    page = 1
    if len(parts) >= 2 and parts[1].isdigit():
        page = max(1, int(parts[1]))

    per_page = 8
    offset = (page - 1) * per_page

    def _compress_ranges(ids: list[int]) -> str:
        if not ids:
            return "—"
        ids = sorted({int(x) for x in ids})
        start = prev = ids[0]
        out: list[str] = []
        for x in ids[1:]:
            if x == prev + 1:
                prev = x
                continue
            out.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = x
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        return ", ".join(out)

    def _short_proof(s: str) -> str:
        s = (s or "").strip()
        if not s or s.upper() == "NO_PROOF":
            return "NO_PROOF"
        return (s[:30] + "…" + s[-10:]) if len(s) > 50 else s

    # 1) total groups (user_id + proof) — без плейсхолдеров
    total_row = await fetchrow(
        """
        SELECT COUNT(*) ::int AS n
        FROM (SELECT 1
              FROM public.exchange_batches eb
              WHERE eb.deleted_at IS NULL
                AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
              GROUP BY eb.user_id,
                       COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF')) t
        """
    )
    total = int((total_row or {}).get("n") or 0)
    if total == 0:
        await message.answer("🛒 На бирже нет заявок (pending/approved).", parse_mode="HTML")
        return

    pages = (total + per_page - 1) // per_page
    if page > pages:
        page = pages
        offset = (page - 1) * per_page

    # 2) page of groups + aggregated cards in one query (без N+1)
    rows = await fetch(
        """
        WITH groups AS (SELECT eb.user_id,
                               MAX(u.username)                                            AS username,
                               COALESCE(NULLIF(BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') AS proof,
                               array_agg(eb.batch_id ORDER BY eb.batch_id)                AS batch_ids,
                               COUNT(*)::int AS batches_cnt, MAX(eb.batch_id) ::int AS last_batch_id
                        FROM public.exchange_batches eb
                                 LEFT JOIN public.users u ON u.user_id = eb.user_id
                        WHERE eb.deleted_at IS NULL
                          AND COALESCE(eb.status, 'pending') IN ('pending', 'approved')
                        GROUP BY eb.user_id, proof
                        ORDER BY last_batch_id DESC
            LIMIT $1
        OFFSET $2 ), items_total AS (
        SELECT
            g.user_id, g.proof, COUNT (*):: int AS items_total
        FROM groups g
            JOIN public.exchange_batches eb
        ON eb.user_id = g.user_id
            AND COALESCE (NULLIF (BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') = g.proof
            JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
        WHERE eb.deleted_at IS NULL
          AND COALESCE (eb.status
            , 'pending') IN ('pending'
            , 'approved')
        GROUP BY g.user_id, g.proof
            ),
            cards AS (
        SELECT
            g.user_id, g.proof, COALESCE (c.card_id, ei.card_id) AS card_id, COALESCE (c.hero_name, ei.hero_name) AS hero_name, COALESCE (c.card_name, ei.card_name) AS card_name, COUNT (*):: int AS qty
        FROM groups g
            JOIN public.exchange_batches eb
        ON eb.user_id = g.user_id
            AND COALESCE (NULLIF (BTRIM(eb.proof_photo_id), ''), 'NO_PROOF') = g.proof
            JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
            LEFT JOIN public.cards c ON c.card_id = ei.card_id
        WHERE eb.deleted_at IS NULL
          AND COALESCE (eb.status
            , 'pending') IN ('pending'
            , 'approved')
        GROUP BY 1, 2, 3, 4, 5
            )
        SELECT g.user_id,
               g.username,
               g.proof,
               g.batch_ids,
               g.batches_cnt,
               COALESCE(it.items_total, 0)::int AS items_total, COALESCE(
                json_agg(
                        json_build_object(
                                'card_id', c.card_id,
                                'hero_name', c.hero_name,
                                'card_name', c.card_name,
                                'qty', c.qty
                        ) ORDER BY c.qty DESC, c.hero_name NULLS LAST, c.card_name
                ) FILTER(WHERE c.card_id IS NOT NULL OR c.card_name IS NOT NULL),
                '[]' ::json
                                                                ) AS cards
        FROM groups g
                 LEFT JOIN items_total it ON it.user_id = g.user_id AND it.proof = g.proof
                 LEFT JOIN cards c ON c.user_id = g.user_id AND c.proof = g.proof
        GROUP BY g.user_id, g.username, g.proof, g.batch_ids, g.batches_cnt, it.items_total, g.last_batch_id
        ORDER BY g.last_batch_id DESC
        """,
        int(per_page),
        int(offset),
    )

    header = (
        f"🛒 <b>Биржа: кто сколько подал (по одному пруфу)</b>\n"
        f"Страница: <b>{page}</b>/<b>{pages}</b> | групп: <b>{total}</b>\n"
        f"Команда: <code>/ex_dump</code> | <code>/ex_dump 2</code>\n"
    )

    out = header

    for r in rows or []:
        user_id = int(r.get("user_id") or 0)
        username = (r.get("username") or "").strip() or None
        proof = (r.get("proof") or "NO_PROOF").strip()
        batch_ids = list(r.get("batch_ids") or [])
        batches_cnt = int(r.get("batches_cnt") or 0)
        items_total = int(r.get("items_total") or 0)

        # cards может прийти как list[dict] (если настроен json codec) ИЛИ как str
        cards_raw = r.get("cards")
        if cards_raw is None:
            cards: list[dict] = []
        elif isinstance(cards_raw, str):
            try:
                parsed = json.loads(cards_raw)
                cards = parsed if isinstance(parsed, list) else []
            except Exception:
                cards = []
        elif isinstance(cards_raw, list):
            cards = [x for x in cards_raw if isinstance(x, dict)]
        else:
            cards = []

        lots_ranges = _compress_ranges([int(x) for x in batch_ids])
        proof_label = _short_proof(proof)
        who = _safe_user_mention(user_id, username)

        lines = [
            "",
            f"👤 {who} (id:<code>{user_id}</code>)",
            f"📸 Пруф: <code>{html.escape(proof_label)}</code>",
            f"🧾 Лоты (batch_id): <code>{html.escape(lots_ranges)}</code> | шт: <b>{batches_cnt}</b>",
            f"🎴 Всего позиций (items): <b>{items_total}</b> | уникальных карт: <b>{len(cards)}</b>",
            "🎴 Карты (qty = сколько одинаковых подано):",
        ]

        show_limit = 25
        for c in cards[:show_limit]:
            if not isinstance(c, dict):
                continue
            qty = int(c.get("qty") or 0)
            cid = c.get("card_id")
            hn = (c.get("hero_name") or "").strip()
            cn = (c.get("card_name") or "").strip()
            title = " — ".join([x for x in [hn, cn] if x]) or "—"
            cid_txt = f"<code>{cid}</code> " if cid else ""
            lines.append(f"• ×<b>{qty}</b> | {cid_txt}{html.escape(title)}")

        if len(cards) > show_limit:
            lines.append(f"… и ещё <b>{len(cards) - show_limit}</b> разных карт (Telegram не резиновый).")

        block = "\n".join(lines)

        # лимит сообщений Telegram
        if len(out) + len(block) > 3500:
            await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
            out = header + block
        else:
            out += block

    if out.strip():
        await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)


import json
import asyncpg


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


@router.message(F.text.regexp(r"^/ex_proof\s+\d+$"), F.chat.type == "private")
@admin_only
async def cmd_ex_proof(message: Message):
    parts = (message.text or "").split()
    batch_id = int(parts[1])

    b = await get_exchange_batch_by_id(batch_id)
    if not b:
        await message.answer(f"🛒 Заявка биржи <code>{batch_id}</code> не найдена.", parse_mode="HTML")
        return

    proof = (b.get("proof_photo_id") or "").strip()
    if (not proof) or (proof.upper() == "NO_PROOF"):
        await message.answer(f"📸 Пруф для <code>{batch_id}</code> не прикреплён.", parse_mode="HTML")
        return

    caption = (
        f"📸 Пруф заявки биржи <code>{batch_id}</code>\n"
        f"<code>{html.escape(proof)}</code>"
    )

    # пытаемся отправить как фото/видео/анимацию/документ
    try:
        await message.answer_photo(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_video(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_animation(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    try:
        await message.answer_document(proof, caption=caption, parse_mode="HTML")
        return
    except Exception:
        pass

    await message.answer(
        f"❌ Не удалось отправить пруф для <code>{batch_id}</code>.\n"
        f"Скорее всего file_id битый/не того типа:\n<code>{html.escape(proof)}</code>",
        parse_mode="HTML",
    )


def _short_media(v: object) -> str:
    # чтобы file_id не раздувал логи
    return short_media_id(v) if "short_media_id" in globals() else (str(v)[:12] + "…" if v else "—")


@router.message(F.text.regexp(r"^/dup_user_cards(?:\s+.+)?$"), F.chat.type == "private")
@admin_only
async def cmd_dup_user_cards(message: Message):
    """
    Пересечения "биржа + стандарт" ТОЛЬКО для одного и того же пользователя (user_id) и одной и той же card_id.
    /dup_user_cards
    /dup_user_cards <user_id>
    /dup_user_cards card <card_id>
    """
    parts = (message.text or "").split()

    user_id_filter: int | None = None
    card_id_filter: int | None = None

    # парсинг аргументов без истерик
    if len(parts) >= 2:
        if parts[1].isdigit():
            user_id_filter = int(parts[1])
        elif parts[1].lower() == "card" and len(parts) >= 3 and parts[2].isdigit():
            card_id_filter = int(parts[2])

    def _ids_preview(ids: list[int] | None, limit: int = 10) -> str:
        if not ids:
            return "—"
        ids2 = [int(x) for x in ids if x is not None][:limit]
        tail = "" if len(ids) <= limit else f" …(+{len(ids) - limit})"
        return ", ".join(str(x) for x in ids2) + tail

    rows = await fetch(
        """
        WITH ex
                 AS (SELECT eb.user_id::bigint AS user_id, ei.card_id::int    AS card_id, COUNT(*)::int AS ex_items_cnt, COUNT(DISTINCT eb.batch_id)::int AS ex_batches_cnt, array_agg(DISTINCT eb.batch_id ORDER BY eb.batch_id) AS ex_batch_ids
                     FROM public.exchange_batches eb
                              JOIN public.exchange_items ei ON ei.batch_id = eb.batch_id
                     WHERE COALESCE(eb.status, 'pending') IN ('pending', 'approved', 'active')
                       AND ei.card_id IS NOT NULL
                     GROUP BY eb.user_id, ei.card_id),
             std
                 AS (SELECT ao.user_id::bigint AS user_id, a.card_id::int     AS card_id, COUNT(DISTINCT a.auction_id)::int AS std_lots_cnt, array_agg(DISTINCT a.auction_id ORDER BY a.auction_id) AS std_auction_ids
                     FROM public.auctions a
                              JOIN public.auction_owners ao ON ao.auction_id = a.auction_id
                     WHERE COALESCE(a.auction_kind, 'standard') = 'standard'
                       AND a.status IN ('pending', 'approved', 'scheduled', 'active')
                       AND a.card_id IS NOT NULL
                     GROUP BY ao.user_id, a.card_id)
        SELECT ex.user_id,
               u.username,
               ex.card_id,
               c.hero_name,
               c.card_name,
               c.deck_id,
               ex.ex_items_cnt,
               ex.ex_batches_cnt,
               ex.ex_batch_ids,
               std.std_lots_cnt,
               std.std_auction_ids
        FROM ex
                 JOIN std USING (user_id, card_id)
                 LEFT JOIN public.users u ON u.user_id = ex.user_id
                 LEFT JOIN public.cards c ON c.card_id = ex.card_id
        WHERE ($1::bigint IS NULL OR ex.user_id = $1::bigint)
          AND ($2::int IS NULL OR ex.card_id = $2::int)
        ORDER BY (ex.ex_items_cnt + std.std_lots_cnt) DESC, ex.user_id, ex.card_id LIMIT 120
        """,
        user_id_filter,
        card_id_filter,
    )

    if not rows:
        extra = ""
        if user_id_filter:
            extra = f" по user_id <code>{user_id_filter}</code>"
        if card_id_filter:
            extra = f" по card_id <code>{card_id_filter}</code>"
        await message.answer(f"✅ Пересечений (тот же пользователь + та же card_id) не найдено{extra}.",
                             parse_mode="HTML")
        return

    # вывод группами по пользователю
    header = (
        "🧨 <b>Дубли: один пользователь продаёт одну и ту же card_id</b>\n"
        "Условие: <b>user_id совпадает</b> + <b>card_id совпадает</b> (биржа + стандарт).\n"
    )
    if user_id_filter:
        header += f"Фильтр user_id: <code>{user_id_filter}</code>\n"
    if card_id_filter:
        header += f"Фильтр card_id: <code>{card_id_filter}</code>\n"
    header += f"Найдено строк: <b>{len(rows)}</b>\n"

    out = header
    cur_uid: int | None = None

    for r in rows:
        uid = int(r.get("user_id") or 0)
        uname = (r.get("username") or "").strip() or None

        if cur_uid != uid:
            cur_uid = uid
            who = _safe_user_mention(uid, uname)
            block = f"\n\n👤 {who} (id:<code>{uid}</code>)\n"
            if len(out) + len(block) > 3500:
                await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
                out = header + block
            else:
                out += block

        cid = int(r.get("card_id") or 0)
        hero = (r.get("hero_name") or "").strip()
        name = (r.get("card_name") or "").strip()
        deck_id = r.get("deck_id")

        title = " — ".join([x for x in [hero, name] if x]) or "—"

        ex_items = int(r.get("ex_items_cnt") or 0)
        ex_batches = int(r.get("ex_batches_cnt") or 0)
        ex_batch_ids = list(r.get("ex_batch_ids") or [])

        std_lots = int(r.get("std_lots_cnt") or 0)
        std_auction_ids = list(r.get("std_auction_ids") or [])

        line = (
            f"• 🎴 <b>{html.escape(title)}</b> | card_id <code>{cid}</code> | deck <code>{deck_id}</code>\n"
            f"   🛒 Биржа: items=<b>{ex_items}</b>, batches=<b>{ex_batches}</b>, batch_id: <code>{html.escape(_ids_preview(ex_batch_ids))}</code>\n"
            f"   ⭐ Стандарт: lots=<b>{std_lots}</b>, auction_id: <code>{html.escape(_ids_preview(std_auction_ids))}</code>\n"
        )

        if len(out) + len(line) > 3500:
            await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)
            out = header + line
        else:
            out += line

    await message.answer(out, parse_mode="HTML", disable_web_page_preview=True)


_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{3,})")


def _extract_usernames_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _USERNAME_RE.finditer(text or ""):
        un = (m.group(1) or "").strip()
        if not un:
            continue
        key = un.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(un)
    return out


def _chunk_lines(lines: list[str], max_len: int = 3500) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for line in lines:
        add_len = len(line) + 1
        if cur and (cur_len + add_len) > max_len:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = add_len
        else:
            cur.append(line)
            cur_len += add_len

    if cur:
        chunks.append("\n".join(cur))
    return chunks


@router.message(Command("ex_not_sent"))
@admin_only
async def cmd_ex_not_sent(message: Message):
    # источник текста: реплай на список или сам текст сообщения
    src = message.reply_to_message or message
    raw = (src.text or src.caption or "").strip()

    # если команда не реплаем, и список вставлен после команды
    if src is message and raw.startswith("/ex_not_sent"):
        raw = raw[len("/ex_not_sent"):].strip()

    if not raw:
        await message.answer(
            "Формат:\n"
            "1) пришли список отдельным сообщением\n"
            "2) ответь на него командой <code>/ex_not_sent</code>\n\n"
            "Либо вставь список прямо после команды.",
            parse_mode="HTML",
        )
        return

    usernames = _extract_usernames_from_text(raw)
    if not usernames:
        await message.answer("В тексте не нашёл ни одного @username.", parse_mode="HTML")
        return

    # SQL: ищем “не отправлено” по победителю (manual_winner_*)
    sql_unsent = """
                 SELECT eb.batch_id,
                        eb.status,
                        eb.deck_id,
                        COALESCE(NULLIF(eb.mode, ''), '—')          AS mode,
                        COALESCE(eb.manual_price, eb.price)         AS price,
                        COALESCE(NULLIF(eb.currency, ''), 'алмазы') AS currency,
                        eb.created_at,
                        (SELECT COUNT(*) ::int
                         FROM public.exchange_items ei
                         WHERE ei.batch_id = eb.batch_id)           AS items_count
                 FROM public.exchange_batches eb
                 WHERE eb.deleted_at IS NULL
                   AND eb.manual_sent_at IS NULL
                   AND (
                     eb.manual_winner_id = COALESCE($1::bigint, -1)
                         OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = lower($2)
                     )
                 ORDER BY eb.created_at DESC NULLS LAST, eb.batch_id DESC LIMIT 200 \
                 """

    sql_exists_any = """
                     SELECT 1
                     FROM public.exchange_batches eb
                     WHERE eb.deleted_at IS NULL
                       AND (
                         eb.manual_winner_id = COALESCE($1::bigint, -1)
                             OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) = lower($2)
                         )
                         LIMIT 1 \
                     """

    missing: dict[str, list[dict]] = {}
    ok: list[str] = []
    not_found: list[str] = []

    for un in usernames:
        # пытаемся найти user_id по username (на случай, если manual_winner_id заполнялся)
        row_u = await fetchrow(
            "SELECT user_id FROM public.users WHERE lower(username)=lower($1) LIMIT 1",
            un,
        )
        winner_id = int(row_u["user_id"]) if row_u else None

        rows_unsent = await fetch(sql_unsent, winner_id, un)
        rows_unsent = [dict(r) for r in (rows_unsent or [])]

        if rows_unsent:
            missing[un] = rows_unsent
            continue

        exists = await fetchrow(sql_exists_any, winner_id, un)
        if exists:
            ok.append(f"@{un}")
        else:
            not_found.append(f"@{un}")

    lines: list[str] = []
    lines.append("🛒 <b>Биржа • проверка “не отправили”</b>")
    lines.append(f"Юзеров в списке: <b>{len(usernames)}</b>")
    lines.append("")

    if not missing:
        lines.append("✅ По этому списку <b>не нашёл</b> лотов, где победителю не отправляли (manual_sent_at пустой).")
        if not_found:
            lines.append("")
            lines.append("⚠️ <b>Не нашёл в БД ни одного лота по:</b>")
            lines.extend([f"• {u}" for u in not_found[:50]])
        for chunk in _chunk_lines(lines):
            await message.answer(chunk, parse_mode="HTML")
        return

    # есть “не отправлено”
    lines.append(f"❌ <b>Найдено НЕ отправлено:</b> {len(missing)} пользователей")
    lines.append("")

    msk = ZoneInfo("Europe/Moscow")

    for un, lots in missing.items():
        lines.append(f"• <b>@{un}</b> — лотов: <b>{len(lots)}</b>")
        for r in lots[:20]:
            bid = int(r.get("batch_id") or 0)
            deck_id = int(r.get("deck_id") or 0)
            mode = (r.get("mode") or "—").strip()
            price = int(r.get("price") or 0)
            cur = (r.get("currency") or "алмазы").strip()
            cnt = int(r.get("items_count") or 0)

            dt = r.get("created_at")
            dt_s = "—"
            if isinstance(dt, datetime):
                # created_at обычно без tzinfo, поэтому просто красиво форматируем
                dt_s = dt.strftime("%d.%m %H:%M")

            lines.append(
                f"    └ <code>{bid}</code> • 📚 {deck_id} • 🎛 {mode} • 🃏 {cnt} • 💰 {price} {cur} • 🕒 {dt_s}"
            )
        if len(lots) > 20:
            lines.append(f"    └ …и ещё {len(lots) - 20}")

    if ok:
        lines.append("")
        lines.append(f"✅ <b>ОК (есть лоты, но “неотправленных” нет):</b> {len(ok)}")
        lines.extend([f"• {u}" for u in ok[:60]])
        if len(ok) > 60:
            lines.append(f"• …и ещё {len(ok) - 60}")

    if not_found:
        lines.append("")
        lines.append(f"⚠️ <b>Не нашёл в БД лотов по:</b> {len(not_found)}")
        lines.extend([f"• {u}" for u in not_found[:60]])
        if len(not_found) > 60:
            lines.append(f"• …и ещё {len(not_found) - 60}")

    for chunk in _chunk_lines(lines):
        await message.answer(chunk, parse_mode="HTML")


from datetime import datetime
from zoneinfo import ZoneInfo


@router.message(Command("ex_unsent"))
async def cmd_ex_unsent(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split()
    deck_id: int | None = None
    if len(parts) == 2:
        try:
            deck_id = int(parts[1].strip())
        except Exception:
            await message.answer("Формат: /ex_unsent [deck_id]")
            return
    elif len(parts) > 2:
        await message.answer("Формат: /ex_unsent [deck_id]")
        return

    rows = await fetch(
        """
        SELECT eb.batch_id,
               eb.user_id,
               u.username                                                                            AS owner_username,
               eb.deck_id,
               COALESCE(NULLIF(eb.mode, ''), '—')                                                    AS mode,
               eb.status,
               eb.created_at,
               eb.manual_winner_id,
               eb.manual_winner_username,
               eb.manual_sent_at,
               (SELECT COUNT(*) ::int FROM public.exchange_items ei WHERE ei.batch_id = eb.batch_id) AS items_count
        FROM public.exchange_batches eb
                 LEFT JOIN public.users u ON u.user_id = eb.user_id
        WHERE eb.status = 'approved'
          AND eb.manual_sent_at IS NULL
          AND ($1::int IS NULL OR eb.deck_id = $1)
        ORDER BY eb.created_at ASC NULLS LAST, eb.batch_id ASC LIMIT 400
        """,
        deck_id,
    )

    if not rows:
        await message.answer(
            "✅ Нет одобренных батчей биржи без отметки отправки (manual_sent_at пустой)."
            + (f" Фильтр: колода {deck_id}." if deck_id else "")
        )
        return

    msk = ZoneInfo("Europe/Moscow")

    total_batches = len(rows)
    total_cards = sum(int(r.get("items_count") or 0) for r in rows)

    lines: list[str] = []
    lines.append("🛒 <b>Биржа • НЕ ОТПРАВЛЕНО (approved + manual_sent_at пустой)</b>")
    if deck_id:
        lines.append(f"Фильтр: колода <b>{deck_id}</b>")
    lines.append(f"Батчей: <b>{total_batches}</b>, карт внутри: <b>{total_cards}</b>")
    lines.append("")

    for r in rows:
        batch_id = int(r["batch_id"])
        did = int(r.get("deck_id") or 0)
        mode = (r.get("mode") or "—").strip()

        owner_username = (r.get("owner_username") or "").strip()
        owner = f"@{owner_username}" if owner_username else f"id{int(r['user_id'])}"

        win_id = r.get("manual_winner_id")
        win_un = (r.get("manual_winner_username") or "").strip()
        winner = "—"
        if win_un:
            winner = win_un if win_un.startswith("@") else f"@{win_un}"
        elif win_id:
            winner = f"id{int(win_id)}"

        cnt = int(r.get("items_count") or 0)

        dt = r.get("created_at")
        dt_s = "—"
        if isinstance(dt, datetime):
            # если naive, просто форматируем
            dt_s = dt.strftime("%d.%m %H:%M")

        flag = "⚠️ без победителя" if (not win_id and not win_un) else ""

        lines.append(
            f"• 🆔 <code>{batch_id}</code> • 📚 {did} • 🎛 {mode} • 🃏 {cnt} • 👤 {owner} • 🏆 {winner} • 🕒 {dt_s} {flag}"
        )

    # чанк по лимиту Телеги
    text = "\n".join(lines)
    if len(text) <= 3800:
        await message.answer(text, parse_mode="HTML")
        return

    chunk: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > 3800:
            await message.answer("\n".join(chunk), parse_mode="HTML")
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk), parse_mode="HTML")


import re
from collections import defaultdict

_USER_LINE_RE = re.compile(r"^@([A-Za-z0-9_]{3,})(.*)$")
_AUTHOR_TS_RE = re.compile(r"^.+,\s*\[\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}\]\s*$", re.I)


def _norm(s: str) -> str:
    s = (s or "").strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s


def _parse_qty_and_card(rest: str, cur_card: str | None) -> tuple[int, str | None]:
    """
    rest examples:
      "Граф"
      "2 Граф 2"
      "Мадс 4"
      "5 Нахом Кевин"
      "5 шт"
      "4 карты"
      ""  (then use cur_card)
    """
    rest = (rest or "").strip()
    if not rest:
        return (1, cur_card)

    # вычленяем количество
    qty = None

    # "5 шт" / "5 карт" / "5 карты" / "5 карта"
    m = re.match(r"^(\d+)\s*(шт\.?|штук|карта|карты|карт)?\b(.*)$", rest, flags=re.I)
    if m and m.group(1):
        qty = int(m.group(1))
        rest2 = (m.group(3) or "").strip()
    else:
        rest2 = rest

    # если не нашли qty в начале: пробуем в конце "Граф 2" / "Виктор 5 карт"
    if qty is None:
        m2 = re.match(r"^(.*?)(?:\s+(\d+))\s*(шт\.?|штук|карта|карты|карт)?\s*$", rest2, flags=re.I)
        if m2 and m2.group(2):
            qty = int(m2.group(2))
            rest2 = (m2.group(1) or "").strip()

    if qty is None:
        qty = 1

    # иногда пишут "2 Граф 2" -> уберем хвостовую цифру, если осталась
    tokens = rest2.split()
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    card = " ".join(tokens).strip()

    # чистим мусорные слова, если остались
    card = re.sub(r"\b(шт\.?|штук|карта|карты|карт)\b", "", card, flags=re.I).strip()
    card = re.sub(r"\s+", " ", card).strip()

    if not card:
        card = cur_card

    return qty, card


def _parse_expected_from_text(text: str) -> dict[tuple[str, str], int]:
    """
    returns {(username_lower, card_norm): expected_qty}
    """
    expected: dict[tuple[str, str], int] = defaultdict(int)
    cur_card: str | None = None
    cur_default_qty = 1

    for raw in (text or "").splitlines():
        line = (raw or "").strip()
        if not line:
            continue

        # пропускаем "Имя, [04.02.2026 19:04:08]"
        if _AUTHOR_TS_RE.match(line):
            continue

        low = line.lower()

        # групповый заголовок, не карта
        if _norm(line) in {"золото 18к"}:
            cur_card = None
            cur_default_qty = 1
            continue

        # заголовки вида "Каин и Авель, по одной карте"
        if "по одной" in low:
            card_title = line.split(",")[0].strip()
            if card_title:
                cur_card = card_title
                cur_default_qty = 1
            continue

        # заголовки вида "Джон (с белкой) 21 карта" / "Лилиан 19 карт"
        if not line.startswith("@"):
            hdr = line.rstrip(":").strip()
            hdr = re.sub(r"\s+\d+\s*карт\w*\s*$", "", hdr, flags=re.I).strip()
            # если это выглядит как название карты (короткая строка) - ставим контекст
            if hdr and len(hdr) <= 60:
                cur_card = hdr
                cur_default_qty = 1
            continue

        # строки вида "@user ...."
        m = _USER_LINE_RE.match(line)
        if not m:
            continue
        uname = _norm(m.group(1))
        rest = (m.group(2) or "").strip()

        qty, card = _parse_qty_and_card(rest, cur_card)
        if card is None:
            continue

        # если в rest вообще нет названия карты (например "@yaaziyaa"), берем cur_card
        # qty по умолчанию 1, но если cur_card задан и в rest пусто, ок
        card_norm = _norm(card)

        # если rest пустой, но у нас стоит cur_default_qty (редко нужно), применим
        if not rest and cur_card:
            qty = cur_default_qty

        expected[(uname, card_norm)] += int(qty)

    return dict(expected)


def _chunk(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines = text.splitlines()
    out, cur, size = [], [], 0
    for ln in lines:
        add = len(ln) + 1
        if cur and size + add > limit:
            out.append("\n".join(cur))
            cur, size = [ln], add
        else:
            cur.append(ln)
            size += add
    if cur:
        out.append("\n".join(cur))
    return out


@router.message(Command("ex_check_list"))
@admin_only
async def cmd_ex_check_list(message: types.Message) -> None:
    src = message.reply_to_message or message
    raw = (src.text or src.caption or "").strip()

    # если не реплаем, а вставили после команды
    if src is message and raw.startswith("/ex_check_list"):
        raw = raw[len("/ex_check_list"):].strip()

    if not raw:
        await message.answer(
            "Формат: пришли список одним сообщением и ответь на него /ex_check_list\n"
            "Или вставь список сразу после команды."
        )
        return

    expected = _parse_expected_from_text(raw)
    if not expected:
        await message.answer("Не смог распарсить список (не вижу строк вида @username ...).")
        return

    winners = sorted({u for (u, _) in expected.keys()})
    # подтягиваем user_id (если есть)
    rows_u = await fetch(
        "SELECT user_id, lower(username) AS uname FROM public.users WHERE lower(username) = ANY($1::text[])",
        winners,
    )
    uid_by_uname = {str(r["uname"]).strip().lower(): int(r["user_id"]) for r in (rows_u or [])}

    unames = winners
    uids = [uid_by_uname.get(u, -1) for u in unames]

    sql = """
          WITH w AS (SELECT unnest($1::text[]) AS uname, unnest($2::bigint[]) AS uid),
               card_items AS (SELECT w.uname                         AS uname,
                                     lower(regexp_replace(
                                             replace(trim(COALESCE(NULLIF(ei.card_name, ''), c.card_name)), 'ё', 'е'),
                                             '\\s+', ' ', 'g'))      AS card_norm,
                                     (eb.manual_sent_at IS NOT NULL) AS is_sent,
                                     COUNT(*) ::int AS qty
                              FROM public.exchange_batches eb
                                       JOIN w
                                            ON (eb.manual_winner_id = w.uid
                                                OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) =
                                                   w.uname)
                                       JOIN public.exchange_items ei
                                            ON ei.batch_id = eb.batch_id
                                       LEFT JOIN public.cards c
                                                 ON c.card_id = ei.card_id
                              WHERE eb.deleted_at IS NULL
                                AND eb.status = 'approved'
                                AND eb.mode IN ('card', 'deck_split')
                              GROUP BY w.uname, card_norm, is_sent),
               deck_items AS (SELECT w.uname                                                    AS uname,
                                     lower(regexp_replace(replace(
                                                                  trim(COALESCE(NULLIF(d.name, ''), (eb.deck_id::text || ' колода'))),
                                                                  'ё', 'е'), '\\s+', ' ', 'g')) AS card_norm,
                                     (eb.manual_sent_at IS NOT NULL)                            AS is_sent,
                                     COUNT(*) ::int AS qty
                              FROM public.exchange_batches eb
                                       JOIN w
                                            ON (eb.manual_winner_id = w.uid
                                                OR lower(replace(coalesce(eb.manual_winner_username, ''), '@', '')) =
                                                   w.uname)
                                       LEFT JOIN public.decks d
                                                 ON d.id = eb.deck_id
                              WHERE eb.deleted_at IS NULL
                                AND eb.status = 'approved'
                                AND eb.mode = 'deck'
                              GROUP BY w.uname, card_norm, is_sent)
          SELECT *
          FROM card_items
          UNION ALL
          SELECT *
          FROM deck_items \
          """

    rows = await fetch(sql, unames, uids)

    sent_map: dict[tuple[str, str], int] = defaultdict(int)
    assigned_map: dict[tuple[str, str], int] = defaultdict(int)

    for r in (rows or []):
        uname = str(r["uname"]).strip().lower()
        card_norm = str(r["card_norm"] or "").strip().lower()
        if not uname or not card_norm:
            continue
        qty = int(r["qty"] or 0)
        assigned_map[(uname, card_norm)] += qty
        if bool(r["is_sent"]):
            sent_map[(uname, card_norm)] += qty

    # сверка
    missing_by_user: dict[str, list[str]] = defaultdict(list)
    total_expected = 0
    total_sent = 0
    total_missing = 0

    for (uname, card_norm), exp_qty in expected.items():
        exp_qty = int(exp_qty)
        total_expected += exp_qty
        sent = int(sent_map.get((uname, card_norm), 0))
        assigned = int(assigned_map.get((uname, card_norm), 0))
        total_sent += min(sent, exp_qty)

        if sent < exp_qty:
            miss = exp_qty - sent
            total_missing += miss
            # красиво: показываем, назначено ли вообще и висит ли "не отправлено"
            tail = ""
            if assigned > sent:
                tail = f" (назначено {assigned}, отправлено {sent})"
            else:
                tail = f" (в БД отправлено {sent})"
            missing_by_user[uname].append(f"• {card_norm} ×{miss}{tail}")

    lines: list[str] = []
    lines.append("📋 <b>Биржа • сверка по принятому списку</b>")
    lines.append(f"Пользователей: <b>{len(winners)}</b>")
    lines.append(
        f"Ожидаемо по списку: <b>{total_expected}</b> • Отмечено отправленным: <b>{total_sent}</b> • Не добито: <b>{total_missing}</b>")
    lines.append("")

    if not missing_by_user:
        lines.append("✅ По этой сверке всё закрыто: по списку нет недоотправленного (по данным БД).")
        for part in _chunk("\n".join(lines)):
            await message.answer(part, parse_mode="HTML")
        return

    lines.append("❌ <b>Кому по списку НЕ хватает (по данным БД):</b>")
    lines.append("")

    # выводим только проблемных
    for uname in sorted(missing_by_user.keys()):
        lines.append(f"<b>@{uname}</b>")
        lines.extend(missing_by_user[uname])
        lines.append("")

    for part in _chunk("\n".join(lines)):
        await message.answer(part, parse_mode="HTML")

@router.message(Command("unlux"), F.chat.type == "private")
@admin_only
async def cmd_remove_luxury(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование:\n"
            "<code>/unlux @username</code>\n"
            "<code>/unlux user_id</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()
    user = None

    if raw.startswith("@"):
        username = raw[1:]
        user = await get_user_by_username(username)
    else:
        try:
            uid = int(raw)
        except ValueError:
            await message.answer("Укажи корректный @username или numeric user_id.")
            return
        user = await get_user(uid)

    if not user:
        await message.answer("Пользователь не найден в базе.")
        return

    user_id = int(user["user_id"])
    username = user.get("username")
    full_name = user.get("full_name") or "—"

    if not bool(user.get("is_luxury")):
        await message.answer(
            f"У пользователя уже нет лакшери-статуса.\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Username: @{username}" if username else f"ID: <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return

    await set_luxury_status(user_id, False)

    await log_audit_action(
        user_id=message.from_user.id,
        action_type="remove_luxury_status",
        auction_id=None,
        details=f"removed luxury from user_id={user_id} username={username or '-'}",
    )

    lines = [
        "✅ Лакшери-статус снят.",
        f"ID: <code>{user_id}</code>",
        f"Имя: {full_name}",
    ]
    if username:
        lines.append(f"Username: @{username}")

    lines.append(
        "\n⚠️ Если пользователь всё ещё состоит в лакшери-чате, "
        "следующий refresh может вернуть статус обратно."
    )

    await message.answer("\n".join(lines), parse_mode="HTML")
