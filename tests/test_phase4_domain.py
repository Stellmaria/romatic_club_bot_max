from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bot.domain.auctions import (
    AuctionAccessDenied,
    AuctionKind,
    AuctionKindNotBiddable,
    BidTooHigh,
    Currency,
    InvalidAuctionTransition,
)
from bot.domain.auctions.rules import (
    assert_kind_access,
    assert_status_transition,
    validate_bid_for_kind,
)

ROOT = Path(__file__).resolve().parents[1]


def test_auction_kind_access_policy_is_canonical() -> None:
    assert AuctionKind.STANDARD.minimum_luxury_level == 0
    assert AuctionKind.REVERSE.minimum_luxury_level == 1
    assert AuctionKind.FAST.minimum_luxury_level == 2
    assert AuctionKind.FREE.minimum_luxury_level == 2
    assert AuctionKind.BLACK.minimum_luxury_level == 2
    assert AuctionKind.EXCHANGE.minimum_luxury_level == 0

    assert_kind_access(AuctionKind.REVERSE, 1)
    with pytest.raises(AuctionAccessDenied):
        assert_kind_access(AuctionKind.FREE, 1)


def test_reverse_auction_uses_starting_ceiling_and_descends() -> None:
    assert validate_bid_for_kind(
        amount=5000,
        currency=Currency.DIAMONDS,
        start_price=5000,
        current_best=None,
        auction_kind=AuctionKind.REVERSE,
    ) == 5000
    with pytest.raises(BidTooHigh):
        validate_bid_for_kind(
            amount=5010,
            currency=Currency.DIAMONDS,
            start_price=5000,
            current_best=None,
            auction_kind=AuctionKind.REVERSE,
        )
    assert validate_bid_for_kind(
        amount=4990,
        currency=Currency.DIAMONDS,
        start_price=5000,
        current_best=5000,
        auction_kind=AuctionKind.REVERSE,
    ) == 4990
    with pytest.raises(BidTooHigh):
        validate_bid_for_kind(
            amount=5000,
            currency=Currency.DIAMONDS,
            start_price=5000,
            current_best=5000,
            auction_kind=AuctionKind.REVERSE,
        )


def test_legacy_reverse_without_ceiling_accepts_only_its_first_bid_compatibly() -> None:
    assert validate_bid_for_kind(
        amount=5000,
        currency=Currency.DIAMONDS,
        start_price=0,
        current_best=None,
        auction_kind=AuctionKind.REVERSE,
    ) == 5000
    with pytest.raises(BidTooHigh):
        validate_bid_for_kind(
            amount=5000,
            currency=Currency.DIAMONDS,
            start_price=0,
            current_best=5000,
            auction_kind=AuctionKind.REVERSE,
        )


def test_free_and_exchange_do_not_enter_numeric_bid_pipeline() -> None:
    for kind in (AuctionKind.FREE, AuctionKind.EXCHANGE):
        with pytest.raises(AuctionKindNotBiddable):
            validate_bid_for_kind(
                amount=100,
                currency=Currency.DIAMONDS,
                start_price=100,
                current_best=None,
                auction_kind=kind,
            )


def test_workflow_transition_policy_rejects_shortcuts() -> None:
    assert_status_transition("pending", "scheduled")
    assert_status_transition("scheduled", "publishing")
    assert_status_transition("publishing", "active")
    with pytest.raises(InvalidAuctionTransition):
        assert_status_transition("pending", "active")


def test_phase4_python_sources_parse() -> None:
    for relative in (
        "bot/repositories/auction_workflows.py",
        "bot/repositories/exchanges.py",
        "bot/services/auction_workflows.py",
        "bot/services/exchanges.py",
        "bot/handlers/auction/publication.py",
        "bot/handlers/auction/kinds.py",
        "bot/telegram/media.py",
    ):
        ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
