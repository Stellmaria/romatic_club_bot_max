from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.core.logging import configure_logging
from bot.domain.auctions.deadlines import winner_deadline_reached


def test_winner_deadline_accepts_aware_database_timestamp() -> None:
    end_time = datetime(2026, 7, 15, 17, 0, tzinfo=timezone.utc)

    assert not winner_deadline_reached(
        end_time,
        now=end_time + timedelta(seconds=59),
    )
    assert winner_deadline_reached(
        end_time,
        now=end_time + timedelta(minutes=1),
    )


def test_winner_deadline_accepts_legacy_naive_moscow_timestamp() -> None:
    legacy_end_time = datetime(2026, 7, 15, 20, 0)
    current_utc = datetime(2026, 7, 15, 17, 1, tzinfo=timezone.utc)

    assert winner_deadline_reached(legacy_end_time, now=current_utc)


def test_winner_deadline_normalizes_different_aware_timezones() -> None:
    moscow = ZoneInfo("Europe/Moscow")
    end_time = datetime(2026, 7, 15, 20, 0, tzinfo=moscow)
    current_utc = datetime(2026, 7, 15, 17, 1, tzinfo=timezone.utc)

    assert winner_deadline_reached(end_time, now=current_utc)


def test_setup_logging_removes_legacy_project_handlers() -> None:
    auction_logger = logging.getLogger("auction")
    auction_bot_logger = logging.getLogger("auction_bot")
    auction_logger.addHandler(logging.NullHandler())
    auction_bot_logger.addHandler(logging.NullHandler())

    configure_logging()

    assert auction_logger.handlers == []
    assert auction_bot_logger.handlers == []
    assert auction_logger.propagate is True
    assert auction_bot_logger.propagate is True
