from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCHANGE_MODULES = (
    "bot/handlers/auction/exchange.py",
    "bot/handlers/auction/exchange_moderation.py",
    "bot/handlers/auction/exchange_catalog.py",
    "bot/handlers/auction/exchange_diagnostics.py",
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
    submission = _top_level_functions(EXCHANGE_MODULES[0])
    moderation = _top_level_functions(EXCHANGE_MODULES[1])
    catalog = _top_level_functions(EXCHANGE_MODULES[2])
    diagnostics = _top_level_functions(EXCHANGE_MODULES[3])

    assert {"exchange_deck_keyboard", "_finalize_exchange_request"} <= submission
    assert "show_pending_exchange_requests" in submission  # compatibility wrapper
    assert {"pending_menu_pick", "exchange_approve", "show_pending_exchange_requests"} <= moderation
    assert {"_kb_exchange_approved_root", "ex_appr_decks", "ex_view_card"} <= catalog
    assert {"cmd_print_ex_multi", "cmd_ex_lot", "cmd_ex_dump"} <= diagnostics

    moved = {
        "pending_menu_pick",
        "exchange_approve",
        "_kb_exchange_approved_root",
        "ex_appr_decks",
        "cmd_print_ex_multi",
        "cmd_ex_lot",
    }
    assert not (moved & submission)


def test_exchange_routers_are_registered_contiguously_in_handler_order() -> None:
    main = _source("bot/bootstrap/routers.py")
    registrations = (
        "dispatcher.include_router(auction_exchange_router)",
        "dispatcher.include_router(auction_exchange_moderation_router)",
        "dispatcher.include_router(auction_exchange_catalog_router)",
        "dispatcher.include_router(auction_exchange_diagnostics_router)",
    )
    positions = [main.index(registration) for registration in registrations]
    assert positions == sorted(positions)

    between = main[positions[0]: positions[-1] + len(registrations[-1])]
    assert between.count("dispatcher.include_router(") == len(registrations)


def test_submission_has_no_top_level_import_of_extracted_modules() -> None:
    tree = ast.parse(_source(EXCHANGE_MODULES[0]), filename=EXCHANGE_MODULES[0])
    top_level_imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "bot.handlers.auction.exchange_moderation" not in top_level_imports
    assert "bot.handlers.auction.exchange_catalog" not in top_level_imports
    assert "bot.handlers.auction.exchange_diagnostics" not in top_level_imports

    finalize = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_finalize_exchange_request"
    )
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "bot.handlers.auction.exchange_moderation"
        for node in finalize.body
    )


def test_admin_consumers_import_extracted_implementations_directly() -> None:
    admin_panel = _source("bot/handlers/admin/admin_panel_shared.py")
    moderation = _source("bot/handlers/admin/moderation_shared.py")

    assert "from bot.handlers.auction.exchange_moderation import (" in admin_panel
    assert "from bot.handlers.auction.exchange_catalog import (" in admin_panel
    assert (
        "from bot.handlers.auction.exchange_moderation "
        "import show_pending_exchange_requests"
    ) in moderation


def test_exchange_split_has_no_unresolved_globals() -> None:
    known = set(dir(builtins)) | {
        "__conditional_annotations__",  # synthetic Python 3.14 symtable name
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
            if (
                table.lookup(name).is_assigned()
                or table.lookup(name).is_imported()
                or table.lookup(name).is_namespace()
            )
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
