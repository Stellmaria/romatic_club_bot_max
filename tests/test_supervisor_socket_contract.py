from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_ENTRYPOINT_SPEC = importlib.util.spec_from_file_location(
    "server_supervisor_entrypoint_under_test",
    SCRIPTS / "server_supervisor_entrypoint.py",
)
assert _ENTRYPOINT_SPEC and _ENTRYPOINT_SPEC.loader
entrypoint = importlib.util.module_from_spec(_ENTRYPOINT_SPEC)
sys.modules[_ENTRYPOINT_SPEC.name] = entrypoint
_ENTRYPOINT_SPEC.loader.exec_module(entrypoint)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def healthy_service(pid: int) -> dict[str, Any]:
    return {
        "running": True,
        "pid": pid,
        "status": "running",
        "started_at": "2026-08-01T21:26:18Z",
        "restart_count": 0,
    }


def test_entrypoint_assigns_configured_group_to_unix_socket() -> None:
    runtime = source("scripts/server_supervisor_entrypoint.py")

    assert 'os.getenv("SERVER_SUPERVISOR_SOCKET_GID"' in runtime
    assert "os.chown(self.server_address, -1, SOCKET_GID)" in runtime
    assert "os.chmod(self.server_address, runtime.SOCKET_MODE)" in runtime


def test_resident_guard_covers_runtime_and_entrypoint() -> None:
    runtime = source("scripts/server_supervisor_entrypoint.py")

    assert '"scripts/server_supervisor.py": runtime.RESIDENT_SOURCE_SHA' in runtime
    assert '"scripts/server_supervisor_entrypoint.py": ENTRYPOINT_SOURCE_SHA' in runtime
    assert "actual != expected" in runtime


def test_status_separates_runtime_health_from_last_failed_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_operation = {
        "id": "old-update",
        "kind": "update",
        "status": "error",
        "message": "historical failure",
    }
    monkeypatch.setattr(
        entrypoint.runtime.state,
        "snapshot",
        lambda: {"operation": previous_operation, "rollback_sha": "abc123"},
    )
    services = {
        "bot": healthy_service(101),
        "userbot": healthy_service(102),
        "supervisor-proxy": healthy_service(103),
    }
    monkeypatch.setattr(
        entrypoint.runtime,
        "_container_status",
        lambda service: dict(services[service]),
    )
    monkeypatch.setattr(
        entrypoint.runtime,
        "_git_status",
        lambda: {"branch": "main", "commit": "deadbeef", "clean": True},
    )

    payload = entrypoint._status_payload()

    assert payload["runtime_healthy"] is True
    assert payload["active_operation"] is None
    assert payload["last_operation"] == previous_operation
    assert payload["operation"] == previous_operation
    assert payload["supervisor_proxy"]["restart_count"] == 0


def test_running_operation_is_reported_as_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_operation = {
        "id": "current-update",
        "kind": "update",
        "status": "running",
    }
    monkeypatch.setattr(
        entrypoint.runtime.state,
        "snapshot",
        lambda: {"operation": current_operation, "rollback_sha": ""},
    )
    monkeypatch.setattr(
        entrypoint.runtime,
        "_container_status",
        lambda service: healthy_service(hash(service) & 0xFFFF),
    )
    monkeypatch.setattr(
        entrypoint.runtime,
        "_git_status",
        lambda: {"branch": "main", "commit": "deadbeef", "clean": True},
    )

    payload = entrypoint._status_payload()

    assert payload["active_operation"] == current_operation
    assert payload["last_operation"] is None


def test_proxy_healthcheck_reaches_host_supervisor() -> None:
    compose = source("compose.yaml")
    service = compose.split("  supervisor-proxy:", 1)[1].split("\n  bot:", 1)[0]

    assert "http://127.0.0.1:8765/health" in service
    assert "urllib.request.urlopen" in service
    assert "payload.get('ok') is True" in service
    assert "socket.create_connection(('127.0.0.1',8765),2)" not in service


def test_installer_reuses_existing_gid_and_verifies_both_processes() -> None:
    installer = source("deploy/server/install-server-supervisor.sh")

    assert 'existing_group="$(getent group "$SUPERVISOR_GID"' in installer
    assert 'SUPERVISOR_GROUP="$existing_group"' in installer
    assert 'f"SERVER_SUPERVISOR_SOCKET_GID={supervisor_gid}"' in installer
    assert 'service_gid="$(ps -o egid=' in installer
    assert 'supervisor-proxy id -g' in installer
    assert 'metadata.st_gid != expected_gid' in installer


def test_systemd_uses_group_aware_entrypoint() -> None:
    unit = source("deploy/systemd/romatic-server-supervisor.service")

    assert "Group=%SUPERVISOR_GROUP%" in unit
    assert "Environment=SERVER_SUPERVISOR_SOCKET_GID=%SUPERVISOR_GID%" in unit
    assert "scripts/server_supervisor_entrypoint.py" in unit
