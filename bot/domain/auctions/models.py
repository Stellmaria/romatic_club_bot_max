from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from bot.core.time import ensure_utc, require_aware
from bot.domain.auctions.bidding import auction_bidding_closes_at
from bot.domain.auctions.enums import (
    AuctionKind,
    AuctionStatus,
    Currency,
    normalize_currency_choices,
)


def _aware_utc(value: datetime, *, name: str) -> datetime:
    return ensure_utc(require_aware(value, name=name))


@dataclass(frozen=True, slots=True)
class Auction:
    auction_id: int
    status: str
    currency: Currency
    start_price: int
    start_time: datetime | None
    end_time: datetime | None
    auction_kind: AuctionKind = AuctionKind.STANDARD
    accepted_currencies: tuple[Currency, ...] = ()
    message_id: int | None = None
    discussion_message_id: int | None = None

    def __post_init__(self) -> None:
        if self.start_time is not None:
            object.__setattr__(self, "start_time", _aware_utc(self.start_time, name="start_time"))
        if self.end_time is not None:
            object.__setattr__(self, "end_time", _aware_utc(self.end_time, name="end_time"))

    @classmethod
    def from_record(cls, row: Mapping[str, Any]) -> "Auction":
        return cls(
            auction_id=int(row["auction_id"]),
            status=str(row.get("status") or ""),
            currency=Currency.from_raw(row.get("currency")),
            start_price=int(row.get("start_price") or 0),
            start_time=row.get("start_time"),
            end_time=row.get("end_time"),
            auction_kind=AuctionKind.from_raw(row.get("auction_kind")),
            accepted_currencies=normalize_currency_choices(
                row.get("accepted_currencies"),
                fallback=row.get("currency"),
            ),
            message_id=int(row["message_id"]) if row.get("message_id") is not None else None,
            discussion_message_id=(
                int(row["discussion_message_id"])
                if row.get("discussion_message_id") is not None
                else None
            ),
        )

    @property
    def normalized_status(self) -> AuctionStatus | None:
        return AuctionStatus.from_raw(self.status)

    def is_active_at(self, now: datetime) -> bool:
        if self.normalized_status is not AuctionStatus.ACTIVE:
            return False
        current_utc = _aware_utc(now, name="now")
        if self.start_time is not None and current_utc < self.start_time:
            return False
        if self.end_time is not None and current_utc >= auction_bidding_closes_at(self.end_time):
            return False
        return True

    def has_ended_at(self, now: datetime) -> bool:
        if self.end_time is None:
            return False
        return _aware_utc(now, name="now") >= auction_bidding_closes_at(self.end_time)

    @property
    def lowest_bid_wins(self) -> bool:
        return self.auction_kind.lowest_bid_wins


@dataclass(frozen=True, slots=True)
class Bid:
    bid_id: int
    auction_id: int
    bidder_id: int
    amount: int
    discussion_message_id: int | None
    placed_at: datetime | None
    created_at: datetime | None
    currency: Currency = Currency.DIAMONDS

    def __post_init__(self) -> None:
        if self.placed_at is not None:
            object.__setattr__(self, "placed_at", _aware_utc(self.placed_at, name="placed_at"))
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _aware_utc(self.created_at, name="created_at"))

    @classmethod
    def from_record(cls, row: Mapping[str, Any]) -> "Bid":
        return cls(
            bid_id=int(row["bid_id"]),
            auction_id=int(row["auction_id"]),
            bidder_id=int(row["bidder_id"]),
            amount=int(row["amount"]),
            discussion_message_id=(
                int(row["discussion_message_id"])
                if row.get("discussion_message_id") is not None
                else None
            ),
            placed_at=row.get("placed_at"),
            created_at=row.get("created_at"),
            currency=Currency.from_raw(row.get("currency") or "алмазы"),
        )


@dataclass(frozen=True, slots=True)
class BidPlacement:
    auction: Auction
    bid: Bid
    previous_max: int | None
    minimum_required: int


@dataclass(frozen=True, slots=True)
class BidRevision:
    auction: Auction
    bid: Bid
    previous_amount: int
    cancelled: bool
    minimum_required: int | None = None


@dataclass(frozen=True, slots=True)
class Autobid:
    autobid_id: int
    auction_id: int
    target_user_id: int
    target_username: str | None
    max_amount: int
    step: int
    is_active: bool

    @classmethod
    def from_record(cls, row: Mapping[str, Any]) -> "Autobid":
        return cls(
            autobid_id=int(row["autobid_id"]),
            auction_id=int(row["auction_id"]),
            target_user_id=int(row["target_user_id"]),
            target_username=(str(row["target_username"]) if row.get("target_username") else None),
            max_amount=int(row["max_amount"]),
            step=int(row["step"]),
            is_active=bool(row["is_active"]),
        )
