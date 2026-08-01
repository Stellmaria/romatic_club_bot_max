#!/usr/bin/env python3
"""Enforce the single PostgreSQL runtime and low-level import boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (ROOT / "db", ROOT / "bot")
ADAPTER_ROOTS = (ROOT / "bot/handlers", ROOT / "bot/domain")
FORBIDDEN_ADAPTER_MODULES = {
    "db.core",
    "db.pool",
    "db.lifecycle",
    "db.legacy_impl",
}
FORBIDDEN_DB_FROM_NAMES = {"core", "pool", "lifecycle", "legacy_impl"}


def _python_files(roots: tuple[Path, ...]):
    for root in roots:
        if not root.exists():
            continue
        yield from root.rglob("*.py")


def _forbidden_imports(tree: ast.AST) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_ADAPTER_MODULES:
                    violations.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module in FORBIDDEN_ADAPTER_MODULES:
                violations.append((node.lineno, node.module or ""))
            elif node.module == "db":
                for alias in node.names:
                    if alias.name in FORBIDDEN_DB_FROM_NAMES:
                        violations.append((node.lineno, f"db.{alias.name}"))
    return violations


def _asyncpg_pool_factory_references(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncpg"
            and node.attr == "create_pool"
        ):
            lines.append(node.lineno)
    return lines


def main() -> int:
    violations: list[str] = []

    for path in sorted(_python_files(ADAPTER_ROOTS)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _forbidden_imports(tree):
            violations.append(
                f"{path.relative_to(ROOT)}:{line}: adapter imports low-level {module}"
            )

    pool_owners: list[tuple[Path, int]] = []
    for path in sorted(_python_files(RUNTIME_ROOTS)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line in _asyncpg_pool_factory_references(tree):
            pool_owners.append((path, line))

    expected_owner = ROOT / "db/pool.py"
    for path, line in pool_owners:
        if path != expected_owner:
            violations.append(
                f"{path.relative_to(ROOT)}:{line}: asyncpg.create_pool outside DatabaseRuntime"
            )
    if len(pool_owners) != 1 or pool_owners[0][0] != expected_owner:
        rendered = ", ".join(
            f"{path.relative_to(ROOT)}:{line}" for path, line in pool_owners
        ) or "none"
        violations.append(
            "exactly one asyncpg.create_pool owner is required in db/pool.py; "
            f"found {rendered}"
        )

    retired = ROOT / "db/legacy_impl.py"
    if retired.exists():
        violations.append("db/legacy_impl.py must not exist")

    if violations:
        print("Database boundary contract failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Database boundary contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
