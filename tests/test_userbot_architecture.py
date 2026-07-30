from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USERBOT = ROOT / "userbot"
SQL_STATEMENT = re.compile(r"^\s*(SELECT|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.I)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _sql_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SQL_STATEMENT.search(node.value)
    ]


def test_userbot_sql_and_pool_access_are_confined_to_repository() -> None:
    repository = USERBOT / "repositories.py"
    assert _sql_literals(repository)

    violations: dict[str, list[tuple[int, str]]] = {}
    for path in USERBOT.rglob("*.py"):
        if path == repository:
            continue
        literals = _sql_literals(path)
        source = path.read_text(encoding="utf-8")
        if literals or "pool.acquire" in source or "get_db_pool" in source:
            violations[path.relative_to(ROOT).as_posix()] = literals
    assert violations == {}


def test_userbot_handlers_depend_on_services_not_database_infrastructure() -> None:
    for path in (USERBOT / "handlers").rglob("*.py"):
        imports = _imports(path)
        forbidden = sorted(
            name
            for name in imports
            if name == "asyncpg" or name == "db" or name.startswith("db.")
        )
        assert forbidden == [], f"{path.relative_to(ROOT)}: {forbidden}"
        source = path.read_text(encoding="utf-8")
        assert "pool.acquire" not in source
        assert _sql_literals(path) == []


def test_userbot_repository_is_telegram_framework_neutral() -> None:
    imports = _imports(USERBOT / "repositories.py")
    forbidden_roots = {name.split(".", 1)[0] for name in imports} & {
        "aiogram",
        "telethon",
        "flask",
    }
    assert forbidden_roots == set()


def test_entrypoint_is_a_thin_sql_free_compatibility_facade() -> None:
    path = USERBOT / "entrypoint.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert functions == {"main", "run"}
    assert len(source.splitlines()) < 80
    assert _sql_literals(path) == []
    assert "pool.acquire" not in source
