# ruff: noqa: RUF001
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from userbot import services


@pytest.mark.asyncio
async def test_userbot_confirms_channel_and_discussion_ids_atomically(monkeypatch) -> None:
    lifecycle = SimpleNamespace(
        bind_by_channel_message=AsyncMock(),
        bind_by_auction=AsyncMock(),
    )
    publication = SimpleNamespace(
        confirm_deferred_publication=AsyncMock(
            return_value={
                "auction_id": 42,
                "_previous_status": "publication_deferred",
                "_final_status": "active",
                "_requires_finalization": False,
            }
        )
    )

    async def create_lifecycle():
        return lifecycle

    async def create_publication():
        return publication

    monkeypatch.setattr(services.AuctionLifecycleService, "create", create_lifecycle)
    monkeypatch.setattr(services.AuctionPublicationService, "create", create_publication)
    monkeypatch.setattr(services, "legacy_config", SimpleNamespace(AUCTION_CHANNEL_ID=-100123456))

    message = SimpleNamespace(
        id=99001,
        message="Лот № 42\nЦена: 10\nПринимаются ставки",
        fwd_from=SimpleNamespace(
            channel_post=88001,
            from_id=SimpleNamespace(channel_id=123456),
        ),
    )

    result = await services._try_bind_root_message(message)

    assert result == 42
    publication.confirm_deferred_publication.assert_awaited_once_with(
        42,
        channel_message_id=88001,
        discussion_message_id=99001,
    )
    lifecycle.bind_by_channel_message.assert_not_awaited()
    lifecycle.bind_by_auction.assert_not_awaited()


@pytest.mark.asyncio
async def test_userbot_does_not_hide_conflicting_confirmation_with_legacy_fallback(
    monkeypatch,
) -> None:
    lifecycle = SimpleNamespace(
        bind_by_channel_message=AsyncMock(return_value=42),
        bind_by_auction=AsyncMock(return_value=42),
    )
    publication = SimpleNamespace(
        confirm_deferred_publication=AsyncMock(side_effect=ValueError("channel message conflict"))
    )

    async def create_lifecycle():
        return lifecycle

    async def create_publication():
        return publication

    monkeypatch.setattr(services.AuctionLifecycleService, "create", create_lifecycle)
    monkeypatch.setattr(services.AuctionPublicationService, "create", create_publication)
    monkeypatch.setattr(services, "legacy_config", SimpleNamespace(AUCTION_CHANNEL_ID=-100123456))

    message = SimpleNamespace(
        id=99002,
        message="Лот № 42\nЦена: 10\nСтавки принимаются",
        fwd_from=SimpleNamespace(
            channel_post=88002,
            from_id=SimpleNamespace(channel_id=123456),
        ),
    )

    assert await services._try_bind_root_message(message) is None
    lifecycle.bind_by_channel_message.assert_not_awaited()
    lifecycle.bind_by_auction.assert_not_awaited()
