from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import main as entrypoint
from bot.application import (
    ApplicationConfigurationError,
    _run_polling_with_worker_monitor,
    validate_settings,
)
from bot.core.settings import settings


def test_application_validation_reports_all_required_configuration() -> None:
    invalid = replace(
        settings,
        bot_token="",
        database_url="",
        auction_channel_id=0,
        discussion_chat_id=0,
        uid_hash_key="",
        uid_enc_key="",
    )
    try:
        validate_settings(invalid)
    except ApplicationConfigurationError as error:
        message = str(error)
    else:  # pragma: no cover
        raise AssertionError("invalid configuration was accepted")

    assert "BOT_TOKEN" in message
    assert "DATABASE_URL" in message
    assert "AUCTION_CHANNEL_ID" in message
    assert "DISCUSSION_CHAT_ID" in message
    assert "UID_HASH_KEY" in message
    assert "UID_ENC_KEY" in message


def test_polling_is_cancelled_when_a_background_worker_fails() -> None:
    class FakeDispatcher:
        cancelled = False

        async def start_polling(self, _bot) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    class FailingManager:
        async def wait_for_failure(self) -> None:
            await asyncio.sleep(0)
            raise ValueError("worker failed")

    async def scenario() -> None:
        dispatcher = FakeDispatcher()
        try:
            await _run_polling_with_worker_monitor(
                dispatcher,  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                FailingManager(),  # type: ignore[arg-type]
            )
        except ValueError as error:
            assert str(error) == "worker failed"
        else:  # pragma: no cover
            raise AssertionError("worker failure was suppressed")
        assert dispatcher.cancelled

    asyncio.run(scenario())


def test_external_cancellation_stops_polling_and_worker_monitor() -> None:
    class FakeDispatcher:
        cancelled = False

        async def start_polling(self, _bot) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    class WaitingManager:
        cancelled = False

        async def wait_for_failure(self) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def scenario() -> None:
        dispatcher = FakeDispatcher()
        manager = WaitingManager()
        running = asyncio.create_task(
            _run_polling_with_worker_monitor(
                dispatcher,  # type: ignore[arg-type]
                object(),
                manager,  # type: ignore[arg-type]
            )
        )
        await asyncio.sleep(0)
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        else:  # pragma: no cover
            raise AssertionError("application cancellation was suppressed")

        assert dispatcher.cancelled
        assert manager.cancelled

    asyncio.run(scenario())


def test_entrypoint_returns_nonzero_for_configuration_error() -> None:
    async def invalid_run() -> None:
        raise ApplicationConfigurationError("missing settings")

    with patch.object(entrypoint, "run_bot", invalid_run):
        assert entrypoint.main() == 2
