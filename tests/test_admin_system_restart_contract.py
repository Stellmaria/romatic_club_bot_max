from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel.py"
SYSTEM_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel_system.py"
CLIENT = ROOT / "bot" / "core" / "supervisor_client.py"


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


def test_system_panel_keeps_owner_gate_and_safe_fallback() -> None:
    source = _source(SYSTEM_PANEL)

    assert "ADMINS_OWNERS" in source
    assert 'callback_data="system:restart:ask"' in source
    assert '"system:restart:do"' in source
    assert '"system:update:do"' in source
    assert '"system:rollback:do"' in source
    assert "process_restart_coordinator.request()" in source
    assert "supervisor_client.update()" in source
    assert 'F.text == "🖥 Система"' in source
    assert '["🖥 Система"]' in source


def test_supervisor_client_requires_explicit_enablement_and_token() -> None:
    source = _source(CLIENT)

    assert 'os.getenv("SUPERVISOR_ENABLED"' in source
    assert 'os.getenv("SUPERVISOR_TOKEN"' in source
    assert '"Authorization": f"Bearer {self.token}"' in source
    assert '"/v1/update"' in source
    assert '"/v1/rollback"' in source
