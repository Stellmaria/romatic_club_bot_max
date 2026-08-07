from __future__ import annotations

import ast
from pathlib import Path

from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_profile_button_exposes_privacy_and_user_lookup() -> None:
    profile = _source("bot/handlers/profile.py")
    access = _source("bot/handlers/user_access_control.py")
    users = _source("bot/handlers/users.py")

    assert 'text="🔐 Данные и приватность"' in profile
    assert 'callback_data="user_profile|privacy"' in profile
    assert 'text="🔎 Проверить пользователя"' in profile
    assert 'callback_data="user_profile|who"' in profile
    assert "PublicWhoFSM.waiting_for_who_target" in profile
    assert "PublicWhoFSM.waiting_for_who_target" in users

    assert "USER_MENU_PROFILE" in access
    assert "show_profile_menu(message, user=message.from_user)" in access

    names = [feature.name for feature in get_router_registry().ordered_features]
    assert names.index("users.access-control") < names.index("users.menu")
    assert names.index("users.profile") < names.index("users.core")


def test_privacy_slash_commands_have_button_equivalents() -> None:
    profile = _source("bot/handlers/profile.py")

    expected = {
        "privacy_export": "user_privacy|export",
        "privacy_delete_request": "user_privacy|delete_confirm",
        "privacy_delete_status": "user_privacy|status",
        "privacy_delete_cancel": "user_privacy|cancel",
    }
    for command, callback in expected.items():
        assert f'Command("{command}")' in profile
        assert f'callback_data="{callback}"' in profile
        assert f'F.data == "{callback}"' in profile

    assert 'callback_data="user_privacy|delete"' in profile
    assert "⚠️ Подать запрос на удаление" in profile


def test_custom_emoji_commands_are_private_admin_only() -> None:
    path = ROOT / "bot/handlers/emoji_setup.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in ("em_migrate", "em_add", "em_list", "em_del"):
        node = functions[name]
        decorators = [ast.unparse(item) for item in node.decorator_list]
        rendered = "\n".join(decorators)
        assert "admin_only" in decorators
        assert "router.message" in rendered
        assert "F.chat.type" in rendered
        assert "private" in rendered


def test_public_command_button_contract_documents_intentional_shortcuts() -> None:
    """Keep slash aliases while ensuring the normal user path stays button driven."""

    menu = _source("bot/handlers/user_menu.py")
    users = _source("bot/handlers/users.py")
    subscriptions = _source("bot/handlers/card_subscribe.py")

    covered = {
        "addlot": (menu, "USER_MENU_ADD_LOT"),
        "my_lots": (menu, "USER_MENU_MY_LOTS"),
        "notifications": (menu, "USER_MENU_NOTIFICATIONS"),
        "subscribe_card": (menu, "USER_MENU_SUBSCRIPTIONS"),
        "my_subscriptions": (subscriptions, 'callback_data="sub:list"'),
        "my_subs": (subscriptions, 'callback_data="sub:list"'),
        "profile": (menu, "USER_MENU_PROFILE"),
        "today": (_source("bot/handlers/user_access_control.py"), "USER_MENU_TODAY"),
    }
    for command, (source, button_token) in covered.items():
        command_present = (
            f'Command("{command}")' in source
            or f'Command("{command}")' in users
            or f'Command("{command}")' in subscriptions
            or command == "addlot"
        )
        assert command_present, command
        assert button_token in source, command

    # Deliberate command-only compatibility/power-user entries.
    intentional_command_only = {"day", "hide_menu"}
    for command in intentional_command_only:
        assert f'Command("{command}")' in users
