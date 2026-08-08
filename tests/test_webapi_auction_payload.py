from __future__ import annotations

from datetime import UTC, datetime

from webapi.app import _serialize_auction


def test_serialize_auction_exposes_only_public_fields() -> None:
    payload = _serialize_auction(
        {
            "auction_id": 42,
            "card_id": 7,
            "card_name": "King",
            "hero_name": "Vlad",
            "start_price": 120,
            "currency": "tea",
            "status": "scheduled",
            "start_time": datetime(2026, 8, 8, 15, 0, tzinfo=UTC),
            "end_time": datetime(2026, 8, 8, 15, 30, 59, tzinfo=UTC),
            "owner_id": 123456789,
            "comment": "private moderation note",
        }
    )

    assert payload == {
        "id": 42,
        "card_id": 7,
        "card_name": "King",
        "hero_name": "Vlad",
        "start_price": 120,
        "currency": "tea",
        "status": "scheduled",
        "start_time": "2026-08-08T15:00:00Z",
        "end_time": "2026-08-08T15:30:59Z",
    }
