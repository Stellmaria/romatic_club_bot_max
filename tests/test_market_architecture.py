from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKET_HANDLER_DIR = ROOT / "bot/handlers/admin/services"
MARKET_HANDLER_FILES = sorted(MARKET_HANDLER_DIR.glob("market*.py"))
MARKET_FLOW_CHILDREN = (
    "market_entry_flow.py",
    "market_create_flow.py",
    "market_edit_flow.py",
    "market_search_flow.py",
    "market_my_sales_flow.py",
)
SQL_PREFIX = re.compile(
    r"^\s*(?:select|insert|update|delete)\b",
    flags=re.IGNORECASE,
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_market_handlers_have_no_database_or_pool_dependency() -> None:
    for path in MARKET_HANDLER_FILES:
        imports = _imports(path)
        assert not any(name == "db" or name.startswith("db.") for name in imports), path

        names = {
            node.id
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Name)
        }
        assert not ({"pool_proxy", "get_db_pool", "db_pool"} & names), path


def test_market_handlers_contain_no_raw_data_mutation_or_queries() -> None:
    for path in MARKET_HANDLER_FILES:
        statements = [
            node.value
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) > 20
            and SQL_PREFIX.search(node.value)
        ]
        assert statements == [], path


def test_market_service_and_repository_form_one_way_boundary() -> None:
    repository = ROOT / "bot/repositories/market.py"
    service = ROOT / "bot/services/market.py"

    assert "bot.handlers" not in " ".join(_imports(repository))
    assert "bot.handlers" not in " ".join(_imports(service))
    assert "db.db" not in _imports(repository)
    assert "db.db" not in _imports(service)
    assert "db.core" not in _imports(repository)
    assert "db.core" not in _imports(service)
    assert "db.pool" in _imports(service)
    assert "bot.repositories.market" in _imports(service)


def test_market_handlers_are_registered_once_at_module_scope() -> None:
    go_handlers: list[tuple[str, str]] = []
    for filename in MARKET_FLOW_CHILDREN:
        path = MARKET_HANDLER_DIR / filename
        tree = _tree(path)
        top_level_functions = {
            id(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                rendered = ast.unparse(decorator)
                if rendered.startswith("router."):
                    assert id(node) in top_level_functions, (
                        f"runtime handler registration is forbidden: {path}:{node.lineno}"
                    )
                if rendered.startswith("router.callback_query") and ":go:" in rendered:
                    go_handlers.append((filename, node.name))

    assert go_handlers == [("market_entry_flow.py", "market_panel_go")]


def _handler_names(router, event_name: str) -> tuple[str, ...]:
    names = [handler.callback.__name__ for handler in router.observers[event_name].handlers]
    for child in router.sub_routers:
        names.extend(_handler_names(child, event_name))
    return tuple(names)


def test_market_aggregate_preserves_handler_order() -> None:
    from bot.handlers.admin.services.market_add_flow import router

    assert tuple(child.name for child in router.sub_routers) == (
        "market_flow_entry",
        "market_flow_create",
        "market_flow_edit",
        "market_flow_search",
        "market_flow_my_sales",
        "market_flow_create_continuation",
        "market_flow_my_sales_continuation",
    )
    assert _handler_names(router, "message") == (
        "sell_start",
        "my_sales_open",
        "my_sales_open",
        "rk_sell",
        "rk_find",
        "_sell_btn",
        "cover_step",
        "msg_custom_variant",
        "cash_code_entered",
        "set_photo_message",
        "set_desc_message",
        "set_qty_message",
        "market_find",
        "market_find_query",
        "market_find_filters",
        "tiers_step",
        "quantity_step",
        "ask_proof_choice",
        "proof_each_photo",
        "description_step",
        "proof_single_photo",
        "proof_single_text",
        "msg_price_bulk",
        "msg_custom_qty_input",
        "price_entered",
        "description_photo_step",
        "my_sales_filter_click",
    )
    assert _handler_names(router, "callback_query") == (
        "market_panel_go",
        "choose_kind",
        "cb_currency_toggle",
        "cb_currency_done",
        "cb_cur_custom",
        "cb_cash_add",
        "cb_set_deck_mode",
        "cb_confirm_yes",
        "edit_action",
        "do_delete_listing",
        "cb_mark_sold",
        "cb_mark_sold_yes",
        "cb_mark_sold_no",
        "do_soldqty",
        "cb_my_sales_tabs",
        "add_proof_skip",
        "add_proof_single",
        "add_proof_each_start",
        "add_proof_each_skip_one",
        "cb_choose_deck",
        "cb_cards_page",
        "cb_cards_reset",
        "cb_toggle_card",
        "cb_back_to_decks",
        "cb_cards_done",
        "cb_cash_toggle",
        "cb_cash_done",
        "cb_cur_add_cashcustom",
        "cb_custom_qty_skip",
        "cb_custom_qty_ask",
        "proof_each_skip",
        "cb_proof_skip",
        "cb_cancel",
        "cb_deckmode_bulk",
        "cb_deckmode_split",
        "my_sales_nav",
        "my_sales_actions",
    )


def test_market_facade_is_thin_and_children_do_not_import_it() -> None:
    facade = MARKET_HANDLER_DIR / "market_add_flow.py"
    assert len(facade.read_text(encoding="utf-8").splitlines()) < 60
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.decorator_list
        for node in _tree(facade).body
    )

    for filename in MARKET_FLOW_CHILDREN:
        imports = _imports(MARKET_HANDLER_DIR / filename)
        assert "bot.handlers.admin.services.market_add_flow" not in imports, filename
