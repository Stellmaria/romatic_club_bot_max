from __future__ import annotations

import asyncio

from bot.core.tasks import BackgroundTaskManager, BackgroundTaskSpec


def test_background_manager_surfaces_worker_failure() -> None:
    async def scenario() -> None:
        async def broken_worker() -> None:
            raise ValueError("boom")

        manager = BackgroundTaskManager()
        manager.start([BackgroundTaskSpec("broken", broken_worker)])
        try:
            await manager.wait_for_failure()
        except RuntimeError as error:
            assert "'broken' failed" in str(error)
            assert isinstance(error.__cause__, ValueError)
        else:  # pragma: no cover - documents the required fail-fast policy
            raise AssertionError("worker failure was not surfaced")
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_background_manager_treats_normal_worker_exit_as_failure() -> None:
    async def scenario() -> None:
        async def short_worker() -> None:
            return None

        manager = BackgroundTaskManager()
        manager.start([BackgroundTaskSpec("short", short_worker)])
        try:
            await manager.wait_for_failure()
        except RuntimeError as error:
            assert "'short' exited unexpectedly" in str(error)
        else:  # pragma: no cover
            raise AssertionError("unexpected worker exit was not surfaced")
        finally:
            await manager.stop()

    asyncio.run(scenario())
