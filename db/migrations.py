"""Backward-compatible facade for the single runtime migration system.

New code must import :mod:`db.migrator` directly.  This module intentionally
contains no database lock, checksum ledger or migration execution logic of its
own; it only preserves historical imports while delegating to the canonical
runner and the canonical ``db/migrations`` directory.
"""

from __future__ import annotations

from pathlib import Path

from db.migrator import (
    MIGRATIONS_DIR,
    Migration,
    _load_migrations,
    apply_migrations,
    migrate_database_url,
)


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return paths discovered and validated by the canonical runner."""

    return [migration.path for migration in _load_migrations(directory)]


__all__ = [
    "MIGRATIONS_DIR",
    "Migration",
    "apply_migrations",
    "migrate_database_url",
    "migration_files",
]
