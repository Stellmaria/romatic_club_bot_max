from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_submission_imports_auction_kind() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
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
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
    assert "selected_kind = AuctionKind.from_raw(kind)" in source
    assert "selected_kind.minimum_luxury_level" in source
    assert "Пока в разработке" not in source
    assert "await state.update_data(auction_kind=selected_kind.value)" in source


def test_stale_unpublished_lots_stop_blocking_new_submissions() -> None:
    source = (ROOT / "db/db.py").read_text(encoding="utf-8")
    assert "async def release_stale_unpublished_lots" in source
    assert "CURRENT_TIMESTAMP - INTERVAL '10 minutes'" in source
    assert "SET status = 'publication_failed'" in source
    assert "async def cancel_owner_unpublished_lots" in source
    assert "SET status = 'cancelled'" in source


def test_owner_has_recovery_command_and_button() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
    assert 'Command("cancel_pending")' in source
    assert 'callback_data="user_cancel_pending_lots"' in source
    assert "await release_stale_unpublished_lots(message.from_user.id)" in source


def test_status_constraint_supports_recovery_states() -> None:
    sql = (
        ROOT / "db/migrations/007_submission_recovery_and_cancel.sql"
    ).read_text(encoding="utf-8")
    assert "'publication_failed'" in sql
    assert "'cancelled'" in sql
    assert "ix_auctions_unpublished_owner_recovery" in sql


def test_publisher_cleanup_is_timezone_safe() -> None:
    source = (ROOT / "bot/handlers/auctions.py").read_text(encoding="utf-8")
    assert "now = utc_now()" in source
    assert "ensure_utc(start_time) > now" in source
    assert "released = await release_stale_unpublished_lots()" in source
