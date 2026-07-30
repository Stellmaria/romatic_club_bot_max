from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.core.time import auction_end_at_59


def test_auction_end_is_final_second_of_displayed_end_minute() -> None:
    start = datetime(2026, 7, 16, 22, 0, 0)
    assert auction_end_at_59(start) == datetime(2026, 7, 16, 22, 30, 59)


def test_auction_end_preserves_timezone_and_clears_microseconds() -> None:
    moscow = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 16, 22, 0, 23, 456789, tzinfo=moscow)
    end = auction_end_at_59(start)

    assert end == datetime(2026, 7, 16, 22, 30, 59, tzinfo=moscow)
    assert end.tzinfo is moscow
