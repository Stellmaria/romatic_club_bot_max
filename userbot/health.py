"""File-backed readiness state for the userbot container."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from time import time
from typing import Any, Protocol

from bot.core.tasks import BackgroundTaskManager, WorkerContext, WorkerState

HEALTH_SCHEMA_VERSION = 1


class ConnectedClient(Protocol):
    def is_connected(self) -> bool: ...


def health_file_path(runtime_dir: Path) -> Path:
    return runtime_dir / "userbot-health.json"


def build_health_payload(
    *,
    status: str,
    connected: bool,
    authorized: bool,
    task_manager: BackgroundTaskManager | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    workers: list[dict[str, Any]] = []
    if task_manager is not None:
        workers = [
            {
                "name": item.name,
                "criticality": item.criticality.value,
                "state": item.state.value,
                "failures": item.failures,
                "restarts": item.restarts,
                "last_started_at": item.last_started_at,
                "last_success_at": item.last_success_at,
                "last_heartbeat_at": item.last_heartbeat_at,
                "last_error": item.last_error,
            }
            for item in task_manager.health()
        ]
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "status": status,
        "updated_at_epoch": time(),
        "connected": connected,
        "authorized": authorized,
        "workers": workers,
        "error": error,
    }


def write_health(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def readiness_status(task_manager: BackgroundTaskManager, *, connected: bool) -> str:
    if not connected:
        return "failed"
    if any(item.state is not WorkerState.RUNNING for item in task_manager.health()):
        return "degraded"
    return "ready"


async def health_reporter_loop(
    context: WorkerContext,
    *,
    task_manager: BackgroundTaskManager,
    telegram_client: ConnectedClient,
    path: Path,
    interval_seconds: float = 5.0,
) -> None:
    while True:
        connected = bool(telegram_client.is_connected())
        status = readiness_status(task_manager, connected=connected)
        write_health(
            path,
            build_health_payload(
                status=status,
                connected=connected,
                authorized=True,
                task_manager=task_manager,
            ),
        )
        context.heartbeat()
        await asyncio.sleep(interval_seconds)


__all__ = [
    "HEALTH_SCHEMA_VERSION",
    "build_health_payload",
    "health_file_path",
    "health_reporter_loop",
    "readiness_status",
    "write_health",
]
