from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_systemd_waits_for_supervisor_socket_readiness() -> None:
    unit = source("deploy/systemd/romatic-server-supervisor.service")
    entrypoint = source("scripts/server_supervisor_entrypoint.py")

    assert "Type=notify" in unit
    assert "NotifyAccess=main" in unit
    assert "TimeoutStartSec=30" in unit
    assert 'os.getenv("NOTIFY_SOCKET", "")' in entrypoint
    assert 'b"READY=1\\nSTATUS=Romatic Server Supervisor socket is ready"' in entrypoint


def test_ready_notification_follows_socket_permissions() -> None:
    entrypoint = source("scripts/server_supervisor_entrypoint.py")

    chown = entrypoint.index("os.chown(self.server_address, -1, SOCKET_GID)")
    chmod = entrypoint.index("os.chmod(self.server_address, runtime.SOCKET_MODE)")
    ready = entrypoint.index("_notify_systemd_ready()", chmod)

    assert chown < chmod < ready
