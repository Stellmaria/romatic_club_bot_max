from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.handlers.admin.presentation import exchange_pending_view
from bot.handlers.auction.exchange import moderation_queue


class _FakeState:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = dict(data or {})

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **kwargs: object) -> None:
        self.data.update(kwargs)


class _FakeBot:
    def __init__(self) -> None:
        self.edit_kwargs: dict[str, object] | None = None

    async def edit_message_text(self, **kwargs: object) -> None:
        self.edit_kwargs = kwargs


@pytest.mark.asyncio
async def test_approval_continuation_shows_next_pending_exchange(
    monkeypatch,
) -> None:
    rows = [{"batch_id": 202, "items_count": 1}]

    class FakeModeration:
        async def pending_batches(self, *, include_luxury: bool) -> list[dict]:
            assert include_luxury is True
            return rows

    async def fake_create(cls):
        return FakeModeration()

    async def fake_send_detail(message, batch):
        assert batch["batch_id"] == 202
        return SimpleNamespace(message_id=303)

    monkeypatch.setattr(
        exchange_pending_view.ExchangeModerationService,
        "create",
        classmethod(fake_create),
    )
    monkeypatch.setattr(
        exchange_pending_view,
        "_send_pending_exchange_detail",
        fake_send_detail,
    )

    state = _FakeState(
        {
            "exchange_pending_detail_message_id": 101,
            "exchange_pending_header_message_id": 102,
            "exchange_pending_page": 0,
        }
    )
    bot = _FakeBot()
    message = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=404),
    )

    await exchange_pending_view.continue_pending_exchange_request_one(
        message,
        state,
        processed_batch_id=101,
    )

    assert bot.edit_kwargs is not None
    assert bot.edit_kwargs["message_id"] == 102
    assert "1</b> из <b>1" in str(bot.edit_kwargs["text"])
    assert state.data["exchange_pending_detail_message_id"] == 303
    assert state.data["exchange_pending_header_message_id"] == 102
    assert state.data["exchange_pending_page"] == 0


@pytest.mark.asyncio
async def test_approval_middleware_runs_handler_then_continues_queue(
    monkeypatch,
) -> None:
    calls: list[object] = []

    class FakeMessage:
        pass

    class FakeFsmContext:
        pass

    event = SimpleNamespace(
        data="exchange_approve|73",
        message=FakeMessage(),
    )
    state = FakeFsmContext()

    async def handler(received_event, data):
        calls.append(("handler", received_event, data))
        return "approved"

    async def continue_queue(message, received_state, *, processed_batch_id):
        calls.append(("continue", message, received_state, processed_batch_id))

    monkeypatch.setattr(moderation_queue, "Message", FakeMessage)
    monkeypatch.setattr(moderation_queue, "FSMContext", FakeFsmContext)
    monkeypatch.setattr(
        exchange_pending_view,
        "continue_pending_exchange_request_one",
        continue_queue,
    )

    result = await moderation_queue.ContinuePendingExchangeQueueMiddleware()(
        handler,
        event,
        {"state": state},
    )

    assert result == "approved"
    assert calls[0][0] == "handler"
    assert calls[1] == ("continue", event.message, state, 73)


def test_exchange_router_registers_queue_continuation_middleware() -> None:
    source = (
        Path(__file__).parents[1] / "bot" / "handlers" / "auction" / "exchange" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "ContinuePendingExchangeQueueMiddleware" in source
    assert "moderation_router.callback_query.middleware(" in source
