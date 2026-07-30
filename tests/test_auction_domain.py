from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.domain.auctions import Auction, BidFormatError, BidStepError, BidTooLow, Currency
from bot.domain.auctions.rules import minimum_next_bid, parse_bid_amount, validate_bid_amount


def test_currency_aliases_share_one_policy() -> None:
    assert Currency.from_raw("чай") is Currency.CUPS
    assert Currency.from_raw("cups") is Currency.CUPS
    assert Currency.from_raw("🪙") is Currency.TREASURES
    assert Currency.TREASURES.bid_step == 10
    assert Currency.DIAMONDS.autobid_step == 90


def test_bid_parser_is_shared_and_supports_thousands_suffix() -> None:
    assert parse_bid_amount("1 250") == 1250
    assert parse_bid_amount("10к") == 10_000
    assert parse_bid_amount("2_500") == 2500
    with pytest.raises(BidFormatError):
        parse_bid_amount("сто")


def test_bid_minimum_and_step_are_validated_from_start_price() -> None:
    assert minimum_next_bid(start_price=100, current_max=None, step=10) == 100
    assert minimum_next_bid(start_price=100, current_max=140, step=10) == 150
    assert validate_bid_amount(
        amount=150,
        currency=Currency.DIAMONDS,
        start_price=100,
        current_max=140,
    ) == 150
    with pytest.raises(BidTooLow):
        validate_bid_amount(
            amount=140,
            currency=Currency.DIAMONDS,
            start_price=100,
            current_max=140,
        )
    with pytest.raises(BidStepError):
        validate_bid_amount(
            amount=155,
            currency=Currency.DIAMONDS,
            start_price=100,
            current_max=140,
        )


def test_auction_activity_handles_time_boundaries() -> None:
    now = datetime.now()
    active = Auction(
        auction_id=1,
        status="active",
        currency=Currency.DIAMONDS,
        start_price=100,
        start_time=now - timedelta(seconds=1),
        end_time=now + timedelta(seconds=1),
    )
    ended = Auction(
        auction_id=2,
        status="active",
        currency=Currency.CUPS,
        start_price=2,
        start_time=now - timedelta(minutes=2),
        end_time=now,
    )
    assert active.is_active_at(now) is True
    assert ended.is_active_at(now) is False
    assert ended.has_ended_at(now) is True
