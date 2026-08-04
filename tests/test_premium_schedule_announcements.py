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
    save_announcement_state,
    utf16_length,
)
from userbot.schedule_publication import render_schedule_announcement


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


def test_render_schedule_announcement_applies_public_currency_and_kind_policy() -> None:
    rendered = render_schedule_announcement(
        date(2026, 8, 1),
        [
            {
                "auction_id": 4,
                "hero_name": "Реверс",
                "card_name": "Реверс",
                "start_time": datetime(2026, 8, 1, 11, 30, tzinfo=timezone.utc),
                "obtain_amount": 40,
                "obtain_type": "diamonds",
                "currency": "чашки",
                "accepted_currencies": ["чашки", "алмазы"],
                "auction_kind": "reverse",
            },
            {
                "auction_id": 2,
                "hero_name": "Чай",
                "card_name": "Чай",
                "start_time": datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc),
                "obtain_amount": 2,
                "obtain_type": "tea",
                "currency": "чай",
                "auction_kind": "fast",
            },
            {
                "auction_id": 1,
                "hero_name": "Алмазы",
                "card_name": "Алмазы",
                "start_time": datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc),
                "obtain_amount": 20,
                "obtain_type": "diamonds",
                "currency": "алмазы",
                "auction_kind": "standard",
            },
            {
                "auction_id": 3,
                "hero_name": "Свободный",
                "card_name": "Свободный",
                "start_time": datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc),
                "obtain_amount": 20,
                "obtain_type": "diamonds",
                "currency": "алмазы",
                "accepted_currencies": ["чай", "алмазы"],
                "auction_kind": "free",
            },
        ],
        {
            "header": 11,
            "card": 22,
            "diamond": 44,
            "tea": 55,
        },
    )

    assert rendered.text.startswith("🦋 АНОНС НА 1 АВГУСТА 🦋")
    assert "🎴 11:30 Алмазы +20💎" in rendered.text
    assert "за алмазы" not in rendered.text
    assert "стандарт" not in rendered.text.casefold()
    assert "🎴 12:30 Чай +2☕ (за чай) · быстрый" in rendered.text
    assert "🎴 13:30 Свободный +20💎 (за чай и алмазы) · свободный" in rendered.text
    assert "🎴 14:30 Реверс +40💎 (за чай и алмазы) · обратный" in rendered.text
    assert [entity.document_id for entity in rendered.entities[:2]] == [11, 11]
    assert all(
        _entity_text(rendered.text, entity) in {"🦋", "🎴", "💎", "☕"}
        for entity in rendered.entities
    )


def test_render_schedule_announcement_uses_special_lot_assets() -> None:
    rendered = render_schedule_announcement(
        date(2026, 8, 4),
        [
            {
                "auction_id": 11,
                "hero_name": "Лот от игрока",
                "card_name": "Любая золотая",
                "start_time": datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc),
                "start_price": 800,
                "currency": "алмазы",
            },
            {
                "auction_id": 12,
                "card_name": "Друзья+",
                "start_time": datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
                "start_price": 10,
                "currency": "чашки",
            },
            {
                "auction_id": 13,
                "card_name": "Премиум пропуск (6 месяцев)",
                "start_time": datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
                "start_price": 1480,
                "currency": "алмазы",
            },
            {
                "auction_id": 14,
                "card_name": "Кручения (50 шт.)",
                "start_time": datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc),
                "start_price": 14,
                "currency": "чашки",
            },
        ],
        {
            "header": 1,
            "lot:any_gold": 101,
            "service:friends_plus": 102,
            "service:subscription_premium": 103,
            "service:spins_50": 104,
            "currency:diamonds": 201,
            "currency:tea": 202,
        },
    )

    assert "Любая золотая" in rendered.text
    assert "Лот от игрока" not in rendered.text
    assert "Друзья+" in rendered.text
    assert "Премиум пропуск (6 месяцев)" in rendered.text
    assert "Кручения (50 шт.)" in rendered.text
    entities = {
        (entity.document_id, _entity_text(rendered.text, entity)) for entity in rendered.entities
    }
    assert (101, "🥇") in entities
    assert (102, "👥") in entities
    assert (103, "💎") in entities
    assert (104, "🎰") in entities


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
