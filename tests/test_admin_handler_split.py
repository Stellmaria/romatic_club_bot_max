from __future__ import annotations

import ast
import importlib
from pathlib import Path

from aiogram import Router

# fmt: off

ROOT = Path(__file__).resolve().parents[1]
ADMIN_DIR = ROOT / "bot" / "handlers" / "admin"

FACADES = ("admin_panel", "moderation")


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


def _handler_count(router: Router) -> int:
    return sum(len(observer.handlers) for observer in router.observers.values())


def _walk_router_tree(router: Router) -> tuple[Router, ...]:
    discovered: list[Router] = []
    pending = [router]
    while pending:
        current = pending.pop()
        discovered.append(current)
        pending.extend(reversed(current.sub_routers))
    return tuple(discovered)


def test_admin_facades_expose_a_unique_reachable_router_graph() -> None:
    """Composition must dispatch to every declared feature exactly once.

    The contract intentionally ignores module order, handler names and handler
    totals. Moving an implementation between feature modules therefore remains
    a behavior-preserving refactor instead of a test failure.
    """

    for facade_name in FACADES:
        facade = importlib.import_module(f"bot.handlers.admin.{facade_name}")
        feature_routers = tuple(facade.FEATURE_ROUTERS)
        reachable = _walk_router_tree(facade.router)

        assert feature_routers
        assert all(isinstance(router, Router) for router in feature_routers)
        assert len(feature_routers) == len(set(map(id, feature_routers)))
        assert len(reachable) == len(set(map(id, reachable)))

        directly_bootstrapped = set(feature_routers) - set(reachable)
        if facade_name == "admin_panel":
            assert directly_bootstrapped == {feature_routers[0]}
        else:
            assert not directly_bootstrapped

        assert all(_handler_count(router) > 0 for router in feature_routers)


def test_admin_compatibility_facades_export_real_symbols() -> None:
    """Historic imports remain usable without coupling tests to function layout."""

    for facade_name in FACADES:
        facade = importlib.import_module(f"bot.handlers.admin.{facade_name}")
        exported = tuple(facade.__all__)

        assert exported
        assert all(hasattr(facade, name) for name in exported)
        assert facade.router in _walk_router_tree(facade.router)


def test_feature_modules_do_not_import_composition_facades() -> None:
    """Keep the meaningful architecture rule: dependencies point inward."""

    forbidden = {
        "bot.handlers.admin.admin_panel",
        "bot.handlers.admin.moderation",
    }
    for facade_name in FACADES:
        facade = importlib.import_module(f"bot.handlers.admin.{facade_name}")
        for feature_router in facade.FEATURE_ROUTERS:
            feature_module = feature_router.name
            if not feature_module.startswith("bot.handlers.admin."):
                continue
            module_name = feature_module.rsplit(".", maxsplit=1)[-1]
            assert not (_imports(module_name) & forbidden), module_name


def test_support_modules_remain_framework_agnostic() -> None:
    """Presentation and notification helpers must not become Telegram adapters."""

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

# fmt: on
