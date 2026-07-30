from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINNER_FACADE = ROOT / "bot/handlers/auction/winner.py"
WINNER_COMPONENTS = ROOT / "bot/handlers/auction/winner_components"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _handlers(path: Path) -> list[str]:
    tree = ast.parse(_source(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("router." in ast.unparse(decorator) for decorator in node.decorator_list)
    ]


def test_winner_facade_is_thin_and_keeps_public_contract() -> None:
    source = _source(WINNER_FACADE)
    assert len(source.splitlines()) < 120
    assert "router.include_router(announcement_router)" in source
    assert "router.include_router(print_win_router)" in source
    assert "router.include_router(print_exchange_router)" in source
    assert "router.include_router(thanks_router)" in source
    for public_name in ("announce_winner", "cmd_print_win", "_post_rules_under_lot", "_send_notifications"):
        assert f"def {public_name}" in source
    assert "from db.db import" not in source


def test_winner_handlers_are_preserved_and_split_by_responsibility() -> None:
    expected = {
        "announcement.py": 5,
        "print_win.py": 14,
        "print_exchange.py": 4,
        "thanks.py": 1,
    }
    names: list[str] = []
    for filename, count in expected.items():
        module_handlers = _handlers(WINNER_COMPONENTS / filename)
        assert len(module_handlers) == count
        names.extend(module_handlers)
    assert len(names) == 24
    assert len(names) == len(set(names))


def test_winner_handlers_use_service_repository_boundary() -> None:
    handler_source = "\n".join(
        _source(path)
        for path in WINNER_COMPONENTS.glob("*.py")
        if path.name != "__init__.py"
    )
    assert "AuctionWinnerService.create()" in handler_source
    assert "from db.db import" not in handler_source
    for sql_keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE"):
        assert sql_keyword not in handler_source

    service = _source(ROOT / "bot/services/auction_winners.py")
    repository = _source(ROOT / "bot/repositories/auction_winners.py")
    assert "AuctionWinnerRepository" in service
    assert "public.auction_manual_results" in repository
    assert "public.auction_win_mailings" in repository
    assert "public.exchange_print_stats" in repository


def test_winner_schema_is_migrated_not_created_by_handlers() -> None:
    migration = _source(ROOT / "migrations/007_winner_workflows.sql")
    assert "CREATE TABLE IF NOT EXISTS public.auction_win_mailings" in migration
    assert "CREATE TABLE IF NOT EXISTS public.auction_manual_results" in migration
    assert "CREATE TABLE IF NOT EXISTS public.admin_thanks_totals" in migration
    assert "chk_auction_win_mailings_target" in migration
    assert "chk_auction_manual_results_amount" in migration

    all_handlers = _source(WINNER_FACADE) + "\n" + "\n".join(
        _source(path) for path in WINNER_COMPONENTS.glob("*.py")
    )
    assert "CREATE TABLE" not in all_handlers
    assert "ALTER TABLE" not in all_handlers


def test_print_win_filter_and_reverse_auction_result_are_correct() -> None:
    print_win = _source(WINNER_COMPONENTS / "print_win.py")
    announcement = _source(WINNER_COMPONENTS / "announcement.py")
    assert '@router.message(Command("print_win"))' in print_win
    assert 'F.text.startswith("/print_win")' not in print_win
    assert "AuctionKind.from_raw(auction.get(\"auction_kind\"))" in print_win
    assert "lowest_wins=kind.lowest_bid_wins" in print_win
    assert "lowest_wins=kind.lowest_bid_wins" in announcement


def test_admin_thanks_increment_is_atomic_and_deduplicates_users() -> None:
    repository = _source(ROOT / "bot/repositories/auction_winners.py")
    block = repository[
        repository.index("async def increment_admin_thanks"):
        repository.index("async def admin_thanks_totals")
    ]
    assert "async with conn.transaction()" in block
    assert "ON CONFLICT (author, user_id) DO NOTHING" in block
    assert "1 if inserted else 0" in block
    totals = repository[repository.index("async def admin_thanks_totals"):]
    assert "COUNT(DISTINCT user_id)" in totals
    assert "lower(trim(leading '@' FROM author))" in totals


def test_winner_split_has_no_unresolved_globals() -> None:
    paths = [WINNER_FACADE, *sorted(WINNER_COMPONENTS.glob("*.py"))]
    known = set(dir(builtins)) | {"__doc__", "__file__", "__name__", "__package__"}
    for path in paths:
        table = symtable.symtable(_source(path), str(path), "exec")
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
        assert not (referenced_globals - defined - known), path
