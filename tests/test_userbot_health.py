from __future__ import annotations

from pathlib import Path
from time import time
from types import SimpleNamespace

from bot.core.tasks import WorkerCriticality, WorkerState
from userbot.health import build_health_payload, readiness_status, write_health
from userbot.healthcheck import check_health


class FakeManager:
    def __init__(self, state: WorkerState) -> None:
        self.state = state

    def health(self):
        return (
            SimpleNamespace(
                name="watchdog",
                criticality=WorkerCriticality.CRITICAL,
                state=self.state,
                failures=1 if self.state is WorkerState.FAILED else 0,
                restarts=0,
                last_started_at=1.0,
                last_success_at=None,
                last_heartbeat_at=1.0,
                last_error="boom" if self.state is WorkerState.FAILED else None,
            ),
        )


def test_failed_watchdog_is_reflected_in_readiness() -> None:
    manager = FakeManager(WorkerState.FAILED)
    assert readiness_status(manager, connected=True) == "degraded"
    payload = build_health_payload(
        status="degraded",
        connected=True,
        authorized=True,
        task_manager=manager,
    )
    assert payload["workers"][0]["state"] == "failed"
    assert payload["workers"][0]["last_error"] == "boom"


def test_healthcheck_requires_fresh_ready_state(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    write_health(
        path,
        {
            "schema_version": 1,
            "status": "ready",
            "updated_at_epoch": time(),
            "connected": True,
            "authorized": True,
            "workers": [],
            "error": None,
        },
    )
    assert check_health(path, max_age_seconds=30)[0] is True

    write_health(
        path,
        {
            "schema_version": 1,
            "status": "degraded",
            "updated_at_epoch": time(),
            "connected": True,
            "authorized": True,
            "workers": [],
            "error": None,
        },
    )
    assert check_health(path, max_age_seconds=30)[0] is False
