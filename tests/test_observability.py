from __future__ import annotations

import logging

from bot.core.logging import JsonLogFormatter
from bot.core.observability import HealthProbeServer, MetricsRegistry, redact
from bot.core.tasks import BackgroundTaskManager


def test_redact_masks_sensitive_mapping_keys_recursively() -> None:
    payload = {
        "token": "secret-token",
        "nested": {"phone": "+79990000000", "safe": "visible"},
        "items": [{"session": "bytes"}],
    }

    assert redact(payload) == {
        "token": "[REDACTED]",
        "nested": {"phone": "[REDACTED]", "safe": "visible"},
        "items": [{"session": "[REDACTED]"}],
    }


def test_json_formatter_has_schema_and_redacts_extras() -> None:
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
    record.bot_token = "must-not-leak"

    rendered = JsonLogFormatter().format(record)

    assert '"schema_version": 1' in rendered
    assert '"correlation_id": "cid-1"' in rendered
    assert "must-not-leak" not in rendered
    assert "[REDACTED]" in rendered


def test_metrics_registry_renders_prometheus_values() -> None:
    metrics = MetricsRegistry()
    metrics.increment("updates_total", result="ok")
    metrics.gauge("outbox_backlog", 3, queue="telegram")

    rendered = metrics.render_prometheus()

    assert 'updates_total{result="ok"} 1' in rendered
    assert 'outbox_backlog{queue="telegram"} 3' in rendered


def test_readiness_requires_database_and_running_workers() -> None:
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
