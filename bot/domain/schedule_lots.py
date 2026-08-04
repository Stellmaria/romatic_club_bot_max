"""Canonical presentation rules for non-catalogue schedule lots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ScheduleAssetSpec:
    key: str
    label: str
    fallback: str


SPECIAL_SCHEDULE_ASSETS: tuple[ScheduleAssetSpec, ...] = (
    ScheduleAssetSpec("lot:any_bronze", "лота «Любая бронзовая»", "🥉"),
    ScheduleAssetSpec("lot:any_silver", "лота «Любая серебряная»", "🥈"),
    ScheduleAssetSpec("lot:any_gold", "лота «Любая золотая»", "🥇"),
    ScheduleAssetSpec("lot:any_diamond", "лота «Любая алмазная»", "💠"),
    ScheduleAssetSpec("lot:any_card", "лота «Любая карта»", "🎴"),
    ScheduleAssetSpec("lot:any_deck", "лота «Любая колода»", "🃏"),
    ScheduleAssetSpec("service:friends_plus", "лота «Друзья+»", "👥"),
    ScheduleAssetSpec("service:progress_slots", "лота «Слоты прогресса»", "📈"),
    ScheduleAssetSpec("service:subscription_gold", "лота «Золотой пропуск»", "🥇"),
    ScheduleAssetSpec("service:subscription_premium", "лота «Премиум пропуск»", "💎"),
    ScheduleAssetSpec("service:spins_10", "лота «10 кручений»", "🎰"),
    ScheduleAssetSpec("service:spins_50", "лота «50 кручений»", "🎰"),
    ScheduleAssetSpec("service:spins_100", "лота «100 кручений»", "🎰"),
    ScheduleAssetSpec("service:deck_constructor", "лота «Колода-конструктор»", "🧩"),
    ScheduleAssetSpec("resource:diamonds_for_tea", "лота «Алмазы за чай»", "💎"),
    ScheduleAssetSpec("resource:tea_for_diamonds", "лота «Чай за алмазы»", "☕"),
)
SPECIAL_SCHEDULE_ASSET_BY_KEY = {spec.key: spec for spec in SPECIAL_SCHEDULE_ASSETS}

_SPINS_RE = re.compile(r"(?:кручени[яй]|spins?)\D*(100|50|10)", re.IGNORECASE)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _title(lot: Mapping[str, Any]) -> str:
    return " ".join(str(lot.get("card_name") or "").split()).strip()


def special_schedule_asset_key(lot: Mapping[str, Any]) -> str | None:  # noqa: C901
    """Return the configured Premium-emoji key for a special schedule lot.

    Current auction rows persist the human-readable title but not every setup
    field used by the submission FSM.  The classifier therefore accepts both
    structured fields (for new code) and stable historical titles (for rows
    already stored in PostgreSQL).
    """

    title = _normalized(_title(lot) or lot.get("hero_name"))
    service = _normalized(lot.get("service"))

    if service == "friends_plus" or title == "друзья+" or title.startswith("друзья плюс"):
        return "service:friends_plus"
    if service == "progress_slots" or title.startswith("слоты прогресса"):
        return "service:progress_slots"

    if service == "subscription_gold" or title.startswith("золотой пропуск"):
        return "service:subscription_gold"
    if service == "subscription_premium" or title.startswith("премиум пропуск"):
        return "service:subscription_premium"

    spins_match = _SPINS_RE.search(title)
    if service == "spins" and not spins_match:
        try:
            quantity = int(lot.get("spins_qty") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity in {10, 50, 100}:
            return f"service:spins_{quantity}"
    if spins_match:
        return f"service:spins_{spins_match.group(1)}"

    if service == "deck_constructor" or title.startswith("колода-конструктор"):
        return "service:deck_constructor"

    if title.startswith("любая бронз") or title.startswith("любое бронз"):
        return "lot:any_bronze"
    if title.startswith("любая серебр") or title.startswith("любое серебр"):
        return "lot:any_silver"
    if title.startswith("любая золот") or title.startswith("любое золот"):
        return "lot:any_gold"
    if title.startswith("любая алмаз") or title.startswith("любое алмаз"):
        return "lot:any_diamond"
    if title.startswith("любая карта") or title.startswith("любой карт"):
        return "lot:any_card"
    if title.startswith("любая колода") or title.startswith("любой колод"):
        return "lot:any_deck"

    compact_title = title.replace(" ", "")
    if "💎за🍵" in compact_title or "алмазы за чай" in title or "diamonds for tea" in title:
        return "resource:diamonds_for_tea"
    if (
        "🍵за💎" in compact_title
        or "☕за💎" in compact_title
        or "чай за алмазы" in title
        or "tea for diamonds" in title
    ):
        return "resource:tea_for_diamonds"
    return None


def special_schedule_asset(lot: Mapping[str, Any]) -> ScheduleAssetSpec | None:
    key = special_schedule_asset_key(lot)
    return SPECIAL_SCHEDULE_ASSET_BY_KEY.get(key) if key else None


def schedule_lot_display_name(lot: Mapping[str, Any]) -> str:
    """Keep special lot names instead of the generic ``Лот от игрока`` hero."""

    card_name = _title(lot)
    if card_name and special_schedule_asset_key(lot):
        return card_name
    hero_name = " ".join(str(lot.get("hero_name") or "").split()).strip()
    return hero_name or card_name or "Без имени"


__all__ = [
    "SPECIAL_SCHEDULE_ASSET_BY_KEY",
    "SPECIAL_SCHEDULE_ASSETS",
    "ScheduleAssetSpec",
    "schedule_lot_display_name",
    "special_schedule_asset",
    "special_schedule_asset_key",
]
