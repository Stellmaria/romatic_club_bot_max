from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic

logger = logging.getLogger(__name__)


class WorkerCriticality(StrEnum):
    CRITICAL = "critical"
    RECOVERABLE = "recoverable"


class RestartPolicy(StrEnum):
    NEVER = "never"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"


class WorkerState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    BACKING_OFF = "backing_off"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True)
class WorkerContext:
    name: str
    _heartbeat: Callable[[], None]

    def heartbeat(self) -> None:
        self._heartbeat()


TaskFactory = Callable[[WorkerContext], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
RandomValue = Callable[[], float]


@dataclass(frozen=True, slots=True)
class BackgroundTaskSpec:
    """Failure and lifecycle policy for one long-running coroutine."""

    name: str
    factory: TaskFactory
    criticality: WorkerCriticality = WorkerCriticality.RECOVERABLE
    restart_policy: RestartPolicy = RestartPolicy.ON_FAILURE
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    jitter: float = 0.2
    max_failures: int = 5
    heartbeat_timeout: float | None = None
    shutdown_timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Worker name must not be empty")
        if self.initial_backoff < 0 or self.max_backoff < self.initial_backoff:
            raise ValueError("Invalid worker backoff configuration")
        if not 0 <= self.jitter <= 1:
            raise ValueError("Worker jitter must be between 0 and 1")
        if self.max_failures < 1:
            raise ValueError("Worker max_failures must be positive")
        if self.heartbeat_timeout is not None and self.heartbeat_timeout <= 0:
            raise ValueError("Worker heartbeat_timeout must be positive")
        if self.shutdown_timeout <= 0:
            raise ValueError("Worker shutdown_timeout must be positive")


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    name: str
    criticality: WorkerCriticality
    state: WorkerState
    failures: int
    restarts: int
    last_started_at: float | None
    last_success_at: float | None
    last_heartbeat_at: float | None
    last_error: str | None


@dataclass(slots=True)
class _WorkerRuntime:
    spec: BackgroundTaskSpec
    state: WorkerState = WorkerState.STARTING
    failures: int = 0
    restarts: int = 0
    last_started_at: float | None = None
    last_success_at: float | None = None
    last_heartbeat_at: float | None = None
    last_error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class BackgroundTaskManager:
    """Supervise workers with bounded restart and deterministic cleanup."""

    def __init__(
        self,
        *,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = monotonic,
        random_value: RandomValue = random.random,
    ) -> None:
        self._sleep = sleep
        self._clock = clock
        self._random_value = random_value
        self._workers: dict[str, _WorkerRuntime] = {}
        self._fatal_failure: asyncio.Future[RuntimeError] | None = None
        self._stopping = False

    def start(self, specs: Iterable[BackgroundTaskSpec]) -> list[asyncio.Task[None]]:
        if self._workers:
            raise RuntimeError("Background tasks have already been started")

        loop = asyncio.get_running_loop()
        self._fatal_failure = loop.create_future()
        for spec in specs:
            if spec.name in self._workers:
                raise ValueError(f"Duplicate background task name: {spec.name}")
            runtime = _WorkerRuntime(spec=spec)
            runtime.task = asyncio.create_task(self._supervise(runtime), name=spec.name)
            self._workers[spec.name] = runtime
        if not self._workers:
            raise ValueError("At least one background task is required")
        return [runtime.task for runtime in self._workers.values() if runtime.task is not None]

    def heartbeat(self, name: str) -> None:
        runtime = self._workers.get(name)
        if runtime is None:
            raise KeyError(f"Unknown background task: {name}")
        runtime.last_heartbeat_at = self._clock()

    def health(self) -> tuple[WorkerHealth, ...]:
        return tuple(
            WorkerHealth(
                name=runtime.spec.name,
                criticality=runtime.spec.criticality,
                state=runtime.state,
                failures=runtime.failures,
                restarts=runtime.restarts,
                last_started_at=runtime.last_started_at,
                last_success_at=runtime.last_success_at,
                last_heartbeat_at=runtime.last_heartbeat_at,
                last_error=runtime.last_error,
            )
            for runtime in self._workers.values()
        )

    async def _supervise(self, runtime: _WorkerRuntime) -> None:
        spec = runtime.spec
        backoff = spec.initial_backoff
        while not self._stopping:
            runtime.state = WorkerState.RUNNING
            runtime.last_started_at = self._clock()
            runtime.last_heartbeat_at = runtime.last_started_at
            context = WorkerContext(spec.name, lambda: self.heartbeat(spec.name))
            try:
                worker = asyncio.create_task(spec.factory(context), name=f"{spec.name}:run")
                await self._wait_worker(worker, runtime)
            except asyncio.CancelledError:
                runtime.state = WorkerState.STOPPED
                raise
            except Exception as error:
                runtime.failures += 1
                runtime.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Background task %s failed", spec.name)
                if not self._should_restart(spec, runtime.failures, failed=True):
                    runtime.state = WorkerState.FAILED
                    self._publish_fatal(runtime, error)
                    return
            else:
                runtime.last_success_at = self._clock()
                runtime.last_error = "Worker exited unexpectedly"
                if not self._should_restart(spec, runtime.failures, failed=False):
                    runtime.state = WorkerState.FAILED
                    self._publish_fatal(runtime, RuntimeError(runtime.last_error))
                    return

            runtime.restarts += 1
            runtime.state = WorkerState.BACKING_OFF
            delay = self._jittered(backoff, spec.jitter)
            await self._sleep(delay)
            backoff = min(spec.max_backoff, max(spec.initial_backoff, backoff * 2))

        runtime.state = WorkerState.STOPPED

    async def _wait_worker(
        self,
        worker: asyncio.Task[None],
        runtime: _WorkerRuntime,
    ) -> None:
        timeout = runtime.spec.heartbeat_timeout
        try:
            if timeout is None:
                await worker
                return
            while True:
                done, _ = await asyncio.wait({worker}, timeout=timeout / 2)
                if worker in done:
                    await worker
                    return
                heartbeat = runtime.last_heartbeat_at or runtime.last_started_at or self._clock()
                if self._clock() - heartbeat > timeout:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                    raise TimeoutError(
                        f"Background task '{runtime.spec.name}' heartbeat timed out"
                    )
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    @staticmethod
    def _should_restart(
        spec: BackgroundTaskSpec,
        failures: int,
        *,
        failed: bool,
    ) -> bool:
        if failures >= spec.max_failures:
            return False
        if spec.restart_policy is RestartPolicy.ALWAYS:
            return True
        if spec.restart_policy is RestartPolicy.ON_FAILURE:
            return failed
        return False

    def _publish_fatal(self, runtime: _WorkerRuntime, error: BaseException) -> None:
        if runtime.spec.criticality is WorkerCriticality.RECOVERABLE:
            logger.error(
                "Recoverable worker %s exhausted restart policy and remains failed",
                runtime.spec.name,
            )
            return
        failure = RuntimeError(f"Critical background task '{runtime.spec.name}' failed")
        failure.__cause__ = error
        if self._fatal_failure is not None and not self._fatal_failure.done():
            self._fatal_failure.set_result(failure)

    def _jittered(self, delay: float, jitter: float) -> float:
        if delay == 0 or jitter == 0:
            return delay
        offset = (self._random_value() * 2 - 1) * jitter
        return max(0.0, delay * (1 + offset))

    async def wait_for_failure(self) -> None:
        """Wait until a critical worker exhausts its restart policy."""
        if self._fatal_failure is None:
            raise RuntimeError("Background tasks have not been started")
        raise await self._fatal_failure

    async def stop(self) -> None:
        self._stopping = True
        workers, self._workers = self._workers, {}
        for runtime in workers.values():
            if runtime.task is not None:
                runtime.task.cancel()
        for runtime in workers.values():
            task = runtime.task
            if task is None:
                continue
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=runtime.spec.shutdown_timeout,
                )
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.error(
                    "Background task %s exceeded shutdown timeout %.1fs",
                    runtime.spec.name,
                    runtime.spec.shutdown_timeout,
                )
            except Exception:
                logger.exception("Background task %s failed during shutdown", runtime.spec.name)
            runtime.state = WorkerState.STOPPED

        if self._fatal_failure is not None and not self._fatal_failure.done():
            self._fatal_failure.cancel()
        self._fatal_failure = None
        self._stopping = False
