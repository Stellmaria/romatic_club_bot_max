"""Shared exchange contracts, presentation primitives and pricing helpers.

This module deliberately has no dependency on an exchange router.  Both the
submission and moderation routers import it, which keeps their import graph
acyclic while preserving the historical public names re-exported by those
routers.
"""

from __future__ import annotations

import html
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from bot.services.exchange_submission import ExchangeSubmissionQueries
from db.cards import get_all_decks


_BR_RE = re.compile(r"(?i)<br\s*/?>")
BR_RE = _BR_RE

CURRENCY_EMOJI = {"алмазы": "💎", "чашки": "🍵", "сокровища": "🪙"}

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

ANNOUNCE_TZ = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

GUIDE_AUTHOR_USERNAME = "Dear_Davidik"
GUIDE_AUTHOR_LINK = (
    f'<a href="https://t.me/{GUIDE_AUTHOR_USERNAME}">@{GUIDE_AUTHOR_USERNAME}</a>'
)
GUIDE_UID_CREDIT = (
    "\n\n✍️ <b>Автор:</b> Анонимный автор"
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
EX_DECKS = [22, 24, 26]

EX_FIXED_PRICE_BY_CARD: dict[tuple[str, str, int], int] = {
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


def _ex20_key(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (value or "").strip().lower())


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


def h(value: object, default: str = "—") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return html.escape(text, quote=False)


def _norm_currency(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if ("💎" in text) or ("алмаз" in text) or ("кристалл" in text) or ("diamond" in text):
        return "алмазы"
    if ("🍵" in text) or ("чаш" in text) or ("чай" in text) or ("cup" in text) or ("tea" in text):
        return "чашки"
    if ("🪙" in text) or ("сокров" in text) or ("treasure" in text):
        return "сокровища"
    return None


def currency_to_emoji(currency: str | None) -> str:
    value = (currency or "").strip()
    aliases = {
        "кристаллы": "💎",
        "чай": "🍵",
    }
    return aliases.get(value.lower()) or CURRENCY_EMOJI.get(
        _norm_currency(value) or value.lower(), "💎"
    )


def _norm_rarity(value: str | None) -> str:
    if not value:
        return "any"
    return RARITY_MAP.get(value.strip().lower(), "any")


def _rarity_norm(value: str | None) -> str:
    return _norm_rarity(value)


def _rarity_badge(value: str | None) -> str:
    rarity = _rarity_norm(value)
    return {
        "bronze": "🥉",
        "silver": "🥈",
        "gold": "🥇",
        "diamond": "💎",
    }.get(rarity, "🔷")


def _cur_emoji(currency: str) -> str:
    return CURRENCY_EMOJI.get(currency, "💎")


def _deck_id_from_row(deck: dict) -> int:
    try:
        return int(deck.get("deck_id") or deck.get("id") or 0)
    except Exception:
        return 0


def _deck_name_from_row(deck: dict) -> str:
    return (deck.get("name") or deck.get("title") or deck.get("deck_name") or "").strip()


def _latest_resource_deck_ids_from_rows(decks: list[dict] | None) -> list[int]:
    ids: set[int] = set()
    for deck in decks or []:
        deck_type = (deck.get("deck_type") or "").strip().lower()
        deck_id = _deck_id_from_row(deck)
        if deck_id and deck_id % 2 == 0 and deck_type == "resource":
            ids.add(deck_id)
    return sorted(sorted(ids, reverse=True)[:EXCHANGE_RESOURCE_DECK_LIMIT])


async def _get_exchange_deck_ids(decks: list[dict] | None = None) -> list[int]:
    ids = _latest_resource_deck_ids_from_rows(decks)
    if ids:
        return ids

    try:
        queries = await ExchangeSubmissionQueries.create()
        ids = await queries.latest_resource_deck_ids(EXCHANGE_RESOURCE_DECK_LIMIT)
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
    by_id = {
        _deck_id_from_row(deck): dict(deck)
        for deck in (decks_all or [])
        if _deck_id_from_row(deck)
    }

    result: list[dict] = []
    for deck_id in allowed_ids:
        row = by_id.get(deck_id, {"deck_id": deck_id, "name": f"{deck_id} колода"})
        row["deck_id"] = deck_id
        if not _deck_name_from_row(row):
            row["name"] = f"{deck_id} колода"
        result.append(row)
    return result


def _gift_emoji(obtain_type: str) -> str:
    value = (obtain_type or "").strip().lower()
    if value == "diamonds":
        return "💎"
    if value == "cups":
        return "🍵"
    if value == "treasures":
        return "🪙"
    return "🎁"


def _norm_ex_obtain_type(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"tea", "cups", "cup", "чай", "чашки", "чашка"}:
        return "cups"
    if normalized in {"diamonds", "diamond", "алмазы", "алмаз"}:
        return "diamonds"
    if normalized in {"treasures", "treasure", "сокровища", "сокровище"}:
        return "treasures"
    return normalized


def _exchange_key_for_card(card: dict) -> tuple[str, str, int]:
    rarity = _norm_rarity(card.get("rarity") or card.get("rarity_norm"))
    obtain_type = _norm_ex_obtain_type(str(card.get("obtain_type") or ""))
    obtain_amount = int(card.get("obtain_amount") or 0)
    return rarity, obtain_type, obtain_amount


def _exchange_price_for_card(card: dict) -> int:
    try:
        if int(card.get("deck_id") or 0) == 20:
            hero_key = _ex20_key(card.get("hero_name"))
            card_key = _ex20_key(card.get("card_name"))
            price = EX_FIXED_PRICE_DECK20_BY_HERO_CARD.get((20, hero_key, card_key))
            if price:
                return int(price)

            obtain_type = _norm_ex_obtain_type(str(card.get("obtain_type") or ""))
            obtain_amount = int(card.get("obtain_amount") or 0)
            gain_price = EX_FIXED_PRICE_DECK20_BY_GAIN.get((obtain_type, obtain_amount))
            if gain_price:
                return int(gain_price)
    except Exception:
        pass

    for key in ("price_diamonds", "exchange_price_diamonds", "price"):
        value = card.get(key)
        if value is None:
            continue
        try:
            price = int(value)
            if price > 0:
                return price
        except Exception:
            pass

    return int(EX_FIXED_PRICE_BY_CARD.get(_exchange_key_for_card(card), 0))


def _exchange_gain_for_card(card: dict) -> tuple[str, int]:
    obtain_type = _norm_ex_obtain_type(str(card.get("obtain_type", "")))
    obtain_amount = int(card.get("obtain_amount") or 0)
    return obtain_type, obtain_amount


def _exchange_gift_for_card(card: dict) -> tuple[str, int]:
    return _exchange_gain_for_card(card)


def tg_clean(text: str) -> str:
    return BR_RE.sub("\n", text or "")
