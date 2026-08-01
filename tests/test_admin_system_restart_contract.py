from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel.py"
SYSTEM_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel_system.py"
ROUTER_BOOTSTRAP = ROOT / "bot" / "bootstrap" / "routers.py"
CLIENT = ROOT / "bot" / "core" / "supervisor_client.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_system_router_is_registered_before_legacy_fsm_routers() -> None:
    admin_panel = _source(ADMIN_PANEL)
    bootstrap = _source(ROUTER_BOOTSTRAP)

    # Keep the public facade inventory intact, but skip nesting the system
    # router so it can be attached directly at the top of dispatch order.
    tree = ast.parse(admin_panel)
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
    feature_names = [ast.unparse(element) for element in feature_assignment.value.elts]
    assert feature_names[0] == "admin_panel_system.router"
    assert "router.include_routers(*FEATURE_ROUTERS[1:])" in admin_panel

    assert (
        "from bot.handlers.admin.admin_panel_system import "
        "router as admin_panel_system_router"
    ) in bootstrap
    system_position = bootstrap.index(
        "dispatcher.include_router(admin_panel_system_router)"
    )
    assert system_position < bootstrap.index(
        "dispatcher.include_router(users_router)"
    )
    assert system_position < bootstrap.index(
        "dispatcher.include_router(auctions_router)"
    )
    assert system_position < bootstrap.index(
        "dispatcher.include_router(admin_panel_router)"
    )


def test_system_panel_is_owner_only_and_hides_button_from_admins() -> None:
    source = _source(SYSTEM_PANEL)

    assert "ADMINS_OWNERS" in source
    assert "def _admin_main_keyboard(*, include_system: bool)" in source
    assert "if include_system:" in source
    assert "include_system=_is_owner(" in source
    assert 'text = "Системные операции доступны только владельцу."' in source
    assert 'F.text == "🖥 Система"' in source

    tree = ast.parse(source)
    handlers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in (
        "show_system_menu",
        "show_restart_confirmation",
        "show_system_callback",
        "show_system_confirmation",
        "run_system_operation",
        "show_system_logs",
        "close_system_callback",
    ):
        decorators = ast.unparse(handlers[name]).split("async def", 1)[0]
        assert "admin_only" not in decorators
        body = ast.unparse(handlers[name])
        assert "_require_owner" in body


def test_system_panel_supports_separate_bot_and_userbot_restart() -> None:
    source = _source(SYSTEM_PANEL)

    assert 'callback_data="system:restart:ask"' in source
    assert 'callback_data="system:userbot-restart:ask"' in source
    assert '"system:restart:do"' in source
    assert '"system:userbot-restart:do"' in source
    assert 'Command("restart_userbot")' in source
    assert "process_restart_coordinator.request()" in source
    assert "supervisor_client.restart_userbot()" in source
    assert "supervisor_client.update()" in source


def test_supervisor_client_requires_explicit_enablement_and_token() -> None:
    source = _source(CLIENT)

    assert 'def from_settings(' in source
    assert 'os.getenv' not in source
    assert 'settings.token' in source
    assert '"Authorization": f"Bearer {self.token}"' in source
    assert '"/v1/restart-userbot"' in source
    assert '"/v1/update"' in source
    assert '"/v1/rollback"' in source
