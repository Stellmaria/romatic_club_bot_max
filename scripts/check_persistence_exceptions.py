#!/usr/bin/env python3
"""Reject new silent persistence failures.

The large legacy modules are being removed incrementally. Active boundaries are
strict now, while the repository-wide rule already forbids the worst form:
catching a broad exception and doing nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRICT_FILES = {
    ROOT / "db/core.py",
    ROOT / "db/errors.py",
    ROOT / "db/users.py",
    ROOT / "db/admin.py",
    ROOT / "db/reliable_mutations.py",
}
PERSISTENCE_ROOTS = (ROOT / "db", ROOT / "bot/repositories")
DIRECT_LEGACY_IMPORT_ALLOWLIST = {
    ROOT / "db/core.py",
    ROOT / "db/legacy.py",
}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in {"Exception", "BaseException"}
    if isinstance(handler.type, ast.Tuple):
        return any(
            isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"}
            for item in handler.type.elts
        )
    return False


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return not handler.body or all(
        isinstance(node, (ast.Pass, ast.Continue)) for node in handler.body
    )


def _imports_legacy_impl(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "db.legacy_impl" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "db" and any(
                alias.name == "legacy_impl" for alias in node.names
            ):
                return True
            if node.module == "db.legacy_impl":
                return True
    return False


def main() -> int:
    violations: list[str] = []
    files = sorted(
        path
        for root in PERSISTENCE_ROOTS
        for path in root.rglob("*.py")
        if "migrations" not in path.parts
    )

    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_broad(node):
                continue
            relative = path.relative_to(ROOT)
            if path in STRICT_FILES:
                violations.append(
                    f"{relative}:{node.lineno}: broad persistence exception is forbidden"
                )
            elif _is_silent(node):
                violations.append(
                    f"{relative}:{node.lineno}: silent broad persistence exception"
                )

        if path not in DIRECT_LEGACY_IMPORT_ALLOWLIST and _imports_legacy_impl(tree):
            violations.append(
                f"{path.relative_to(ROOT)}: direct db.legacy_impl import is forbidden"
            )

    if violations:
        print("Persistence exception contract failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Persistence exception contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
