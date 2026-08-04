from __future__ import annotations

from pathlib import Path

import pytest

from db.migrator import (
    MigrationCompatibility,
    RollbackStrategy,
    _load_migrations,
)


def write_migration(directory: Path, name: str, sql: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(sql, encoding="utf-8")


def test_legacy_migration_gets_conservative_policy(tmp_path: Path) -> None:
    write_migration(tmp_path, "019_legacy.sql", "SELECT 1;")

    migration = _load_migrations(tmp_path)[0]

    assert migration.policy.compatibility is MigrationCompatibility.LEGACY
    assert migration.policy.rollback is RollbackStrategy.RESTORE_REQUIRED
    assert migration.policy.code_rollback_safe is False
    assert "verified pre-deploy backup" in migration.policy.note


def test_new_expand_migration_requires_explicit_policy(tmp_path: Path) -> None:
    write_migration(
        tmp_path,
        "020_add_nullable_column.sql",
        """-- compatibility: expand
-- rollback: code-only-safe
-- note: Adds a nullable column ignored by the old application release.
ALTER TABLE example ADD COLUMN optional_value text;
""",
    )

    migration = _load_migrations(tmp_path)[0]

    assert migration.policy.compatibility is MigrationCompatibility.EXPAND
    assert migration.policy.rollback is RollbackStrategy.CODE_ONLY_SAFE
    assert migration.policy.code_rollback_safe is True


def test_new_migration_without_policy_is_rejected(tmp_path: Path) -> None:
    write_migration(tmp_path, "020_missing_policy.sql", "SELECT 1;")

    with pytest.raises(RuntimeError, match="required policy metadata"):
        _load_migrations(tmp_path)


def test_contract_migration_policy_is_preserved(tmp_path: Path) -> None:
    write_migration(
        tmp_path,
        "020_drop_old_column.sql",
        """-- compatibility: contract
-- rollback: restore-required
-- note: Drops a legacy column after every old application instance is removed.
ALTER TABLE example DROP COLUMN old_value;
""",
    )

    migration = _load_migrations(tmp_path)[0]

    assert migration.policy.compatibility is MigrationCompatibility.CONTRACT
    assert migration.policy.rollback is RollbackStrategy.RESTORE_REQUIRED


def test_new_migration_cannot_claim_legacy_policy(tmp_path: Path) -> None:
    write_migration(
        tmp_path,
        "020_bad_legacy.sql",
        """-- compatibility: legacy
-- rollback: restore-required
-- note: This is intentionally invalid metadata for a new migration.
SELECT 1;
""",
    )

    with pytest.raises(RuntimeError, match="cannot declare compatibility=legacy"):
        _load_migrations(tmp_path)
