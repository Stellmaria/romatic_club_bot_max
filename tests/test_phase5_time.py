from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from bot.core.time import (
    LEGACY_CALLBACK_COMPATIBILITY_SUNSET,
    MOSCOW,
    business_today,
    ensure_utc,
    moscow_date,
    parse_callback_timestamp,
    parse_timestamp,
    require_aware,
    serialize_callback_timestamp,
    serialize_timestamp,
    to_moscow,
    utc_now,
)
from bot.telegram.callback_parser import (
    parse_callback_timestamp as parse_callback_from_adapter,
)


@dataclass(frozen=True)
class FrozenClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def test_naive_legacy_timestamp_is_interpreted_as_moscow_wall_time() -> None:
    value = ensure_utc(datetime(2026, 1, 15, 12, 30))

    assert value.tzinfo is UTC
    assert value == datetime(2026, 1, 15, 9, 30, tzinfo=UTC)


def test_application_boundary_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware(datetime(2026, 1, 15, 12, 30), name="scheduled_at")


def test_injected_clock_is_normalized_to_utc() -> None:
    clock = FrozenClock(datetime(2026, 8, 3, 16, 30, tzinfo=MOSCOW))

    assert utc_now(clock=clock) == datetime(2026, 8, 3, 13, 30, tzinfo=UTC)


def test_business_date_uses_explicit_moscow_year_boundary() -> None:
    clock = FrozenClock(datetime(2026, 12, 31, 21, 30, tzinfo=UTC))

    assert business_today(clock=clock) == date(2027, 1, 1)


def test_aware_timestamp_keeps_its_instant_and_renders_in_moscow() -> None:
    value = datetime(2026, 7, 1, 21, 45, tzinfo=UTC)
    rendered = to_moscow(value)

    assert rendered.hour == 0
    assert rendered.minute == 45
    assert moscow_date(value).isoformat() == "2026-07-02"


def test_iso_timestamp_serialization_is_canonical_utc() -> None:
    source = datetime(2026, 8, 3, 16, 45, 23, tzinfo=MOSCOW)

    encoded = serialize_timestamp(source)

    assert encoded == "2026-08-03T13:45:23Z"
    assert parse_timestamp(encoded) == datetime(2026, 8, 3, 13, 45, 23, tzinfo=UTC)


def test_callback_timestamp_round_trip_uses_versioned_utc_payload() -> None:
    source = datetime(2026, 8, 3, 13, 45, 23, tzinfo=UTC)

    encoded = serialize_callback_timestamp(source)

    assert encoded.startswith("u1:")
    assert parse_callback_timestamp(encoded) == source
    assert parse_callback_from_adapter(encoded) == source


def test_legacy_naive_callback_is_interpreted_as_moscow_until_sunset() -> None:
    parsed = parse_callback_timestamp("2026-08-03T16:45:00")

    assert parsed == datetime(2026, 8, 3, 13, 45, tzinfo=UTC)
    assert date(2026, 11, 1) == LEGACY_CALLBACK_COMPATIBILITY_SUNSET


def test_invalid_callback_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid callback timestamp"):
        parse_callback_timestamp("u1:not-an-epoch")


def test_ensure_utc_rejects_non_datetime_values() -> None:
    with pytest.raises(TypeError):
        ensure_utc("2026-01-01")  # type: ignore[arg-type]
