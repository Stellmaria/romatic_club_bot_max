from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_DIR = ROOT / "bot" / "handlers" / "admin"

SPLITS = {
    "admin_panel": (
        "admin_panel_system",
        "admin_panel_requests",
        "admin_panel_schedule",
        "admin_panel_sections",
        "admin_user_lists",
        "admin_panel_exchange",
    ),
    "moderation": (
        "moderation_lots",
        "moderation_schedule",
        "moderation_pending",
        "moderation_diagnostics",
        "moderation_clik",
    ),
}

EXPECTED_HANDLER_BOUNDS = {
    "admin_panel_system": (
        "show_admin_menu_with_system",
        "close_system_callback",
        8,
    ),
    "admin_panel_requests": ("admreq_back", "cb_exchange_approved_root", 21),
    "admin_panel_schedule": ("edit_schedule_button", "edit_price_handler", 19),
    "admin_panel_sections": ("show_decks_for_cards", "admin_help", 25),
    "admin_user_lists": ("show_admins_list", "paginate_admin_user_list", 4),
    "admin_panel_exchange": ("cmd_card_video", "ex1_reject_reason", 16),
    "moderation_lots": ("fsm_back_handler", "add_deck_command", 18),
    "moderation_schedule": ("schedule_command", "force_publish_handler", 5),
    "moderation_pending": ("edit_pending_lot_menu", "universal_trusted_cancel", 16),
    "moderation_diagnostics": ("cmd_lux_wait", "cmd_user_dbg", 9),
    "moderation_clik": ("clik_cmd", "clik_got_order", 33),
}


def _tree(module: str) -> ast.Module:
    return ast.parse((ADMIN_DIR / f"{module}.py").read_text(encoding="utf-8"))


def _imports(module: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _decorated_handlers(module: str) -> list[str]:
    return [
        node.name
        for node in _tree(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(item, ast.Name) and item.id == "router"
            for decorator in node.decorator_list
            for item in ast.walk(decorator)
        )
    ]


def test_admin_facades_are_thin_and_keep_feature_priority() -> None:
    for facade, feature_names in SPLITS.items():
        path = ADMIN_DIR / f"{facade}.py"
        assert len(path.read_text(encoding="utf-8").splitlines()) < 80
        assert _decorated_handlers(facade) == []

        imported = importlib.import_module(f"bot.handlers.admin.{facade}")
        router_modules = tuple(router.name for router in imported.FEATURE_ROUTERS)
        assert router_modules == tuple(
            f"bot.handlers.admin.{feature}" for feature in feature_names
        )


def test_every_admin_handler_has_one_feature_owner_in_original_order() -> None:
    for facade, feature_names in SPLITS.items():
        all_names: list[str] = []
        for feature in feature_names:
            names = _decorated_handlers(feature)
            first, last, count = EXPECTED_HANDLER_BOUNDS[feature]
            assert (names[0], names[-1], len(names)) == (first, last, count)
            all_names.extend(names)

        assert len(all_names) == len(set(all_names))
        expected_total = 93 if facade == "admin_panel" else 81
        assert len(all_names) == expected_total

        compatibility_module = importlib.import_module(
            f"bot.handlers.admin.{facade}"
        )
        assert all(hasattr(compatibility_module, name) for name in all_names)


def test_split_modules_do_not_import_composition_facades() -> None:
    feature_modules = {feature for features in SPLITS.values() for feature in features}
    feature_modules.update({"admin_panel_shared", "moderation_shared"})

    forbidden = {
        "bot.handlers.admin.admin_panel",
        "bot.handlers.admin.moderation",
    }
    for module in feature_modules:
        assert not (_imports(module) & forbidden), module

    assert "bot.handlers.admin.admin_panel_shared" in _imports("moderation_shared")


def test_admin_feature_modules_stay_reviewable() -> None:
    limits = {
        "admin_panel_shared": 1_200,
        "moderation_shared": 900,
    }
    for features in SPLITS.values():
        limits.update({feature: 900 for feature in features})

    for module, limit in limits.items():
        line_count = len(
            (ADMIN_DIR / f"{module}.py").read_text(encoding="utf-8").splitlines()
        )
        assert line_count <= limit, f"{module} grew to {line_count} lines"
