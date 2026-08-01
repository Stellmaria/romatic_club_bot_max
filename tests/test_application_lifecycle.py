from __future__ import annotations

import asyncio
from unittest.mock import patch

import main as entrypoint
from bot.application import _run_polling_with_worker_monitor
from bot.core.settings import ConfigurationError, ConfigurationIssue


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
    error = ConfigurationError([ConfigurationIssue("BOT_TOKEN", "is required")])
    with (
        patch.object(entrypoint, "load_project_environment"),
        patch.object(entrypoint.BotProcessSettings, "from_env", side_effect=error),
    ):
        assert entrypoint.main() == 2
