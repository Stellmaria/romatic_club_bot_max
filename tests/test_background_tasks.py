from __future__ import annotations

import asyncio

from bot.core.tasks import (
    BackgroundTaskManager,
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
)


def test_background_manager_surfaces_critical_worker_failure() -> None:
    async def scenario() -> None:
        async def broken_worker(_context) -> None:
            raise ValueError("boom")

        manager = BackgroundTaskManager()
        manager.start(
            [
                BackgroundTaskSpec(
                    "broken",
                    broken_worker,
                    criticality=WorkerCriticality.CRITICAL,
                    restart_policy=RestartPolicy.NEVER,
                )
            ]
        )
        try:
            await asyncio.wait_for(manager.wait_for_failure(), timeout=1)
        except RuntimeError as error:
            assert "Critical background task 'broken' failed" in str(error)
            assert isinstance(error.__cause__, ValueError)
        else:  # pragma: no cover - documents the required fail-fast policy
            raise AssertionError("critical worker failure was not surfaced")
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_background_manager_treats_critical_normal_exit_as_failure() -> None:
    async def scenario() -> None:
        async def short_worker(_context) -> None:
            return None

        manager = BackgroundTaskManager()
        manager.start(
            [
                BackgroundTaskSpec(
                    "short",
                    short_worker,
                    criticality=WorkerCriticality.CRITICAL,
                    restart_policy=RestartPolicy.NEVER,
                )
            ]
        )
        try:
            await asyncio.wait_for(manager.wait_for_failure(), timeout=1)
        except RuntimeError as error:
            assert "Critical background task 'short' failed" in str(error)
            assert isinstance(error.__cause__, RuntimeError)
            assert "exited unexpectedly" in str(error.__cause__)
        else:  # pragma: no cover
            raise AssertionError("unexpected critical worker exit was not surfaced")
        finally:
            await manager.stop()

    asyncio.run(scenario())
