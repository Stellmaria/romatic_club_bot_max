from __future__ import annotations

import pytest

from bot.domain.auctions import Currency, normalize_currency_choices
from bot.domain.schedule_lots import (
    SPECIAL_SCHEDULE_ASSETS,
    schedule_lot_display_name,
    special_schedule_asset_key,
)
from userbot.schedule_announcements import schedule_configuration_issues


@pytest.mark.parametrize(
    ("lot", "expected_key"),
    [
        ({"card_name": "Любая бронзовая"}, "lot:any_bronze"),
        ({"card_name": "Любая серебряная"}, "lot:any_silver"),
        ({"card_name": "Любая золотая"}, "lot:any_gold"),
        ({"card_name": "Любая алмазная"}, "lot:any_diamond"),
        ({"card_name": "Любая карта"}, "lot:any_card"),
        ({"card_name": "Любая колода"}, "lot:any_deck"),
        ({"card_name": "Друзья+"}, "service:friends_plus"),
        ({"card_name": "Слоты прогресса"}, "service:progress_slots"),
        ({"card_name": "Золотой пропуск (12 месяцев)"}, "service:subscription_gold"),
        ({"card_name": "Премиум пропуск (1 месяц)"}, "service:subscription_premium"),
        ({"card_name": "Кручения (10 шт.)"}, "service:spins_10"),
        ({"card_name": "Кручения (50 шт.)"}, "service:spins_50"),
        ({"card_name": "Кручения (100 шт.)"}, "service:spins_100"),
        ({"card_name": "Колода-конструктор"}, "service:deck_constructor"),
        ({"card_name": "Ресурсная карта (💎 за 🍵)"}, "resource:diamonds_for_tea"),
        ({"card_name": "Ресурсная карта (🍵 за 💎)"}, "resource:tea_for_diamonds"),
        ({"service": "friends_plus", "card_name": "Сервис"}, "service:friends_plus"),
        ({"service": "spins", "spins_qty": 100, "card_name": "Кручения"}, "service:spins_100"),
    ],
)
def test_special_schedule_lot_classification(lot: dict[str, object], expected_key: str) -> None:
    assert special_schedule_asset_key(lot) == expected_key


def test_special_asset_inventory_has_unique_keys() -> None:
    keys = [asset.key for asset in SPECIAL_SCHEDULE_ASSETS]
    assert len(keys) == len(set(keys)) == 16


def test_special_lot_uses_card_name_instead_of_generic_hero() -> None:
    lot = {"hero_name": "Лот от игрока", "card_name": "Любая золотая"}
    assert schedule_lot_display_name(lot) == "Любая золотая"


def test_special_lot_audit_requires_its_asset_not_catalog_card() -> None:
    issues = schedule_configuration_issues(
        [
            {
                "auction_id": 77,
                "card_id": None,
                "card_name": "Друзья+",
                "start_price": 10,
                "currency": "чашки",
            }
        ],
        {"currency:tea": {"custom_emoji_id": 1}},
    )

    assert any("Друзья+" in issue for issue in issues)
    assert not any("карточка не найдена" in issue for issue in issues)
    assert not any("не определена колода" in issue for issue in issues)


def test_currency_choices_support_legacy_combined_values() -> None:
    assert normalize_currency_choices("чашки_алмазы") == (
        Currency.CUPS,
        Currency.DIAMONDS,
    )
    assert normalize_currency_choices(None, fallback="чай и/или алмазы") == (
        Currency.CUPS,
        Currency.DIAMONDS,
    )
