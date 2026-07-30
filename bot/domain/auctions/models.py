from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Any
from zoneinfo import ZoneInfo

from bot.domain.auctions.enums import AuctionKind, AuctionStatus, Currency


_LEGACY_TZ = ZoneInfo("Europe/Moscow")


def _compatible_now(now: datetime, reference: datetime) -> datetime:
    if reference.tzinfo is None and now.tzinfo is not None:
        return now.astimezone(_LEGACY_TZ).replace(tzinfo=None)
    if reference.tzinfo is not None and now.tzinfo is None:
        return now.replace(tzinfo=_LEGACY_TZ).astimezone(reference.tzinfo)
    if reference.tzinfo is not None and now.tzinfo is not None:
        return now.astimezone(reference.tzinfo)
    return now


@dataclass(frozen=True, slots=True)
class Auction:
    auction_id: int
    status: str
    currency: Currency
    start_price: int
    start_time: datetime | None
    end_time: datetime | None
    auction_kind: AuctionKind = AuctionKind.STANDARD
    message_id: int | None = None
    discussion_message_id: int | None = None

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
        if self.start_time is not None:
            comparable_now = _compatible_now(now, self.start_time)
            if comparable_now < self.start_time:
                return False
        if self.end_time is not None:
            comparable_now = _compatible_now(now, self.end_time)
            if comparable_now >= self.end_time:
                return False
        return True

    def has_ended_at(self, now: datetime) -> bool:
        if self.end_time is None:
            return False
        return _compatible_now(now, self.end_time) >= self.end_time

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
