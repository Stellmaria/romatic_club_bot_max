from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BackgroundTaskSpec:
    """Description of a long-running background coroutine."""

    name: str
    factory: TaskFactory


class BackgroundTaskManager:
    """Starts named background jobs and shuts them down predictably."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def start(self, specs: Iterable[BackgroundTaskSpec]) -> list[asyncio.Task[None]]:
        if self._tasks:
            raise RuntimeError("Background tasks have already been started")

        for spec in specs:
            task = asyncio.create_task(self._run_guarded(spec), name=spec.name)
            self._tasks.append(task)
        return list(self._tasks)

    async def _run_guarded(self, spec: BackgroundTaskSpec) -> None:
        try:
            await spec.factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background task %s terminated unexpectedly", spec.name)
            raise

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
