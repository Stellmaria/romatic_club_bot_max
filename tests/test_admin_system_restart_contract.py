from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel.py"
SYSTEM_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel_system.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_system_router_is_registered_before_legacy_admin_menu() -> None:
    source = _source(ADMIN_PANEL)
    tree = ast.parse(source)

    feature_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "FEATURE_ROUTERS"
            for target in node.targets
        )
    )
    assert isinstance(feature_assignment.value, ast.Tuple)
    first_router = feature_assignment.value.elts[0]
    assert isinstance(first_router, ast.Attribute)
    assert isinstance(first_router.value, ast.Name)
    assert first_router.value.id == "admin_panel_system"
    assert first_router.attr == "router"


def test_system_panel_keeps_owner_gate_and_restart_confirmation() -> None:
    source = _source(SYSTEM_PANEL)

    assert "ADMINS_OWNERS" in source
    assert 'callback_data="system:restart:ask"' in source
    assert 'callback_data="system:restart:do"' in source
    assert "process_restart_coordinator.request()" in source
    assert 'F.text == "🖥 Система"' in source
    assert '["🖥 Система"]' in source
