from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_STATEMENT = re.compile(
    r"\b(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+(?:public\.)?|"
    r"DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b",
    re.IGNORECASE | re.DOTALL,
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _python_files(relative: str) -> list[Path]:
    return sorted((ROOT / relative).rglob("*.py"))


def _sql_literals(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SQL_STATEMENT.search(node.value)
    ]


def _driver_calls(path: Path) -> list[tuple[int, str]]:
    """Find low-level connection operations, not repository transactions."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    operations = {"acquire", "fetch", "fetchrow", "fetchval", "execute", "executemany"}
    return [
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in operations
    ]


def test_domain_has_no_framework_or_infrastructure_dependencies() -> None:
    forbidden_roots = {"aiogram", "asyncpg", "telethon", "flask", "db"}
    for path in _python_files("bot/domain"):
        roots = {name.split(".", 1)[0].lower() for name in _imports(path)}
        assert not (roots & forbidden_roots), path.relative_to(ROOT)


def test_application_layers_do_not_depend_on_handlers_or_legacy_db() -> None:
    for layer in ("bot/features", "bot/services", "bot/repositories"):
        for path in _python_files(layer):
            imports = _imports(path)
            forbidden = {
                name
                for name in imports
                if name == "db.db" or name.startswith("bot.handlers")
            }
            assert not forbidden, f"{path.relative_to(ROOT)}: {sorted(forbidden)}"


def test_database_infrastructure_does_not_depend_on_handlers() -> None:
    for path in _python_files("db"):
        forbidden = {
            name for name in _imports(path) if name.startswith("bot.handlers")
        }
        assert not forbidden, f"{path.relative_to(ROOT)}: {sorted(forbidden)}"


def test_repositories_are_framework_neutral() -> None:
    forbidden_roots = {"aiogram", "flask", "telethon"}
    for path in _python_files("bot/repositories"):
        roots = {name.split(".", 1)[0].lower() for name in _imports(path)}
        assert not (roots & forbidden_roots), path.relative_to(ROOT)


def test_services_delegate_sql_and_low_level_driver_operations_to_repositories() -> None:
    for path in _python_files("bot/services"):
        relative = path.relative_to(ROOT)
        roots = {name.split(".", 1)[0].lower() for name in _imports(path)}
        assert "asyncpg" not in roots, relative
        assert _sql_literals(path) == [], relative
        assert _driver_calls(path) == [], relative


def test_infrastructure_bridges_do_not_import_handlers() -> None:
    for path in _python_files("bot/bridges"):
        imports = _imports(path)
        forbidden = sorted(name for name in imports if name.startswith("bot.handlers"))
        assert forbidden == [], f"{path.relative_to(ROOT)}: {forbidden}"


def test_bridges_delegate_database_access_to_gateways() -> None:
    for path in _python_files("bot/bridges"):
        relative = path.relative_to(ROOT)
        roots = {name.split(".", 1)[0].lower() for name in _imports(path)}
        assert "asyncpg" not in roots, relative
        assert _sql_literals(path) == [], relative
        assert _driver_calls(path) == [], relative


def test_handlers_do_not_import_the_legacy_database_facade() -> None:
    """Local imports are included so the boundary cannot be bypassed."""

    offenders = []
    for path in _python_files("bot/handlers"):
        if "db.db" in _imports(path):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_production_uses_package_modules_not_root_compatibility_facades() -> None:
    forbidden = {"config", "db.db", "fsm_states"}
    violations: dict[str, list[str]] = {}
    roots = ("bot", "db", "scripts", "userbot")
    paths = [path for root in roots for path in _python_files(root)]
    paths.extend(
        ROOT / name
        for name in (
            "backfill.py",
            "find_discussion_id.py",
            "main.py",
            "migrate_uid_encryption.py",
        )
    )
    for path in paths:
        legacy = sorted(_imports(path) & forbidden)
        if legacy:
            violations[path.relative_to(ROOT).as_posix()] = legacy

    assert violations == {}
