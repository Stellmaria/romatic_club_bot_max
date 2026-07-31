from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon.tl.types import MessageEntityCustomEmoji

from userbot.schedule_announcements import (
    announcement_target_date,
    extract_custom_emoji_assignments,
    load_announcement_state,
    missing_required_emoji_keys,
    render_schedule_announcement,
    save_announcement_state,
    utf16_length,
)


def _entity_text(text: str, entity: MessageEntityCustomEmoji) -> str:
    encoded = text.encode("utf-16-le")
    start = entity.offset * 2
    end = (entity.offset + entity.length) * 2
    return encoded[start:end].decode("utf-16-le")


def test_extract_custom_emoji_assignments_uses_utf16_offsets() -> None:
    text = "header = 🦋\nhero:Сонхва = 🧑"
    header_prefix = "header = "
    hero_prefix = "header = 🦋\nhero:Сонхва = "
    message = SimpleNamespace(
        message=text,
        entities=[
            MessageEntityCustomEmoji(
                offset=utf16_length(header_prefix),
                length=utf16_length("🦋"),
                document_id=101,
            ),
            MessageEntityCustomEmoji(
                offset=utf16_length(hero_prefix),
                length=utf16_length("🧑"),
                document_id=202,
            ),
        ],
    )

    assert extract_custom_emoji_assignments(message) == {
        "header": 101,
        "hero:сонхва": 202,
    }


def test_render_schedule_announcement_adds_custom_entities() -> None:
    rendered = render_schedule_announcement(
        date(2026, 8, 1),
        [
            {
                "auction_id": 2,
                "hero_name": "Сонхва",
                "card_name": "Сонхва",
                "start_time": datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc),
                "start_price": 20,
                "currency": "diamonds",
                "comment": "за чай @hidden",
            },
            {
                "auction_id": 1,
                "hero_name": "Граф",
                "card_name": "Граф",
                "start_time": datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc),
                "start_price": 2,
                "currency": "tea",
                "comment": "-",
            },
        ],
        {
            "header": 11,
            "card": 22,
            "hero:сонхва": 33,
            "diamond": 44,
            "tea": 55,
        },
    )

    assert rendered.text.startswith("🦋 АНОНС НА 1 АВГУСТА 🦋")
    assert "🎴 11:30 Граф +2☕" in rendered.text
    assert "🎴 12:30 Сонхва +20💎 (за чай)" in rendered.text
    assert "@hidden" not in rendered.text
    assert [entity.document_id for entity in rendered.entities] == [11, 11, 22, 55, 33, 44]
    assert [_entity_text(rendered.text, entity) for entity in rendered.entities] == [
        "🦋",
        "🦋",
        "🎴",
        "☕",
        "🎴",
        "💎",
    ]


def test_announcement_target_date_only_after_configured_time() -> None:
    before = datetime(2026, 7, 31, 22, 59, tzinfo=timezone.utc)
    # 22:59 UTC is already 01:59 in Moscow on the next calendar day.
    assert announcement_target_date(before, hour=23, minute=0) is None

    at_time_moscow = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    assert announcement_target_date(at_time_moscow, hour=23, minute=0) == date(2026, 8, 1)


def test_state_round_trip_and_required_keys(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    state = {
        "emoji_ids": {"header": 1, "card": 2, "diamond": 3, "tea": 4},
        "published": {"2026-08-01": {"message_id": 99}},
    }
    save_announcement_state(path, state)

    assert load_announcement_state(path) == state
    assert missing_required_emoji_keys(state["emoji_ids"]) == ()
    assert missing_required_emoji_keys({"header": 1}) == ("card", "diamond", "tea")
