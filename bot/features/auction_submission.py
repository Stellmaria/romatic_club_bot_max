"""Pricing, validation and presentation support for lot submission."""

import html
import logging
import re
from datetime import date, datetime, timedelta
from datetime import time as dtime
from datetime import timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from bot.features.exchange.contracts import (
    EX_WHOLE_DECK_PRICE,
    _exchange_gain_for_card,
    _exchange_price_for_card,
)
from bot.services.auction_submission import AuctionSubmissionCatalogService

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

ANY_DECK_PHOTO_ID = (
    "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"
)
ANY_CARD_PHOTO_ID = (
    "AgACAgQAAxkBAAEIUTBpaNQXPz1Hs-BRv8cVslhg336rfgACnQtrG4d4QVO6Nvb-lxW0sgEAAwIAA3kAAzgE"
)

ANY_DECK_VIDEO_ID = (
    "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"
)
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
    "эпик": "diamond",
    "epic": "diamond",
    "алмаз": "diamond",
    "алмазная": "diamond",
    "diamond": "diamond",
    "золото": "gold",
    "gold": "gold",
    "серебро": "silver",
    "silver": "silver",
    "бронза": "bronze",
    "bronze": "bronze",
    "любая": "any",
    "any": "any",
}
RARITY_PRICE_KEYS = {"bronze", "silver", "gold", "diamond"}

DECK_SCOPE_KEYS = {"deck", "whole_deck", "full_deck", "колода", "вся_колода"}

EX_DECK_COVER_MEDIA: dict[int, str] = {
    18: "BAACAgQAAxkBAAEJWWdpfjDFq_YchjQPKpaJhd8O4TntKwAC9RsAAna_-FO_2KeAQzb4DzgE",
    20: "BAACAgQAAxkBAAEJWWFpfjCEe81DEJBAHn9BKBYWgGvrAwAC8hsAAna_-FN0tX00LgFtCDgE",
    22: "BAACAgIAAyEFAASe0o_mAAEBMZ5pwF8nenGGWKgz-6vB1kc0pbPF2QACEJsAArogAAFKmdXz6_xXigI6BA",
}


def _exchange_deck_cover_id(deck_id: int) -> str:
    did = int(deck_id or 0)
    # сначала конкретная заставка для 16/18/20, иначе общий фолбэк (если задан)
    return (EX_DECK_COVER_MEDIA.get(did) or ANY_DECK_VIDEO_ID or "").strip()


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
    return bool(
        data.get("deck_id")
        and not data.get("card_id")
        and any_card not in {"1", "true", "yes", "да", "any", "любая"}
    )


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
    cur = (currency or "").strip()
    aliases = {"кристаллы": "💎", "чай": "🍵"}
    return aliases.get(cur.lower()) or CURRENCY_EMOJI.get(
        _norm_currency(cur) or cur.lower(), "💎"
    )


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

    catalog = await AuctionSubmissionCatalogService.create()
    full_cards = await catalog.cards_for_deck(int(deck_id))
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
        catalog = await AuctionSubmissionCatalogService.create()
        return _norm_obtain_type(await catalog.obtain_type(int(cid)))

    return None


async def _get_deck_type_from_state_or_db(data: dict) -> str | None:
    dt = (data.get("deck_type") or "").strip().lower()
    if dt in {"roulette", "resource"}:
        return dt

    catalog = await AuctionSubmissionCatalogService.create()

    if data.get("card_id"):
        deck_type = await catalog.deck_type_for_card(int(data["card_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if data.get("deck_id"):
        deck_type = await catalog.deck_type_for_deck(int(data["deck_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if data.get("card_name") and data.get("hero_name"):
        deck_type = await catalog.deck_type_for_identity(
            card_name=str(data["card_name"]),
            hero_name=str(data["hero_name"]),
        )
        if deck_type in ("roulette", "resource"):
            return deck_type

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
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True, one_time_keyboard=True)


def auction_currency_kb(auction_kind: str | None) -> ReplyKeyboardMarkup:
    """Currency choices for reverse and free auction workflows."""
    kind = str(auction_kind or "standard").strip().lower()
    if kind in {"reverse", "free"}:
        rows = [
            [
                KeyboardButton(text="🍵 Чай"),
                KeyboardButton(text="💎 Алмазы"),
            ],
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
    for token, val in (
        ("бронз", "bronze"),
        ("сереб", "silver"),
        ("золот", "gold"),
        ("эпик", "diamond"),
        ("алмаз", "diamond"),
    ):
        if token in cname:
            return val

    # добиваем из БД
    cid = data.get("card_id")
    if cid:
        try:
            catalog = await AuctionSubmissionCatalogService.create()
            card = await catalog.card(int(cid))
            if card:
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

    any_cups_by_decktype = globals().get(
        "ANY_CARD_CUPS_BY_DECKTYPE", {"roulette": 8, "resource": 6}
    )
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
            return _ret(rng[0], rng[1], f"{SERVICE_TITLES.get(service, service)} ({months} мес.)")

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

        return _ret(global_min, 10**9, "Колода (тип неизвестен) — без потолка")

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
                    return _ret(
                        global_min, int(table[rarity]), f"Ресурсная ({obtain_type}, {rarity})"
                    )
                return _ret(
                    global_min,
                    int(max(table.values())),
                    f"Ресурсная ({obtain_type}, редкость не указана)",
                )

    # известны тип деки + редкость
    if deck_type and (rarity in rarity_keys):
        try:
            deck_cap = int(MAX_CARD_BY_DECKTYPE[deck_type][currency][rarity])
        except KeyError:
            deck_cap = 10**9

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
            deck_any_cap = int(max(dt_caps)) if dt_caps else 10**9
        except Exception:
            deck_any_cap = 10**9

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
    end_local = start_local + timedelta(days=1)  # полузакрытый интервал [start, end)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


from aiogram.exceptions import TelegramNetworkError


async def safe_answer(message, text: str, **kwargs):
    try:
        return await message.answer(text, **kwargs)
    except TelegramNetworkError as e:
        log.warning("safe_answer network error: %s", e)
        return None


async def _ensure_membership(
    bot, user_id: int, channel_id: int, discussion_chat_id: int
) -> bool | None:
    try:
        m1 = await bot.get_chat_member(channel_id, user_id)
        m2 = await bot.get_chat_member(discussion_chat_id, user_id)

        ok1 = getattr(m1, "status", None) not in {"left", "kicked"}
        ok2 = getattr(m2, "status", None) not in {"left", "kicked"}
        return bool(ok1 and ok2)

    except TelegramNetworkError as e:
        log.warning("membership network error for user %s: %s", user_id, e)
        return None

    except TelegramBadRequest as e:
        log.warning("membership bad request for user %s: %s", user_id, e)
        return False

    except Exception as e:
        log.exception("membership check failed for user %s: %s", user_id, e)
        return None
