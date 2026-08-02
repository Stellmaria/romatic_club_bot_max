from __future__ import annotations

import ast
from pathlib import Path

from bot.bootstrap.routers import get_router_registry

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_submission_imports_auction_kind() -> None:
    source = _source("bot/handlers/auction/submission.py")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "bot.domain.auctions"
        for alias in node.names
    }
    assert "AuctionKind" in imports
    assert "auction_kind == AuctionKind.REVERSE.value" in source


def test_all_supported_auction_kinds_continue_to_deck_selection() -> None:
    source = _source("bot/handlers/auction/submission.py")
    assert "selected_kind = AuctionKind.from_raw(kind)" in source
    assert "selected_kind.minimum_luxury_level" in source
    assert "Пока в разработке" not in source
    assert "await state.update_data(auction_kind=selected_kind.value)" in source


def test_stale_unpublished_lots_stop_blocking_new_submissions() -> None:
    source = _source("db/repositories/auctions.py")
    assert "async def release_stale_unpublished_lots" in source
    assert "CURRENT_TIMESTAMP - INTERVAL '10 minutes'" in source
    assert "SET status = 'publication_failed'" in source
    assert "async def cancel_owner_unpublished_lots" in source
    assert "SET status = 'cancelled'" in source


def test_owner_has_recovery_command_and_button() -> None:
    source = _source("bot/handlers/auction/submission_recovery.py")
    features = {feature.name: feature for feature in get_router_registry().ordered_features}

    assert 'Command("cancel_pending")' in source
    assert 'callback_data="user_cancel_pending_lots"' in source
    assert "await release_stale_unpublished_lots(int(user_id))" in source
    assert "await cancel_owner_unpublished_lots(int(user_id))" in source
    recovery = features["auctions.submission-recovery"]
    assert recovery.router is not None
    assert recovery.router.name == "bot.handlers.auction.submission_recovery"
    assert recovery.callback_namespaces == ("submission_recovery",)


def test_status_constraint_supports_recovery_states() -> None:
    sql = _source("db/migrations/007_submission_recovery_and_cancel.sql")
    assert "'publication_failed'" in sql
    assert "'cancelled'" in sql
    assert "ix_auctions_unpublished_owner_recovery" in sql


def test_publisher_cleanup_is_timezone_safe() -> None:
    source = _source("bot/handlers/auction/publication.py")
    assert "stale_ids = await service.recover_stale()" in source
    assert "service.claim_due(now=utc_now(), limit=20)" in source
    assert "except asyncio.CancelledError" in source
