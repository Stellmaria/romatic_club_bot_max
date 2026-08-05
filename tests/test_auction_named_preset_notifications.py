from __future__ import annotations

from pathlib import Path

import pytest

from db.subscriptions import preset_keys_for_auction

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("lot_title", "expected_key"),
    [
        ("Золотой пропуск 1 месяц", "subscription_gold_1"),
        ("Золотая подписка 3 месяца", "subscription_gold_3"),
        ("Gold pass 6 months", "subscription_gold_6"),
        ("Премиум подписка 12 месяцев", "subscription_premium_12"),
        ("Друзья Плюс", "friends_plus"),
        ("Друзья+", "friends_plus"),
        ("Слоты прогресса", "progress_slots"),
        ("Кручения 10", "spins_10"),
        ("Spins 50", "spins_50"),
        ("Кручений 100", "spins_100"),
    ],
)
def test_named_subscription_preset_key_is_resolved(
    lot_title: str,
    expected_key: str,
) -> None:
    keys = preset_keys_for_auction(lot_title=lot_title)

    assert expected_key in keys


def test_auction_notifier_collects_generic_preset_subscribers() -> None:
    source = (ROOT / "bot" / "auction_notify.py").read_text(encoding="utf-8")

    assert "subscribers_for_auction_presets" in source
    assert source.count("await subscribers_for_auction_presets(") >= 2
    assert "uids_presets" in source
