from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from bot.core.tasks import BackgroundTaskManager, WorkerState

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
operation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "operation_id", default=""
)

_SECRET_KEY = re.compile(r"(?:token|secret|password|phone|uid|session)", re.I)


def new_correlation_id(prefix: str = "op") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def bind_observation_context(*, correlation_id: str, operation_id: str = "") -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    return (
        correlation_id_var.set(correlation_id),
        operation_id_var.set(operation_id),
    )


def reset_observation_context(tokens: tuple[contextvars.Token[str], contextvars.Token[str]]) -> None:
    correlation_id_var.reset(tokens[0])
    operation_id_var.reset(tokens[1])


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    return value


class ObservationContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get() or None
        record.operation_id = operation_id_var.get() or None
        return True


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    live: bool
    ready: bool
    database: bool
    workers: tuple[dict[str, Any], ...]


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    @staticmethod
    def _key(name: str, labels: Mapping[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((labels or {}).items()))

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        self._counters[self._key(name, labels)] += value

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._gauges[self._key(name, labels)] = value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for (name, labels), value in sorted(self._counters.items()):
            lines.append(f"{name}{_render_labels(labels)} {value}")
        for (name, labels), value in sorted(self._gauges.items()):
            lines.append(f"{name}{_render_labels(labels)} {value:g}")
        return "\n".join(lines) + ("\n" if lines else "")


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = [f'{key}="{value.replace(chr(34), chr(92) + chr(34))}"' for key, value in labels]
    return "{" + ",".join(escaped) + "}"


class HealthProbeServer:
    def __init__(
        self,
        *,
        database_ready: Callable[[], bool],
        task_manager: Callable[[], BackgroundTaskManager | None],
        metrics: MetricsRegistry,
        host: str = "127.0.0.1",
        port: int = 8081,
    ) -> None:
        self._database_ready = database_ready
        self._task_manager = task_manager
        self._metrics = metrics
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._started_at = time.monotonic()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    def snapshot(self) -> HealthSnapshot:
        manager = self._task_manager()
        worker_health = () if manager is None else manager.health()
        workers = tuple(asdict(item) for item in worker_health)
        database = self._database_ready()
        ready_workers = bool(worker_health) and all(
            item.state not in {WorkerState.FAILED, WorkerState.STOPPED}
            for item in worker_health
        )
        ready = database and ready_workers
        self._metrics.gauge("process_uptime_seconds", time.monotonic() - self._started_at)
        self._metrics.gauge("application_ready", 1 if ready else 0)
        for item in worker_health:
            self._metrics.gauge("worker_failures", item.failures, worker=item.name)
            self._metrics.gauge("worker_restarts", item.restarts, worker=item.name)
        return HealthSnapshot(live=True, ready=ready, database=database, workers=workers)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            path = request_line.decode("ascii", errors="ignore").split(" ")[1]
            snapshot = self.snapshot()
            if path == "/healthz":
                await _respond(writer, 200, {"live": snapshot.live})
            elif path == "/readyz":
                await _respond(writer, 200 if snapshot.ready else 503, asdict(snapshot))
            elif path == "/metrics":
                body = self._metrics.render_prometheus().encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
            else:
                await _respond(writer, 404, {"error": "not_found"})
        except (asyncio.TimeoutError, IndexError):
            await _respond(writer, 400, {"error": "bad_request"})
        finally:
            writer.close()
            await writer.wait_closed()


async def _respond(writer: asyncio.StreamWriter, status: int, payload: Mapping[str, Any]) -> None:
    body = json.dumps(redact(payload), default=str, ensure_ascii=False).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 503: "Service Unavailable"}[status]
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


__all__ = [
    "HealthProbeServer",
    "MetricsRegistry",
    "ObservationContextFilter",
    "bind_observation_context",
    "correlation_id_var",
    "new_correlation_id",
    "operation_id_var",
    "redact",
    "reset_observation_context",
]
