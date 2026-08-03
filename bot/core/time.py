"""Canonical time policy for the application.

Runtime instants cross domain and persistence boundaries as timezone-aware UTC
``datetime`` values. User-facing schedule values are rendered in
``Europe/Moscow``. A small compatibility path still accepts historical naive
Telegram callback/state timestamps as Moscow wall time; new payloads use the
versioned UTC callback format from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Final, Protocol
from zoneinfo import ZoneInfo

UTC: Final = UTC
MOSCOW: Final = ZoneInfo("Europe/Moscow")
LEGACY_CALLBACK_COMPATIBILITY_SUNSET: Final = date(2026, 11, 1)
_CALLBACK_TIMESTAMP_PREFIX: Final = "u1:"


class ClockSource(Protocol):
    """Minimal clock port consumed by timezone helpers."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock returning aware UTC instants."""

    def now(self) -> datetime:
        return datetime.now(UTC)


_SYSTEM_CLOCK: Final = SystemClock()


def require_aware(value: datetime, *, name: str = "value") -> datetime:
    """Reject a naive datetime at an application or persistence boundary."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def utc_now(*, clock: ClockSource = _SYSTEM_CLOCK) -> datetime:
    """Return the current instant as an aware UTC datetime."""

    return ensure_utc(require_aware(clock.now(), name="clock.now()"))


def now_in(zone: tzinfo, *, clock: ClockSource = _SYSTEM_CLOCK) -> datetime:
    """Return the current instant rendered in ``zone``."""

    return utc_now(clock=clock).astimezone(zone)


def moscow_now(*, clock: ClockSource = _SYSTEM_CLOCK) -> datetime:
    """Return the current instant rendered in the Moscow business timezone."""

    return now_in(MOSCOW, clock=clock)


def business_today(
    *,
    zone: tzinfo = MOSCOW,
    clock: ClockSource = _SYSTEM_CLOCK,
) -> date:
    """Return the current business date in an explicitly selected timezone."""

    return now_in(zone, clock=clock).date()


def ensure_utc(value: datetime, *, assume_tz: tzinfo = MOSCOW) -> datetime:
    """Normalize a datetime to aware UTC.

    Historical callback/state payloads stored naive Moscow wall-clock values.
    During the documented compatibility window those values are interpreted as
    Moscow time. New application and persistence boundaries should call
    :func:`require_aware` before this function when legacy input is impossible.
    """

    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=assume_tz)
    return value.astimezone(UTC)


def parse_timestamp(
    value: datetime | str,
    *,
    assume_tz: tzinfo = MOSCOW,
) -> datetime:
    """Parse an ISO timestamp and return an aware UTC instant.

    A trailing ``Z`` is accepted. Naive strings are legacy Moscow wall time.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("timestamp must not be empty")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    else:
        raise TypeError("timestamp must be a datetime or ISO string")
    return ensure_utc(parsed, assume_tz=assume_tz)


def serialize_timestamp(value: datetime) -> str:
    """Serialize an instant as canonical second-precision UTC ISO-8601."""

    return ensure_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def serialize_callback_timestamp(value: datetime) -> str:
    """Serialize a compact versioned UTC timestamp for Telegram callbacks."""

    epoch_seconds = int(ensure_utc(value).timestamp())
    return f"{_CALLBACK_TIMESTAMP_PREFIX}{epoch_seconds}"


def parse_callback_timestamp(value: str) -> datetime:
    """Parse current callback timestamps and legacy ISO callback payloads.

    Legacy naive ISO payloads are interpreted as Moscow wall time until
    ``LEGACY_CALLBACK_COMPATIBILITY_SUNSET``. The constant is operational
    documentation; removing the compatibility branch requires a dedicated
    release after that date.
    """

    raw = value.strip()
    if raw.startswith(_CALLBACK_TIMESTAMP_PREFIX):
        encoded = raw.removeprefix(_CALLBACK_TIMESTAMP_PREFIX)
        try:
            epoch_seconds = int(encoded)
        except ValueError as exc:
            raise ValueError("invalid callback timestamp") from exc
        return datetime.fromtimestamp(epoch_seconds, UTC)
    return parse_timestamp(raw, assume_tz=MOSCOW)


def auction_end_at_59(start_time: datetime) -> datetime:
    """Return the persisted last bidding second for a 30-minute slot.

    Existing database constraints and rows store the final accepted bidding
    second as ``HH:30:59``. The canonical exclusive deadline is therefore the
    next whole minute, ``HH:31:00``.
    """

    if not isinstance(start_time, datetime):
        raise TypeError("start_time must be a datetime")
    normalized_start = start_time.replace(second=0, microsecond=0)
    return (normalized_start + timedelta(minutes=30)).replace(second=59, microsecond=0)


def to_moscow(value: datetime) -> datetime:
    """Return a datetime suitable for user-facing Moscow-time rendering."""

    return ensure_utc(value).astimezone(MOSCOW)


def to_moscow_wall(value: datetime) -> datetime:
    """Return presentation-only Moscow wall time without ``tzinfo``.

    This representation is only for keyboard/grid formatting. It must not be
    passed to domain services or persisted.
    """

    return to_moscow(value).replace(tzinfo=None)


def schedule_slot_key(value: datetime) -> datetime:
    """Normalize a schedule timestamp to a presentation-only Moscow grid key."""

    return to_moscow_wall(value).replace(second=0, microsecond=0)


def moscow_date(value: datetime) -> date:
    return to_moscow(value).date()


def moscow_time(value: datetime) -> time:
    return to_moscow(value).time().replace(tzinfo=None)


__all__ = [
    "LEGACY_CALLBACK_COMPATIBILITY_SUNSET",
    "MOSCOW",
    "UTC",
    "ClockSource",
    "SystemClock",
    "auction_end_at_59",
    "business_today",
    "ensure_utc",
    "moscow_date",
    "moscow_now",
    "moscow_time",
    "now_in",
    "parse_callback_timestamp",
    "parse_timestamp",
    "require_aware",
    "schedule_slot_key",
    "serialize_callback_timestamp",
    "serialize_timestamp",
    "to_moscow",
    "to_moscow_wall",
    "utc_now",
]
