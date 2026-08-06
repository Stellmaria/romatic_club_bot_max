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
MAX_PREORDER_QUANTITY = 99


def normalize_preorder_items(items: Mapping[str, object] | None) -> dict[str, int]:
    """Return a stable, positive-only preorder composition."""

    source = items or {}
    normalized: dict[str, int] = {}
    for rarity in PREORDER_RARITIES:
        try:
            quantity = int(source.get(rarity, 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
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
    current = int(result.get(rarity, 0))
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


def build_preorder_title(
    *,
    deck_id: int,
    deck_name: str | None,
    items: Mapping[str, object] | None,
) -> str:
    """Build the canonical single-lot title stored in the existing auction schema."""

    composition = format_preorder_composition(items)
    if not composition:
        raise ValueError("preorder composition must contain at least one item")

    clean_name = " ".join(str(deck_name or "").split())
    deck_label = f"Колода №{int(deck_id)}"
    if clean_name:
        deck_label += f" «{clean_name}»"
    return f"Предзаказ: {deck_label}, {composition}"
