from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.auction import publication
from bot.use_cases.auction_publication import PublishAuctionCommand, PublishAuctionUseCase
from userbot.publication_reconciliation import (
    extract_auction_id,
    reconcile_recent_auction_publications,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_use_case_keeps_zero_message_id_pending_without_database_commit() -> None:
    events: list[object] = []

    async def claim(auction_id: int):
        events.append(("claim", auction_id))
        return {"auction_id": auction_id, "status": "publishing"}

    async def build_payload(_auction):
        events.append("payload")
        return "payload"

    async def send(_auction, _payload):
        events.append("telegram")
        return 0

    async def mark_published(_auction_id: int, _message_id: int):
        events.append("committed")
        return True

    async def mark_failed(_auction_id: int, _error: str):
        events.append("failed")

    async def after_published(_auction, _message_id: int):
        events.append("after")

    result = await PublishAuctionUseCase(
        claim=claim,
        build_payload=build_payload,
        send=send,
        mark_published=mark_published,
        mark_failed=mark_failed,
        after_published=after_published,
    ).execute(PublishAuctionCommand(auction_id=9217))

    assert result.message_id == 0
    assert result.pending_confirmation is True
    assert events == [("claim", 9217), "payload", "telegram"]


@pytest.mark.asyncio
async def test_publish_handler_marks_zero_message_as_awaiting_channel_post(monkeypatch) -> None:
    publication_service = SimpleNamespace(
        mark_published=AsyncMock(side_effect=AssertionError("zero must not be committed")),
        mark_failed=AsyncMock(),
    )
    recovery_service = SimpleNamespace(
        mark_awaiting_channel_post=AsyncMock(return_value=True),
    )

    async def load_context(_auction):
        return ({"auction_id": 9217}, {}, {}, 1)

    async def send_publication(_bot, **_kwargs):
        return SimpleNamespace(message_id=0)

    monkeypatch.setattr(publication, "_publication_context", load_context)
    monkeypatch.setattr(
        publication,
        "render_auction_caption",
        lambda *_args, **_kwargs: "Лот №9217",
    )
    monkeypatch.setattr(publication, "_media_id", lambda *_args: "file-id")
    monkeypatch.setattr(publication, "_send_publication", send_publication)

    result = await publication.publish_auction_lot(
        SimpleNamespace(),
        {"auction_id": 9217, "status": "publishing"},
        channel_id=-100123,
        channel_username=None,
        publication_service=publication_service,
        publication_recovery_service=recovery_service,
    )

    assert result is None
    publication_service.mark_published.assert_not_awaited()
    publication_service.mark_failed.assert_not_awaited()
    recovery_service.mark_awaiting_channel_post.assert_awaited_once_with(9217)


@pytest.mark.asyncio
async def test_userbot_reconciles_real_channel_message_id() -> None:
    messages = (
        SimpleNamespace(id=4567, message="🏓АУКЦИОН 🏓\n\nЛот №9217\n"),
        SimpleNamespace(id=4566, message="Обычный пост"),
    )

    class Client:
        def iter_messages(self, channel, *, limit):
            assert channel == -100123
            assert limit == 100

            async def stream():
                for message in messages:
                    yield message

            return stream()

    class Recovery:
        def __init__(self):
            self.calls: list[tuple[int, int]] = []

        async def recoverable_auction_ids(self, *, limit: int) -> list[int]:
            assert limit == 100
            return [9217]

        async def confirm_channel_post(self, auction_id: int, *, message_id: int) -> bool:
            self.calls.append((auction_id, message_id))
            return True

    recovery = Recovery()
    recovered = await reconcile_recent_auction_publications(
        Client(),
        channel=-100123,
        limit=100,
        service=recovery,
    )

    assert recovered == 1
    assert recovery.calls == [(9217, 4567)]


@pytest.mark.asyncio
async def test_userbot_skips_channel_history_when_nothing_is_stuck() -> None:
    class Client:
        def iter_messages(self, _channel, *, limit):
            raise AssertionError(f"history must not be read, requested limit={limit}")

    class Recovery:
        async def recoverable_auction_ids(self, *, limit: int) -> list[int]:
            assert limit == 100
            return []

    recovered = await reconcile_recent_auction_publications(
        Client(),
        channel=-100123,
        limit=100,
        service=Recovery(),
    )

    assert recovered == 0


def test_publication_recovery_contract_is_fail_closed_and_started() -> None:
    repository = (ROOT / "bot/repositories/publication_recovery.py").read_text(
        encoding="utf-8"
    )
    publication_source = (ROOT / "bot/handlers/auction/publication.py").read_text(
        encoding="utf-8"
    )
    application_source = (ROOT / "userbot/application.py").read_text(
        encoding="utf-8"
    )

    assert "actual_message_id <= 0" in repository
    assert "status = 'publication_failed'" in repository
    assert "publication_error = $3" in repository
    assert "recoverable_auction_ids" in repository
    assert "result.pending_confirmation" in publication_source
    assert "mark_awaiting_channel_post(auction_id)" in publication_source
    assert "publication_reconciliation_watchdog(telegram_client)" in application_source


def test_lot_id_parser_rejects_unrelated_or_invalid_posts() -> None:
    assert extract_auction_id("Лот №9217") == 9217
    assert extract_auction_id("лот № 42") == 42
    assert extract_auction_id("Лот №0") is None
    assert extract_auction_id("расписание на сегодня") is None
