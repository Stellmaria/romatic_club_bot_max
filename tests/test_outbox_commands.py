# ruff: noqa: ARG002
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bot.telegram.outbox as outbox_module
from bot.telegram.outbox import TelegramRateLimiter, deliver_outbox_batch
from bot.telegram.outbox_commands import (
    OutboxCommandError,
    build_command,
    decode_command,
)


def test_build_and_decode_send_message_command() -> None:
    command = build_command("send_message", {"text": "hello", "parse_mode": "HTML"})

    assert command.command_type == "send_message"
    assert command.version == 1
    assert command.payload["text"] == "hello"
    assert decode_command(command.as_json()) == command


def test_legacy_payload_is_migrated_on_read() -> None:
    command = decode_command({"text": "legacy"}, legacy_method="send_message")

    assert command.command_type == "send_message"
    assert command.version == 1
    assert command.payload == {"text": "legacy"}


def test_unknown_or_invalid_command_is_rejected() -> None:
    with pytest.raises(OutboxCommandError):
        build_command("delete_everything", {"text": "nope"})

    with pytest.raises(OutboxCommandError):
        decode_command({"command_type": "send_message", "version": 999, "payload": {"text": "x"}})

    with pytest.raises(OutboxCommandError):
        build_command("copy_message", {"from_chat_id": 1})

    with pytest.raises(OutboxCommandError):
        build_command("refresh_auction_publication", {"auction_id": 0})


def test_refresh_auction_publication_command_is_typed() -> None:
    command = build_command("refresh_auction_publication", {"auction_id": "42"})

    assert command.command_type == "refresh_auction_publication"
    assert command.payload == {"auction_id": 42}


class _Repository:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sent: list[int] = []
        self.failed: list[tuple[int, str]] = []

    async def claim_batch(self, *, limit: int) -> list[dict[str, object]]:
        return self.rows[:limit]

    async def mark_sent(self, outbox_id: int, *, message_id: int) -> bool:
        self.sent.append(outbox_id)
        return True

    async def retry_after(self, outbox_id: int, **_: object) -> bool:
        return True

    async def mark_failed(self, outbox_id: int, *, delivery_state: str, **_: object) -> bool:
        self.failed.append((outbox_id, delivery_state))
        return True


class _Bot:
    def __init__(self) -> None:
        self.active_by_chat: set[int] = set()
        self.order: list[tuple[int, str]] = []
        self.overlap_detected = False

    async def send_message(self, chat_id: int, text: str, **_: object) -> SimpleNamespace:
        if chat_id in self.active_by_chat:
            self.overlap_detected = True
        self.active_by_chat.add(chat_id)
        await asyncio.sleep(0)
        self.order.append((chat_id, text))
        self.active_by_chat.remove(chat_id)
        return SimpleNamespace(message_id=len(self.order))

    async def copy_message(self, chat_id: int, **_: object) -> SimpleNamespace:
        return await self.send_message(chat_id, "copy")


@pytest.mark.asyncio
async def test_parallel_delivery_preserves_order_inside_chat() -> None:
    rows = [
        {"outbox_id": 1, "chat_id": 10, "method": "send_message", "payload": {"text": "a"}},
        {"outbox_id": 2, "chat_id": 20, "method": "send_message", "payload": {"text": "x"}},
        {"outbox_id": 3, "chat_id": 10, "method": "send_message", "payload": {"text": "b"}},
        {"outbox_id": 4, "chat_id": 20, "method": "send_message", "payload": {"text": "y"}},
    ]
    repository = _Repository(rows)
    bot = _Bot()

    delivered = await deliver_outbox_batch(
        bot,
        repository,  # type: ignore[arg-type]
        concurrency=4,
        limiter=TelegramRateLimiter(global_rate=10000, per_chat_rate=10000),
    )

    assert delivered == 4
    assert bot.overlap_detected is False
    assert [text for chat_id, text in bot.order if chat_id == 10] == ["a", "b"]
    assert [text for chat_id, text in bot.order if chat_id == 20] == ["x", "y"]


@pytest.mark.asyncio
async def test_refresh_publication_command_uses_existing_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def refresh(_bot: object, *, auction_id: int) -> int:
        calls.append(auction_id)
        return 88004

    monkeypatch.setattr(outbox_module, "_refresh_auction_publication", refresh)
    rows = [
        {
            "outbox_id": 5,
            "chat_id": -100123,
            "method": "send_message",
            "payload": build_command(
                "refresh_auction_publication",
                {"auction_id": 42},
            ).as_json(),
        }
    ]
    repository = _Repository(rows)

    delivered = await deliver_outbox_batch(
        _Bot(),
        repository,  # type: ignore[arg-type]
        limiter=TelegramRateLimiter(global_rate=10000, per_chat_rate=10000),
    )

    assert delivered == 1
    assert calls == [42]
    assert repository.sent == [5]
