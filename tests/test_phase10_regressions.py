from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_REPOSITORIES = ROOT / "db/repositories"
EXPECTED_REPOSITORIES = {
    "admin.py",
    "auctions.py",
    "autobids.py",
    "bids.py",
    "cards.py",
    "exchanges.py",
    "market.py",
    "post_stats.py",
    "stats.py",
    "subscriptions.py",
    "uid.py",
    "users.py",
}


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _literal_all(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                return list(ast.literal_eval(node.value))
    raise AssertionError(f"{path} has no literal __all__")


def test_legacy_database_module_is_a_thin_sql_free_facade() -> None:
    path = ROOT / "db/db.py"
    source = path.read_text(encoding="utf-8")

    assert len(source.splitlines()) < 700
    assert _functions(path) == {"init_db"}
    assert "await apply_migrations(pool)" in source
    assert "asyncpg.create_pool" not in source
    assert "db_pool: Optional" not in source

    upper = source.upper()
    for token in (
        "SELECT ",
        "INSERT INTO",
        "UPDATE PUBLIC.",
        "DELETE FROM",
        "CREATE TABLE",
        "ALTER TABLE",
    ):
        assert token not in upper


def test_phase10_repository_split_covers_the_legacy_api() -> None:
    actual = {
        path.name
        for path in DB_REPOSITORIES.glob("*.py")
        if path.name not in {"__init__.py", "_compat.py"}
    }
    assert actual == EXPECTED_REPOSITORIES | {"media_assets.py"}

    exported: set[str] = set()
    total_functions = 0
    for filename in EXPECTED_REPOSITORIES:
        path = DB_REPOSITORIES / filename
        names = _functions(path)
        declared = set(_literal_all(path))
        assert names == declared
        assert len(names) <= 40
        exported.update(declared)
        total_functions += len(names)

    assert total_functions == 235
    facade_exports = set(_literal_all(ROOT / "db/db.py"))
    assert exported <= facade_exports


def test_repository_layer_does_not_depend_on_telegram_handlers() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(DB_REPOSITORIES.glob("*.py"))
    )
    assert "from bot.handlers" not in combined
    assert "import bot.handlers" not in combined
    assert "from db.db import" not in combined


def test_cross_domain_compatibility_bridge_is_small_and_explicit() -> None:
    compat = DB_REPOSITORIES / "_compat.py"
    compat_functions = _functions(compat)
    assert compat_functions == {
        "_has_column",
        "_normalize_username",
        "auction_exists",
        "get_user",
        "get_user_by_username",
        "is_user_uid_banned",
    }

    for path in sorted(DB_REPOSITORIES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "from db.repositories._compat import *" not in source


def test_pool_lifecycle_has_a_stable_proxy_for_legacy_imports() -> None:
    source = (ROOT / "db/core.py").read_text(encoding="utf-8")
    assert "class PoolProxy" in source
    assert "db_pool = PoolProxy()" in source
    assert "db_pool.bind(pool)" in source
    assert "db_pool.clear()" in source
    assert "@wraps(func)" in source


def test_application_services_use_core_pool_not_legacy_facade() -> None:
    paths = [*sorted((ROOT / "bot/services").glob("*.py")), ROOT / "bot/telegram/outbox.py"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "from db.db import get_db_pool" not in combined
    assert combined.count("from db.core import get_db_pool") >= 14
