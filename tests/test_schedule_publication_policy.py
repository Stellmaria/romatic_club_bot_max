from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from bot.core.time import MOSCOW
from db import schedule_publication as publication_db
from userbot import schedule_publication


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        auction_channel_id=-100123,
        schedule_announcements_hour=23,
        schedule_announcements_minute=0,
    )


def test_publication_waits_for_real_last_auction_close() -> None:
    target_date = date(2026, 8, 3)
    close = datetime(2026, 8, 3, 0, 31, tzinfo=MOSCOW)

    assert schedule_publication.schedule_publication_ready_at(
        target_date,
        last_auction_close=close,
        config=_config(),
    ) == close


def test_publication_uses_configured_time_when_previous_day_has_no_auctions() -> None:
    assert schedule_publication.schedule_publication_ready_at(
        date(2026, 8, 3),
        last_auction_close=None,
        config=_config(),
    ) == datetime(2026, 8, 2, 23, 0, tzinfo=MOSCOW)


def test_pin_rotation_pins_new_before_unpinning_previous(monkeypatch) -> None:
    calls: list[tuple[str, int, int, bool]] = []

    class FakeClient:
        async def pin_message(self, channel_id: int, message_id: int, *, notify: bool):
            calls.append(("pin", channel_id, message_id, notify))

        async def unpin_message(self, channel_id: int, message_id: int, *, notify: bool):
            calls.append(("unpin", channel_id, message_id, notify))

    async def fake_previous(target_date: date) -> int:
        assert target_date == date(2026, 8, 3)
        return 77

    monkeypatch.setattr(
        schedule_publication,
        "get_previous_published_schedule_message",
        fake_previous,
    )
    asyncio.run(
        schedule_publication.ensure_schedule_pin(
            FakeClient(),
            date(2026, 8, 3),
            88,
            config=_config(),
        )
    )

    assert calls == [
        ("pin", -100123, 88, False),
        ("unpin", -100123, 77, False),
    ]


def test_last_auction_query_uses_next_minute_deadline_and_completed_states(monkeypatch) -> None:
    captured: dict[str, object] = {}
    closes_at = datetime(2026, 8, 3, 0, 31, tzinfo=MOSCOW)

    async def fake_fetchrow(query: str, *args: object):
        captured["query"] = query
        captured["args"] = args
        return {"closes_at": closes_at}

    monkeypatch.setattr(publication_db, "fetchrow", fake_fetchrow)
    result = asyncio.run(publication_db.get_last_auction_close_for_day(date(2026, 8, 2)))

    assert result == closes_at
    sql = str(captured["query"])
    assert "date_trunc('minute', end_time) + interval '1 minute'" in sql
    assert "'active'" in sql
    assert "'finished'" in sql
    assert captured["args"] == (date(2026, 8, 2),)
