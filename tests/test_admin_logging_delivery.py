from __future__ import annotations

import logging
from typing import Any

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.services import admin_logging


class RecordingBot:
    def __init__(self, outcomes: list[BaseException | object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _retry_after(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=SendMessage(chat_id=-100123, text="audit"),
        message="Too Many Requests",
        retry_after=seconds,
    )


@pytest.mark.asyncio
async def test_send_message_safe_waits_and_retries_flood_control(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(admin_logging.asyncio, "sleep", sleep)
    bot = RecordingBot([_retry_after(3), object()])

    with caplog.at_level(logging.WARNING):
        delivered = await admin_logging.send_message_safe(bot, -100123, "audit")

    assert delivered is True
    assert len(bot.calls) == 2
    assert sleeps == [3.0]
    assert "rate limited" in caplog.text
    assert "unexpected error" not in caplog.text


@pytest.mark.asyncio
async def test_send_message_safe_stops_after_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(admin_logging.asyncio, "sleep", sleep)
    bot = RecordingBot([_retry_after(2), _retry_after(5)])

    with caplog.at_level(logging.WARNING):
        delivered = await admin_logging.send_message_safe(bot, -100124, "audit")

    assert delivered is False
    assert len(bot.calls) == 2
    assert sleeps == [2.0]
    assert caplog.text.count("rate limited") == 2
    assert "unexpected error" not in caplog.text
