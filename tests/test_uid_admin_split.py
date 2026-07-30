from __future__ import annotations

import ast
from pathlib import Path

from bot.handlers.admin import master_ban
from bot.handlers.admin import telegram_user_bans
from bot.handlers.admin import uid_admin_bans
from bot.handlers.admin import uid_verification_admin as facade
from bot.handlers.admin import uid_verification_review
from bot.handlers.admin import uid_verification_revision
from bot.handlers.admin import uid_whois


ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "bot/handlers/admin"

EXPECTED_HANDLER_ORDER = (
    "uid_ban_menu",
    "uid_ban_start",
    "uid_ban_got_target",
    "uid_ban_got_reason",
    "uid_unban_start",
    "uid_unban_got_target",
    "uid_ban_list",
    "cmd_uidban",
    "cmd_uidunban",
    "cmd_uidbans",
    "verif_menu_cmd",
    "verif_menu_button",
    "verif_menu_cb",
    "verif_list",
    "verif_view",
    "verif_send_proof",
    "verif_send_deals",
    "verif_approve",
    "verif_reject",
    "verif_reject_reason",
    "cmd_whois",
    "whois_waiting_target",
    "verif_approve_blocked",
    "uidv_revision_start",
    "uidv_revision_toggle",
    "uidv_revision_reason",
    "uidv_revision_reason_msg",
    "uidv_revision_send",
    "user_ban_start",
    "user_ban_got_target",
    "user_ban_got_reason",
    "user_unban_start",
    "user_unban_got_target",
    "user_ban_list",
    "master_ban_start",
    "master_ban_got_user",
    "master_ban_got_uid",
    "master_ban_got_reason",
    "master_unban_start",
    "master_unban_got_user",
    "master_unban_got_uid",
)

EXPECTED_CONTRACTS = {
    "uid_ban_menu": "router.message(F.text == '⛔ UID-бан', F.chat.type == 'private')",
    "uid_ban_start": "router.message(F.text == '⛔ Забанить UID', F.chat.type == 'private')",
    "uid_ban_got_target": "router.message(ModActionFSM.waiting_for_uid_ban_target, F.chat.type == 'private')",
    "uid_ban_got_reason": "router.message(ModActionFSM.waiting_for_uid_ban_reason, F.chat.type == 'private')",
    "uid_unban_start": "router.message(F.text == '✅ Разбанить UID', F.chat.type == 'private')",
    "uid_unban_got_target": "router.message(ModActionFSM.waiting_for_uid_unban_target, F.chat.type == 'private')",
    "uid_ban_list": "router.message(F.text == '📋 Список UID-банов', F.chat.type == 'private')",
    "cmd_uidban": "router.message(Command('uidban'), F.chat.type == 'private')",
    "cmd_uidunban": "router.message(Command('uidunban'), F.chat.type == 'private')",
    "cmd_uidbans": "router.message(Command('uidbans'), F.chat.type == 'private')",
    "verif_menu_cmd": "router.message(Command('verif'), F.chat.type == 'private')",
    "verif_menu_button": "router.message(F.text == '🧾 Верификация', F.chat.type == 'private')",
    "verif_menu_cb": "router.callback_query(F.data == 'uidv|menu')",
    "verif_list": "router.callback_query(F.data.startswith('uidv|list|'))",
    "verif_view": "router.callback_query(F.data.startswith('uidv|view|') | F.data.startswith('uidv|view_one|'))",
    "verif_send_proof": "router.callback_query(F.data.startswith('uidv|proof|'))",
    "verif_send_deals": "router.callback_query(F.data.startswith('uidv|deals|'))",
    "verif_approve": "router.callback_query(F.data.startswith('uidv|approve|'))",
    "verif_reject": "router.callback_query(F.data.startswith('uidv|reject|'))",
    "verif_reject_reason": "router.message(ModActionFSM.waiting_for_reject_uid_verification_reason, F.chat.type == 'private')",
    "cmd_whois": "router.message(Command('whois'), F.chat.type == 'private')",
    "whois_waiting_target": "router.message(ModActionFSM.waiting_for_whois_target, F.chat.type == 'private')",
    "verif_approve_blocked": "router.callback_query(F.data.startswith('uidv|approve_blocked|'))",
    "uidv_revision_start": "router.callback_query(F.data.startswith('uidv|rev|'))",
    "uidv_revision_toggle": "router.callback_query(F.data.startswith('uidv|rev_toggle|'))",
    "uidv_revision_reason": "router.callback_query(F.data.startswith('uidv|rev_reason|'))",
    "uidv_revision_reason_msg": "router.message(UIDVerificationRevisionFSM.waiting_reason, F.chat.type == 'private')",
    "uidv_revision_send": "router.callback_query(F.data.startswith('uidv|rev_send|'))",
    "user_ban_start": "router.message(F.text == '🚫 Забанить пользователя', F.chat.type == 'private')",
    "user_ban_got_target": "router.message(ModActionFSM.waiting_for_user_ban_target, F.chat.type == 'private')",
    "user_ban_got_reason": "router.message(ModActionFSM.waiting_for_user_ban_reason, F.chat.type == 'private')",
    "user_unban_start": "router.message(F.text == '✅ Разбанить пользователя', F.chat.type == 'private')",
    "user_unban_got_target": "router.message(ModActionFSM.waiting_for_user_unban_target, F.chat.type == 'private')",
    "user_ban_list": "router.message(F.text == '📋 Список банов пользователей', F.chat.type == 'private')",
    "master_ban_start": "router.message(F.text == '💣 Мастер-бан', F.chat.type == 'private')",
    "master_ban_got_user": "router.message(ModActionFSM.waiting_for_master_ban_user, F.chat.type == 'private')",
    "master_ban_got_uid": "router.message(ModActionFSM.waiting_for_master_ban_uid, F.chat.type == 'private')",
    "master_ban_got_reason": "router.message(ModActionFSM.waiting_for_master_ban_reason, F.chat.type == 'private')",
    "master_unban_start": "router.message(F.text == '🧹 Мастер-разбан', F.chat.type == 'private')",
    "master_unban_got_user": "router.message(ModActionFSM.waiting_for_master_unban_user, F.chat.type == 'private')",
    "master_unban_got_uid": "router.message(ModActionFSM.waiting_for_master_unban_uid, F.chat.type == 'private')",
}

LEGACY_PUBLIC_SYMBOLS = {
    "Any",
    "Bot",
    "Command",
    "F",
    "FSMContext",
    "InlineKeyboardBuilder",
    "Iterable",
    "Message",
    "ModActionFSM",
    "REQUIRED_CONFIRMS",
    "Router",
    "TelegramBadRequest",
    "TelegramForbiddenError",
    "UIDVerificationRevisionFSM",
    "UID_HEX_RE",
    "USERNAME_RE",
    "ZoneInfo",
    "admin_only",
    "admin_tag",
    "apply_master_ban",
    "apply_master_unban",
    "approve_uid_verification_request",
    "ban_user",
    "build_thanks_kb",
    "datetime",
    "get_uid_profile_binding",
    "get_uid_verification_request",
    "get_user_basic_info_by_username",
    "get_user_by_username",
    "get_user_id_by_uid_any",
    "get_user_id_by_username",
    "get_user_verified_uid",
    "get_username_by_user_id",
    "get_whois_admin_payload",
    "html",
    "list_active_user_bans",
    "list_uid_bans",
    "list_uid_verification_requests",
    "mask_uid",
    "mask_uid_by_last4",
    "menu_keyboard",
    "re",
    "reject_uid_verification_request",
    "remove_uid_ban",
    "router",
    "safe_call_answer",
    "safe_edit",
    "send_admin_log",
    "set_uid_verification_request_revision",
    "timedelta",
    "timezone",
    "types",
    "unban_user",
    "upsert_uid_ban",
    *EXPECTED_HANDLER_ORDER,
}


def _handlers(path: Path, router_name: str = "router") -> tuple[tuple[str, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            expression = ast.unparse(decorator)
            if expression.startswith(f"{router_name}."):
                normalized = expression.replace(f"{router_name}.", "router.", 1)
                result.append((node.name, normalized))
                assert any(
                    isinstance(item, ast.Name) and item.id == "admin_only"
                    for item in node.decorator_list
                ), node.name
    return tuple(result)


def test_aggregate_router_preserves_exact_historical_handler_order() -> None:
    assert facade.router.sub_routers == [
        uid_admin_bans.router,
        uid_verification_review.router,
        uid_whois.router,
        uid_verification_review.late_router,
        uid_verification_revision.router,
        telegram_user_bans.router,
        master_ban.router,
    ]

    ordered = (
        *_handlers(ADMIN / "uid_admin_bans.py"),
        *_handlers(ADMIN / "uid_verification_review.py"),
        *_handlers(ADMIN / "uid_whois.py"),
        *_handlers(ADMIN / "uid_verification_review.py", "late_router"),
        *_handlers(ADMIN / "uid_verification_revision.py"),
        *_handlers(ADMIN / "telegram_user_bans.py"),
        *_handlers(ADMIN / "master_ban.py"),
    )
    assert tuple(name for name, _ in ordered) == EXPECTED_HANDLER_ORDER
    assert len(ordered) == len(set(name for name, _ in ordered)) == 41
    assert dict(ordered) == EXPECTED_CONTRACTS


def test_facade_is_thin_and_resolves_symbols_to_focused_owners() -> None:
    source = (ADMIN / "uid_verification_admin.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) < 100
    owners = {
        **{name: uid_admin_bans for name in EXPECTED_HANDLER_ORDER[:10]},
        **{name: uid_verification_review for name in EXPECTED_HANDLER_ORDER[10:20]},
        **{name: uid_whois for name in EXPECTED_HANDLER_ORDER[20:22]},
        "verif_approve_blocked": uid_verification_review,
        **{name: uid_verification_revision for name in EXPECTED_HANDLER_ORDER[23:28]},
        **{name: telegram_user_bans for name in EXPECTED_HANDLER_ORDER[28:34]},
        **{name: master_ban for name in EXPECTED_HANDLER_ORDER[34:]},
    }
    for name, owner in owners.items():
        assert getattr(facade, name) is getattr(owner, name)
    assert LEGACY_PUBLIC_SYMBOLS <= set(facade.__all__)
    assert all(hasattr(facade, name) for name in LEGACY_PUBLIC_SYMBOLS)


def test_shared_layers_do_not_depend_on_workflow_handlers() -> None:
    shared_files = (
        ADMIN / "uid_admin_shared.py",
        ADMIN / "uid_admin_resolvers.py",
        ADMIN / "uid_admin_presentation.py",
    )
    workflow_modules = {
        "uid_admin_bans",
        "uid_verification_review",
        "uid_whois",
        "uid_verification_revision",
        "telegram_user_bans",
        "master_ban",
        "uid_verification_admin",
    }
    for path in shared_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not (imports & workflow_modules), path.name


def test_workflow_handlers_have_no_handler_to_handler_dependencies() -> None:
    paths = tuple(
        ADMIN / name
        for name in (
            "uid_admin_bans.py",
            "uid_verification_review.py",
            "uid_whois.py",
            "uid_verification_revision.py",
            "telegram_user_bans.py",
            "master_ban.py",
        )
    )
    workflow_modules = {path.stem for path in paths}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not (imports & (workflow_modules - {path.stem})), path.name
