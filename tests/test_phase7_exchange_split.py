from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path


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
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
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
    bootstrap = _source("bot/bootstrap/routers.py")

    for child in (
        "submission_router",
        "moderation_router",
        "catalog_router",
        "diagnostics_router",
    ):
        assert package.count(f"router.include_router({child})") == 1

    assert bootstrap.count("dispatcher.include_router(auction_exchange_router)") == 1
    assert "auction_exchange_diagnostics_router" not in bootstrap
    assert "auction_exchange_moderation_router" not in bootstrap
    assert "auction_exchange_catalog_router" not in bootstrap


def test_exchange_components_import_shared_contracts_without_sibling_cycles() -> None:
    for relative in EXCHANGE_MODULES[1:]:
        tree = ast.parse(_source(relative), filename=relative)
        top_level_imports = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "bot.handlers.auction.exchange" not in top_level_imports


def test_admin_consumers_use_current_exchange_owners() -> None:
    admin_panel = _source("bot/handlers/admin/admin_panel_shared.py")
    moderation = _source("bot/handlers/admin/moderation_shared.py")

    assert "exchange_moderation" in admin_panel or "exchange.moderation" in admin_panel
    assert "exchange_catalog" in admin_panel or "exchange.catalog" in admin_panel
    assert "show_pending_exchange_requests" in moderation


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
