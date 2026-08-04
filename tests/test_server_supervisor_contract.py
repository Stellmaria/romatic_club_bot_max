from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SUPERVISOR_SPEC = importlib.util.spec_from_file_location(
    "server_supervisor_under_test", ROOT / "scripts/server_supervisor.py"
)
assert _SUPERVISOR_SPEC and _SUPERVISOR_SPEC.loader
server_supervisor = importlib.util.module_from_spec(_SUPERVISOR_SPEC)
sys.modules[_SUPERVISOR_SPEC.name] = server_supervisor
_SUPERVISOR_SPEC.loader.exec_module(server_supervisor)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_server_runtime_exposes_only_fixed_actions() -> None:
    runtime = source("scripts/server_supervisor.py")

    for route in (
        '"/v1/status"',
        '"/v1/logs"',
        '"/v1/restart"',
        '"/v1/restart-userbot"',
        '"/v1/update"',
        '"/v1/rollback"',
    ):
        assert route in runtime
    assert '"restart", "bot"' in runtime
    assert '"restart", "userbot"' in runtime
    assert '["bash", "deploy/server/deploy.sh"]' in runtime
    assert "shell=True" not in runtime
    assert "docker.sock" not in runtime


def test_supervisor_guards_resident_source_before_invoking_deploy() -> None:
    runtime = source("scripts/server_supervisor.py")

    assert "RESIDENT_SOURCE_SHA = _git_blob_sha(Path(__file__).resolve())" in runtime
    guard = runtime.index("_guard_resident_supervisor(target_sha)")
    invoke = runtime.index('["bash", "deploy/server/deploy.sh"]', guard)
    assert guard < invoke
    assert "Host Server Supervisor is running stale code" in runtime
    assert "Restart romatic-server-supervisor.service once" in runtime
    assert runtime.index("_guard_resident_supervisor(rollback_sha)") < runtime.index(
        '["bash", "deploy/server/deploy.sh"]',
        runtime.index("_guard_resident_supervisor(rollback_sha)"),
    )


def test_update_rebuilds_hardened_postgres_and_application_services() -> None:
    runtime = source("scripts/server_supervisor.py")
    deploy = source("deploy/server/deploy.sh")

    target_guard = runtime.index("_guard_resident_supervisor(target_sha)")
    deploy_invocation = runtime.index('["bash", "deploy/server/deploy.sh"]', target_guard)
    target_environment = runtime.index('"ROMATIC_DEPLOY_TARGET_SHA": target_sha', deploy_invocation)
    assert target_guard < deploy_invocation < target_environment
    assert "build --pull postgres bot userbot supervisor-proxy" in deploy
    assert "up -d --remove-orphans postgres supervisor-proxy bot userbot" in deploy
    assert "wait_service bot" in deploy
    assert "wait_service userbot" in deploy
    assert "wait_service supervisor-proxy" in deploy


def test_supervisor_rejects_target_with_different_resident_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_supervisor, "RESIDENT_SOURCE_SHA", "resident")

    def fake_git(*args: str, **kwargs: object) -> server_supervisor.CommandResult:
        assert args == ("rev-parse", "target:scripts/server_supervisor.py")
        return server_supervisor.CommandResult("target", "", 0)

    monkeypatch.setattr(server_supervisor, "_git", fake_git)

    with pytest.raises(RuntimeError, match="running stale code"):
        server_supervisor._guard_resident_supervisor("target")


def test_proxy_is_isolated_from_docker_and_checkout() -> None:
    compose = source("compose.yaml")
    service = compose.split("  supervisor-proxy:", 1)[1].split("\n  bot:", 1)[0]

    assert "Dockerfile.server-supervisor-proxy" in service
    assert "read_only: true" in service
    assert ('user: "${ROMATIC_SUPERVISOR_GID:-10001}:${ROMATIC_SUPERVISOR_GID:-10001}"') in service
    assert "cap_drop:\n      - ALL" in service
    assert "no-new-privileges:true" in service
    assert "docker.sock" not in service
    assert "/srv/romatic-club-max" not in service
    assert "ports:" not in service


def test_restart_targets_are_separate_but_update_rebuilds_runtime_images() -> None:
    runtime = source("scripts/server_supervisor.py")
    deploy = source("deploy/server/deploy.sh")

    assert "def _restart_bot()" in runtime
    assert '_compose("restart", "bot")' in runtime
    assert "def _restart_userbot()" in runtime
    assert '_compose("restart", "userbot")' in runtime
    assert '"/v1/restart-userbot": ("userbot-restart", _restart_userbot)' in runtime
    assert "build --pull postgres bot userbot supervisor-proxy" in deploy
    assert "up -d --remove-orphans postgres supervisor-proxy bot userbot" in deploy


def test_deploy_keeps_backup_health_and_rollback_gates() -> None:
    deploy = source("deploy/server/deploy.sh")

    assert "pg_dump" in deploy
    assert "pg_restore -l" in deploy
    assert 'exec -T postgres pg_restore -l < "$backup_path"' in deploy
    assert 'cat "$backup_path" |' not in deploy
    assert "git merge-base --is-ancestor" in deploy
    assert "ROMATIC_DEPLOY_TARGET_SHA" in deploy
    assert "Romatic server smoke OK" in deploy
    assert "rolling application code back" in deploy
    assert "DOCKER_CONFIG" in deploy
    assert "COMPOSE_BAKE=false" in deploy


def test_postgres_image_matches_production_major_version() -> None:
    compose = source("compose.yaml")
    env_example = source(".env.example")
    postgres_dockerfile = source("Dockerfile.postgres")

    assert "${POSTGRES_IMAGE:-romatic-postgres:17-alpine-hardened}" in compose
    assert "dockerfile: Dockerfile.postgres" in compose
    assert "POSTGRES_IMAGE=romatic-postgres:17-alpine-hardened" in env_example
    assert postgres_dockerfile.startswith("FROM postgres:17-alpine@sha256:")
    assert "PG_MAJOR=17" in postgres_dockerfile
    assert "postgres:16-alpine" not in compose
    assert "postgres:16-alpine" not in postgres_dockerfile


def test_systemd_runtime_stays_unprivileged() -> None:
    unit = source("deploy/systemd/romatic-server-supervisor.service")

    assert "User=%SERVICE_USER%" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ReadWritePaths=%APP_DIR% %DATA_DIR% /tmp" in unit
    assert "DOCKER_CONFIG=%DATA_DIR%/runtime/docker-config" in unit
    assert "User=root" not in unit


def test_installer_generates_token_and_runtime_directories() -> None:
    installer = source("deploy/server/install-server-supervisor.sh")

    assert '"SUPERVISOR_ENABLED": "true"' in installer
    assert "secrets.token_urlsafe(48)" in installer
    assert "runtime/docker-config" in installer
    assert "systemctl restart romatic-server-supervisor.service" in installer
    assert "systemctl reload romatic-compose.service" in installer


def test_deploy_snapshots_and_restores_telethon_session_transactionally() -> None:
    deploy = source("deploy/server/deploy.sh")

    assert "snapshot_userbot_session" in deploy
    assert 'sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)' in deploy
    assert "source.backup(target)" in deploy
    assert "PRAGMA quick_check" in deploy
    assert "Target Telethon session compatibility preflight OK" in deploy
    assert 'SQLiteSession("/tmp/session-probe/userbot")' in deploy
    assert "restore_userbot_session" in deploy
    assert "Telethon session restore failed; userbot will remain stopped." in deploy
    assert deploy.index("snapshot_userbot_session") < deploy.index('git reset --hard "$target_sha"')


def test_deploy_session_transfer_respects_hardened_application_uid() -> None:
    deploy = source("deploy/server/deploy.sh")

    assert '--user "$app_uid:$app_gid"' in deploy
    assert "--user 0:0" not in deploy
    assert "sys.stdout.buffer.write(target_path.read_bytes())" in deploy
    assert 'cat "$session_snapshot_path" |' in deploy
    assert '-v "$data_dir/backups:/backup"' not in deploy
    assert '-v "$session_snapshot_path:' not in deploy
    assert 'chmod 0600 "$snapshot_tmp"' in deploy


def test_panel_does_not_report_pidless_or_restarting_container_as_healthy() -> None:
    panel = source("bot/handlers/admin/admin_panel_system.py")

    assert 'status == "running"' in panel
    assert "isinstance(pid, int)" in panel
    assert "pid > 0" in panel
    assert 'health in {"", "healthy"}' in panel
    assert 'status == "restarting"' in panel
    assert 'return "⚠️", f"не здоров' in panel
