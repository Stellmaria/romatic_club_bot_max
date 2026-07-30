from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATES_MODULE = PROJECT_ROOT / "bot" / "telegram" / "states.py"
EXCLUDED_PARTS = {".git", ".venv", "build", "dist", "__pycache__"}


def _declared_fsm_states() -> dict[str, set[str]]:
    tree = ast.parse(STATES_MODULE.read_text(encoding="utf-8"))
    declared: dict[str, set[str]] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        state_names = {
            statement.targets[0].id
            for statement in node.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "State"
        }
        if state_names:
            declared[node.name] = state_names

    return declared


def test_fsm_references_point_to_declared_states() -> None:
    declared = _declared_fsm_states()
    invalid: list[str] = []

    for path in PROJECT_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue

            group_name = node.value.id
            if group_name in declared and node.attr not in declared[group_name]:
                relative_path = path.relative_to(PROJECT_ROOT)
                invalid.append(f"{relative_path}:{node.lineno}: {group_name}.{node.attr}")

    assert not invalid, "References to undeclared FSM states:\n" + "\n".join(sorted(invalid))
