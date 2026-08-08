from __future__ import annotations

from datetime import UTC, datetime

from webapi.auction_home import _serialize_auction


def test_serialize_auction_exposes_screen_fields_without_private_owner_data() -> None:
    payload = _serialize_auction(
        {
            "auction_id": 42,
            "card_id": 7,
            "card_name": "Chaos",
            "hero_name": "Set",
            "card_num": 15,
            "deck_id": 3,
            "deck_name": "Desert",
            "rarity": "gold",
            "story": "Red Nile",
            "quote": "private quote",
            "obtain_type": "diamonds",
            "obtain_amount": 80,
            "start_price": 800,
            "currency": "алмазы",
            "status": "active",
            "auction_kind": "standard",
            "message_id": 9308,
            "start_time": datetime(2026, 8, 8, 15, 0, tzinfo=UTC),
            "end_time": datetime(2026, 8, 8, 15, 30, 59, tzinfo=UTC),
            "comment": "private moderation note",
        },
        owners=[
            {
                "user_id": 123456789,
                "username": "seller",
                "full_name": "Seller Name",
                "is_trusted": False,
                "is_luxury": True,
            }
        ],
        current_bid=930,
        channel_username="@card_house",
    )

    assert payload["id"] == 42
    assert payload["current_bid"] == 930
    assert payload["display_price"] == 930
    assert payload["telegram_url"] == "https://t.me/card_house/9308"
    assert payload["seller"] == {
        "display_name": "Seller Name",
        "verified": False,
    }
    assert payload["card"] == {
        "id": 7,
        "name": "Chaos",
        "hero_name": "Set",
        "num": 15,
        "deck_id": 3,
        "deck_name": "Desert",
        "rarity": "gold",
        "story": "Red Nile",
        "quote": "private quote",
        "obtain_type": "diamonds",
        "obtain_amount": 80,
        "image_url": "/api/webapp/cards/7/image",
    }
    assert "user_id" not in payload["seller"]
    assert "balance" not in payload
    assert "comment" not in payload
