from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE_MODULES = (
    "bot/handlers/auction/exchange/common.py",
    "bot/handlers/auction/exchange/submission.py",
    "bot/handlers/auction/exchange/moderation.py",
    "bot/handlers/auction/exchange/catalog.py",
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _top_level_functions(relative: str) -> set[str]:
    tree = ast.parse(_source(relative), filename=relative)
    return {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_exchange_blocks_are_extracted_by_responsibility() -> None:
    common = _top_level_functions(EXCHANGE_MODULES[0])
    submission = _top_level_functions(EXCHANGE_MODULES[1])
    moderation = _top_level_functions(EXCHANGE_MODULES[2])
    catalog = _top_level_functions(EXCHANGE_MODULES[3])

    assert "exchange_deck_keyboard" in common
    assert {"ex_deck_selected", "ex_mode_selected"} <= submission
    assert {"pending_menu_pick", "exchange_approve", "show_pending_exchange_requests"} <= moderation
    assert {"_kb_exchange_approved_root", "_q_exchange_approved_decks"} <= catalog
    assert "exchange_approve" not in submission


def test_exchange_routers_are_registered_once_in_handler_order() -> None:
    package = _source("bot/handlers/auction/exchange/__init__.py")

    for child in (
        "submission_router",
        "moderation_router",
        "catalog_router",
        "diagnostics_router",
    ):
        assert package.count(f"router.include_router({child})") == 1

    matches = [
        feature
        for feature in get_router_registry().ordered_features
        if feature.name == "exchange.catalog"
    ]
    assert len(matches) == 1
    exchange = matches[0]
    assert exchange.router is not None
    assert exchange.router.name == "auction_exchange"
    assert exchange.callback_namespaces == ("exchange", "ex_view")


def test_exchange_components_import_shared_contracts_without_sibling_cycles() -> None:
    for relative in EXCHANGE_MODULES[1:]:
        tree = ast.parse(_source(relative), filename=relative)
        top_level_imports = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "bot.handlers.auction.exchange" not in top_level_imports


def test_admin_consumers_use_public_exchange_contracts() -> None:
    requests = _source("bot/handlers/admin/admin_panel_requests.py")
    diagnostics = _source("bot/handlers/admin/moderation_diagnostics.py")
    queue = _source("bot/handlers/admin/presentation/exchange_queue.py")
    admin_exchange = _source("bot/handlers/admin/admin_panel_exchange.py")

    assert "from bot.handlers.auction.exchange_catalog import (" in requests
    assert "kb_exchange_approved_root" in requests
    assert "show_pending_exchange_requests" in diagnostics
    assert "format_pending_exchange_batch_card" in queue
    assert "from bot.handlers.auction.exchange.common import currency_to_emoji" in admin_exchange
    assert "from bot.handlers.auction.exchange import currency_to_emoji" not in admin_exchange
    assert "import _" not in requests


def test_exchange_split_has_no_unresolved_globals() -> None:
    known = set(dir(builtins)) | {
        "__conditional_annotations__",
        "__doc__",
        "__file__",
        "__name__",
        "__package__",
    }
    for relative in EXCHANGE_MODULES:
        table = symtable.symtable(_source(relative), relative, "exec")
        defined = {
            name
            for name in table.get_identifiers()
            if table.lookup(name).is_assigned()
            or table.lookup(name).is_imported()
            or table.lookup(name).is_namespace()
        }
        referenced_globals: set[str] = set()
        pending = [table]
        while pending:
            current = pending.pop()
            referenced_globals.update(
                name
                for name in current.get_identifiers()
                if current.lookup(name).is_global() and current.lookup(name).is_referenced()
            )
            pending.extend(current.get_children())
        assert not (referenced_globals - defined - known), relative
