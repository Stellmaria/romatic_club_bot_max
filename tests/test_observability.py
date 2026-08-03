from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import types

from bot.core.logging import JsonLogFormatter
from bot.core.observability import (
    HealthProbeServer,
    MetricsRegistry,
    correlation_id_var,
    operation_id_var,
    redact,
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
    payload = {
        "token": hidden_value,
        "nested": {"phone": "+79990000000", "safe": "visible"},
        "items": [{"session": "bytes"}],
        "error": (
            f"request failed for {sample_credential} "
            "at postgresql://user:password@postgres/database"
        ),
    }

    redacted = redact(payload)

    assert redacted["token"] == "[REDACTED]"
    assert redacted["nested"] == {"phone": "[REDACTED]", "safe": "visible"}
    assert redacted["items"] == [{"session": "[REDACTED]"}]
    assert "AAAAAAAA" not in redacted["error"]
    assert ":password@" not in redacted["error"]


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


def test_metrics_registry_renders_prometheus_values_and_histograms() -> None:
    metrics = MetricsRegistry()
    metrics.increment("updates_total", result="ok")
    metrics.gauge("outbox_backlog", 3, queue="telegram")
    metrics.observe("update_latency_seconds", 0.2, update_type="message")

    rendered = metrics.render_prometheus()

    assert 'updates_total{result="ok"} 1' in rendered
    assert 'outbox_backlog{queue="telegram"} 3' in rendered
    assert (
        'update_latency_seconds_bucket{update_type="message",le="0.25"} 1'
        in rendered
    )
    assert 'update_latency_seconds_count{update_type="message"} 1' in rendered
    assert 'update_latency_seconds_sum{update_type="message"} 0.2' in rendered


async def test_update_middleware_binds_context_and_records_latency() -> None:
    moments = iter((10.0, 10.2))
    metrics = MetricsRegistry()
    middleware = ObservabilityMiddleware(clock=moments.__next__)
    update = types.Update(
        update_id=42,
        message=types.Message(
            message_id=7,
            date=datetime.now(timezone.utc),
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
    assert (
        'telegram_updates_total{action="/start",update_type="message"} 1'
        in rendered
    )
    assert "private text" not in rendered
    assert (
        "telegram_update_latency_seconds_count"
        '{action="/start",update_type="message"} 1'
        in rendered
    )


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
    )

    assert probe.snapshot().ready is True
