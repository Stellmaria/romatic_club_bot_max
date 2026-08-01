from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_DIR = ROOT / "bot" / "handlers" / "admin"

SPLITS = {
    "admin_panel": (
        "admin_panel_system",
        "admin_panel_requests",
        "admin_panel_schedule",
        "admin_panel_sections",
        "admin_user_lists",
        "admin_panel_exchange",
    ),
    "moderation": (
        "moderation_lots",
        "moderation_schedule",
        "moderation_pending",
        "moderation_diagnostics",
        "moderation_clik",
    ),
}


def _tree(module: str) -> ast.Module:
    path = ADMIN_DIR / f"{module}.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(module: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _decorated_handlers(module: str) -> list[str]:
    return [
        node.name
        for node in _tree(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(item, ast.Name) and item.id == "router"
            for decorator in node.decorator_list
            for item in ast.walk(decorator)
        )
    ]


def _registered_handler_count(router: object) -> int:
    observers = getattr(router, "observers")
    return sum(len(observer.handlers) for observer in observers.values())


def test_admin_facades_compose_each_feature_once_in_priority_order() -> None:
    for facade, feature_names in SPLITS.items():
        imported = importlib.import_module(f"bot.handlers.admin.{facade}")
        feature_routers = tuple(imported.FEATURE_ROUTERS)

        assert len(feature_routers) == len(set(map(id, feature_routers)))
        assert tuple(router.name for router in feature_routers) == tuple(
            f"bot.handlers.admin.{feature}" for feature in feature_names
        )

        nested = tuple(imported.router.sub_routers)
        expected_nested = (
            feature_routers[1:] if facade == "admin_panel" else feature_routers
        )
        assert nested == expected_nested


def test_each_feature_router_owns_handlers_and_facade_reexports_them() -> None:
    for facade, feature_names in SPLITS.items():
        compatibility_module = importlib.import_module(
            f"bot.handlers.admin.{facade}"
        )
        all_handler_names: list[str] = []

        for feature in feature_names:
            module = importlib.import_module(f"bot.handlers.admin.{feature}")
            names = _decorated_handlers(feature)
            assert names, f"{feature} has no adapter behavior"
            assert _registered_handler_count(module.router) == len(names)
            all_handler_names.extend(names)

        assert len(all_handler_names) == len(set(all_handler_names))
        assert all(
            hasattr(compatibility_module, name) for name in all_handler_names
        )


def test_split_modules_do_not_depend_on_composition_facades() -> None:
    forbidden = {
        "bot.handlers.admin.admin_panel",
        "bot.handlers.admin.moderation",
    }
    for feature_names in SPLITS.values():
        for module in feature_names:
            assert not (_imports(module) & forbidden), module


def test_dependency_warehouses_are_retired_and_support_modules_are_router_free() -> None:
    assert not (ADMIN_DIR / "admin_panel_shared.py").exists()
    assert not (ADMIN_DIR / "moderation_shared.py").exists()

    support_paths = [
        *sorted((ADMIN_DIR / "presentation").glob("*.py")),
        ROOT / "bot" / "services" / "admin_auction_notifications.py",
    ]
    for path in support_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Router"
            for node in ast.walk(tree)
        ), path
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.decorator_list
            for node in tree.body
        ), path


def test_admin_feature_and_support_modules_have_ratchet_budgets() -> None:
    for feature_names in SPLITS.values():
        for module in feature_names:
            path = ADMIN_DIR / f"{module}.py"
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            assert line_count <= 1_100, f"{module} grew to {line_count} lines"

    support_paths = [
        *sorted((ADMIN_DIR / "presentation").glob("*.py")),
        ROOT / "bot" / "services" / "admin_auction_notifications.py",
    ]
    for path in support_paths:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 400, f"{path.name} grew to {line_count} lines"
