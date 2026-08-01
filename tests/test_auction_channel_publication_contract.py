from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.auction import publication
from bot.telegram.media import bot_send_media_any


@pytest.mark.asyncio
async def test_publication_keeps_media_at_telegram_caption_limit(monkeypatch) -> None:
    sent = SimpleNamespace(message_id=41)
    media_sender = AsyncMock(return_value=sent)
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(publication, "bot_send_media_any", media_sender)

    caption = "x" * publication.MAX_TG_CAPTION_LEN
    result = await publication._send_publication(
        bot,
        target=-100123,
        media="telegram-file-id",
        caption=caption,
    )

    assert result is sent
    media_sender.assert_awaited_once_with(
        bot,
        chat_id=-100123,
        file_id="telegram-file-id",
        caption=caption,
        parse_mode="HTML",
        protect_content=True,
        raise_on_failure=True,
    )
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_rejects_caption_over_media_limit(monkeypatch) -> None:
    media_sender = AsyncMock()
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(publication, "bot_send_media_any", media_sender)

    with pytest.raises(ValueError, match="caption is too long"):
        await publication._send_publication(
            bot,
            target=-100123,
            media="telegram-file-id",
            caption="x" * (publication.MAX_TG_CAPTION_LEN + 1),
        )

    media_sender.assert_not_awaited()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_does_not_replace_failed_media_with_text(monkeypatch) -> None:
    media_sender = AsyncMock(return_value=None)
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(publication, "bot_send_media_any", media_sender)

    with pytest.raises(RuntimeError, match="did not return a message"):
        await publication._send_publication(
            bot,
            target=-100123,
            media="telegram-file-id",
            caption="lot",
        )

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_only_publication_is_protected() -> None:
    sent = SimpleNamespace(message_id=42)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=sent))

    result = await publication._send_publication(
        bot,
        target=-100123,
        media=None,
        caption="lot",
    )

    assert result is sent
    bot.send_message.assert_awaited_once_with(
        -100123,
        "lot",
        parse_mode="HTML",
        disable_web_page_preview=True,
        protect_content=True,
    )


@pytest.mark.asyncio
async def test_bot_media_delivery_forwards_content_protection() -> None:
    sent = SimpleNamespace(message_id=43)
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=sent),
        send_video=AsyncMock(),
        send_animation=AsyncMock(),
    )

    result = await bot_send_media_any(
        bot,
        chat_id=-100123,
        file_id="telegram-file-id",
        caption="lot",
        protect_content=True,
        raise_on_failure=True,
    )

    assert result is sent
    bot.send_photo.assert_awaited_once_with(
        chat_id=-100123,
        photo="telegram-file-id",
        caption="lot",
        parse_mode="HTML",
        reply_markup=None,
        disable_notification=False,
        protect_content=True,
    )
    bot.send_video.assert_not_awaited()
    bot.send_animation.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_media_delivery_does_not_swallow_unknown_result() -> None:
    bot = SimpleNamespace(
        send_photo=AsyncMock(side_effect=RuntimeError("delivery result is unknown")),
        send_video=AsyncMock(),
        send_animation=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="delivery result is unknown"):
        await bot_send_media_any(
            bot,
            chat_id=-100123,
            file_id="telegram-file-id",
            caption="lot",
            raise_on_failure=True,
        )

    bot.send_video.assert_not_awaited()
    bot.send_animation.assert_not_awaited()


@pytest.mark.asyncio
async def test_lot_is_marked_published_only_after_telegram_message(monkeypatch) -> None:
    events: list[object] = []

    class PublicationService:
        async def mark_published(self, auction_id: int, *, message_id: int) -> bool:
            events.append(("marked", auction_id, message_id))
            return True

        async def mark_failed(self, auction_id: int, *, error: str) -> None:
            events.append(("failed", auction_id, error))

    async def load_context(_auction):
        return ({"auction_id": 7}, {}, {}, 1)

    async def send_publication(_bot, **_kwargs):
        events.append("telegram")
        return SimpleNamespace(message_id=77)

    def close_background_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(publication, "_publication_context", load_context)
    monkeypatch.setattr(publication, "render_auction_caption", lambda *_args, **_kwargs: "lot")
    monkeypatch.setattr(publication, "_media_id", lambda *_args: "telegram-file-id")
    monkeypatch.setattr(publication, "_send_publication", send_publication)
    monkeypatch.setattr(publication.asyncio, "create_task", close_background_task)

    result = await publication.publish_auction_lot(
        SimpleNamespace(),
        {"auction_id": 7, "status": "publishing"},
        channel_id=-100123,
        channel_username=None,
        publication_service=PublicationService(),
    )

    assert result == 77
    assert events == ["telegram", ("marked", 7, 77)]


@pytest.mark.asyncio
async def test_failed_channel_delivery_never_marks_lot_active(monkeypatch) -> None:
    events: list[object] = []

    class PublicationService:
        async def mark_published(self, auction_id: int, *, message_id: int) -> bool:
            events.append(("marked", auction_id, message_id))
            return True

        async def mark_failed(self, auction_id: int, *, error: str) -> None:
            events.append(("failed", auction_id, error))

    async def load_context(_auction):
        return ({"auction_id": 8}, {}, {}, 1)

    async def fail_delivery(_bot, **_kwargs):
        raise RuntimeError("channel delivery failed")

    monkeypatch.setattr(publication, "_publication_context", load_context)
    monkeypatch.setattr(publication, "render_auction_caption", lambda *_args, **_kwargs: "lot")
    monkeypatch.setattr(publication, "_media_id", lambda *_args: "telegram-file-id")
    monkeypatch.setattr(publication, "_send_publication", fail_delivery)

    result = await publication.publish_auction_lot(
        SimpleNamespace(),
        {"auction_id": 8, "status": "publishing"},
        channel_id=-100123,
        channel_username=None,
        publication_service=PublicationService(),
    )

    assert result is None
    assert len(events) == 1
    assert events[0][0:2] == ("failed", 8)
    assert "channel delivery failed" in events[0][2]
