from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

UTC = timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime."""

    return datetime.now(UTC)


def ensure_utc(value: datetime, *, assume_tz: tzinfo = MOSCOW) -> datetime:
    """Normalize a datetime to UTC.

    Telegram callbacks created before Phase 5 contain naive Moscow wall-clock
    values.  Treat those values as Moscow time during the transition; aware
    values retain their actual instant.
    """

    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=assume_tz)
    return value.astimezone(UTC)


def auction_end_at_59(start_time: datetime) -> datetime:
    """Return the persisted compatibility timestamp for a 30-minute slot.

    Existing database constraints and rows store the slot end as ``HH:30:59``.
    Bid acceptance must not compare this raw value directly: the canonical
    domain deadline normalizes it back to the displayed ``HH:30`` boundary.
    """

    if not isinstance(start_time, datetime):
        raise TypeError("start_time must be a datetime")
    normalized_start = start_time.replace(second=0, microsecond=0)
    return (normalized_start + timedelta(minutes=30)).replace(second=59, microsecond=0)


def to_moscow(value: datetime) -> datetime:
    """Return a datetime suitable for user-facing Moscow-time rendering."""

    return ensure_utc(value).astimezone(MOSCOW)


def to_moscow_wall(value: datetime) -> datetime:
    """Return Moscow wall-clock time without ``tzinfo``.

    Schedule keyboards are built from naive local datetimes such as
    ``2026-07-16 22:30``. Database values may be aware UTC datetimes. Mixing
    those representations directly makes occupied slots appear three hours
    earlier and can offer an already occupied slot as free.
    """

    return to_moscow(value).replace(tzinfo=None)


def schedule_slot_key(value: datetime) -> datetime:
    """Normalize a schedule timestamp to a minute-precision Moscow grid key."""

    return to_moscow_wall(value).replace(second=0, microsecond=0)


def moscow_date(value: datetime):
    return to_moscow(value).date()


def moscow_time(value: datetime):
    return to_moscow(value).time().replace(tzinfo=None)
