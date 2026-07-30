from __future__ import annotations

from datetime import datetime, timezone

from bot.core.time import ensure_utc, moscow_date, to_moscow


def test_naive_legacy_timestamp_is_interpreted_as_moscow_wall_time() -> None:
    value = ensure_utc(datetime(2026, 1, 15, 12, 30))
    assert value.tzinfo is timezone.utc
    assert value == datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)


def test_aware_timestamp_keeps_its_instant_and_renders_in_moscow() -> None:
    value = datetime(2026, 7, 1, 21, 45, tzinfo=timezone.utc)
    rendered = to_moscow(value)
    assert rendered.hour == 0
    assert rendered.minute == 45
    assert moscow_date(value).isoformat() == "2026-07-02"


def test_ensure_utc_rejects_non_datetime_values() -> None:
    try:
        ensure_utc("2026-01-01")  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("ensure_utc must reject non-datetime values")
