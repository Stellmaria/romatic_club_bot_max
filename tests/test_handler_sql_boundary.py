from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "bot" / "handlers"

SQL_PATTERN = re.compile(
    r"(?:\bSELECT\b.+\bFROM\b|\bINSERT\s+INTO\b|"
    r"\bUPDATE\s+(?:public\.)?[a-z_]\w*\s+SET\b|"
    r"\bDELETE\s+FROM\b|\bWITH\s+[a-z_]\w*\s+AS\s*\()",
    re.IGNORECASE | re.DOTALL,
)


def _sql_string_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SQL_PATTERN.search(node.value)
    ]


def test_handlers_do_not_own_postgresql_queries() -> None:
    violations: dict[str, list[int]] = {}
    for path in sorted(HANDLERS.rglob("*.py")):
        relative = path.relative_to(HANDLERS)
        lines = _sql_string_lines(path)
        if lines:
            violations[relative.as_posix()] = lines

    assert violations == {}


def test_completed_handler_migrations_do_not_use_legacy_query_helpers() -> None:
    completed = (
        "admin/helper/new/admin_actions.py",
        "admin/moderation_diagnostics.py",
        "admin/services/schedule.py",
        "admin/admin_panel_shared.py",
        "auction/admin_lifecycle.py",
        "admin/helper/admin_constants.py",
        "users.py",
    )
    for relative in completed:
        path = HANDLERS / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "db.core" not in imported_modules, relative
        assert _sql_string_lines(path) == [], relative
