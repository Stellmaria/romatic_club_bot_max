from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_legacy_bid_handlers_were_removed_from_monolith() -> None:
    functions = _top_level_functions(ROOT / "bot/handlers/auction_comments.py")
    assert "filter_auction_bids" not in functions
    assert "edited_bid_handler" not in functions
    assert "parse_bid" not in functions
    assert "get_max_bid_for_auction" not in functions


def test_autobid_commands_were_removed_from_auction_monolith() -> None:
    functions = _top_level_functions(ROOT / "bot/handlers/auctions.py")
    assert "cmd_autobid_set" not in functions
    assert "cmd_autobid_stop" not in functions
    assert "cmd_autobid_list" not in functions
    assert "_default_step_for_currency" not in functions


def test_new_auction_routers_are_registered() -> None:
    source = (ROOT / "bot/bootstrap/routers.py").read_text(encoding="utf-8")
    assert "auction_bidding_router" in source
    assert "auction_autobid_router" in source
    assert "dispatcher.include_router(auction_bidding_router)" in source
    assert "dispatcher.include_router(auction_autobid_router)" in source


def test_bid_repository_uses_row_lock_and_message_id_uniqueness() -> None:
    repository = (ROOT / "bot/repositories/auction_bids.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/003_auction_bid_integrity.sql").read_text(encoding="utf-8")
    assert "FOR UPDATE" in repository
    assert "async with connection.transaction()" in repository
    assert "ux_bids_discussion_message_id" in migration
    assert "bid_duplicate_archive" in migration


def test_userbot_reuses_shared_pool_and_shared_bid_service() -> None:
    repository = (ROOT / "userbot/repositories.py").read_text(encoding="utf-8")
    handler = (ROOT / "userbot/handlers/new_messages.py").read_text(encoding="utf-8")
    assert "asyncpg.create_pool" not in repository
    assert "pool=await get_db_pool()" in repository
    assert "AuctionBidService.create()" in handler
    assert "await service.place_for_auction(" in handler


def test_oops_workflow_is_implemented() -> None:
    source = (ROOT / "userbot/handlers/new_messages.py").read_text(encoding="utf-8")
    assert "await service.revise_bid(" in source
    assert "revision_window_seconds=OOPS_EDIT_WINDOW_SEC" in source
    assert "ТВОЙ БЛОК /oops" not in source
    assert "(Вставь сюда твой исходный блок /oops" not in source


def test_autobid_password_has_no_public_default() -> None:
    source = (ROOT / "config.py").read_text(encoding="utf-8")
    assert 'AUTOBID_SET_PASSWORD = os.getenv("AUTOBID_SET_PASSWORD", "").strip()' in source
    assert '"2069"' not in source


def test_bidding_router_does_not_swallow_messages_when_disabled() -> None:
    source = (ROOT / "bot/handlers/auction/bidding.py").read_text(encoding="utf-8")
    assert "from aiogram.dispatcher.event.bases import SkipHandler" in source
    assert "if not _bot_bid_validation_enabled():\n        raise SkipHandler" in source
