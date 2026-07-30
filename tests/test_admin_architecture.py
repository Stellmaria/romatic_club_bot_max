from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MODULE_PATHS = {
    "bot.handlers.admin.helper.new.admin_actions": (
        ROOT / "bot/handlers/admin/helper/new/admin_actions.py"
    ),
    "bot.handlers.admin.helper.new.formatting": (
        ROOT / "bot/handlers/admin/helper/new/formatting.py"
    ),
    "bot.handlers.admin.logs_admin": ROOT / "bot/handlers/admin/logs_admin.py",
    "bot.presentation.admin": ROOT / "bot/presentation/admin.py",
    "bot.repositories.admin_logs": ROOT / "bot/repositories/admin_logs.py",
    "bot.services.admin_logging": ROOT / "bot/services/admin_logging.py",
    "bot.services.admin_owners": ROOT / "bot/services/admin_owners.py",
}

LEGACY_MODULES = {
    "bot.handlers.admin.helper.new.admin_actions",
    "bot.handlers.admin.helper.new.formatting",
    "bot.handlers.admin.logs_admin",
}


def _tree(module: str) -> ast.Module:
    return ast.parse(MODULE_PATHS[module].read_text(encoding="utf-8"))


def _imports(module: str) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _bound_names(module: str) -> set[str]:
    names: set[str] = set()
    for node in _tree(module).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"import cycle found at {module}: {graph}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_admin_import_graph_is_acyclic_including_local_imports() -> None:
    graph = {
        module: _imports(module).intersection(MODULE_PATHS)
        for module in MODULE_PATHS
    }
    _assert_acyclic(graph)

    # The compatibility facade and formatter must never reach back into the
    # legacy handler modules, even through a function-local import.
    assert not (_imports("bot.handlers.admin.logs_admin") & LEGACY_MODULES)
    assert not (
        _imports("bot.handlers.admin.helper.new.formatting") & LEGACY_MODULES
    )
    assert "bot.handlers.admin.logs_admin" not in _imports(
        "bot.handlers.admin.helper.new.admin_actions"
    )


def test_formatting_module_contains_no_io_or_database_workflows() -> None:
    formatter_module = "bot.handlers.admin.helper.new.formatting"
    imports = _imports(formatter_module)
    assert "aiogram" not in imports
    assert "db.db" not in imports
    assert "bot.handlers.admin.logs_admin" not in imports
    assert not any(
        isinstance(node, ast.AsyncFunctionDef) for node in _tree(formatter_module).body
    )

    for application_module in (
        "bot.repositories.admin_logs",
        "bot.services.admin_logging",
        "bot.services.admin_owners",
    ):
        dependencies = _imports(application_module)
        assert not any(
            dependency.startswith("bot.handlers") for dependency in dependencies
        )
        assert "db.db" not in dependencies


def test_legacy_public_imports_are_preserved_by_facades() -> None:
    logging_names = _bound_names("bot.handlers.admin.logs_admin")
    assert {"send_admin_log", "send_lot_edit_log", "short_media_id"} <= logging_names

    formatting_names = _bound_names("bot.handlers.admin.helper.new.formatting")
    assert {
        "format_admin_action_log",
        "format_field_change_block",
        "format_pending_lot",
        "get_lot_owners_with_levels",
    } <= formatting_names

    action_names = _bound_names("bot.handlers.admin.helper.new.admin_actions")
    assert {
        "format_owner_html",
        "format_owners_block",
        "get_lot_owners_text",
        "send_admin_log",
    } <= action_names


def test_admin_actions_keeps_security_boundary_and_registers_no_handlers() -> None:
    imports = _imports("bot.handlers.admin.helper.new.admin_actions")
    assert "bot.security" in imports
    assert "bot.services.admin_logging" in imports

    decorated_top_level = [
        node.name
        for module in LEGACY_MODULES
        for node in _tree(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.decorator_list
    ]
    assert decorated_top_level == []
