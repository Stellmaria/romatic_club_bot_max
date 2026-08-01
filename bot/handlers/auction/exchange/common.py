from __future__ import annotations

"""Exchange flow component extracted during refactoring phase 7."""

import html
import re
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.handlers.helper.helpers_users import emoji_by_currency
from bot.services.auction_media import resolve_media_file_id
from bot.services.exchange_submission import ExchangeSubmissionQueries
from db.legacy import get_all_decks, get_card_by_id, get_exchange_cards_for_deck

EX_MODE_DECK = "deck"


EX_MODE_CARD = "card"


EX_STATUS_APPROVED = "approved"


EX_MODE_DECK_SPLIT = "deck_split"  # “часть колоды” / набор карт


EX_CARDLIKE_MODES = (EX_MODE_CARD, EX_MODE_DECK_SPLIT)


EX_MODE_CARDLIKE = EX_CARDLIKE_MODES


ANY_DECK_VIDEO_ID = "AgACAgQAAxkBAAEIUFBpaM1cQg4yvRq7X_ds4hxYKus3cgACmAtrG4d4QVPiV2yuTCUgTAEAAwIAA3kAAzgE"


_BR_RE = re.compile(r"(?i)<br\s*/?>")


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


RARITY_MAP = {
    "эпик": "diamond", "epic": "diamond", "алмаз": "diamond", "алмазная": "diamond", "diamond": "diamond",
    "золото": "gold", "gold": "gold",
    "серебро": "silver", "silver": "silver",
    "бронза": "bronze", "bronze": "bronze",
    "любая": "any", "any": "any",
}


EX_DECK_COVER_MEDIA: dict[int, str] = {
    18: "BAACAgQAAxkBAAEJWWdpfjDFq_YchjQPKpaJhd8O4TntKwAC9RsAAna_-FO_2KeAQzb4DzgE",
    20: "BAACAgQAAxkBAAEJWWFpfjCEe81DEJBAHn9BKBYWgGvrAwAC8hsAAna_-FN0tX00LgFtCDgE",
    22: "BAACAgIAAyEFAASe0o_mAAEBMZ5pwF8nenGGWKgz-6vB1kc0pbPF2QACEJsAArogAAFKmdXz6_xXigI6BA",
}


async def _exchange_deck_cover_id(deck_id: int) -> str:
    did = int(deck_id or 0)
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
        e = emoji_by_currency(cur)  # noqa: SLF001 (да, protected, зато работает)
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


async def _get_deck_type_from_state_or_db(data: dict) -> str | None:
    dt = (data.get("deck_type") or "").strip().lower()
    if dt in {"roulette", "resource"}:
        return dt

    if data.get("card_id"):
        deck_type = await (await ExchangeSubmissionQueries.create()).deck_type_for_card(int(data["card_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if data.get("deck_id"):
        deck_type = await (await ExchangeSubmissionQueries.create()).deck_type_for_deck(int(data["deck_id"]))
        if deck_type in ("roulette", "resource"):
            return deck_type

    if data.get("card_name") and data.get("hero_name"):
        deck_type = await (await ExchangeSubmissionQueries.create()).deck_type_for_card_identity(
            data["card_name"], data["hero_name"]
        )
        if deck_type in ("roulette", "resource"):
            return deck_type

    return None


def _currency_label(currency: str) -> str:
    return {"алмазы": "алмазы", "чашки": "чай", "сокровища": "сокровища"}.get(currency, currency)


CURRENCY_EMOJI = {"алмазы": "💎", "чашки": "🍵", "сокровища": "🪙"}


def _cur_emoji(currency: str) -> str:
    return CURRENCY_EMOJI.get(currency, "💎")


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


ANNOUNCE_TZ = ZoneInfo("Europe/Moscow")  # если показываешь время в МСК


UTC = timezone.utc


GUIDE_AUTHOR_USERNAME = "Dear_Davidik"


GUIDE_AUTHOR_LINK = f'<a href="https://t.me/{GUIDE_AUTHOR_USERNAME}">@{GUIDE_AUTHOR_USERNAME}</a>'


GUIDE_UID_CREDIT = (
    f"\n\n✍️ <b>Автор:</b> Анонимный автор"
    f"\n✍️ <b>Написал и оформил:</b> {GUIDE_AUTHOR_LINK}"
)


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
        ids = await (await ExchangeSubmissionQueries.create()).latest_resource_deck_ids(
            EXCHANGE_RESOURCE_DECK_LIMIT
        )
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


def _currency_emoji(cur: str) -> str:
    c = (cur or "").strip().lower()
    if "алмаз" in c or c in ("💎", "diamond", "diamonds"):
        return "💎"
    if "чаш" in c or c in ("🍵", "cups"):
        return "🍵"
    if "сокров" in c or c in ("🪙", "treasures"):
        return "🪙"
    return "💎"


def _h(s: str | None) -> str:
    return html.escape((s or "").strip(), quote=False)


def _exchange_key_for_card(card: dict) -> tuple[str, str, int]:
    rarity = _norm_rarity(card.get("rarity") or card.get("rarity_norm"))
    ot = _norm_ex_obtain_type(str(card.get("obtain_type") or ""))
    oa = int(card.get("obtain_amount") or 0)
    return rarity, ot, oa


BR_RE = re.compile(r"(?i)<br\s*/?>")


def tg_clean(text: str) -> str:
    return BR_RE.sub("\n", text or "")


def _fmt_dt_msk(dt: Any) -> str:
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(ANNOUNCE_TZ).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)


def _tg_clean(text: str) -> str:
    return _BR_RE.sub("\n", text or "")


def _user_link(user_id: int, username: Optional[str]) -> str:
    label = f"@{username}" if username else f"id:{user_id}"
    return f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'

# Public feature contracts. Private names remain temporary local aliases.
cur_emoji = _cur_emoji
currency_emoji = _currency_emoji
currency_label = _currency_label
deck_id_from_row = _deck_id_from_row
deck_price_for_deck = _deck_price_for_deck
digits_int = _digits_int
exchange_cards_kb = _exchange_cards_kb
exchange_deck_cover_id = _exchange_deck_cover_id
exchange_gain_for_card = _exchange_gain_for_card
exchange_gift_for_card = _exchange_gift_for_card
exchange_key_for_card = _exchange_key_for_card
exchange_price_for_card = _exchange_price_for_card
fmt_dt_msk = _fmt_dt_msk
format_gain_line = _format_gain_line
get_exchange_deck_ids = _get_exchange_deck_ids
get_exchange_decks_for_menu = _get_exchange_decks_for_menu
gift_emoji = _gift_emoji
escape_html = _h
load_full_cards_for_deck = _load_full_cards_for_deck
normalize_card_ids = _normalize_card_ids
rarity_badge = _rarity_badge
rarity_norm = _rarity_norm
sum_gains = _sum_gains
clean_telegram_text = _tg_clean
user_link = _user_link
