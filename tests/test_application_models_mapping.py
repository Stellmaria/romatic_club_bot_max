from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bot.application_models import RecordMappingError, map_auction, optional_int
from bot.domain.auctions.enums import AuctionKind, Currency


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (7, 7),
        ("8", 8),
        (b"9", 9),
        (bytearray(b"10"), 10),
    ],
)
def test_optional_int_accepts_supported_persistence_values(
    value: object, expected: int | None
) -> None:
    assert optional_int({"value": value}, "value") == expected


@pytest.mark.parametrize("value", [True, "not-an-int", object()])
def test_optional_int_rejects_ambiguous_values(value: object) -> None:
    with pytest.raises(RecordMappingError, match="must be int or None"):
        optional_int({"value": value}, "value")


def test_map_auction_preserves_typed_instants_and_optional_ids() -> None:
    local_start = datetime(2026, 8, 3, 16, 30, tzinfo=timezone(timedelta(hours=3)))
    record = map_auction(
        {
            "auction_id": 4,
            "status": "scheduled",
            "auction_kind": AuctionKind.STANDARD.value,
            "currency": Currency.DIAMONDS.value,
            "start_price": 100,
            "card_id": "12",
            "message_id": 13,
            "start_time": local_start,
            "end_time": local_start + timedelta(minutes=30),
        }
    )

    assert record.card_id == 12
    assert record.message_id == 13
    assert record.start_time == datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    assert record.end_time == datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
