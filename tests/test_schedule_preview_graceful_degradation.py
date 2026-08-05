# ruff: noqa: RUF001
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from userbot import schedule_publication


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, dict[str, object]]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs: object,
    ) -> SimpleNamespace:
        self.calls.append((chat_id, text, kwargs))
        return SimpleNamespace(id=100 + len(self.calls))


@pytest.mark.asyncio
async def test_incomplete_schedule_sends_preview_then_separate_warning(monkeypatch) -> None:
    target_date = date(2026, 8, 6)
    lots = [
        {
            "auction_id": 9244,
            "hero_name": "Неизвестная карточка",
            "card_name": "Неизвестная карточка",
            "start_time": datetime(2026, 8, 6, 9, 0, tzinfo=UTC),
            "obtain_amount": 0,
            "obtain_type": "diamonds",
            "currency": "diamonds",
        }
    ]

    async def no_review(_target_date: date):
        return None

    async def preview_target():
        return {"chat_id": -100123, "thread_id": 77}

    async def schedule_lots(_target_date: date):
        return lots

    async def emoji_assets():
        return {"header": 1, "card": 2, "diamond": 3}

    record_preview = AsyncMock()
    monkeypatch.setattr(schedule_publication, "get_publication_review", no_review)
    monkeypatch.setattr(schedule_publication, "get_preview_target", preview_target)
    monkeypatch.setattr(schedule_publication, "get_schedule_lots_for_day", schedule_lots)
    monkeypatch.setattr(schedule_publication, "get_emoji_assets", emoji_assets)
    monkeypatch.setattr(schedule_publication, "record_pending_preview", record_preview)
    client = FakeClient()

    message_id = await schedule_publication.send_schedule_review_preview(
        client,
        target_date,
    )

    assert message_id == 101
    assert len(client.calls) == 2
    preview_chat, preview_text, preview_kwargs = client.calls[0]
    warning_chat, warning_text, warning_kwargs = client.calls[1]
    assert preview_chat == warning_chat == -100123
    assert preview_text.startswith("🦋 АНОНС НА 6 АВГУСТА 🦋")
    assert preview_kwargs["reply_to"] == 77
    assert preview_kwargs["buttons"]
    assert "Расписание показано" in warning_text
    assert "лот 9244: не определена колода" in warning_text
    assert "/schedule_setup" in warning_text
    assert "/schedule_audit" in warning_text
    assert warning_kwargs["reply_to"] == 77
    record_preview.assert_awaited_once_with(
        target_date,
        chat_id=-100123,
        thread_id=77,
        message_id=101,
    )
