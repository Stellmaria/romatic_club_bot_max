from __future__ import annotations

from pathlib import Path

import pytest

from bot.domain.preorders import (
    MAX_PREORDER_QUANTITY,
    PREORDER_MODE_ITEMS,
    PREORDER_MODE_WHOLE_DECK,
    build_preorder_title,
    change_preorder_quantity,
    format_preorder_composition,
    normalize_preorder_items,
    normalize_preorder_mode,
    preorder_total,
    validate_preorder_selection,
)

ROOT = Path(__file__).resolve().parents[1]


def test_preorder_supports_mixed_rarity_quantities() -> None:
    items = {}
    items = change_preorder_quantity(items, "bronze", 1)
    items = change_preorder_quantity(items, "bronze", 1)
    items = change_preorder_quantity(items, "gold", 1)

    assert items == {"bronze": 2, "gold": 1}
    assert preorder_total(items) == 3
    assert format_preorder_composition(items) == "2× бронза + 1× золото"


def test_preorder_uses_epic_label_and_stable_order() -> None:
    title = build_preorder_title(
        deck_id=31,
        deck_name="Новая история",
        items={"epic": 1, "silver": 2, "bronze": 1},
    )

    assert title == (
        "Предзаказ: Колода №31 «Новая история», "
        "1× бронза + 2× серебро + 1× эпик"
    )


def test_preorder_supports_whole_deck_as_an_alternative_mode() -> None:
    mode, items = validate_preorder_selection(
        mode=PREORDER_MODE_WHOLE_DECK,
        items={},
    )

    assert mode == PREORDER_MODE_WHOLE_DECK
    assert items == {}
    assert (
        build_preorder_title(
            deck_id=29,
            deck_name="Будущая история",
            mode=mode,
            items=items,
        )
        == "Предзаказ: Колода №29 «Будущая история», целая колода"
    )


def test_whole_deck_mode_rejects_separate_rarity_items() -> None:
    with pytest.raises(ValueError, match="cannot contain separate items"):
        validate_preorder_selection(
            mode=PREORDER_MODE_WHOLE_DECK,
            items={"bronze": 1},
        )


def test_old_preorder_drafts_default_to_items_mode() -> None:
    assert normalize_preorder_mode(None) == PREORDER_MODE_ITEMS
    assert normalize_preorder_mode("unknown") == PREORDER_MODE_ITEMS
    assert normalize_preorder_mode(PREORDER_MODE_WHOLE_DECK) == PREORDER_MODE_WHOLE_DECK


def test_preorder_normalization_drops_invalid_and_zero_values() -> None:
    assert normalize_preorder_items(
        {
            "bronze": "2",
            "silver": 0,
            "gold": "invalid",
            "epic": -3,
            "diamond": 10,
        }
    ) == {"bronze": 2}


def test_preorder_quantity_is_bounded() -> None:
    items = {"bronze": MAX_PREORDER_QUANTITY}
    assert change_preorder_quantity(items, "bronze", 1) == items
    assert change_preorder_quantity({"bronze": 1}, "bronze", -1) == {}


@pytest.mark.parametrize("rarity", ["diamond", "any", "legendary"])
def test_preorder_rejects_unsupported_rarities(rarity: str) -> None:
    with pytest.raises(ValueError):
        change_preorder_quantity({}, rarity, 1)


def test_repository_filters_and_revalidates_empty_decks() -> None:
    source = (ROOT / "bot/repositories/auction_submission.py").read_text(
        encoding="utf-8"
    )

    assert "async def future_empty_decks" in source
    assert "async def future_empty_deck" in source
    assert source.count("NOT EXISTS") >= 2
    assert "FROM public.cards c" in source
    assert "WHERE c.deck_id = d.id" in source


def test_preorder_router_has_priority_over_legacy_submission() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")

    assert "router.include_routers(preorder.router, submission.router" in source


def test_preorder_whole_deck_requires_explicit_clear_confirmation() -> None:
    source = (ROOT / "bot/handlers/auction/preorder.py").read_text(encoding="utf-8")

    assert 'callback_data="preorder:whole"' in source
    assert 'callback_data="preorder:whole:confirm"' in source
    assert 'callback_data="preorder:whole:cancel"' in source
    assert "При переходе на целую колоду этот состав будет очищен" in source
