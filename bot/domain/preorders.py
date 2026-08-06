"""Domain rules for composing future-deck preorder lots."""

from __future__ import annotations

from collections.abc import Mapping

PREORDER_RARITIES: tuple[str, ...] = ("bronze", "silver", "gold", "epic")
PREORDER_RARITY_LABELS: dict[str, str] = {
    "bronze": "Бронза",
    "silver": "Серебро",
    "gold": "Золото",
    "epic": "Эпик",
}
PREORDER_MODE_ITEMS = "items"
PREORDER_MODE_WHOLE_DECK = "whole_deck"
PREORDER_MODES: tuple[str, ...] = (
    PREORDER_MODE_ITEMS,
    PREORDER_MODE_WHOLE_DECK,
)
MAX_PREORDER_QUANTITY = 99
PREORDER_MIN_START_PRICE = 1_000
PREORDER_MAX_START_PRICE = 6_000


def _quantity(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def normalize_preorder_mode(value: object) -> str:
    """Return a supported preorder mode, preserving old drafts as item mode."""

    normalized = str(value or "").strip().lower()
    if normalized == PREORDER_MODE_WHOLE_DECK:
        return PREORDER_MODE_WHOLE_DECK
    return PREORDER_MODE_ITEMS


def normalize_preorder_items(items: Mapping[str, object] | None) -> dict[str, int]:
    """Return a stable, positive-only preorder composition."""

    source = items or {}
    normalized: dict[str, int] = {}
    for rarity in PREORDER_RARITIES:
        quantity = _quantity(source.get(rarity, 0))
        if quantity > 0:
            normalized[rarity] = min(quantity, MAX_PREORDER_QUANTITY)
    return normalized


def change_preorder_quantity(
    items: Mapping[str, object] | None,
    rarity: str,
    delta: int,
) -> dict[str, int]:
    """Increment or decrement one allowed rarity without leaving valid bounds."""

    if rarity not in PREORDER_RARITIES:
        raise ValueError(f"unsupported preorder rarity: {rarity}")
    if delta not in {-1, 1}:
        raise ValueError("preorder quantity delta must be -1 or 1")

    result = normalize_preorder_items(items)
    current = result.get(rarity, 0)
    updated = max(0, min(MAX_PREORDER_QUANTITY, current + delta))
    if updated:
        result[rarity] = updated
    else:
        result.pop(rarity, None)
    return {key: result[key] for key in PREORDER_RARITIES if key in result}


def preorder_total(items: Mapping[str, object] | None) -> int:
    return sum(normalize_preorder_items(items).values())


def format_preorder_composition(items: Mapping[str, object] | None) -> str:
    normalized = normalize_preorder_items(items)
    return " + ".join(
        f"{normalized[rarity]}× {PREORDER_RARITY_LABELS[rarity].lower()}"
        for rarity in PREORDER_RARITIES
        if rarity in normalized
    )


def validate_preorder_selection(
    *,
    mode: object,
    items: Mapping[str, object] | None,
) -> tuple[str, dict[str, int]]:
    """Validate that a preorder uses exactly one composition mode."""

    normalized_mode = normalize_preorder_mode(mode)
    normalized_items = normalize_preorder_items(items)

    if normalized_mode == PREORDER_MODE_WHOLE_DECK:
        if normalized_items:
            raise ValueError("whole-deck preorder cannot contain separate items")
        return normalized_mode, {}

    if not normalized_items:
        raise ValueError("item preorder must contain at least one item")
    return normalized_mode, normalized_items


def validate_preorder_start_price(value: object) -> int:
    """Validate the single start-price range used by every preorder composition."""

    if isinstance(value, bool):
        raise ValueError("preorder start price must be an integer")
    if isinstance(value, int):
        price = value
    elif isinstance(value, str):
        try:
            price = int(value)
        except ValueError as exc:
            raise ValueError("preorder start price must be an integer") from exc
    else:
        raise ValueError("preorder start price must be an integer")

    if not PREORDER_MIN_START_PRICE <= price <= PREORDER_MAX_START_PRICE:
        raise ValueError(
            "preorder start price must be between "
            f"{PREORDER_MIN_START_PRICE} and {PREORDER_MAX_START_PRICE}"
        )
    return price


def build_preorder_title(
    *,
    deck_id: int,
    deck_name: str | None,
    items: Mapping[str, object] | None,
    mode: object = PREORDER_MODE_ITEMS,
) -> str:
    """Build the canonical single-lot title stored in the existing auction schema."""

    normalized_mode, normalized_items = validate_preorder_selection(
        mode=mode,
        items=items,
    )
    if normalized_mode == PREORDER_MODE_WHOLE_DECK:
        composition = "целая колода"
    else:
        composition = format_preorder_composition(normalized_items)

    clean_name = " ".join(str(deck_name or "").split())
    deck_label = f"Колода №{int(deck_id)}"
    if clean_name:
        deck_label += f" «{clean_name}»"
    return f"Предзаказ: {deck_label}, {composition}"
