from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_NAMES = (
    "card_economy_mutation",
    "card_economy_luxury",
    "card_economy_subscriptions",
    "card_economy_winner_print",
)
CAPABILITY_FILES = tuple(
    ROOT / "bot/handlers/admin/helper/new" / f"{name}.py"
    for name in CAPABILITY_NAMES
)
SHARED = ROOT / "bot/handlers/admin/helper/new/card_economy_shared.py"
FACADE = ROOT / "bot/handlers/admin/helper/new/card_economy.py"


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


def _walk_handlers(router, event_name: str):
    yield from router.observers[event_name].handlers
    for child in router.sub_routers:
        yield from _walk_handlers(child, event_name)


def test_card_economy_aggregate_preserves_handler_order_and_admin_wrapping() -> None:
    facade = importlib.import_module(
        "bot.handlers.admin.helper.new.card_economy"
    )
    assert tuple(child.name for child in facade.router.sub_routers) == (
        "admin_card_economy_mutation",
        "admin_card_economy_luxury",
        "admin_card_economy_subscriptions",
        "admin_card_economy_winner_print",
    )

    messages = tuple(
        (
            handler.callback.__name__,
            bool(getattr(handler.callback, "__wrapped__", None)),
            len(handler.filters),
        )
        for handler in _walk_handlers(facade.router, "message")
    )
    assert messages == (
        ("economy_root", True, 1),
        ("cmd_decktype", True, 1),
        ("fsm_deck_id", True, 2),
        ("fsm_deck_type", True, 2),
        ("cmd_obtain", True, 1),
        ("fsm_obtain_card_id", True, 2),
        ("fsm_obtain_type", True, 2),
        ("fsm_obtain_amount", True, 2),
        ("cmd_lux_top", False, 2),
        ("subs_confirm_broadcast", True, 1),
        ("subs_confirm_test", True, 1),
        ("cmd_id", False, 1),
        ("cmd_print", False, 1),
    )

    callbacks = tuple(
        (
            handler.callback.__name__,
            bool(getattr(handler.callback, "__wrapped__", None)),
            len(handler.filters),
        )
        for handler in _walk_handlers(facade.router, "callback_query")
    )
    assert callbacks == (
        ("economy_cb", True, 1),
        ("lux_top_pager", False, 1),
        ("open_presets_manager_from_decks", False, 2),
        ("open_subscribe_from_broadcast", False, 1),
        ("open_presets_manager_from_decks", False, 2),
        ("subs_confirm_callback", False, 1),
        ("sc_confirm_all", False, 1),
        ("sc_close", False, 1),
        ("sc_confirm", False, 1),
        ("sc_unsubscribe", False, 1),
    )


EXPECTED_ROUTER_CONTRACTS = (
    ("economy_root", "router.message(F.text == '💰 Экономика')"),
    ("economy_cb", "router.callback_query(F.data.startswith('economy:'))"),
    ("cmd_decktype", "router.message(Command('decktype'))"),
    ("fsm_deck_id", "router.message(EconomyFSM.deck_id, F.text)"),
    ("fsm_deck_type", "router.message(EconomyFSM.deck_type, F.text)"),
    ("cmd_obtain", "router.message(Command('obtain'))"),
    ("fsm_obtain_card_id", "router.message(EconomyFSM.obtain_card_id, F.text)"),
    ("fsm_obtain_type", "router.message(EconomyFSM.obtain_type, F.text)"),
    (
        "fsm_obtain_amount",
        "router.message(EconomyFSM.obtain_amount, F.text.regexp('^\\\\d+$'))",
    ),
    (
        "cmd_lux_top",
        "router.message(Command('lux_top'), F.chat.type == 'private')",
    ),
    ("lux_top_pager", "router.callback_query(F.data.startswith('lt:'))"),
    (
        "subs_confirm_broadcast",
        "router.message(Command('subs_confirm_broadcast'))",
    ),
    (
        "open_presets_manager_from_decks",
        "router.callback_query(CardSubscribeFSM.waiting_for_deck, "
        "F.data.in_({'sub:presets_open', 'sub:preset:any_card'}))",
    ),
    (
        "<register>",
        "router.callback_query.register(open_subscribe_from_broadcast, "
        "F.data == 'sub:open')",
    ),
    (
        "<register>",
        "router.callback_query.register(open_presets_manager_from_decks, "
        "CardSubscribeFSM.waiting_for_deck, "
        "F.data.in_({'sub:presets_open', 'sub:preset:any_card'}))",
    ),
    (
        "subs_confirm_callback",
        "router.callback_query(F.data.startswith(f'{SUBS_CONFIRM_CB}:'))",
    ),
    ("subs_confirm_test", "router.message(Command('subs_confirm_test'))"),
    ("sc_confirm_all", "router.callback_query(F.data == 'sc:ok_all')"),
    ("sc_close", "router.callback_query(F.data == 'sc:close')"),
    ("sc_confirm", "router.callback_query(F.data.startswith(CONF_CB_PREFIX))"),
    ("cmd_id", "router.message(Command('id'))"),
    (
        "sc_unsubscribe",
        "router.callback_query(F.data.startswith(UNSUB_CB_PREFIX))",
    ),
    ("cmd_print", "router.message(Command('print'))"),
)


def _router_contracts(path: Path) -> list[tuple[str, str]]:
    contracts: list[tuple[str, str]] = []
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                rendered = ast.unparse(decorator)
                if rendered.startswith("router."):
                    contracts.append((node.name, rendered))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            rendered = ast.unparse(node.value)
            if rendered.startswith("router.") and ".register(" in rendered:
                contracts.append(("<register>", rendered))
    return contracts


def test_card_economy_command_callback_and_fsm_contracts_are_unchanged() -> None:
    actual = tuple(
        contract
        for path in CAPABILITY_FILES
        for contract in _router_contracts(path)
    )
    assert actual == EXPECTED_ROUTER_CONTRACTS


def test_card_economy_facade_is_thin_and_reexports_legacy_symbols() -> None:
    facade = importlib.import_module(
        "bot.handlers.admin.helper.new.card_economy"
    )
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) < 50
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in _tree(FACADE).body
    )

    implementation_paths = (SHARED, *CAPABILITY_FILES)
    legacy_definitions = {
        node.name
        for path in implementation_paths
        for node in _tree(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        name for name in legacy_definitions if not hasattr(facade, name)
    } == set()


def test_card_economy_capabilities_form_an_acyclic_shared_dependency_star() -> None:
    prefix = "bot.handlers.admin.helper.new.card_economy_"
    allowed = {f"{prefix}shared"}
    for path in CAPABILITY_FILES:
        capability_dependencies = {
            dependency
            for dependency in _imports(path)
            if dependency.startswith(prefix)
        }
        assert capability_dependencies <= allowed, path.name

    assert not {
        dependency
        for dependency in _imports(SHARED)
        if dependency.startswith(prefix)
    }

