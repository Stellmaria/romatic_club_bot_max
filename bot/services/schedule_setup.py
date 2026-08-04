# ruff: noqa: RUF001
"""Pure workflow helpers for the Premium schedule setup master."""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aiogram.types import Message

from bot.domain.schedule_lots import SPECIAL_SCHEDULE_ASSETS
from db.schedule_setup import (
    get_all_decks_for_setup,
    get_cards_for_setup,
    get_emoji_assets,
    set_setup_session,
)


@dataclass(frozen=True, slots=True)
class AssetSpec:
    key: str
    label: str
    fallback: str


COMMON_ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec("rarity:bronze", "редкости «Бронза»", "🤎"),
    AssetSpec("rarity:silver", "редкости «Серебро»", "🩶"),
    AssetSpec("rarity:gold", "редкости «Золото»", "💛"),
    AssetSpec("rarity:epic", "редкости «Эпик»", "💜"),
    AssetSpec("currency:diamonds", "награды «Алмазы»", "💎"),
    AssetSpec("currency:tea", "награды «Чай»", "☕"),
    AssetSpec("whole_deck", "лота «Вся колода»", "🃏"),
    *(AssetSpec(spec.key, spec.label, spec.fallback) for spec in SPECIAL_SCHEDULE_ASSETS),
)
ASSET_BY_KEY = {asset.key: asset for asset in COMMON_ASSETS}

_RARITY_ALIASES = {
    "bronze": "bronze",
    "бронза": "bronze",
    "бронзовая": "bronze",
    "silver": "silver",
    "серебро": "silver",
    "серебряная": "silver",
    "gold": "gold",
    "золото": "gold",
    "золотая": "gold",
    "epic": "epic",
    "эпик": "epic",
    "diamond": "epic",
    "diamonds": "epic",
    "алмаз": "epic",
    "алмазная": "epic",
}
_DIAMOND_REWARDS = {"bronze": 20, "silver": 40, "gold": 80, "epic": 120}
_TEA_REWARDS = {"bronze": 2, "silver": 4, "gold": 8, "epic": 12}
_CARD_TEXT_LIMIT = 180


def normalize_rarity(value: object) -> str | None:
    return _RARITY_ALIASES.get(str(value or "").strip().casefold())


def normalize_obtain_type(value: object) -> str | None:
    normalized = str(value or "").strip().casefold()
    if normalized in {"diamonds", "diamond", "алмазы", "алмаз", "gems"}:
        return "diamonds"
    if normalized in {"tea", "cups", "cup", "чай", "чашка", "чашки"}:
        return "tea"
    return None


def expected_reward(rarity: object, obtain_type: object) -> int | None:
    normalized_rarity = normalize_rarity(rarity)
    normalized_type = normalize_obtain_type(obtain_type)
    if not normalized_rarity or not normalized_type:
        return None
    table = _DIAMOND_REWARDS if normalized_type == "diamonds" else _TEA_REWARDS
    return table[normalized_rarity]


def validate_card_economy(card: Mapping[str, Any]) -> tuple[bool, str]:
    rarity = normalize_rarity(card.get("rarity"))
    obtain_type = normalize_obtain_type(card.get("obtain_type"))
    try:
        amount = int(card.get("obtain_amount") or 0)
    except (TypeError, ValueError):
        amount = 0

    if not rarity:
        return False, f"Неизвестная редкость: {card.get('rarity') or 'не указана'}"
    if not obtain_type:
        return False, "Не указан тип награды: нужны diamonds или tea"

    expected = expected_reward(rarity, obtain_type)
    if amount != expected:
        currency = "алмазов" if obtain_type == "diamonds" else "чая"
        return False, f"Ожидалось {expected} {currency}, записано {amount}"
    return True, "Экономика соответствует редкости"


def reward_text(card: Mapping[str, Any]) -> str:
    obtain_type = normalize_obtain_type(card.get("obtain_type"))
    try:
        amount = int(card.get("obtain_amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    suffix = "💎" if obtain_type == "diamonds" else "☕" if obtain_type == "tea" else "?"
    return f"+{amount}{suffix}"


def extract_single_custom_emoji(message: Message) -> int | None:
    entities = list(message.entities or ()) + list(message.caption_entities or ())
    ids: list[str] = []
    for entity in entities:
        entity_type = getattr(entity.type, "value", entity.type)
        if str(entity_type) != "custom_emoji" or not entity.custom_emoji_id:
            continue
        ids.append(str(entity.custom_emoji_id))
    if len(ids) != 1:
        return None
    try:
        return int(ids[0])
    except ValueError:
        return None


def custom_emoji_html(custom_emoji_id: int | None, fallback: str = "🎴") -> str:
    if not custom_emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{int(custom_emoji_id)}">{fallback}</tg-emoji>'


def _short_field(value: object, *, limit: int = _CARD_TEXT_LIMIT) -> str:
    text = " ".join(str(value or "—").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return html.escape(text)


def format_card_review(card: Mapping[str, Any]) -> str:
    economy_ok, economy_message = validate_card_economy(card)
    emoji_id = card.get("card_emoji_id")
    emoji_preview = custom_emoji_html(int(emoji_id) if emoji_id else None)
    status = "✅" if economy_ok else "❌"
    story = _short_field(card.get("story"))
    quote = _short_field(card.get("quote"))
    deck_name = html.escape(str(card.get("deck_name") or "—"))
    return (
        f"{emoji_preview} <b>Проверка карточки</b>\n\n"
        f"<b>ID:</b> <code>{int(card['card_id'])}</code>\n"
        f"<b>Колода:</b> №{int(card['deck_id'])} · {deck_name}\n"
        f"<b>Номер в колоде:</b> {card.get('num') or '—'}\n"
        f"<b>Карта:</b> {html.escape(str(card.get('card_name') or '—'))}\n"
        f"<b>Герой:</b> {html.escape(str(card.get('hero_name') or '—'))}\n"
        f"<b>Редкость:</b> {html.escape(str(card.get('rarity') or '—'))}\n"
        f"<b>Награда:</b> {reward_text(card)} "
        f"(<code>{html.escape(str(card.get('obtain_type') or '—'))}</code>)\n"
        f"<b>Premium emoji ID:</b> <code>{emoji_id or 'не заполнен'}</code>\n"
        f"<b>Экономика:</b> {status} {html.escape(economy_message)}\n\n"
        f"<b>История:</b> {story}\n"
        f"<b>Цитата:</b> {quote}"
    )


async def select_next_setup_step(user_id: int) -> dict[str, Any]:
    assets = await get_emoji_assets()
    for asset in COMMON_ASSETS:
        if asset.key not in assets:
            await set_setup_session(user_id, stage="asset_emoji", asset_key=asset.key)
            return {"kind": "asset", "asset": asset}

    decks = await get_all_decks_for_setup()
    for deck in decks:
        deck_id = int(deck["deck_id"])
        if not deck.get("deck_emoji_id"):
            await set_setup_session(user_id, stage="deck_emoji", deck_id=deck_id)
            return {"kind": "deck", "deck": deck}

        cards = await get_cards_for_setup(deck_id)
        for card in cards:
            if bool(card.get("emoji_verified")):
                continue
            stage = "card_review" if card.get("card_emoji_id") else "card_emoji"
            await set_setup_session(
                user_id,
                stage=stage,
                deck_id=deck_id,
                card_id=int(card["card_id"]),
            )
            return {"kind": "card", "card": card, "stage": stage}

    return {"kind": "complete"}


__all__ = [
    "ASSET_BY_KEY",
    "COMMON_ASSETS",
    "AssetSpec",
    "custom_emoji_html",
    "expected_reward",
    "extract_single_custom_emoji",
    "format_card_review",
    "normalize_obtain_type",
    "normalize_rarity",
    "reward_text",
    "select_next_setup_step",
    "validate_card_economy",
]
