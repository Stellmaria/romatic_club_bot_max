"""Typed data crossing application and persistence boundaries.

Persistence adapters must construct these immutable models before returning data
to services and use cases. Telegram presentation code may convert them to its
own view models, but asyncpg records and untyped dictionaries stop here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Mapping, TypeVar

from bot.core.time import ensure_utc, require_aware
from bot.domain.auctions.enums import AuctionKind, Currency


def _aware_utc(value: datetime, *, name: str) -> datetime:
    return ensure_utc(require_aware(value, name=name))


class RecordMappingError(ValueError):
    """Raised when a persistence row does not satisfy a typed model contract."""


class ModerationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class UserIdentity:
    user_id: int
    username: str | None = None
    full_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuctionRecord:
    auction_id: int
    status: str
    auction_kind: AuctionKind
    currency: Currency
    start_price: int
    card_id: int | None = None
    card_name: str | None = None
    hero_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    message_id: int | None = None
    comment: str = ""

    def __post_init__(self) -> None:
        if self.start_time is not None:
            object.__setattr__(self, "start_time", _aware_utc(self.start_time, name="start_time"))
        if self.end_time is not None:
            object.__setattr__(self, "end_time", _aware_utc(self.end_time, name="end_time"))


@dataclass(frozen=True, slots=True)
class ExchangeRecord:
    batch_id: int
    user_id: int
    deck_id: int
    status: ModerationStatus
    mode: str
    currency: Currency
    price: int
    comment: str = ""
    proof_photo_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeItemRecord:
    batch_id: int
    card_id: int
    card_name: str
    hero_name: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleLotRecord:
    auction_id: int
    target_date: date
    start_time: datetime
    end_time: datetime
    card_name: str
    auction_kind: AuctionKind

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_time", _aware_utc(self.start_time, name="start_time"))
        object.__setattr__(self, "end_time", _aware_utc(self.end_time, name="end_time"))


@dataclass(frozen=True, slots=True)
class UidVerificationRecord:
    request_id: int
    user_id: int
    status: ModerationStatus
    uid_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _aware_utc(self.created_at, name="created_at"))


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    event_id: int
    event_type: str
    aggregate_id: str
    payload_json: str
    created_at: datetime
    attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _aware_utc(self.created_at, name="created_at"))


T = TypeVar("T")


def required(row: Mapping[str, object], key: str, expected: type[T]) -> T:
    value = row.get(key)
    if not isinstance(value, expected):
        raise RecordMappingError(
            f"required field {key!r} must be {expected.__name__}, " f"got {type(value).__name__}"
        )
    return value


def optional_int(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise RecordMappingError(f"field {key!r} must be int or None")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RecordMappingError(f"field {key!r} must be int or None") from exc


def map_auction(row: Mapping[str, object]) -> AuctionRecord:
    return AuctionRecord(
        auction_id=int(required(row, "auction_id", int)),
        status=required(row, "status", str),
        auction_kind=AuctionKind.from_raw(required(row, "auction_kind", str)),
        currency=Currency.from_raw(required(row, "currency", str)),
        start_price=int(required(row, "start_price", int)),
        card_id=optional_int(row, "card_id"),
        card_name=str(row["card_name"]) if row.get("card_name") is not None else None,
        hero_name=str(row["hero_name"]) if row.get("hero_name") is not None else None,
        start_time=row.get("start_time") if isinstance(row.get("start_time"), datetime) else None,
        end_time=row.get("end_time") if isinstance(row.get("end_time"), datetime) else None,
        message_id=optional_int(row, "message_id"),
        comment=str(row.get("comment") or ""),
    )


def map_exchange(row: Mapping[str, object]) -> ExchangeRecord:
    return ExchangeRecord(
        batch_id=int(required(row, "batch_id", int)),
        user_id=int(required(row, "user_id", int)),
        deck_id=int(required(row, "deck_id", int)),
        status=ModerationStatus(required(row, "status", str)),
        mode=required(row, "mode", str),
        currency=Currency.from_raw(required(row, "currency", str)),
        price=int(required(row, "price", int)),
        comment=str(row.get("comment") or ""),
        proof_photo_id=(str(row.get("proof_photo_id") or "").strip() or None),
    )


__all__ = [
    "AuctionRecord",
    "ExchangeItemRecord",
    "ExchangeRecord",
    "ModerationStatus",
    "OutboxRecord",
    "RecordMappingError",
    "ScheduleLotRecord",
    "UidVerificationRecord",
    "UserIdentity",
    "map_auction",
    "map_exchange",
]
