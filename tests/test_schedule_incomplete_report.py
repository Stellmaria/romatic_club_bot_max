# ruff: noqa: RUF001
from __future__ import annotations

from bot.handlers.admin.schedule_setup_restart import (
    _card_issues,
    _safe_text,
    _split_messages,
)


def _complete_card() -> dict[str, object]:
    return {
        "card_id": 277,
        "deck_id": 1,
        "card_name": "Карта",
        "hero_name": "Герой",
        "image_id": "telegram-image",
        "rarity": "bronze",
        "obtain_type": "diamonds",
        "obtain_amount": 20,
        "story": "История",
        "quote": "Цитата",
        "card_emoji_id": 123456789,
        "emoji_verified": True,
    }


def test_complete_card_has_no_reported_issues() -> None:
    assert _card_issues(_complete_card()) == []


def test_report_catches_missing_quote_emoji_and_invalid_reward() -> None:
    card = _complete_card()
    card.update(
        {
            "quote": "   ",
            "card_emoji_id": None,
            "emoji_verified": False,
            "obtain_amount": 0,
        }
    )

    assert _card_issues(card) == [
        "цитата",
        "мини-эмодзи",
        "экономика: Ожидалось 20 алмазов, записано 0",
    ]


def test_report_catches_unverified_existing_emoji() -> None:
    card = _complete_card()
    card["emoji_verified"] = False

    assert _card_issues(card) == ["мини-эмодзи не проверен"]


def test_report_text_is_html_escaped() -> None:
    assert _safe_text("<b>A&B</b>") == "&lt;b&gt;A&amp;B&lt;/b&gt;"


def test_long_report_is_split_below_telegram_limit() -> None:
    assert _split_messages("HEAD", ["1234", "5678"], limit=10) == [
        "HEAD\n1234",
        "5678",
    ]
