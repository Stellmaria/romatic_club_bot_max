from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from bot.services.schedule_setup import COMMON_ASSETS, expected_reward, validate_card_economy
from userbot.schedule_announcements import schedule_configuration_issues
from userbot.schedule_publication import render_schedule_announcement

ROOT = Path(__file__).resolve().parents[1]


def _entity_text(text, entity) -> str:
    encoded = text.encode("utf-16-le")
    start = entity.offset * 2
    end = (entity.offset + entity.length) * 2
    return encoded[start:end].decode("utf-16-le")


def test_card_economy_matrix_matches_rarity() -> None:
    assert expected_reward("bronze", "diamonds") == 20
    assert expected_reward("серебро", "tea") == 4  # noqa: RUF001 - Russian rarity alias
    assert expected_reward("gold", "diamonds") == 80
    assert expected_reward("эпик", "tea") == 12

    ok, _ = validate_card_economy({"rarity": "gold", "obtain_type": "tea", "obtain_amount": 8})
    assert ok is True

    ok, reason = validate_card_economy(
        {"rarity": "gold", "obtain_type": "diamonds", "obtain_amount": 8}
    )
    assert ok is False
    assert "Ожидалось 80" in reason


def test_setup_master_contains_every_special_lot_asset() -> None:
    keys = {asset.key for asset in COMMON_ASSETS}
    assert {
        "lot:any_bronze",
        "lot:any_silver",
        "lot:any_gold",
        "lot:any_diamond",
        "lot:any_card",
        "lot:any_deck",
        "service:friends_plus",
        "service:progress_slots",
        "service:subscription_gold",
        "service:subscription_premium",
        "service:spins_10",
        "service:spins_50",
        "service:spins_100",
        "service:deck_constructor",
        "resource:diamonds_for_tea",
        "resource:tea_for_diamonds",
    } <= keys


def test_enriched_schedule_template_uses_card_rarity_deck_and_rewards() -> None:
    rendered = render_schedule_announcement(
        date(2026, 8, 2),
        [
            {
                "auction_id": 1,
                "card_id": 101,
                "card_name": "Ава",
                "hero_name": "Ава",
                "start_time": datetime(2026, 8, 2, 8, 0, tzinfo=UTC),
                "rarity": "gold",
                "obtain_type": "diamonds",
                "obtain_amount": 80,
                "currency": "tea",
                "card_emoji_id": 1001,
                "card_emoji_verified": True,
                "resolved_deck_id": 2,
                "deck_emoji_id": 2002,
            },
            {
                "auction_id": 2,
                "card_name": "Вся 20 колода",
                "hero_name": "",
                "start_time": datetime(2026, 8, 2, 14, 0, tzinfo=UTC),
                "whole_deck": True,
                "resolved_deck_id": 20,
                "deck_emoji_id": 2020,
                "deck_diamonds": 380,
                "deck_tea": 10,
                "currency": "diamonds",
                "accepted_currencies": ["чай", "алмазы"],
                "auction_kind": "free",
            },
        ],
        {
            "header": 10,
            "rarity:gold": {"custom_emoji_id": 30},
            "currency:diamonds": {"custom_emoji_id": 40},
            "currency:tea": {"custom_emoji_id": 50},
            "whole_deck": {"custom_emoji_id": 60},
        },
    )

    assert "11:00" in rendered.text
    assert "Ава" in rendered.text
    assert "+80💎 (за чай)" in rendered.text
    assert "17:00 Вся 20 колода" in rendered.text
    assert "+380💎 +10☕ (за чай и алмазы) · свободный" in rendered.text
    assert [entity.document_id for entity in rendered.entities] == [
        10,
        10,
        1001,
        30,
        2002,
        40,
        60,
        2020,
        40,
        50,
    ]
    assert [_entity_text(rendered.text, entity) for entity in rendered.entities] == [
        "🦋",
        "🦋",
        "🎴",
        "🔹",
        "🗂",
        "💎",
        "🃏",
        "🗂",
        "💎",
        "☕",
    ]


def test_configuration_audit_blocks_unverified_card_and_missing_deck_emoji() -> None:
    issues = schedule_configuration_issues(
        [
            {
                "auction_id": 7,
                "card_id": 12,
                "resolved_deck_id": 3,
                "card_emoji_id": 100,
                "card_emoji_verified": False,
                "deck_emoji_id": None,
                "rarity": "silver",
                "obtain_type": "diamonds",
                "obtain_amount": 40,
            }
        ],
        {
            "rarity:silver": {"custom_emoji_id": 1},
            "currency:diamonds": {"custom_emoji_id": 2},
        },
    )

    assert any("колода 3" in issue for issue in issues)
    assert any("не подтверждены" in issue for issue in issues)


def test_source_contract_has_preview_approval_and_topic_target() -> None:
    announcements = (ROOT / "userbot" / "schedule_announcements.py").read_text(encoding="utf-8")
    handler = (ROOT / "bot" / "handlers" / "admin" / "schedule_setup.py").read_text(
        encoding="utf-8"
    )
    migration = (ROOT / "db" / "migrations" / "011_schedule_setup_master.sql").read_text(
        encoding="utf-8"
    )

    assert "_PREVIEW_HOUR = 22" in announcements
    assert "_PREVIEW_MINUTE = 30" in announcements
    assert 'review.get("status") == "approved"' in announcements
    assert "telegram_client.get_messages" in announcements
    assert "approved_preview.message" in announcements
    assert "Button.inline" in announcements
    assert "message.message_thread_id" in handler
    assert "Всё верно, следующая" in handler
    assert "schedule_publication_reviews" in migration
    assert "schedule_setup_sessions" in migration
