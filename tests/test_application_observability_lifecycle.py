from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot.application import _run_polling_with_worker_monitor


class _PollingCompletesDispatcher:
    async def start_polling(self, _bot: object) -> None:
        await asyncio.sleep(0)


class _WaitingTaskManager:
    def __init__(self) -> None:
        self.cancelled = False

    async def wait_for_failure(self) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def test_worker_monitor_is_cancelled_after_polling_completes() -> None:
    manager = _WaitingTaskManager()

    await _run_polling_with_worker_monitor(
        _PollingCompletesDispatcher(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        manager,  # type: ignore[arg-type]
    )

    assert manager.cancelled is True


class _WaitingDispatcher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def start_polling(self, _bot: object) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class _FailingTaskManager:
    async def wait_for_failure(self) -> Any:
        await asyncio.sleep(0)
        raise RuntimeError("critical worker failed")


async def test_worker_failure_cancels_polling_and_is_reraised() -> None:
    dispatcher = _WaitingDispatcher()

    with pytest.raises(RuntimeError, match="critical worker failed"):
        await _run_polling_with_worker_monitor(
            dispatcher,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            _FailingTaskManager(),  # type: ignore[arg-type]
        )

    assert dispatcher.started.is_set()
    assert dispatcher.cancelled is True
