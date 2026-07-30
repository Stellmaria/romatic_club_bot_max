from __future__ import annotations

import ast
import importlib
from pathlib import Path

from bot.services.winner import get_winner

ROOT = Path(__file__).resolve().parents[1]

HANDLER_MODULES = (
    "bot/handlers/auction/winner_manual.py",
    "bot/handlers/auction/winner_exchange.py",
    "bot/handlers/auction/winner_print.py",
)

FEATURE_MODULES = (
    "bot/features/winner/common.py",
    "bot/features/winner/resolution.py",
    "bot/features/winner/presentation.py",
    "bot/features/winner/manual.py",
    "bot/features/winner/notifications.py",
    "bot/features/winner/exchange.py",
    "bot/features/winner/legacy.py",
    "bot/features/winner/feedback.py",
)

EXPECTED_HANDLER_ORDER = (
    "cb_print_win_edit_manual_winner",
    "cb_print_win_edit_manual_owner",
    "cb_print_win_edit_manual_amount",
    "cb_print_win_clear_manual",
    "msg_print_win_edit_single_field",
    "cmd_print_win_missed",
    "cmd_ex_owners",
    "cmd_print_ex",
    "cb_print_ex",
    "ex_manual_input",
    "cmd_print_win",
    "cb_win_edit_amt",
    "cb_win_edit_user",
    "handle_pending_edit",
    "cb_winner_send",
    "cb_winner_skip",
    "cb_print_win_refresh",
    "cb_print_win_send_owner",
    "cb_print_win_send_winner",
    "cb_print_win_send_both",
    "cb_print_win_manual",
    "msg_print_win_manual",
    "cb_win_thanks",
    "cb_print_win_edit_manual_comment",
)

EXPECTED_SERVICE_CONTRACTS = {
    *EXPECTED_HANDLER_ORDER,
    "announce_winner",
    "_post_rules_under_lot",
    "get_winner",
    "PENDING_EDIT",
    "PENDING_WIN_FIELD_EDIT",
    "PENDING_WIN_MANUAL",
    "WIN_DRAFTS",
    "CB_WIN_SEND",
    "CB_WIN_SKIP",
    "CB_WIN_SEND_OWNER",
    "CB_WIN_SEND_WINNER",
    "CB_WIN_REFRESH",
    "CB_WIN_MANUAL",
    "CB_WIN_SEND_BOTH",
    "CB_WIN_EDIT_MANUAL_WINNER",
    "CB_WIN_EDIT_MANUAL_OWNER",
    "CB_WIN_EDIT_MANUAL_AMOUNT",
    "CB_WIN_CLEAR_MANUAL",
}


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _imports(relative: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _decorated_functions(relative: str) -> list[str]:
    return [
        node.name
        for node in _tree(relative).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list
    ]


def test_winner_facade_keeps_only_the_two_real_compatibility_hooks() -> None:
    facade = _tree("bot/handlers/auction/winner.py")
    functions = {
        node.name
        for node in facade.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert functions == {"announce_winner", "_post_rules_under_lot"}
    assert len(_source("bot/handlers/auction/winner.py").splitlines()) < 80


def test_winner_handler_decorators_keep_the_original_dispatch_order() -> None:
    actual = tuple(name for relative in HANDLER_MODULES for name in _decorated_functions(relative))
    assert actual == EXPECTED_HANDLER_ORDER

    bootstrap = _source("bot/bootstrap/routers.py")
    registrations = (
        "dispatcher.include_router(auction_winner_manual_router)",
        "dispatcher.include_router(auction_winner_exchange_router)",
        "dispatcher.include_router(auction_winner_print_router)",
    )
    offsets = [bootstrap.index(item) for item in registrations]
    assert offsets == sorted(offsets)


def test_winner_handlers_are_thin_and_contain_no_database_queries() -> None:
    forbidden_tokens = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for relative in HANDLER_MODULES:
        tree = _tree(relative)
        strings = {
            node.value.upper()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not any(token in value for token in forbidden_tokens for value in strings), relative
        assert "db.db" not in _imports(relative)


def test_winner_persistence_and_service_boundaries_are_one_way() -> None:
    service_imports = set().union(*(_imports(path) for path in FEATURE_MODULES))
    repository_imports = _imports("bot/repositories/winner.py")
    assert "db.db" not in service_imports | repository_imports
    assert not any(name.startswith("bot.handlers") for name in service_imports)
    assert not any(name.startswith("bot.handlers") for name in repository_imports)
    assert "db.pool" in service_imports
    assert "asyncpg" in repository_imports

    service_strings = set().union(
        *(
            {
                node.value.upper()
                for node in ast.walk(_tree(path))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            for path in FEATURE_MODULES
        )
    )
    assert not any(
        token in value
        for token in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
        for value in service_strings
    )


def test_winner_modules_import_without_telegram_or_postgres_connections() -> None:
    for module in (
        "bot.handlers.auction.winner",
        "bot.handlers.auction.winner_manual",
        "bot.handlers.auction.winner_exchange",
        "bot.handlers.auction.winner_print",
        "bot.services.winner",
        "bot.repositories.winner",
        *(path.removesuffix(".py").replace("/", ".") for path in FEATURE_MODULES),
    ):
        importlib.import_module(module)


def test_winner_service_facade_preserves_symbol_identity_and_shared_state() -> None:
    facade = importlib.import_module("bot.services.winner")
    feature = importlib.import_module("bot.features.winner")

    assert facade.__all__ == feature.__all__
    assert EXPECTED_SERVICE_CONTRACTS <= set(feature.__all__)
    assert len(_source("bot/services/winner.py").splitlines()) < 20
    for name in feature.__all__:
        assert getattr(facade, name) is getattr(feature, name), name

    assert facade.PENDING_EDIT is feature.PENDING_EDIT
    assert facade.PENDING_WIN_FIELD_EDIT is feature.PENDING_WIN_FIELD_EDIT
    assert facade.PENDING_WIN_MANUAL is feature.PENDING_WIN_MANUAL


def test_winner_feature_modules_are_acyclic_and_bounded() -> None:
    module_names = {Path(path).stem for path in FEATURE_MODULES}
    edges: dict[str, set[str]] = {name: set() for name in module_names}

    for path in FEATURE_MODULES:
        owner = Path(path).stem
        assert len(_source(path).splitlines()) < 700, path
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                if node.module in module_names:
                    edges[owner].add(node.module)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"winner feature import cycle at {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in edges[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in module_names:
        visit(module)


def test_get_winner_preserves_auction_kind_rules() -> None:
    bids = [
        {"bidder_id": 1, "amount": 120, "placed_at": 2},
        {"bidder_id": 2, "amount": 140, "placed_at": 1},
        {"bidder_id": 3, "amount": 120, "placed_at": 1},
    ]
    assert get_winner(bids, "standard")["bidder_id"] == 2
    assert get_winner(bids, "reverse")["bidder_id"] == 3
    assert get_winner(bids, "free") is None
    assert get_winner([], "standard") is None
