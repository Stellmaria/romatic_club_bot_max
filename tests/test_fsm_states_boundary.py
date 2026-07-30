from __future__ import annotations

import ast
from pathlib import Path

import fsm_states as legacy_states
from bot.telegram import states


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_fsm_module_reexports_canonical_classes() -> None:
    assert legacy_states.__all__ == states.__all__
    assert states.__all__

    for name in states.__all__:
        canonical = getattr(states, name)
        assert getattr(legacy_states, name) is canonical
        assert canonical.__module__ == "bot.telegram.states"


def test_fsm_exports_follow_class_declaration_order() -> None:
    tree = ast.parse((PROJECT_ROOT / "bot" / "telegram" / "states.py").read_text("utf-8"))
    declared_groups = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "StatesGroup" for base in node.bases)
    ]

    assert declared_groups == states.__all__


def test_bot_package_does_not_import_legacy_fsm_facade() -> None:
    violations: list[str] = []

    for source_path in (PROJECT_ROOT / "bot").rglob("*.py"):
        tree = ast.parse(source_path.read_text("utf-8"), filename=str(source_path))
        if any(
            (isinstance(node, ast.ImportFrom) and node.module == "fsm_states")
            or (
                isinstance(node, ast.Import)
                and any(alias.name == "fsm_states" for alias in node.names)
            )
            for node in ast.walk(tree)
        ):
            violations.append(source_path.relative_to(PROJECT_ROOT).as_posix())

    assert violations == []
