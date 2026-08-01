from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DB_MODULES = (
    "core",
    "users",
    "auctions",
    "auction_lifecycle_queries",
    "admin",
    "cards",
    "schedule_queries",
    "subscriptions",
    "market",
    "exchange",
    "posts",
    "uid",
)


def _tree(relative: str) -> ast.Module:
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
    return result


def test_legacy_facade_is_thin_and_exports_each_query_implementation() -> None:
    from db import db as facade

    facade_path = ROOT / "db/db.py"
    assert len(facade_path.read_text(encoding="utf-8").splitlines()) < 100
    assert not {
        node.name
        for node in _tree("db/db.py").body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    implementations: dict[str, object] = {}
    for module_name in DB_MODULES:
        module = __import__(f"db.{module_name}", fromlist=["unused"])
        for name in module.__all__:
            assert name not in implementations, f"duplicate database API owner: {name}"
            implementations[name] = getattr(module, name)

    assert implementations
    for name, implementation in implementations.items():
        assert getattr(facade, name) is implementation, name
    assert set(facade.__all__) == {*implementations, "db_pool"}


def test_every_in_repository_legacy_import_is_still_available() -> None:
    from db import db as facade

    missing: dict[str, list[str]] = {}
    roots = (ROOT / "bot", ROOT / "userbot", ROOT / "scripts")
    paths = [path for root in roots for path in root.rglob("*.py")]
    paths.extend((ROOT / "main.py", ROOT / "find_discussion_id.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.ImportFrom)
                or node.module not in {"db.db", "db.legacy"}
            ):
                continue
            for alias in node.names:
                if alias.name != "*" and not hasattr(facade, alias.name):
                    missing.setdefault(alias.name, []).append(str(path.relative_to(ROOT)))
    assert missing == {}


def test_database_domains_do_not_depend_on_facade_handlers_or_direct_connections() -> None:
    for module_name in DB_MODULES:
        path = ROOT / "db" / f"{module_name}.py"
        source = path.read_text(encoding="utf-8")
        imports = _imports(ast.parse(source, filename=str(path)))
        assert "db.db" not in imports
        assert not any(name.startswith("bot.handlers") for name in imports)
        assert "asyncpg.connect(" not in source
        assert "asyncpg.create_pool(" not in source
        assert "DATABASE_URL" not in source

    pool_source = (ROOT / "db/pool.py").read_text(encoding="utf-8")
    assert pool_source.count("asyncpg.create_pool(") == 1
    assert "class DatabaseRuntime" in pool_source


def test_database_domain_import_graph_is_acyclic() -> None:
    graph: dict[str, set[str]] = {name: set() for name in DB_MODULES}
    for module_name in DB_MODULES:
        imports = _imports(_tree(f"db/{module_name}.py"))
        graph[module_name] = {
            imported.removeprefix("db.")
            for imported in imports
            if imported.startswith("db.") and imported.removeprefix("db.") in graph
        }

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module_name: str) -> None:
        assert module_name not in visiting, f"database import cycle at {module_name}"
        if module_name in visited:
            return
        visiting.add(module_name)
        for dependency in graph[module_name]:
            visit(dependency)
        visiting.remove(module_name)
        visited.add(module_name)

    for module_name in graph:
        visit(module_name)


class _Connection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executions.append((query, args))
        return "INSERT 0 1"


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self._connection)


@pytest.mark.asyncio
async def test_runtime_adapter_reaches_extracted_functions() -> None:
    from db import core
    from db import db as facade

    previous_runtime = core.current_database_runtime()
    connection = _Connection()
    pool = _Pool(connection)
    try:
        core.db_pool.clear()
        core.db_pool.bind(pool)
        assert facade.db_pool is core.db_pool
        assert facade.db_pool.pool is pool
        assert await facade.get_db_pool() is pool
        await facade.add_user(42, "test_user", "Test User")
    finally:
        core.db_pool.clear()
        if previous_runtime is not None:
            core.install_database_runtime(previous_runtime)

    assert len(connection.executions) == 1
    query, args = connection.executions[0]
    assert "INSERT INTO users" in query
    assert args == (42, "test_user", "Test User")
