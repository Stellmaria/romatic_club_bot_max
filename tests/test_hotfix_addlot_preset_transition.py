from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "bot/handlers/auctions.py"


def _top_level_functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_legacy_router_defines_currency_transition_helper_used_by_presets() -> None:
    functions = _top_level_functions()
    assert "_ask_for_currency" in functions

    helper = ast.unparse(functions["_ask_for_currency"])
    assert "UserAddLotFSM.waiting_for_currency" in helper
    assert "auction_currency_kb(kind)" in helper

    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert source.count("await _ask_for_currency(") >= 10


def test_pending_preview_accepts_custom_free_auction_terms() -> None:
    functions = _top_level_functions()
    preview = functions["_send_user_pending_lot_preview"]
    kwonly_names = [argument.arg for argument in preview.args.kwonlyargs]

    assert "accepted_currencies" in kwonly_names
    assert "custom_offer_terms" in kwonly_names
    assert "custom_terms=custom_offer_terms" in ast.unparse(preview)
