from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.backup_archive import encrypt, verify

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shell_recovery_scripts_parse() -> None:
    for path in (
        "deploy/server/deploy.sh",
        "deploy/server/restore-drill.sh",
        "deploy/server/archive-backups.sh",
        "deploy/server/install-database-recovery-timers.sh",
    ):
        subprocess.run(
            ["bash", "-n", str(ROOT / path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_compose_has_one_controlled_schema_executor() -> None:
    compose = source("compose.yaml")
    bot = compose.split("  bot:", 1)[1].split("\n  userbot:", 1)[0]
    userbot = compose.split("  userbot:", 1)[1].split("\nvolumes:", 1)[0]
    runner = compose.split("  migration-runner:", 1)[1].split("\n  bot:", 1)[0]

    assert 'DB_AUTO_MIGRATE: "false"' in bot
    assert 'DB_AUTO_MIGRATE: "false"' in userbot
    assert 'DB_AUTO_MIGRATE: "false"' in runner
    assert "python\", \"-m\", \"db.migrator\", \"apply\", \"--json" in runner
    assert "profiles: [\"operations\"]" in runner
    assert "romatic-database" in runner
    assert "romatic-application" not in runner
    assert "romatic-supervisor-control" not in runner
    assert "cap_drop:\n      - ALL" in runner
    assert "no-new-privileges:true" in runner
    assert "restart: \"no\"" in runner


def test_restore_drill_is_disposable_and_not_host_published() -> None:
    compose = source("compose.yaml")
    service = compose.split("  restore-drill-postgres:", 1)[1].split(
        "\n  supervisor-proxy:", 1
    )[0]

    assert "profiles: [\"operations\"]" in service
    assert "/var/lib/postgresql/data" in service
    assert "tmpfs:" in service
    assert "restart: \"no\"" in service
    assert "ports:" not in service
    assert "restore-drill-postgres" in service


def test_deploy_orders_restore_and_migrations_before_runtime_replacement() -> None:
    deploy = source("deploy/server/deploy.sh")

    backup = deploy.index("Creating pre-deploy PostgreSQL dump")
    restore = deploy.index("Running disposable PostgreSQL restore drill")
    plan = deploy.index("Planning production migrations")
    apply = deploy.index("Applying production migrations")
    replace = deploy.index('up -d --remove-orphans postgres supervisor-proxy bot userbot')

    assert backup < restore < plan < apply < replace
    assert "migration-runner" in deploy
    assert "config.database.auto_migrate is False" in deploy
    assert "forward-fix-or-restore" in deploy
    assert "Automatic code rollback is blocked" in deploy
    assert "Database was not automatically restored" in deploy


def test_restore_drill_runs_full_restore_and_business_probes() -> None:
    drill = source("deploy/server/restore-drill.sh")

    assert "pg_restore" in drill
    assert "--exit-on-error" in drill
    assert "migration-runner python -m db.migrator apply --json" in drill
    assert "migration-runner python -m db.migrator verify --json" in drill
    assert "public.auctions" in drill
    assert "non_positive_message_ids" in drill
    assert "Disposable PostgreSQL restore drill passed" in drill
    assert "restore-drills" in drill


def test_recovery_runbook_defines_service_objectives_and_matrix() -> None:
    runbook = source("docs/runbooks/database-recovery.md")

    for required in (
        "RPO:",
        "RTO:",
        "Restore drill frequency:",
        "Rollback matrix",
        "code-only-safe",
        "forward-fix",
        "restore-required",
        "AES-256-GCM",
    ):
        assert required in runbook


def test_backup_archive_encrypts_and_detects_tampering(tmp_path: Path) -> None:
    source_path = tmp_path / "backup.dump"
    archive_path = tmp_path / "backup.dump.aes256gcm"
    key_path = tmp_path / "key"
    source_path.write_bytes((b"database-backup\n" * 4096) + b"tail")
    key_path.write_bytes(bytes(range(32)))

    encrypted = encrypt(source_path, archive_path, key_path)
    verified = verify(archive_path, key_path)

    assert encrypted["plaintext_sha256"] == verified["plaintext_sha256"]
    assert encrypted["plaintext_size"] == verified["plaintext_size"]
    assert archive_path.read_bytes() != source_path.read_bytes()

    tampered = bytearray(archive_path.read_bytes())
    tampered[-20] ^= 0x01
    archive_path.write_bytes(tampered)
    with pytest.raises(Exception):
        verify(archive_path, key_path)


def test_recovery_timers_are_persistent() -> None:
    restore_timer = source("deploy/systemd/romatic-restore-drill.timer")
    archive_timer = source("deploy/systemd/romatic-backup-archive.timer")

    assert "OnCalendar=Sun" in restore_timer
    assert "Persistent=true" in restore_timer
    assert "OnCalendar=*-*-*" in archive_timer
    assert "Persistent=true" in archive_timer
