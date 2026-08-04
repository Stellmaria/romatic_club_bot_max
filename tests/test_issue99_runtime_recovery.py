# ruff: noqa: RUF001
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from userbot.publication_recovery import discover_issue99_repair_actions


def _forwarded_message(
    auction_id: int,
    *,
    message_id: int,
    channel_message_id: int,
    source_channel_id: int = 123456,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        message=f"Лот № {auction_id}\nЦена: 10\nПринимаются ставки",
        date=datetime(2026, 8, 3, 16, 30, tzinfo=UTC),
        fwd_from=SimpleNamespace(
            channel_post=channel_message_id,
            from_id=SimpleNamespace(channel_id=source_channel_id),
        ),
    )


def _channel_message(
    auction_id: int,
    *,
    message_id: int,
    at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        message=f"Лот № {auction_id}\nЦена: 10\nСтавки принимаются",
        date=at or datetime(2026, 8, 3, 16, 30, tzinfo=UTC),
        fwd_from=None,
    )


class _Client:
    def __init__(
        self,
        messages: dict[tuple[int, int], Any],
        search_results: list[Any],
    ) -> None:
        self.messages = messages
        self.search_results = search_results

    async def get_messages(self, entity: int, *, ids: int) -> Any:
        return self.messages.get((int(entity), int(ids)))

    async def iter_messages(self, *_args: object, **_kwargs: object):
        for message in self.search_results:
            yield message


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        auction_channel_id=-100123456,
        discussion_chat_id=-100654321,
    )


def _rows() -> list[dict[str, Any]]:
    started = datetime(2026, 8, 3, 16, 30, tzinfo=UTC)
    return [
        {
            "auction_id": auction_id,
            "publication_started_at": started,
            "start_time": started,
        }
        for auction_id in (3797, 7523, 9210, 9217, 9221, 9243)
    ]


@pytest.mark.asyncio
async def test_discovers_all_issue99_actions_from_exact_telegram_metadata() -> None:
    settings = _settings()
    messages = {
        (settings.discussion_chat_id, 1148772): _forwarded_message(
            9210,
            message_id=1148772,
            channel_message_id=12010,
        ),
        (settings.discussion_chat_id, 1149339): _forwarded_message(
            9217,
            message_id=1149339,
            channel_message_id=12017,
        ),
        (settings.discussion_chat_id, 1149326): _forwarded_message(
            9221,
            message_id=1149326,
            channel_message_id=12021,
        ),
        (settings.auction_channel_id, 5927): _channel_message(3797, message_id=5927),
        (settings.auction_channel_id, 10139): _channel_message(7523, message_id=10139),
    }
    client = _Client(
        messages,
        [_channel_message(9243, message_id=12043)],
    )

    discovery = await discover_issue99_repair_actions(
        client,
        settings,  # type: ignore[arg-type]
        _rows(),
    )

    assert discovery.unresolved == ()
    assert [action.auction_id for action in discovery.actions] == [
        3797,
        7523,
        9210,
        9217,
        9221,
        9243,
    ]
    by_id = {action.auction_id: action for action in discovery.actions}
    assert by_id[9210].channel_message_id == 12010
    assert by_id[9210].discussion_message_id == 1148772
    assert by_id[3797].action == "normalize_published"
    assert by_id[9243].channel_message_id == 12043


@pytest.mark.asyncio
async def test_rejects_discussion_root_forwarded_from_another_channel() -> None:
    settings = _settings()
    messages = {
        (settings.discussion_chat_id, 1148772): _forwarded_message(
            9210,
            message_id=1148772,
            channel_message_id=12010,
            source_channel_id=999999,
        ),
        (settings.discussion_chat_id, 1149339): _forwarded_message(
            9217,
            message_id=1149339,
            channel_message_id=12017,
        ),
        (settings.discussion_chat_id, 1149326): _forwarded_message(
            9221,
            message_id=1149326,
            channel_message_id=12021,
        ),
        (settings.auction_channel_id, 5927): _channel_message(3797, message_id=5927),
        (settings.auction_channel_id, 10139): _channel_message(7523, message_id=10139),
    }

    discovery = await discover_issue99_repair_actions(
        _Client(messages, [_channel_message(9243, message_id=12043)]),
        settings,  # type: ignore[arg-type]
        _rows(),
    )

    assert 9210 not in {action.auction_id for action in discovery.actions}
    assert discovery.unresolved[0]["auction_id"] == 9210
    assert "another channel" in str(discovery.unresolved[0]["error"])


@pytest.mark.asyncio
async def test_rejects_ambiguous_channel_search_for_9243() -> None:
    settings = _settings()
    messages = {
        (settings.discussion_chat_id, 1148772): _forwarded_message(
            9210,
            message_id=1148772,
            channel_message_id=12010,
        ),
        (settings.discussion_chat_id, 1149339): _forwarded_message(
            9217,
            message_id=1149339,
            channel_message_id=12017,
        ),
        (settings.discussion_chat_id, 1149326): _forwarded_message(
            9221,
            message_id=1149326,
            channel_message_id=12021,
        ),
        (settings.auction_channel_id, 5927): _channel_message(3797, message_id=5927),
        (settings.auction_channel_id, 10139): _channel_message(7523, message_id=10139),
    }

    discovery = await discover_issue99_repair_actions(
        _Client(
            messages,
            [
                _channel_message(9243, message_id=12043),
                _channel_message(9243, message_id=12044),
            ],
        ),
        settings,  # type: ignore[arg-type]
        _rows(),
    )

    assert 9243 not in {action.auction_id for action in discovery.actions}
    unresolved = next(item for item in discovery.unresolved if item["auction_id"] == 9243)
    assert "2 exact posts" in str(unresolved["error"])
