from __future__ import annotations

import html
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from html import escape as _h
from typing import Any, Optional

from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.domain.auctions import InvalidExchangeTransition
from bot.handlers.admin.action_support.exchange import (
    _safe_user_mention,
    format_exchange_moderation_log,
    notify_exchange_user_moderation,
)
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import short_media_id
from bot.handlers.admin.services.market_utils import safe_edit_text
from bot.features.exchange.contracts import (
    ANNOUNCE_TZ,
    BR_RE,
    CURRENCY_EMOJI,
    EXCHANGE_RESOURCE_DECK_LIMIT,
    EX_DECKS,
    EX_FIXED_PRICE_BY_CARD,
    EX_FIXED_PRICE_DECK20_BY_GAIN,
    EX_FIXED_PRICE_DECK20_BY_HERO_CARD,
    EX_WHOLE_DECK_PRICE,
    GUIDE_AUTHOR_LINK,
    GUIDE_AUTHOR_USERNAME,
    GUIDE_UID_CRAFT_PHOTO_ID,
    GUIDE_UID_CRAFT_TEXT,
    GUIDE_UID_CREDIT,
    RARITY_MAP,
    UTC,
    _BR_RE,
    _cur_emoji,
    _deck_id_from_row,
    _deck_name_from_row,
    _ex20_key,
    _exchange_gain_for_card,
    _exchange_gift_for_card,
    _exchange_key_for_card,
    _exchange_price_for_card,
    _get_exchange_deck_ids,
    _get_exchange_decks_for_menu,
    _gift_emoji,
    _latest_resource_deck_ids_from_rows,
    _norm_currency,
    _norm_ex_obtain_type,
    _norm_rarity,
    _rarity_badge,
    _rarity_norm,
    currency_to_emoji,
    h,
    tg_clean,
)
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.admin_logging import send_admin_log
from bot.services.exchange_media import get_exchange_cover_media as _get_exchange_cover_media
from bot.services.exchange_submission import ExchangeSubmissionQueries
from bot.services.exchanges import ExchangeService
from bot.services.luxury import get_user_luxury_level, is_luxury_member
from bot.telegram.media import answer_media_any as _answer_media_any, safe_send_media
from bot.core.settings import (
    ADMINS,
    AUCTION_CHANNEL_ID,
    AUCTION_CHANNEL_USERNAME,
    LUXURY_CHAT_ID,
    LUXURY_CHAT_ID_LVL2,
)
from db.auctions import (
    count_sold_by_card_id,
    count_sold_same_card,
    show_pending_auction_lots,
)
from db.cards import (
    get_all_decks,
    get_card_by_id,
    get_cards_by_deck,
    get_cards_by_ids,
    get_cards_ids_by_deck,
    get_deck_by_id,
)
from db.exchange import (
    get_exchange_approved_cards_by_deck,
    get_exchange_batch,
    get_exchange_batch_by_id,
    get_exchange_cards_for_batch,
    get_exchange_cards_for_deck,
    get_exchange_items_by_batch_id,
    mark_exchange_manual_sent,
    set_exchange_manual_winner,
)
from db.users import (
    get_user,
    get_user_by_username,
    is_luxury_user,
)
from db.admin import (
    is_admin,
    log_admin_action,
)
from bot.telegram.states import ExchangeFSM, ModActionFSM, UserAddLotFSM

router = Router(name="auction_exchange")

EX_MODE_DECK = "deck"

EX_MODE_CARD = "card"

EX_STATUS_APPROVED = "approved"

EX_MODE_DECK_SPLIT = "deck_split"  # “часть колоды” / набор карт

EX_CARDLIKE_MODES = (EX_MODE_CARD, EX_MODE_DECK_SPLIT)

EX_MODE_CARDLIKE = EX_CARDLIKE_MODES

ANY_DECK_VIDEO_ID = "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"

MIN_START = {
    "алмазы": 30,
    "чашки": 2,
    "сокровища": 10,
}

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

EX_DECK_COVER_MEDIA: dict[int, str] = {
    18: "BAACAgQAAxkBAAEJWWdpfjDFq_YchjQPKpaJhd8O4TntKwAC9RsAAna_-FO_2KeAQzb4DzgE",
    20: "BAACAgQAAxkBAAEJWWFpfjCEe81DEJBAHn9BKBYWgGvrAwAC8hsAAna_-FN0tX00LgFtCDgE",
    22: "BAACAgIAAyEFAASe0o_mAAEBMZ5pwF8nenGGWKgz-6vB1kc0pbPF2QACEJsAArogAAFKmdXz6_xXigI6BA",
}

def _exchange_deck_cover_id(deck_id: int) -> str:
    did = int(deck_id or 0)
    # сначала конкретная заставка для 16/18/20, иначе общий фолбэк (если задан)
    return (EX_DECK_COVER_MEDIA.get(did) or ANY_DECK_VIDEO_ID or "").strip()

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

async def _get_deck_type_from_state_or_db(data: dict) -> str | None:
    dt = (data.get("deck_type") or "").strip().lower()
    if dt in {"roulette", "resource"}:
        return dt

    has_identity = bool(data.get("card_name") and data.get("hero_name"))
    if not (data.get("card_id") or data.get("deck_id") or has_identity):
        return None

    queries = await ExchangeSubmissionQueries.create()

    if data.get("card_id"):
        deck_type = await queries.deck_type_for_card(int(data["card_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if data.get("deck_id"):
        deck_type = await queries.deck_type_for_deck(int(data["deck_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if has_identity:
        deck_type = await queries.deck_type_for_card_identity(
            data["card_name"],
            data["hero_name"],
        )
        if deck_type in ("roulette", "resource"):
            return deck_type

    return None

def _currency_label(currency: str) -> str:
    return {"алмазы": "алмазы", "чашки": "чай", "сокровища": "сокровища"}.get(currency, currency)

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
        cover_id = _exchange_deck_cover_id(deck_id_i)

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
    service = await ExchangeService.create()
    batch = await service.submit(
        user_id=int(user_id),
        deck_id=int(deck_id),
        mode=(mode or "card").strip() or "card",
        currency=(currency or "алмазы").strip() or "алмазы",
        price=int(price or 0),
        comment=(comment or "-").strip() or "-",
        proof_photo_id=(proof_photo_id or "NO_PROOF").strip() or "NO_PROOF",
        card_ids=[int(cid) for cid in (card_ids or [])],
    )
    return int(batch["batch_id"])


# auctions.py

async def _finalize_exchange_request(
        message: Message,
        state: FSMContext,
        bot: Bot,
        proof_photo_id: str | None = None,
) -> None:
    # Lazy import keeps submission independent from the moderation router at
    # module-import time while preserving the existing notification workflow.
    from bot.handlers.auction.exchange_moderation import (
        _fmt_dt_msk,
        _send_user_exchange_confirmation,
        _send_user_exchange_confirmation_copies,
        _send_user_exchange_confirmation_deck_split,
    )

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
            queries = await ExchangeSubmissionQueries.create()
            deck_name = await queries.deck_name(deck_id_i)
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

            batch_id = await _db_create_exchange_batch(
                user_id=user_id,
                username=message.from_user.username or "",
                deck_id=deck_id_i,
                mode=mode,
                card_ids=[int(cid)],
                currency=currency,
                price=price_one,
                comment=comment,
                proof_photo_id=proof_photo_id,
            )
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
            batch_id = await _db_create_exchange_batch(
                user_id=user_id,
                username=message.from_user.username or "",
                deck_id=deck_id_i,
                mode=mode,
                card_ids=[cid],
                currency=currency,
                price=price_one,
                comment=comment,
                proof_photo_id=proof_photo_id,
            )
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

    batch_id = await _db_create_exchange_batch(
        user_id=user_id,
        username=message.from_user.username or "",
        deck_id=deck_id_i,
        mode=mode,
        card_ids=[int(cid) for cid in card_ids],
        currency=currency,
        price=int(price_i or 0),
        comment=comment,
        proof_photo_id=proof_photo_id,
    )

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
    # Keep this as a normal assignment: Python 3.14 exposes conditional local
    # annotations as a synthetic unresolved module global in ``symtable``.
    proof_photo_id = None  # type: str | None

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


# Stable public bridge used by the lot-creation flow.  These aliases remain in
# the submission module so existing consumers do not depend on moderation.
exchange_deck_id_from_row = _deck_id_from_row
get_exchange_deck_ids = _get_exchange_deck_ids
get_exchange_decks_for_menu = _get_exchange_decks_for_menu
exchange_price_for_card = _exchange_price_for_card
exchange_gain_for_card = _exchange_gain_for_card
clean_telegram_text = tg_clean


async def show_pending_exchange_requests(message: types.Message, page: int = 0) -> None:
    """Backward-compatible lazy entry point for the extracted moderation UI."""
    from bot.handlers.auction.exchange_moderation import (
        show_pending_exchange_requests as _show_pending_exchange_requests,
    )

    await _show_pending_exchange_requests(message, page=page)
