from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import server_supervisor as runtime  # noqa: E402

ENTRYPOINT_SOURCE_SHA = runtime._git_blob_sha(Path(__file__).resolve())


def _configured_socket_gid() -> int:
    raw_value = os.getenv("SERVER_SUPERVISOR_SOCKET_GID", str(os.getegid())).strip()
    try:
        gid = int(raw_value)
    except ValueError as error:
        raise SystemExit("SERVER_SUPERVISOR_SOCKET_GID must be an integer") from error
    if gid < 0:
        raise SystemExit("SERVER_SUPERVISOR_SOCKET_GID must not be negative")
    return gid


SOCKET_GID = _configured_socket_gid()


def _notify_systemd_ready() -> None:
    notify_socket = os.getenv("NOTIFY_SOCKET", "").strip()
    if not notify_socket:
        return
    address = f"\0{notify_socket[1:]}" if notify_socket.startswith("@") else notify_socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(
            b"READY=1\nSTATUS=Romatic Server Supervisor socket is ready"
        )


class GroupAwareUnixHTTPServer(runtime.UnixHTTPServer):
    """Create the control socket with the group shared by the proxy container."""

    def server_bind(self) -> None:
        super().server_bind()
        os.chown(self.server_address, -1, SOCKET_GID)
        os.chmod(self.server_address, runtime.SOCKET_MODE)
        _notify_systemd_ready()


def _target_blob_sha(target_sha: str, path: str) -> str:
    return runtime._git("rev-parse", f"{target_sha}:{path}").stdout.strip()


def _guard_resident_supervisor(target_sha: str) -> None:
    expected = {
        "scripts/server_supervisor.py": runtime.RESIDENT_SOURCE_SHA,
        "scripts/server_supervisor_entrypoint.py": ENTRYPOINT_SOURCE_SHA,
    }
    actual = {path: _target_blob_sha(target_sha, path) for path in expected}
    if actual != expected:
        raise RuntimeError(
            "Host Server Supervisor is running stale code; deployment aborted. "
            "Restart romatic-server-supervisor.service once from a trusted host "
            "after this version is installed, then retry deployment."
        )


def _service_is_healthy(status: dict[str, Any]) -> bool:
    return (
        status.get("running") is True
        and status.get("status") == "running"
        and int(status.get("restart_count") or 0) == 0
    )


def _status_payload() -> dict[str, Any]:
    snapshot = runtime.state.snapshot()
    operation = dict(snapshot.get("operation") or {})
    bot = runtime._container_status("bot")
    userbot = runtime._container_status("userbot")
    supervisor_proxy = runtime._container_status("supervisor-proxy")
    active_operation = operation if operation.get("status") == "running" else None
    last_operation = (
        operation
        if operation.get("id") and operation.get("status") != "running"
        else None
    )
    return {
        "ok": True,
        "runtime_healthy": all(
            _service_is_healthy(service)
            for service in (bot, userbot, supervisor_proxy)
        ),
        "pid": os.getpid(),
        "bot": bot,
        "userbot": userbot,
        "supervisor_proxy": supervisor_proxy,
        "git": runtime._git_status(),
        # Compatibility field retained for existing clients.
        "operation": operation,
        "active_operation": active_operation,
        "last_operation": last_operation,
        "rollback_sha": snapshot.get("rollback_sha", ""),
        "runtime": "romatic_server_supervisor",
    }


runtime.UnixHTTPServer = GroupAwareUnixHTTPServer
runtime._guard_resident_supervisor = _guard_resident_supervisor
runtime._status_payload = _status_payload


if __name__ == "__main__":
    runtime.main()
