from __future__ import annotations

import ast
from pathlib import Path

import db.migrations as compatibility
import db.migrator as runtime


ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_facade_delegates_to_runtime_runner() -> None:
    assert compatibility.apply_migrations is runtime.apply_migrations
    assert compatibility.migrate_database_url is runtime.migrate_database_url
    assert compatibility.MIGRATIONS_DIR == runtime.MIGRATIONS_DIR
    assert compatibility.migration_files() == [
        migration.path for migration in runtime._load_migrations()
    ]


def test_only_runtime_module_defines_apply_migrations() -> None:
    definitions: list[str] = []
    for path in sorted((ROOT / "db").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "apply_migrations"
            for node in ast.walk(tree)
        ):
            definitions.append(path.name)

    assert definitions == ["migrator.py"]


def test_production_and_maintenance_entrypoints_use_runtime_runner() -> None:
    lifecycle = (ROOT / "db/lifecycle.py").read_text(encoding="utf-8")
    uid_script = (ROOT / "scripts/migrate_uid_encryption.py").read_text(
        encoding="utf-8"
    )

    assert "from db.migrator import apply_migrations" in lifecycle
    assert "from db.migrator import apply_migrations" in uid_script
    assert "from db.migrations import apply_migrations" not in uid_script


def test_archived_history_is_not_packaged_as_runtime_data() -> None:
    packaging = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    archive_notice = (
        ROOT / "database/migrations/README.md"
    ).read_text(encoding="utf-8")

    assert 'database = ["*.sql", "README.md"]' in packaging
    assert 'db = ["migrations/*.sql"]' in packaging
    assert "не исполняются" in archive_notice
    assert "db/migrations" in archive_notice
