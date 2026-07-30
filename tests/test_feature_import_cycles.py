from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEATURE_MODULES = {
    "bot.handlers.auction.exchange",
    "bot.handlers.auction.exchange_common",
    "bot.handlers.auction.exchange_moderation",
    "bot.handlers.admin.services.market_add_flow",
    "bot.handlers.admin.services.market_manage_flow",
    "bot.handlers.admin.services.market_sales",
}


def _module_path(module: str) -> Path:
    return ROOT.joinpath(*module.split(".")).with_suffix(".py")


def _resolve_from_import(current: str, node: ast.ImportFrom) -> set[str]:
    if node.level:
        package = current.rpartition(".")[0]
        target = importlib.util.resolve_name(
            "." * node.level + (node.module or ""),
            package,
        )
    else:
        target = node.module or ""

    candidates = {target} if target else set()
    candidates.update(
        f"{target}.{alias.name}" if target else alias.name
        for alias in node.names
        if alias.name != "*"
    )
    return candidates


def _feature_import_graph() -> dict[str, set[str]]:
    graph = {module: set() for module in FEATURE_MODULES}
    for module in FEATURE_MODULES:
        tree = ast.parse(_module_path(module).read_text(encoding="utf-8"), filename=module)
        # ast.walk intentionally includes imports nested inside handlers.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                candidates = _resolve_from_import(module, node)
            else:
                continue
            graph[module].update(candidates & FEATURE_MODULES)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(module: str) -> list[str] | None:
        if module in active_set:
            start = active.index(module)
            return [*active[start:], module]
        if module in visited:
            return None

        active.append(module)
        active_set.add(module)
        for dependency in sorted(graph[module]):
            cycle = visit(dependency)
            if cycle:
                return cycle
        active.pop()
        active_set.remove(module)
        visited.add(module)
        return None

    for module in sorted(graph):
        cycle = visit(module)
        if cycle:
            return cycle
    return None


def test_exchange_and_market_feature_imports_are_acyclic() -> None:
    graph = _feature_import_graph()

    # This edge is a local compatibility import and proves that the graph does
    # not accidentally inspect only module-level imports.
    assert (
        "bot.handlers.auction.exchange_moderation"
        in graph["bot.handlers.auction.exchange"]
    )
    assert _find_cycle(graph) is None, graph
