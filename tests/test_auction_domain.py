from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from bot.domain.auctions import (
    Auction,
    BidFormatError,
    BidStepError,
    BidTooLow,
    Currency,
    auction_bidding_closes_at,
    comparison_units,
    parse_bid_offer,
)
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
    assert (
        validate_bid_amount(
            amount=150,
            currency=Currency.DIAMONDS,
            start_price=100,
            current_max=140,
        )
        == 150
    )
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


def test_auction_accepts_entire_displayed_bidding_minute() -> None:
    displayed_bidding_minute = datetime(2026, 8, 1, 18, 30, tzinfo=timezone.utc)
    close_instant = displayed_bidding_minute + timedelta(minutes=1)
    auction = Auction(
        auction_id=1,
        status="active",
        currency=Currency.DIAMONDS,
        start_price=100,
        start_time=displayed_bidding_minute - timedelta(minutes=30),
        end_time=displayed_bidding_minute.replace(second=59),
    )

    assert auction_bidding_closes_at(auction.end_time) == close_instant
    assert auction.is_active_at(close_instant - timedelta(microseconds=1)) is True
    assert auction.is_active_at(close_instant) is False
    assert auction.has_ended_at(close_instant - timedelta(microseconds=1)) is False
    assert auction.has_ended_at(close_instant) is True


def test_finalization_claim_waits_until_next_minute_boundary() -> None:
    source = (ROOT / "bot/repositories/auctions.py").read_text(encoding="utf-8")
    assert "date_trunc('minute', end_time) + INTERVAL '1 minute' <= $1" in source
    assert "date_trunc('minute', end_time) <= $1" not in source


def test_mixed_currency_offer_requires_marker_and_uses_project_rate() -> None:
    accepted = (Currency.CUPS, Currency.DIAMONDS)
    with pytest.raises(BidFormatError):
        parse_bid_offer("12", accepted_currencies=accepted, fallback=Currency.CUPS)
    tea = parse_bid_offer("12 чай", accepted_currencies=accepted, fallback=Currency.CUPS)
    diamonds = parse_bid_offer("120 алмазов", accepted_currencies=accepted, fallback=Currency.CUPS)
    assert comparison_units(tea.amount, tea.currency) == 120
    assert comparison_units(diamonds.amount, diamonds.currency) == 120
