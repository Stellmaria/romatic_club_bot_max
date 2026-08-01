#!/usr/bin/env python3
"""Enforce explicit, acyclic imports inside Telegram handler adapters."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_ROOT = ROOT / "bot" / "handlers"
RETIRED_WAREHOUSES = (
    HANDLERS_ROOT / "admin" / "admin_panel_shared.py",
    HANDLERS_ROOT / "admin" / "moderation_shared.py",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    file_path = ROOT / relative.with_suffix(".py")
    if file_path.exists():
        return file_path
    package_path = ROOT / relative / "__init__.py"
    return package_path if package_path.exists() else None


def _resolve_from_import(current: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    current_path = _module_path(current)
    if current_path is None:
        return node.module or ""
    package = current if current_path.name == "__init__.py" else current.rpartition(".")[0]
    return importlib.util.resolve_name("." * node.level + (node.module or ""), package)


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


def main() -> int:
    violations: list[str] = []
    paths = sorted(HANDLERS_ROOT.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    graph: dict[str, set[str]] = {module: set() for module in modules}

    for retired in RETIRED_WAREHOUSES:
        if retired.exists():
            violations.append(
                f"{retired.relative_to(ROOT)}: retired dependency warehouse still exists"
            )

    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules and alias.name != module:
                        graph[module].add(alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue

            target = _resolve_from_import(module, node)
            if any(alias.name == "*" for alias in node.names):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: wildcard import from {target}"
                )
            if target.startswith("bot.handlers"):
                private = [alias.name for alias in node.names if alias.name.startswith("_")]
                if private:
                    rendered = ", ".join(private)
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: imports private handler names "
                        f"from {target}: {rendered}"
                    )
            if target in modules and target != module:
                graph[module].add(target)
            for alias in node.names:
                candidate = f"{target}.{alias.name}" if target else alias.name
                if candidate in modules and candidate != module:
                    graph[module].add(candidate)

    cycle = _find_cycle(graph)
    if cycle:
        violations.append("handler import cycle: " + " -> ".join(cycle))

    if violations:
        print("Handler import boundary contract failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print("Handler import boundary contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
