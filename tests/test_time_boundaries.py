from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.application_models import AuctionRecord
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

    assert auction.start_time == datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
    assert auction.end_time == datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)


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

    assert command.start_time == datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
    assert command.end_time == datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
