from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MODULE = "bot.handlers.admin.helper.new.admin_actions"
IMPLEMENTATION_MODULES = (
    "bot.handlers.admin.action_support.exchange",
    "bot.handlers.admin.action_support.transport",
    "bot.handlers.admin.action_support.moderation",
    "bot.handlers.admin.action_support.roles",
    "bot.handlers.admin.action_support.forms",
    "bot.handlers.admin.action_support.scheduled_edits",
)

# Characterization snapshot of every function and module constant owned by the
# original admin_actions.py before it was decomposed.
EXPECTED_OWNED_SYMBOLS = {
    "MAX_TG_LEN", "SAFE_SPLIT", "BR_RE", "DT_FMT", "_safe_user_mention", "_as_str",
    "_admin_link_html", "format_exchange_moderation_log", "notify_exchange_user_moderation",
    "format_exchange_new_request_log", "_looks_like_file_id", "safe_answer_photo", "tg_clean",
    "_get_exchange_cover_media_admin", "_media_kind_from_error_admin",
    "_send_exchange_batch_card_admin", "build_exchange_pending_keyboard", "_cur_emoji",
    "_safe_strip", "parse_datetime_field", "_to_msk", "_human_wait", "_resolve_bot_from_message",
    "_ensure_sender", "as_message", "require_bot", "_call_maybe_await", "format_date_time_block",
    "owner_or_secret_required", "safe_edit_message", "notify_owners", "send_log_to_chats",
    "verify_log_chats", "get_cancel_text", "process_universal_cancel_text",
    "process_universal_cancel_callback", "send_lot_card_safe", "MAX_DEBUG_LEN", "MSK_TZ", "UTC",
    "show_pendinglots", "_delete_row_lot_id", "_delete_request_created_str",
    "_delete_request_keyboard", "_clip_caption", "_build_channel_link", "_build_discussion_link",
    "_currency_label", "_rarity_label", "_gift_line", "_delete_request_text",
    "show_delete_requests_for_moderation", "_extract_reason_text", "_get_obj_row_lot",
    "_log_reject_admin_action", "_to_int", "_notify_lot_owners", "process_reject_action",
    "add_admin_role", "remove_admin_role", "_parse_admin_command_args", "_ensure_bot_or_fail",
    "_admin_link_text", "_remove_admin_flow", "_add_admin_flow", "do_admin_add_remove",
    "admin_add_remove", "give_trusted_status", "remove_trusted_status", "_resolve_user_or_error",
    "_extract_who_text", "_trusted_result_text", "_actor_and_bot_or_fail", "_do_trusted_action",
    "start_preview_schedule", "start_edit_schedule", "add_deck_fsm_entry", "start_add_card_fsm",
    "owners_to_links_text", "get_lot_by_channel_message_id", "EX_WHOLE_DECK_MODES",
    "_update_auction_field", "_short_media_id", "_fmt_dt_msk", "_fmt_window_msk", "_obtain_emoji",
    "_yn_uid", "_rarity_line", "_pick_sold_count", "_user_status_label", "_format_change_lines",
    "_build_owner_notice_text", "_bot_send_media_any", "_notify_owners_and_log",
    "apply_scheduled_time_change", "apply_scheduled_price_change", "apply_scheduled_currency_change",
    "apply_scheduled_comment_change", "apply_scheduled_photo_change",
    "apply_scheduled_auction_kind_change", "apply_scheduled_craft_uid_change",
}

COMPATIBILITY_EXPORTS = {
    "admin_secret_matches",
    "admin_tag",
    "build_thanks_kb",
    "format_admin_action_log",
    "format_owner_html",
    "format_owners_block",
    "format_pending_lot",
    "get_lot_owners_text",
    "get_lot_owners_with_levels",
    "safe_send_media",
    "send_admin_log",
}


def _module_path(module: str) -> Path:
    return ROOT.joinpath(*module.split(".")).with_suffix(".py")


def _import_targets(module: str) -> set[str]:
    tree = ast.parse(_module_path(module).read_text(encoding="utf-8"), filename=module)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
            targets.update(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def test_legacy_facade_is_thin_and_complete() -> None:
    facade_path = _module_path(LEGACY_MODULE)
    assert len(facade_path.read_text(encoding="utf-8").splitlines()) <= 150

    facade = importlib.import_module(LEGACY_MODULE)
    owners: dict[str, object] = {}
    for module_name in IMPLEMENTATION_MODULES:
        module = importlib.import_module(module_name)
        for name in module.__all__:
            assert name not in owners, f"duplicate owner for {name}"
            owners[name] = module

    assert EXPECTED_OWNED_SYMBOLS <= set(owners)
    assert set(facade.__all__) == set(owners) | COMPATIBILITY_EXPORTS
    for name, owner in owners.items():
        assert getattr(facade, name) is getattr(owner, name)


def test_action_support_import_graph_is_acyclic() -> None:
    modules = {*IMPLEMENTATION_MODULES, LEGACY_MODULE}
    graph = {
        module: _import_targets(module).intersection(modules)
        for module in modules
    }
    visited: set[str] = set()
    active: set[str] = set()

    def visit(module: str) -> None:
        if module in active:
            raise AssertionError(f"admin-action import cycle at {module}: {graph}")
        if module in visited:
            return
        active.add(module)
        for dependency in graph[module]:
            visit(dependency)
        active.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_production_consumers_do_not_import_legacy_facade() -> None:
    facade_path = _module_path(LEGACY_MODULE)
    violations: list[str] = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        if path == facade_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == LEGACY_MODULE:
                violations.append(str(path.relative_to(ROOT)))
            elif isinstance(node, ast.Import) and any(
                alias.name == LEGACY_MODULE for alias in node.names
            ):
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []
