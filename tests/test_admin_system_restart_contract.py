from __future__ import annotations

import ast
from pathlib import Path

from aiogram import Router

from bot.bootstrap.router_registry import (
    FeatureRegistration,
    RoutePriority,
    RouterRegistry,
)


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PANEL = ROOT / "bot" / "handlers" / "admin" / "admin_panel_system.py"
CLIENT = ROOT / "bot" / "core" / "supervisor_client.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_system_priority_is_composed_before_application_and_broad_routes() -> None:
    registry = RouterRegistry(
        (
            FeatureRegistration(
                name="admin.panel",
                priority=RoutePriority.CALLBACKS,
                router=Router(name="admin-panel"),
            ),
            FeatureRegistration(
                name="auctions.core",
                priority=RoutePriority.EXACT_COMMANDS,
                router=Router(name="auctions-core"),
            ),
            FeatureRegistration(
                name="users.core",
                priority=RoutePriority.EXACT_COMMANDS,
                router=Router(name="users-core"),
            ),
            FeatureRegistration(
                name="admin.system",
                priority=RoutePriority.SYSTEM_OWNER,
                router=Router(name="admin-system"),
                commands=("system", "restart"),
                callback_namespaces=("system",),
            ),
        )
    )

    names = [feature.name for feature in registry.ordered_features]
    system_position = names.index("admin.system")
    assert system_position < names.index("users.core")
    assert system_position < names.index("auctions.core")
    assert system_position < names.index("admin.panel")


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

    assert "def from_settings(" in source
    assert "os.getenv" not in source
    assert "settings.token" in source
    assert '"Authorization": f"Bearer {self.token}"' in source
    assert '"/v1/restart-userbot"' in source
    assert '"/v1/update"' in source
    assert '"/v1/rollback"' in source
