from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bot.application_models import (
    AuctionRecord,
    ModerationStatus,
    OutboxRecord,
    ScheduleLotRecord,
    UidVerificationRecord,
)
from bot.domain.auctions import Auction, AuctionKind, Currency
from bot.use_cases.auction_moderation import ScheduleAuctionCommand


def test_application_record_rejects_naive_persistence_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AuctionRecord(
            auction_id=1,
            status="scheduled",
            auction_kind=AuctionKind.STANDARD,
            currency=Currency.DIAMONDS,
            start_price=100,
            start_time=datetime(2026, 8, 3, 16, 30),
        )


def test_domain_model_normalizes_aware_timestamp_to_utc() -> None:
    source = datetime(2026, 8, 3, 16, 30, tzinfo=timezone(timedelta(hours=3)))

    auction = Auction(
        auction_id=1,
        status="scheduled",
        currency=Currency.DIAMONDS,
        start_price=100,
        start_time=source,
        end_time=source + timedelta(minutes=30),
    )

    assert auction.start_time == datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    assert auction.end_time == datetime(2026, 8, 3, 14, 0, tzinfo=UTC)


def test_schedule_command_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ScheduleAuctionCommand(
            auction_id=1,
            start_time=datetime(2026, 8, 3, 16, 30),
            end_time=datetime(2026, 8, 3, 17, 0),
        )


def test_schedule_command_normalizes_aware_values_to_utc() -> None:
    start = datetime(2026, 8, 3, 16, 30, tzinfo=timezone(timedelta(hours=3)))

    command = ScheduleAuctionCommand(
        auction_id=1,
        start_time=start,
        end_time=start + timedelta(minutes=30),
    )

    assert command.start_time == datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    assert command.end_time == datetime(2026, 8, 3, 14, 0, tzinfo=UTC)


def test_all_persisted_record_timestamps_normalize_to_utc() -> None:
    source = datetime(2026, 8, 3, 16, 30, tzinfo=timezone(timedelta(hours=3)))

    auction = AuctionRecord(
        auction_id=2,
        status="scheduled",
        auction_kind=AuctionKind.STANDARD,
        currency=Currency.DIAMONDS,
        start_price=100,
        end_time=source,
    )
    lot = ScheduleLotRecord(
        auction_id=2,
        target_date=source.date(),
        start_time=source,
        end_time=source + timedelta(minutes=30),
        card_name="Test card",
        auction_kind=AuctionKind.STANDARD,
    )
    verification = UidVerificationRecord(
        request_id=3,
        user_id=4,
        status=ModerationStatus.PENDING,
        uid_hash="hash",
        created_at=source,
    )
    outbox = OutboxRecord(
        event_id=5,
        event_type="test",
        aggregate_id="2",
        payload_json="{}",
        created_at=source,
        attempts=0,
    )

    expected = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    assert auction.end_time == expected
    assert lot.start_time == expected
    assert lot.end_time == expected + timedelta(minutes=30)
    assert verification.created_at == expected
    assert outbox.created_at == expected
