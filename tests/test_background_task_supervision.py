from __future__ import annotations

import asyncio

import pytest

from bot.core.tasks import (
    BackgroundTaskManager,
    BackgroundTaskSpec,
    RestartPolicy,
    WorkerCriticality,
    WorkerState,
)


@pytest.mark.asyncio
async def test_recoverable_worker_restarts_without_process_failure() -> None:
    attempts = 0
    running = asyncio.Event()
    release = asyncio.Event()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def worker(context) -> None:
        nonlocal attempts
        attempts += 1
        context.heartbeat()
        if attempts == 1:
            raise RuntimeError("temporary")
        running.set()
        await release.wait()

    manager = BackgroundTaskManager(sleep=fake_sleep, random_value=lambda: 0.5)
    manager.start(
        [
            BackgroundTaskSpec(
                "recoverable",
                worker,
                criticality=WorkerCriticality.RECOVERABLE,
                restart_policy=RestartPolicy.ON_FAILURE,
                initial_backoff=2.0,
                max_failures=3,
            )
        ]
    )

    await asyncio.wait_for(running.wait(), timeout=1)
    health = manager.health()[0]
    assert attempts == 2
    assert sleeps == [2.0]
    assert health.state is WorkerState.RUNNING
    assert health.failures == 1
    assert health.restarts == 1
    assert health.last_error == "RuntimeError: temporary"

    release.set()
    await manager.stop()


@pytest.mark.asyncio
async def test_critical_worker_exhaustion_surfaces_process_failure() -> None:
    async def worker(_context) -> None:
        raise RuntimeError("database unavailable")

    manager = BackgroundTaskManager(sleep=lambda _delay: asyncio.sleep(0))
    manager.start(
        [
            BackgroundTaskSpec(
                "critical",
                worker,
                criticality=WorkerCriticality.CRITICAL,
                restart_policy=RestartPolicy.ON_FAILURE,
                initial_backoff=0,
                max_backoff=0,
                max_failures=2,
            )
        ]
    )

    with pytest.raises(RuntimeError, match="Critical background task 'critical' failed"):
        await asyncio.wait_for(manager.wait_for_failure(), timeout=1)

    health = manager.health()[0]
    assert health.state is WorkerState.FAILED
    assert health.failures == 2
    assert health.restarts == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_heartbeat_timeout_is_treated_as_failure() -> None:
    async def hung_worker(_context) -> None:
        await asyncio.Event().wait()

    manager = BackgroundTaskManager()
    manager.start(
        [
            BackgroundTaskSpec(
                "hung-critical",
                hung_worker,
                criticality=WorkerCriticality.CRITICAL,
                restart_policy=RestartPolicy.NEVER,
                heartbeat_timeout=0.02,
            )
        ]
    )

    with pytest.raises(RuntimeError, match="hung-critical"):
        await asyncio.wait_for(manager.wait_for_failure(), timeout=1)

    health = manager.health()[0]
    assert health.state is WorkerState.FAILED
    assert health.last_error is not None
    assert "heartbeat timed out" in health.last_error
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_cancels_worker_and_clears_runtime() -> None:
    cancelled = asyncio.Event()

    async def worker(_context) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    manager = BackgroundTaskManager()
    manager.start([BackgroundTaskSpec("worker", worker, shutdown_timeout=0.5)])
    await asyncio.sleep(0)

    await manager.stop()

    assert cancelled.is_set()
    assert manager.health() == ()
