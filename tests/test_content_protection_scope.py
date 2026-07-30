from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TRUE_FILES = {
    Path("bot/handlers/admin/services/schedule.py"),
    Path("bot/handlers/auction/schedule.py"),
    Path("bot/handlers/auctions.py"),
    Path("bot/handlers/admin/helper/new/card_economy.py"),
    Path("bot/handlers/admin/helper/new/card_economy_shared.py"),
}


def _is_protect_true(node: ast.AST) -> bool:
    if isinstance(node, ast.keyword):
        return (
            node.arg == "protect_content"
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        )
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "protect_content"
                and isinstance(value, ast.Constant)
                and value.value is True
            ):
                return True
    return False


def test_only_luxury_handlers_enable_content_protection() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(part in {".venv", "dist", "__pycache__", "tests"} for part in relative.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if _is_protect_true(node) and relative not in ALLOWED_TRUE_FILES:
                violations.append(f"{relative}:{getattr(node, 'lineno', '?')}")

    assert violations == []
