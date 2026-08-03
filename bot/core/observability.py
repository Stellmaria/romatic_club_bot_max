from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast

from bot.core.tasks import BackgroundTaskManager, WorkerCriticality, WorkerState

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="",
)
operation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "operation_id",
    default="",
)

_SECRET_KEY = re.compile(r"(?:token|secret|password|phone|uid|session)", re.I)
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_DSN_PASSWORD = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@",
    re.I,
)
_PHONE_NUMBER = re.compile(r"(?<!\d)\+?\d(?:[\s()-]*\d){9,14}(?!\d)")
_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class _ContextLogRecord(Protocol):
    correlation_id: str | None
    operation_id: str | None


def new_correlation_id(prefix: str = "op") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def bind_observation_context(
    *,
    correlation_id: str,
    operation_id: str = "",
) -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    return (
        correlation_id_var.set(correlation_id),
        operation_id_var.set(operation_id),
    )


def reset_observation_context(
    tokens: tuple[contextvars.Token[str], contextvars.Token[str]],
) -> None:
    correlation_id_var.reset(tokens[0])
    operation_id_var.reset(tokens[1])


def _redact_text(value: str) -> str:
    value = _BOT_TOKEN.sub("[REDACTED_TOKEN]", value)
    value = _DSN_PASSWORD.sub(r"\g<prefix>[REDACTED]@", value)
    return _PHONE_NUMBER.sub("[REDACTED_PHONE]", value)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class ObservationContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context_record = cast(_ContextLogRecord, record)
        context_record.correlation_id = correlation_id_var.get() or None
        context_record.operation_id = operation_id_var.get() or None
        return True


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    live: bool
    ready: bool
    database: bool
    workers: tuple[dict[str, Any], ...]


type MetricLabels = tuple[tuple[str, str], ...]
type MetricKey = tuple[str, MetricLabels]


class MetricsRegistry:
    """Small in-process Prometheus registry without external dependencies."""

    def __init__(self) -> None:
        self._counters: Counter[MetricKey] = Counter()
        self._gauges: dict[MetricKey, float] = {}
        self._histogram_buckets: Counter[tuple[str, MetricLabels, float]] = Counter()
        self._histogram_counts: Counter[MetricKey] = Counter()
        self._histogram_sums: dict[MetricKey, float] = {}

    @staticmethod
    def _key(name: str, labels: Mapping[str, str] | None) -> MetricKey:
        if not _METRIC_NAME.fullmatch(name):
            raise ValueError(f"Invalid metric name: {name!r}")
        normalized: list[tuple[str, str]] = []
        for key, value in (labels or {}).items():
            if not _LABEL_NAME.fullmatch(key):
                raise ValueError(f"Invalid metric label: {key!r}")
            normalized.append((key, str(value)))
        return name, tuple(sorted(normalized))

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        self._counters[self._key(name, labels)] += value

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._gauges[self._key(name, labels)] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
        **labels: str,
    ) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("Histogram observation must be a finite non-negative number")
        key = self._key(name, labels)
        self._histogram_counts[key] += 1
        self._histogram_sums[key] = self._histogram_sums.get(key, 0.0) + value
        for upper_bound in buckets:
            if value <= upper_bound:
                self._histogram_buckets[(key[0], key[1], upper_bound)] += 1
        self._histogram_buckets[(key[0], key[1], math.inf)] += 1

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for (name, labels), value in sorted(self._counters.items()):
            lines.append(f"{name}{_render_labels(labels)} {value}")
        for (name, labels), value in sorted(self._gauges.items()):
            lines.append(f"{name}{_render_labels(labels)} {value:g}")
        for (name, labels), count in sorted(self._histogram_counts.items()):
            buckets = sorted(
                (
                    (upper_bound, value)
                    for (bucket_name, bucket_labels, upper_bound), value in (
                        self._histogram_buckets.items()
                    )
                    if bucket_name == name and bucket_labels == labels
                ),
                key=lambda item: item[0],
            )
            for upper_bound, bucket_count in buckets:
                rendered_bound = "+Inf" if math.isinf(upper_bound) else f"{upper_bound:g}"
                bucket_labels = (*labels, ("le", rendered_bound))
                lines.append(f"{name}_bucket{_render_labels(bucket_labels)} {bucket_count}")
            lines.append(f"{name}_count{_render_labels(labels)} {count}")
            lines.append(
                f"{name}_sum{_render_labels(labels)} " f"{self._histogram_sums[(name, labels)]:g}"
            )
        return "\n".join(lines) + ("\n" if lines else "")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(labels: MetricLabels) -> str:
    if not labels:
        return ""
    escaped = [f'{key}="{_escape_label(value)}"' for key, value in labels]
    return "{" + ",".join(escaped) + "}"


class HealthProbeServer:
    def __init__(
        self,
        *,
        database_ready: Callable[[], bool],
        task_manager: Callable[[], BackgroundTaskManager | None],
        metrics: MetricsRegistry,
        database_metrics: Callable[[], Mapping[str, float]] | None = None,
        host: str = "127.0.0.1",
        port: int = 8081,
    ) -> None:
        self._database_ready = database_ready
        self._task_manager = task_manager
        self._metrics = metrics
        self._database_metrics = database_metrics
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._started_at = time.monotonic()

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("Health probe server is already started")
        self._server = await asyncio.start_server(
            self._handle,
            self._host,
            self._port,
        )

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
        critical_workers = tuple(
            item for item in worker_health if item.criticality is WorkerCriticality.CRITICAL
        )
        ready_workers = bool(worker_health) and all(
            item.state not in {WorkerState.FAILED, WorkerState.STOPPED} for item in critical_workers
        )
        ready = database and ready_workers
        self._metrics.gauge(
            "process_uptime_seconds",
            time.monotonic() - self._started_at,
        )
        self._metrics.gauge("application_ready", 1 if ready else 0)
        self._metrics.gauge("database_ready", 1 if database else 0)
        if self._database_metrics is not None:
            for name, value in self._database_metrics().items():
                self._metrics.gauge(name, value)
        for item in worker_health:
            self._metrics.gauge("worker_failures", item.failures, worker=item.name)
            self._metrics.gauge("worker_restarts", item.restarts, worker=item.name)
            self._metrics.gauge(
                "worker_ready",
                0 if item.state in {WorkerState.FAILED, WorkerState.STOPPED} else 1,
                worker=item.name,
                criticality=item.criticality.value,
            )
        return HealthSnapshot(
            live=True,
            ready=ready,
            database=database,
            workers=workers,
        )

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            method, path, _version = request_line.decode("ascii").strip().split(maxsplit=2)
            if method != "GET":
                await _respond(writer, 405, {"error": "method_not_allowed"})
                return
            snapshot = self.snapshot()
            if path == "/healthz":
                await _respond(writer, 200, {"live": snapshot.live})
            elif path == "/readyz":
                await _respond(
                    writer,
                    200 if snapshot.ready else 503,
                    asdict(snapshot),
                )
            elif path == "/metrics":
                body = self._metrics.render_prometheus().encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain; version=0.0.4\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
            else:
                await _respond(writer, 404, {"error": "not_found"})
        except (TimeoutError, UnicodeError, ValueError):
            await _respond(writer, 400, {"error": "bad_request"})
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()


async def _respond(
    writer: asyncio.StreamWriter,
    status: int,
    payload: Mapping[str, Any],
) -> None:
    body = json.dumps(redact(payload), default=str, ensure_ascii=False).encode()
    reason = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        503: "Service Unavailable",
    }[status]
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
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
