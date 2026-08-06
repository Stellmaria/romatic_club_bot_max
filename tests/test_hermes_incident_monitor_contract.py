from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/hermes_incident_monitor.py"
spec = importlib.util.spec_from_file_location("max_hermes_incident_monitor_test", MODULE_PATH)
assert spec and spec.loader
monitor_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = monitor_mod
spec.loader.exec_module(monitor_mod)


def _monitor(previous: dict[str, object] | None = None):
    monitor = monitor_mod.Monitor.__new__(monitor_mod.Monitor)
    monitor._state = {"probes": previous or {}}
    monitor._not_running_counts = {}
    monitor._initial_not_running = set()
    monitor._unhealthy_counts = {}
    monitor.not_running_polls = 2
    monitor.unhealthy_polls = 2
    monitor.cooldown_seconds = 600
    monitor._active_status = "idle"
    monitor._last_event_key = ""
    monitor._last_event_at = 0.0
    return monitor


def test_restart_count_increase_is_an_incident() -> None:
    monitor = _monitor(
        {
            "bot": {
                "container_id": "same",
                "running": True,
                "health": "healthy",
                "restart_count": 1,
            }
        }
    )
    probe = monitor_mod.Probe("bot", "same", True, "running", "healthy", 2, 0)
    assert monitor.reason(probe) == "container-auto-restarted"


def test_healthy_container_recreation_is_not_an_incident() -> None:
    monitor = _monitor(
        {
            "bot": {
                "container_id": "old",
                "running": True,
                "health": "healthy",
                "restart_count": 0,
            }
        }
    )
    probe = monitor_mod.Probe("bot", "new", True, "running", "healthy", 0, 0)
    assert monitor.reason(probe) is None


def test_not_running_requires_consecutive_polls() -> None:
    monitor = _monitor(
        {
            "bot": {
                "container_id": "old",
                "running": True,
                "health": "healthy",
                "restart_count": 0,
            }
        }
    )
    probe = monitor_mod.Probe("bot", None, False, "missing", None, 0, None)
    assert monitor.reason(probe) is None
    assert monitor.reason(probe) == "container-not-running"


def test_transient_not_running_probe_is_cleared_by_recovery() -> None:
    monitor = _monitor(
        {
            "bot": {
                "container_id": "old",
                "running": True,
                "health": "healthy",
                "restart_count": 0,
            }
        }
    )
    missing = monitor_mod.Probe("bot", None, False, "missing", None, 0, None)
    healthy = monitor_mod.Probe("bot", "new", True, "running", "healthy", 0, 0)

    assert monitor.reason(missing) is None
    assert monitor.reason(healthy) is None
    assert monitor.reason(missing) is None


def test_initial_not_running_requires_consecutive_polls() -> None:
    monitor = _monitor()
    probe = monitor_mod.Probe("bot", None, False, "missing", None, 0, None)
    assert monitor.reason(probe) is None
    assert monitor.reason(probe) == "initial-not-running"


def test_unhealthy_requires_consecutive_polls() -> None:
    monitor = _monitor(
        {
            "userbot": {
                "container_id": "same",
                "running": True,
                "health": "healthy",
                "restart_count": 0,
            }
        }
    )
    probe = monitor_mod.Probe("userbot", "same", True, "running", "unhealthy", 0, 0)
    assert monitor.reason(probe) is None
    assert monitor.reason(probe) == "container-unhealthy"


def test_redaction_removes_credentials() -> None:
    value = monitor_mod.redact(
        "Authorization: Bearer secret-secret-secret "
        "DATABASE_URL=postgresql://user:db-password@postgres:5432/card_hunter "
        "BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    )
    assert "secret-secret-secret" not in value
    assert "db-password" not in value
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in value


def test_active_run_blocks_duplicate_submission() -> None:
    monitor = _monitor()
    monitor._active_status = "running"
    assert monitor._active_status in monitor_mod.ACTIVE_STATUSES


def test_cooldown_data_is_stable() -> None:
    monitor = _monitor()
    probe = monitor_mod.Probe("bot", "container", False, "exited", None, 3, 1)
    key = monitor._event_key(probe, "container-not-running")
    monitor._last_event_key = key
    monitor._last_event_at = time.time()
    assert key == monitor._last_event_key
    assert time.time() - monitor._last_event_at < monitor.cooldown_seconds


def test_monitor_has_only_read_only_runtime_commands() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'self._compose("ps", "-q", service)' in source
    assert 'self._compose("logs", "--no-color"' in source
    for forbidden in (
        'self._compose("restart"',
        'self._compose("up"',
        'self._compose("down"',
        'self._compose("stop"',
        'self._compose("rm"',
        "systemctl",
        "docker.sock",
    ):
        assert forbidden not in source


def test_systemd_unit_is_sandboxed() -> None:
    unit = (
        ROOT / "deploy/systemd/romatic-hermes-incident-monitor.service"
    ).read_text(encoding="utf-8")
    assert "User=velvet" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "PrivateTmp=true" in unit
    assert "EnvironmentFile=/srv/hermes-operator-control/incident.env" in unit
    assert "User=root" not in unit
