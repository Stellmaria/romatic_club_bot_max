from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ProcessTerminator = Callable[[], None]


def terminate_current_process() -> None:
    """Terminate this process and let Docker Compose start a fresh container."""

    logger.warning("Process self-restart requested; sending SIGTERM")
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except (AttributeError, OSError):
        os._exit(75)


@dataclass(slots=True)
class ProcessRestartCoordinator:
    terminator: ProcessTerminator = terminate_current_process
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)

    def _active_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def request(self, *, delay_seconds: float = 1.5) -> bool:
        """Schedule one restart and reject duplicate confirmations."""

        delay = max(0.1, min(float(delay_seconds), 30.0))
        async with self._active_lock():
            if self._task is not None and not self._task.done():
                return False
            self._task = asyncio.create_task(
                self._terminate_after_delay(delay),
                name="romatic-process-self-restart",
            )
            return True

    async def _terminate_after_delay(self, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)
        self.terminator()

    @property
    def pending(self) -> bool:
        return self._task is not None and not self._task.done()


process_restart_coordinator = ProcessRestartCoordinator()


__all__ = (
    "ProcessRestartCoordinator",
    "process_restart_coordinator",
    "terminate_current_process",
)
