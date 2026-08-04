from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime

import pytest
from aiogram import types

from bot.core.logging import JsonLogFormatter
from bot.core.observability import (
    HealthProbeServer,
    MetricsRegistry,
    ObservationContextFilter,
    bind_observation_context,
    correlation_id_var,
    new_correlation_id,
    operation_id_var,
    redact,
    reset_observation_context,
)
from bot.core.tasks import (
    BackgroundTaskManager,
    WorkerCriticality,
    WorkerHealth,
    WorkerState,
)
from bot.middlewares.observability import ObservabilityMiddleware


def test_redact_masks_sensitive_values_recursively() -> None:
    hidden_value = "".join(("must", "-not-leak"))
    sample_credential = ":".join(("123456789", "A" * 35))
    sensitive_key = "".join(("to", "ken"))
    payload = {
        sensitive_key: hidden_value,
        "nested": {"phone": "+79990000000", "safe": "visible"},
        "items": [{"session": "bytes"}],
        "error": (
            f"request failed for {sample_credential} "
            "at postgresql://user:password@postgres/database"
        ),
    }

    redacted = redact(payload)

    assert redacted[sensitive_key] == "[REDACTED]"
    assert redacted["nested"] == {"phone": "[REDACTED]", "safe": "visible"}
    assert redacted["items"] == [{"session": "[REDACTED]"}]
    assert "AAAAAAAA" not in redacted["error"]
    assert ":password@" not in redacted["error"]


def test_redact_normalizes_collections_and_preserves_scalars() -> None:
    assert redact(("safe", "+7 (999) 000-00-00")) == ["safe", "[REDACTED_PHONE]"]
    assert redact({"alpha", "beta"}) in (["alpha", "beta"], ["beta", "alpha"])
    assert redact(42) == 42


def test_json_formatter_has_schema_context_and_redacts_extras() -> None:
    hidden_value = "".join(("must", "-not-leak"))
    record = logging.LogRecord(
        name="auction_bot.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processed",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "cid-1"
    record.operation_id = "auction:publish"
    record.bot_token = hidden_value

    rendered = JsonLogFormatter().format(record)

    assert '"schema_version": 1' in rendered
    assert '"correlation_id": "cid-1"' in rendered
    assert '"operation_id": "auction:publish"' in rendered
    assert hidden_value not in rendered
    assert "[REDACTED]" in rendered


def test_observation_context_filter_binds_and_resets_context() -> None:
    tokens = bind_observation_context(correlation_id="cid-filter", operation_id="op-filter")
    try:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
        assert ObservationContextFilter().filter(record) is True
        assert record.correlation_id == "cid-filter"  # type: ignore[attr-defined]
        assert record.operation_id == "op-filter"  # type: ignore[attr-defined]
    finally:
        reset_observation_context(tokens)

    assert correlation_id_var.get() == ""
    assert operation_id_var.get() == ""
    assert new_correlation_id("job").startswith("job-")


def test_metrics_registry_renders_prometheus_values_and_histograms() -> None:
    metrics = MetricsRegistry()
    metrics.increment("updates_total", result="ok")
    metrics.gauge("outbox_backlog", 3, queue="telegram")
    metrics.observe("update_latency_seconds", 0.2, update_type="message")

    rendered = metrics.render_prometheus()

    assert 'updates_total{result="ok"} 1' in rendered
    assert 'outbox_backlog{queue="telegram"} 3' in rendered
    assert 'update_latency_seconds_bucket{update_type="message",le="0.25"} 1' in rendered
    assert 'update_latency_seconds_count{update_type="message"} 1' in rendered
    assert 'update_latency_seconds_sum{update_type="message"} 0.2' in rendered


def test_metrics_registry_validates_names_values_and_escaping() -> None:
    metrics = MetricsRegistry()
    assert metrics.render_prometheus() == ""

    with pytest.raises(ValueError, match="Invalid metric name"):
        metrics.increment("bad metric")
    with pytest.raises(ValueError, match="Invalid metric label"):
        metrics.increment("valid_metric", **{"bad-label": "value"})
    for invalid in (-1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="finite non-negative"):
            metrics.observe("latency_seconds", invalid)

    metrics.increment("escaped_total", label='line\n"quoted"\\path')
    assert 'label="line\\n\\"quoted\\"\\\\path"' in metrics.render_prometheus()


async def test_update_middleware_binds_context_and_records_latency() -> None:
    moments = iter((10.0, 10.2))
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(clock=moments.__next__)
    update = types.Update(
        update_id=42,
        message=types.Message(
            message_id=7,
            date=datetime.now(UTC),
            chat=types.Chat(id=100, type="private"),
            from_user=types.User(
                id=200,
                is_bot=False,
                first_name="Tester",
            ),
            text="/start private text that must not become a metric label",
        ),
    )
    observed: dict[str, str] = {}

    async def handler(_event: object, _data: dict[str, object]) -> str:
        observed["correlation_id"] = correlation_id_var.get()
        observed["operation_id"] = operation_id_var.get()
        return "handled"

    result = await middleware(handler, update, {"metrics_registry": metrics})

    assert result == "handled"
    assert observed == {
        "correlation_id": "telegram-update-42",
        "operation_id": "message:/start",
    }
    assert correlation_id_var.get() == ""
    rendered = metrics.render_prometheus()
    assert 'telegram_updates_total{action="/start",update_type="message"} 1' in rendered
    assert "private text" not in rendered
    assert (
        "telegram_update_latency_seconds_count"
        '{action="/start",update_type="message"} 1' in rendered
    )


async def test_update_middleware_records_and_reraises_failures() -> None:
    moments = iter((2.0, 1.5))
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(clock=moments.__next__)

    async def handler(_event: object, _data: dict[str, object]) -> None:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await middleware(handler, object(), {"metrics_registry": metrics})

    rendered = metrics.render_prometheus()
    assert "telegram_update_errors_total" in rendered
    assert 'error_type="RuntimeError"' in rendered
    assert "telegram_update_latency_seconds_sum" in rendered
    assert correlation_id_var.get() == ""


def test_readiness_requires_database_and_workers() -> None:
    metrics = MetricsRegistry()
    task_manager: BackgroundTaskManager | None = None
    probe = HealthProbeServer(
        database_ready=lambda: True,
        task_manager=lambda: task_manager,
        metrics=metrics,
    )

    snapshot = probe.snapshot()

    assert snapshot.live is True
    assert snapshot.database is True
    assert snapshot.ready is False


def test_recoverable_worker_failure_does_not_hide_critical_readiness() -> None:
    class FakeManager:
        def health(self) -> tuple[WorkerHealth, ...]:
            return (
                WorkerHealth(
                    name="critical-worker",
                    criticality=WorkerCriticality.CRITICAL,
                    state=WorkerState.RUNNING,
                    failures=0,
                    restarts=0,
                    last_started_at=1.0,
                    last_success_at=None,
                    last_heartbeat_at=1.0,
                    last_error=None,
                ),
                WorkerHealth(
                    name="recoverable-worker",
                    criticality=WorkerCriticality.RECOVERABLE,
                    state=WorkerState.FAILED,
                    failures=5,
                    restarts=4,
                    last_started_at=1.0,
                    last_success_at=None,
                    last_heartbeat_at=1.0,
                    last_error="failed",
                ),
            )

    manager = FakeManager()
    probe = HealthProbeServer(
        database_ready=lambda: True,
        task_manager=lambda: manager,  # type: ignore[arg-type]
        metrics=MetricsRegistry(),
        database_metrics=lambda: {"database_pool_free": 2.0},
    )

    snapshot = probe.snapshot()
    rendered = probe._metrics.render_prometheus()

    assert snapshot.ready is True
    assert "database_pool_free 2" in rendered
    assert 'worker_failures{worker="recoverable-worker"} 5' in rendered
    assert 'worker_ready{criticality="recoverable",worker="recoverable-worker"} 0' in rendered


async def _probe_request(port: int, request: bytes) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    status = int(head.split(maxsplit=2)[1])
    return status, body


async def test_health_probe_serves_all_routes_and_closes_cleanly() -> None:
    probe = HealthProbeServer(
        database_ready=lambda: True,
        task_manager=lambda: None,
        metrics=MetricsRegistry(),
        port=0,
    )
    await probe.start()
    with pytest.raises(RuntimeError, match="already started"):
        await probe.start()
    server = probe._server
    assert server is not None and server.sockets
    port = int(server.sockets[0].getsockname()[1])

    try:
        status, body = await _probe_request(port, b"GET /healthz HTTP/1.1\r\n\r\n")
        assert status == 200
        assert json.loads(body) == {"live": True}

        status, body = await _probe_request(port, b"GET /readyz HTTP/1.1\r\n\r\n")
        assert status == 503
        assert json.loads(body)["ready"] is False

        status, body = await _probe_request(port, b"GET /metrics HTTP/1.1\r\n\r\n")
        assert status == 200
        assert b"application_ready 0" in body

        status, body = await _probe_request(port, b"GET /missing HTTP/1.1\r\n\r\n")
        assert status == 404
        assert json.loads(body) == {"error": "not_found"}

        status, body = await _probe_request(port, b"POST /healthz HTTP/1.1\r\n\r\n")
        assert status == 405
        assert json.loads(body) == {"error": "method_not_allowed"}

        status, body = await _probe_request(port, b"malformed\r\n")
        assert status == 400
        assert json.loads(body) == {"error": "bad_request"}
    finally:
        await probe.close()
        await probe.close()
